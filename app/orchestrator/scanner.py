"""Scanner module - replaces bin/open-work with database queries."""

import logging
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, Project

logger = logging.getLogger(__name__)


class Scanner:
    """Scans for eligible work using database queries."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_eligible_tasks(self) -> list[dict[str, Any]]:
        """Get tasks that are ready to be worked on.
        
        Replaces: bin/open-work script
        
        Returns tasks where:
        - status='active'
        - work_state in ('not_started', 'ready')
        - agent is assigned (routed by PM)
        """
        try:
            result = await self.db.execute(
                select(Task).where(
                    Task.status == "active",
                    Task.work_state.in_(["not_started", "ready"]),
                    Task.agent != None,
                    Task.agent != "",
                    (Task.sync_state == None) | (Task.sync_state != "conflict"),
                )
            )
            tasks = result.scalars().all()
            
            return [self._task_to_dict(task) for task in tasks]
        except Exception as e:
            logger.error(f"Failed to get eligible tasks: {e}")
            return []

    async def get_unrouted_tasks(self) -> list[dict[str, Any]]:
        """Get tasks that need routing (no agent assigned, not started).
        
        Returns tasks where:
        - status='active'
        - work_state in ('not_started', 'ready')
        - agent is null or empty (needs PM routing)
        """
        try:
            result = await self.db.execute(
                select(Task).where(
                    Task.status == "active",
                    Task.work_state.in_(["not_started", "ready"]),
                    (Task.agent == None) | (Task.agent == ""),
                    (Task.sync_state == None) | (Task.sync_state != "conflict"),
                )
            )
            tasks = result.scalars().all()
            
            return [self._task_to_dict(task) for task in tasks]
        except Exception as e:
            logger.error(f"Failed to get unrouted tasks: {e}")
            return []

    async def get_projects(self) -> list[dict[str, Any]]:
        """Get all active projects."""
        try:
            result = await self.db.execute(
                select(Project).where(Project.archived == False)
            )
            projects = result.scalars().all()
            
            return [self._project_to_dict(project) for project in projects]
        except Exception as e:
            logger.error(f"Failed to get projects: {e}")
            return []

    def _task_to_dict(self, task: Task) -> dict[str, Any]:
        """Convert SQLAlchemy Task model to dict."""
        return {
            "id": task.id,
            "title": task.title,
            "status": task.status,
            "work_state": task.work_state,
            "review_state": task.review_state,
            "project_id": task.project_id,
            "notes": task.notes,
            "owner": task.owner,
            "agent": task.agent,
            "kind": "task",
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        }

    def _project_to_dict(self, project: Project) -> dict[str, Any]:
        """Convert SQLAlchemy Project model to dict."""
        return {
            "id": project.id,
            "title": project.title,
            "type": project.type,
            "archived": project.archived,
            "notes": project.notes,
        }
