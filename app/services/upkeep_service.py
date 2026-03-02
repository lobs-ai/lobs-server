"""Upkeep service — scheduled review sweeps and documentation maintenance.

Callables:
  - upkeep.review_sweep: Scan for completed programmer tasks lacking review
  - upkeep.doc_scan: Scan project repos for stale/missing documentation
"""

import logging
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


async def review_sweep(db: AsyncSession, worker_manager=None, context=None, **kw) -> dict[str, Any]:
    """Find completed programmer tasks that haven't been reviewed yet.

    Returns a list of task summaries for Lobs to triage.
    Does NOT auto-create tasks — Lobs decides what's worth reviewing.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    result = await db.execute(
        select(Task).where(
            and_(
                Task.agent == "programmer",
                Task.status == "completed",
                Task.updated_at >= cutoff,
            )
        ).order_by(Task.updated_at.desc())
    )
    completed_tasks = result.scalars().all()

    if not completed_tasks:
        return {"status": "ok", "unreviewed": 0, "message": "No recent programmer completions"}

    # Check which already have a reviewer task linked
    reviewed_task_ids = set()
    for task in completed_tasks:
        review_result = await db.execute(
            select(Task.id).where(
                and_(
                    Task.agent == "reviewer",
                    Task.notes.contains(task.id),
                )
            )
        )
        if review_result.scalar_one_or_none():
            reviewed_task_ids.add(task.id)

    unreviewed = [t for t in completed_tasks if t.id not in reviewed_task_ids]

    if not unreviewed:
        return {"status": "ok", "unreviewed": 0, "message": "All recent tasks already reviewed"}

    summaries = []
    for t in unreviewed:
        summaries.append({
            "task_id": t.id,
            "title": t.title,
            "project_id": t.project_id,
            "completed_at": t.updated_at.isoformat() if t.updated_at else None,
        })

    return {
        "status": "ok",
        "unreviewed": len(unreviewed),
        "tasks": summaries,
    }


async def doc_scan(db: AsyncSession, worker_manager=None, context=None, **kw) -> dict[str, Any]:
    """Scan project repos for stale or missing documentation.

    Checks README.md, ARCHITECTURE.md staleness, and doc drift
    (code commits without corresponding doc updates).
    """
    from app.models import Project

    result = await db.execute(
        select(Project).where(
            and_(
                Project.archived == False,
                Project.repo_path != None,
                Project.repo_path != "",
            )
        )
    )
    projects = result.scalars().all()

    findings = []
    for project in projects:
        findings.extend(_scan_repo_docs(project.repo_path, project.id, project.title))

    if not findings:
        return {"status": "ok", "findings": 0, "message": "All docs look current"}

    return {"status": "ok", "findings": len(findings), "details": findings}


def _scan_repo_docs(repo_path: str, project_id: str, project_title: str) -> list[dict]:
    """Scan a single repo for doc staleness. Pure git/filesystem checks."""
    from pathlib import Path

    findings = []
    repo = Path(repo_path)

    if not repo.exists():
        return [{"project": project_id, "type": "missing_repo", "detail": f"Path not found: {repo_path}"}]

    # README check
    readme = repo / "README.md"
    if not readme.exists():
        findings.append({
            "project": project_id, "project_title": project_title,
            "type": "missing_readme", "detail": "No README.md", "priority": "high",
        })
    else:
        readme_age = _file_last_commit_age(repo_path, "README.md")
        code_age = _last_code_commit_age(repo_path)
        if readme_age and code_age and readme_age > 30 and code_age < 7:
            findings.append({
                "project": project_id, "project_title": project_title,
                "type": "stale_readme",
                "detail": f"README last updated {readme_age}d ago, code changed {code_age}d ago",
                "priority": "medium",
            })

    # ARCHITECTURE.md for substantial projects
    arch = repo / "ARCHITECTURE.md"
    code_files = sum(1 for _ in repo.rglob("*.py")) + sum(1 for _ in repo.rglob("*.swift")) + sum(1 for _ in repo.rglob("*.ts"))
    if code_files > 20 and not arch.exists():
        findings.append({
            "project": project_id, "project_title": project_title,
            "type": "missing_architecture",
            "detail": f"No ARCHITECTURE.md ({code_files} code files)",
            "priority": "low",
        })

    # Doc drift: code commits without doc commits
    drift = _check_doc_drift(repo_path)
    if drift and drift > 10:
        findings.append({
            "project": project_id, "project_title": project_title,
            "type": "doc_drift",
            "detail": f"{drift} code commits in 14d with no doc changes",
            "priority": "medium",
        })

    return findings


def _file_last_commit_age(repo_path: str, filepath: str) -> int | None:
    try:
        r = subprocess.run(
            ["git", "-C", repo_path, "log", "-1", "--format=%ct", "--", filepath],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            ts = int(r.stdout.strip())
            return (datetime.now(timezone.utc) - datetime.fromtimestamp(ts, tz=timezone.utc)).days
    except Exception:
        pass
    return None


def _last_code_commit_age(repo_path: str) -> int | None:
    try:
        r = subprocess.run(
            ["git", "-C", repo_path, "log", "-1", "--format=%ct", "--",
             "*.py", "*.swift", "*.ts", "*.js", "*.tsx", "*.jsx"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            ts = int(r.stdout.strip())
            return (datetime.now(timezone.utc) - datetime.fromtimestamp(ts, tz=timezone.utc)).days
    except Exception:
        pass
    return None


def _check_doc_drift(repo_path: str) -> int | None:
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
        code_r = subprocess.run(
            ["git", "-C", repo_path, "log", "--oneline", f"--since={cutoff}", "--",
             "*.py", "*.swift", "*.ts", "*.js"],
            capture_output=True, text=True, timeout=5,
        )
        doc_r = subprocess.run(
            ["git", "-C", repo_path, "log", "--oneline", f"--since={cutoff}", "--",
             "*.md", "docs/"],
            capture_output=True, text=True, timeout=5,
        )
        if code_r.returncode == 0 and doc_r.returncode == 0:
            code_n = len(code_r.stdout.strip().split("\n")) if code_r.stdout.strip() else 0
            doc_n = len(doc_r.stdout.strip().split("\n")) if doc_r.stdout.strip() else 0
            return max(0, code_n - doc_n)
    except Exception:
        pass
    return None
