"""Monitor module - health checks and stuck task detection.

Port of ~/lobs-orchestrator/orchestrator/core/monitor.py
Replaces file reads with DB queries.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, WorkerStatus

logger = logging.getLogger(__name__)


class Monitor:
    """Monitor system health and detect stuck tasks."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.warning_timeout = 1800  # 30 minutes
        self.kill_timeout = 3600  # 1 hour

    async def check_stuck_tasks(self) -> list[dict[str, Any]]:
        """
        Find tasks stuck in 'in_progress' state for too long.
        
        Returns list of stuck task details.
        """
        try:
            now = datetime.now(timezone.utc)
            warning_cutoff = now - timedelta(seconds=self.warning_timeout)
            kill_cutoff = now - timedelta(seconds=self.kill_timeout)

            result = await self.db.execute(
                select(Task).where(
                    Task.work_state == "in_progress",
                    Task.updated_at < warning_cutoff
                )
            )
            tasks = result.scalars().all()

            stuck = []
            for task in tasks:
                age_seconds = (now - task.updated_at).total_seconds()
                severity = "critical" if task.updated_at < kill_cutoff else "warning"
                
                stuck.append({
                    "id": task.id,
                    "title": task.title,
                    "project_id": task.project_id,
                    "age_seconds": age_seconds,
                    "severity": severity,
                    "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                })
                
                logger.warning(
                    f"[MONITOR] Stuck task detected: {task.id[:8]} "
                    f"({task.project_id}) - {int(age_seconds/60)}m old - {severity}"
                )

            return stuck

        except Exception as e:
            logger.error(f"Failed to check stuck tasks: {e}", exc_info=True)
            return []

    async def check_worker_health(self) -> dict[str, Any]:
        """
        Check health of active workers.
        
        Returns dict with worker health status.
        """
        try:
            result = await self.db.execute(
                select(WorkerStatus).where(WorkerStatus.id == 1)
            )
            status = result.scalar_one_or_none()

            if not status or not status.active:
                return {
                    "healthy": True,
                    "active": False,
                    "message": "No active workers"
                }

            now = datetime.now(timezone.utc)
            
            # Check heartbeat timeout
            if status.last_heartbeat:
                age_seconds = (now - status.last_heartbeat).total_seconds()
                if age_seconds > 300:  # 5 minutes
                    return {
                        "healthy": False,
                        "active": True,
                        "worker_id": status.worker_id,
                        "message": f"Worker heartbeat stale ({int(age_seconds/60)}m)",
                        "heartbeat_age_seconds": age_seconds
                    }

            # Check task duration
            if status.started_at:
                duration_seconds = (now - status.started_at).total_seconds()
                if duration_seconds > self.kill_timeout:
                    return {
                        "healthy": False,
                        "active": True,
                        "worker_id": status.worker_id,
                        "current_task": status.current_task,
                        "message": f"Worker running too long ({int(duration_seconds/60)}m)",
                        "duration_seconds": duration_seconds
                    }

            return {
                "healthy": True,
                "active": True,
                "worker_id": status.worker_id,
                "current_task": status.current_task,
                "current_project": status.current_project,
                "message": "Worker healthy"
            }

        except Exception as e:
            logger.error(f"Failed to check worker health: {e}", exc_info=True)
            return {
                "healthy": False,
                "error": str(e)
            }

    async def get_health_summary(self) -> dict[str, Any]:
        """Get overall system health summary."""
        stuck_tasks = await self.check_stuck_tasks()
        worker_health = await self.check_worker_health()

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "worker": worker_health,
            "stuck_tasks": {
                "count": len(stuck_tasks),
                "tasks": stuck_tasks
            },
            "healthy": worker_health.get("healthy", True) and len(stuck_tasks) == 0
        }
