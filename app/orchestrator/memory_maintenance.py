"""Daily memory maintenance for all OpenClaw agent workspaces.

Audits and cleans up agent memory files according to OpenClaw best practices:
- memory/ files should be YYYY-MM-DD.md dated daily logs
- Non-dated files in memory/ get consolidated into MEMORY.md
- Daily files older than 30 days get pruned
- MEMORY.md stays concise and curated
- Stale spawn/autoassign sessions get cleaned up

Runs as a hook in the RoutineRunner or as a daily phase in the control loop.
Can also spawn an OpenClaw worker for deeper AI-assisted cleanup.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Agent workspace paths (relative to home)
HOME = Path.home()
AGENT_WORKSPACES = {
    "main": HOME / ".openclaw" / "workspace",
    "programmer": HOME / ".openclaw" / "workspace-programmer",
    "researcher": HOME / ".openclaw" / "workspace-researcher",
    "architect": HOME / ".openclaw" / "workspace-architect",
    "reviewer": HOME / ".openclaw" / "workspace-reviewer",
    "writer": HOME / ".openclaw" / "workspace-writer",
}

SESSIONS_BASE = HOME / ".openclaw" / "agents"

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}")
MAX_DAILY_AGE_DAYS = 30
MAX_SESSION_AGE_HOURS = 1
MAX_MEMORY_MD_LINES = 300


def _is_dated_file(filename: str) -> bool:
    """Check if a filename starts with YYYY-MM-DD."""
    return bool(DATE_PATTERN.match(filename))


def _file_age_days(filepath: Path) -> float:
    """Get age of file in days based on filename date or mtime."""
    name = filepath.stem
    match = DATE_PATTERN.match(name)
    if match:
        try:
            file_date = datetime.strptime(match.group(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return (now - file_date).total_seconds() / 86400
        except ValueError:
            pass
    # Fallback to mtime
    mtime = filepath.stat().st_mtime
    return (time.time() - mtime) / 86400


def audit_workspace(workspace_path: Path) -> dict[str, Any]:
    """Audit a single workspace for memory best practices.
    
    Returns a report dict with findings and actions taken.
    """
    report: dict[str, Any] = {
        "workspace": str(workspace_path),
        "exists": workspace_path.exists(),
        "non_dated_files": [],
        "old_daily_files": [],
        "memory_md_lines": 0,
        "actions_taken": [],
    }

    if not workspace_path.exists():
        return report

    memory_dir = workspace_path / "memory"
    memory_md = workspace_path / "MEMORY.md"

    # Check MEMORY.md
    if memory_md.exists():
        lines = memory_md.read_text().splitlines()
        report["memory_md_lines"] = len(lines)

    # Audit memory/ directory
    if memory_dir.exists():
        for f in sorted(memory_dir.glob("*.md")):
            if not _is_dated_file(f.name):
                report["non_dated_files"].append(f.name)
            else:
                age = _file_age_days(f)
                if age > MAX_DAILY_AGE_DAYS:
                    report["old_daily_files"].append({
                        "name": f.name,
                        "age_days": round(age, 1),
                    })

    return report


def cleanup_stale_sessions() -> dict[str, Any]:
    """Remove stale spawn/autoassign sessions from all agents."""
    report: dict[str, Any] = {"agents_cleaned": {}, "total_removed": 0}
    now_ms = time.time() * 1000

    if not SESSIONS_BASE.exists():
        return report

    for agent_dir in SESSIONS_BASE.iterdir():
        if not agent_dir.is_dir():
            continue

        sessions_file = agent_dir / "sessions" / "sessions.json"
        if not sessions_file.exists():
            continue

        try:
            data = json.loads(sessions_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        if not isinstance(data, dict):
            continue

        original_count = len(data)
        cleaned = {}
        removed = 0

        for key, val in data.items():
            is_spawn = "spawn-" in key or "autoassign-" in key
            updated = val.get("updatedAt", 0)
            age_hours = (now_ms - updated) / 3600000

            if is_spawn and age_hours > MAX_SESSION_AGE_HOURS:
                removed += 1
                # Also remove transcript file
                sid = val.get("sessionId", "")
                if sid:
                    for transcript in (agent_dir / "sessions").glob(f"{sid}*.jsonl"):
                        try:
                            transcript.unlink()
                        except OSError:
                            pass
            else:
                cleaned[key] = val

        if removed > 0:
            sessions_file.write_text(json.dumps(cleaned, indent=2))
            report["agents_cleaned"][agent_dir.name] = removed
            report["total_removed"] += removed

    return report


def consolidate_non_dated_files(workspace_path: Path, *, dry_run: bool = False) -> list[str]:
    """Move content from non-dated memory/ files into MEMORY.md, then delete them.
    
    Returns list of files consolidated.
    """
    memory_dir = workspace_path / "memory"
    memory_md = workspace_path / "MEMORY.md"
    consolidated = []

    if not memory_dir.exists():
        return consolidated

    non_dated = [f for f in sorted(memory_dir.glob("*.md")) if not _is_dated_file(f.name)]
    if not non_dated:
        return consolidated

    # Read existing MEMORY.md
    existing_content = ""
    if memory_md.exists():
        existing_content = memory_md.read_text()

    additions = []
    for f in non_dated:
        content = f.read_text().strip()
        if not content:
            if not dry_run:
                f.unlink()
            consolidated.append(f.name)
            continue

        # Add a section header from the filename
        section_name = f.stem.replace("-", " ").replace("_", " ").title()
        additions.append(f"\n## {section_name}\n\n{content}\n")
        consolidated.append(f.name)

        if not dry_run:
            f.unlink()

    if additions and not dry_run:
        # Append to MEMORY.md
        new_content = existing_content.rstrip() + "\n" + "\n".join(additions)
        memory_md.write_text(new_content)

    return consolidated


def prune_old_daily_files(workspace_path: Path, *, dry_run: bool = False) -> list[str]:
    """Delete daily memory files older than MAX_DAILY_AGE_DAYS.
    
    Returns list of files pruned.
    """
    memory_dir = workspace_path / "memory"
    pruned = []

    if not memory_dir.exists():
        return pruned

    for f in sorted(memory_dir.glob("*.md")):
        if _is_dated_file(f.name) and _file_age_days(f) > MAX_DAILY_AGE_DAYS:
            pruned.append(f.name)
            if not dry_run:
                f.unlink()

    return pruned


async def run_memory_maintenance(routine=None) -> dict[str, Any]:
    """Full memory maintenance pass across all agent workspaces.
    
    This is the main entry point, suitable as a RoutineRunner hook or
    direct call from the control loop.
    """
    results: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "workspaces": {},
        "sessions": {},
        "summary": {},
    }

    total_consolidated = 0
    total_pruned = 0

    for agent_name, ws_path in AGENT_WORKSPACES.items():
        if not ws_path.exists():
            continue

        # Audit
        audit = audit_workspace(ws_path)

        # Consolidate non-dated files into MEMORY.md
        consolidated = consolidate_non_dated_files(ws_path)
        total_consolidated += len(consolidated)

        # Prune old daily files
        pruned = prune_old_daily_files(ws_path)
        total_pruned += len(pruned)

        results["workspaces"][agent_name] = {
            "audit": audit,
            "consolidated": consolidated,
            "pruned": pruned,
        }

    # Clean stale sessions
    session_report = cleanup_stale_sessions()
    results["sessions"] = session_report

    results["summary"] = {
        "workspaces_audited": len([w for w in AGENT_WORKSPACES.values() if w.exists()]),
        "files_consolidated": total_consolidated,
        "files_pruned": total_pruned,
        "sessions_removed": session_report["total_removed"],
    }

    logger.info(
        "[MEMORY_MAINTENANCE] Complete: consolidated=%d pruned=%d sessions_removed=%d",
        total_consolidated,
        total_pruned,
        session_report["total_removed"],
    )

    return results
