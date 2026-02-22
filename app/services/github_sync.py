"""GitHub issue sync and issue-backed task helpers."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project as ProjectModel
from app.models import Task as TaskModel


class GitHubSyncService:
    """Synchronize GitHub issues to internal tasks for GitHub-tracked projects."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def sync_project(self, project: ProjectModel, *, push: bool = False) -> dict[str, Any]:
        if project.tracking != "github" or not project.github_repo:
            raise HTTPException(status_code=400, detail="Project is not configured for GitHub tracking")

        try:
            cmd = [
                "gh", "issue", "list",
                "--repo", project.github_repo,
                "--state", "all",
                "--limit", "200",
                "--json", "number,title,state,updatedAt,labels,assignees,url",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
            if proc.returncode != 0:
                raise HTTPException(status_code=500, detail=f"GitHub CLI error: {proc.stderr}")

            issues = json.loads(proc.stdout)
            label_filter = self._normalize_label_filter(project.github_label_filter)

            imported, updated, conflicts, pushed = 0, 0, 0, 0
            me_login = self._gh_login()

            existing_tasks_q = await self.db.execute(
                select(TaskModel).where(
                    TaskModel.project_id == project.id,
                    TaskModel.external_source == "github",
                )
            )
            existing_tasks = {
                (t.external_id or str(t.github_issue_number)): t
                for t in existing_tasks_q.scalars().all()
            }

            for issue in issues:
                if not self._issue_matches_filter(issue, label_filter):
                    continue

                key = str(issue["number"])
                issue_updated = datetime.fromisoformat(issue["updatedAt"].replace("Z", "+00:00"))
                issue_state = (issue.get("state") or "open").lower()
                meta = self._issue_meta(issue, me_login=me_login)
                mapped_status = "completed" if issue_state == "closed" else "active"
                task = existing_tasks.get(key)

                if not task:
                    self.db.add(
                        TaskModel(
                            id=f"gh-{project.id}-{issue['number']}",
                            title=issue["title"],
                            status=mapped_status,
                            owner="lobs",
                            work_state="not_started" if mapped_status == "active" else None,
                            project_id=project.id,
                            github_issue_number=issue["number"],
                            external_source="github",
                            external_id=key,
                            external_updated_at=issue_updated,
                            sync_state=("synced" if meta["eligible_for_claim"] else "ineligible"),
                            conflict_payload={"github": meta},
                        )
                    )
                    imported += 1
                    continue

                # Convert datetimes to timezone-aware for comparison
                task_updated_aware = task.updated_at.replace(tzinfo=timezone.utc) if task.updated_at and not task.updated_at.tzinfo else task.updated_at
                external_updated_aware = task.external_updated_at.replace(tzinfo=timezone.utc) if task.external_updated_at and not task.external_updated_at.tzinfo else task.external_updated_at
                
                if (
                    task_updated_aware
                    and external_updated_aware
                    and task_updated_aware > external_updated_aware
                    and issue_updated > external_updated_aware
                ):
                    task.sync_state = "conflict"
                    task.conflict_payload = {
                        "remote_title": issue["title"],
                        "remote_state": issue_state,
                        "remote_updated_at": issue["updatedAt"],
                        "github": meta,
                    }
                    conflicts += 1
                    continue

                task.title = issue["title"]
                task.status = mapped_status
                if mapped_status == "active" and not task.work_state:
                    task.work_state = "not_started"
                task.external_updated_at = issue_updated
                task.sync_state = "synced" if meta["eligible_for_claim"] else "ineligible"
                existing_payload = task.conflict_payload if isinstance(task.conflict_payload, dict) else {}
                existing_payload["github"] = meta
                task.conflict_payload = existing_payload
                updated += 1

            if push:
                pushed = await self._push_local_changes(project)

            return {
                "status": "synced",
                "project_id": project.id,
                "repo": project.github_repo,
                "issues_count": len(issues),
                "imported": imported,
                "updated": updated,
                "conflicts": conflicts,
                "pushed": pushed,
                "push_enabled": bool(push),
            }

        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="GitHub sync timed out")

    async def ensure_issue_for_task(self, project: ProjectModel, *, title: str, body: str | None) -> dict[str, Any]:
        """Create a GitHub issue and return core metadata."""
        body_text = body or ""
        create_cmd = [
            "gh", "issue", "create",
            "--repo", project.github_repo,
            "--title", title,
            "--body", body_text,
        ]
        proc = subprocess.run(create_cmd, capture_output=True, text=True, timeout=45)
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"GitHub issue create failed: {proc.stderr.strip()}")

        issue_url = proc.stdout.strip().splitlines()[-1].strip()
        view_cmd = [
            "gh", "issue", "view", issue_url,
            "--repo", project.github_repo,
            "--json", "number,state,url,updatedAt",
        ]
        view_proc = subprocess.run(view_cmd, capture_output=True, text=True, timeout=30)
        if view_proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"GitHub issue view failed: {view_proc.stderr.strip()}")

        issue_data = json.loads(view_proc.stdout)
        issue_data["updatedAt"] = datetime.fromisoformat(issue_data["updatedAt"].replace("Z", "+00:00"))
        return issue_data

    async def _push_local_changes(self, project: ProjectModel) -> int:
        pushed = 0
        local_changed_q = await self.db.execute(
            select(TaskModel).where(
                TaskModel.project_id == project.id,
                TaskModel.external_source == "github",
                TaskModel.sync_state.in_(["local_changed", "synced"]),
            )
        )
        for task in local_changed_q.scalars().all():
            if not task.github_issue_number:
                continue

            new_state = "close" if task.status == "completed" else "reopen"
            edit_cmd = [
                "gh", "issue", "edit", str(task.github_issue_number),
                "--repo", project.github_repo,
                "--title", task.title,
            ]
            state_cmd = ["gh", "issue", new_state, str(task.github_issue_number), "--repo", project.github_repo]

            edit_proc = subprocess.run(edit_cmd, capture_output=True, text=True, timeout=30)
            if edit_proc.returncode != 0:
                continue

            subprocess.run(state_cmd, capture_output=True, text=True, timeout=30)
            task.sync_state = "synced"
            task.external_updated_at = datetime.now(timezone.utc)
            pushed += 1

        return pushed

    async def claim_issue_for_task(self, project: ProjectModel, task: TaskModel) -> tuple[bool, str]:
        """Claim an issue via explicit label+assignee handshake before execution."""
        if not project.github_repo or not task.github_issue_number:
            return False, "missing_repo_or_issue"

        issue = self._fetch_issue(project.github_repo, task.github_issue_number)
        me_login = self._gh_login()
        meta = self._issue_meta(issue, me_login=me_login)

        if not meta["eligible_for_claim"] and not meta["claimed_by_lobs"]:
            return False, f"not_eligible:{meta['eligibility_reason']}"

        if meta["claimed_by_lobs"]:
            return True, "already_claimed"

        edit_cmd = [
            "gh", "issue", "edit", str(task.github_issue_number),
            "--repo", project.github_repo,
            "--add-label", "lobs:claimed",
            "--add-assignee", me_login,
        ]
        proc = subprocess.run(edit_cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            return False, f"claim_failed:{proc.stderr.strip()}"

        return True, "claimed"

    @staticmethod
    def _normalize_label_filter(raw: Any) -> set[str]:
        if raw is None:
            return set()
        if isinstance(raw, str):
            return {raw.strip().lower()} if raw.strip() else set()
        if isinstance(raw, list):
            return {str(x).strip().lower() for x in raw if str(x).strip()}
        return set()

    @staticmethod
    def _issue_matches_filter(issue: dict[str, Any], label_filter: set[str]) -> bool:
        if not label_filter:
            return True
        labels = issue.get("labels") or []
        names = {str((l or {}).get("name", "")).strip().lower() for l in labels}
        return bool(names & label_filter)

    def _fetch_issue(self, repo: str, issue_number: int) -> dict[str, Any]:
        cmd = [
            "gh", "issue", "view", str(issue_number),
            "--repo", repo,
            "--json", "number,title,state,updatedAt,labels,assignees,url",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"GitHub issue view failed: {proc.stderr.strip()}")
        return json.loads(proc.stdout)

    def _gh_login(self) -> str:
        cmd = ["gh", "api", "user", "--jq", ".login"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"GitHub auth not ready: {proc.stderr.strip()}")
        login = (proc.stdout or "").strip()
        if not login:
            raise HTTPException(status_code=500, detail="GitHub login unavailable")
        return login

    @staticmethod
    def _issue_meta(issue: dict[str, Any], *, me_login: str) -> dict[str, Any]:
        state = str(issue.get("state") or "open").lower()
        labels = {
            str((label or {}).get("name", "")).strip().lower()
            for label in (issue.get("labels") or [])
            if str((label or {}).get("name", "")).strip()
        }
        assignees = {
            str((a or {}).get("login", "")).strip().lower()
            for a in (issue.get("assignees") or [])
            if str((a or {}).get("login", "")).strip()
        }

        ready_label = "lobs:ready"
        claim_label = "lobs:claimed"
        blocked_labels = {"blocked", "wip", "on-hold"}

        is_open = state == "open"
        has_ready = ready_label in labels
        has_blocker = bool(labels & blocked_labels)
        assigned_elsewhere = bool(assignees) and me_login.lower() not in assignees
        claimed_by_lobs = (claim_label in labels) and (me_login.lower() in assignees)

        eligible_for_claim = is_open and has_ready and (not has_blocker) and (not assigned_elsewhere)
        if not is_open:
            reason = "closed"
        elif not has_ready:
            reason = "missing_ready_label"
        elif has_blocker:
            reason = "blocked"
        elif assigned_elsewhere:
            reason = "assigned_elsewhere"
        else:
            reason = "eligible"

        return {
            "url": issue.get("url"),
            "state": state,
            "labels": sorted(labels),
            "assignees": sorted(assignees),
            "ready_label": ready_label,
            "claim_label": claim_label,
            "eligible_for_claim": bool(eligible_for_claim),
            "claimed_by_lobs": bool(claimed_by_lobs),
            "eligibility_reason": reason,
        }
