"""Agent tracker - per-agent status tracking.

Port of ~/lobs-orchestrator/orchestrator/core/agent_tracker.py
Replaces JSON file writes with DB updates to agent_status table.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional
import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentStatus

logger = logging.getLogger(__name__)


class AgentTracker:
    """Tracks per-agent-type status in the database."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def mark_working(
        self,
        agent_type: str,
        task_id: str,
        project_id: str,
        activity: str
    ) -> None:
        """Mark an agent as actively working on a task (with retry-on-lock)."""
        for _attempt in range(5):
            try:
                if _attempt > 0:
                    await asyncio.sleep(_attempt * 0.5)
                    
                status = await self._get_or_create_status(agent_type)
                
                status.status = "working"
                status.activity = activity[:500] if activity else None
                status.thinking = None
                status.current_task_id = task_id
                status.current_project_id = project_id
                status.last_active_at = datetime.now(timezone.utc)

                await self.db.commit()

                logger.info(
                    f"[AGENT_TRACKER] {agent_type} -> working: {activity[:80]}"
                )
                return

            except Exception as e:
                if _attempt < 4:
                    logger.debug(f"[AGENT_TRACKER] Failed to mark working (attempt {_attempt + 1}/5): {e}, retrying...")
                    await self.db.rollback()
                else:
                    logger.error(f"Failed to mark agent working after 5 attempts: {e}", exc_info=True)
                    try:
                        await self.db.rollback()
                    except Exception:
                        pass

    async def update_thinking(self, agent_type: str, snippet: str) -> None:
        """Update the thinking snippet for an active agent (with retry-on-lock)."""
        for _attempt in range(5):
            try:
                if _attempt > 0:
                    await asyncio.sleep(_attempt * 0.5)
                    
                status = await self._get_or_create_status(agent_type)
                
                if status.status == "idle":
                    return

                status.thinking = snippet[:500] if snippet else None
                await self.db.commit()
                return

            except Exception as e:
                if _attempt < 4:
                    logger.debug(f"[AGENT_TRACKER] Failed to update thinking (attempt {_attempt + 1}/5): {e}, retrying...")
                    await self.db.rollback()
                else:
                    logger.error(f"Failed to update thinking after 5 attempts: {e}", exc_info=True)
                    try:
                        await self.db.rollback()
                    except Exception:
                        pass

    async def mark_completed(
        self,
        agent_type: str,
        task_id: str,
        duration_seconds: float
    ) -> None:
        """Mark a task as successfully completed (with retry-on-lock)."""
        for _attempt in range(5):
            try:
                if _attempt > 0:
                    await asyncio.sleep(_attempt * 0.5)
                    
                status = await self._get_or_create_status(agent_type)
                
                now = datetime.now(timezone.utc)
                status.last_completed_task_id = task_id
                status.last_completed_at = now
                status.last_active_at = now

                # Update stats
                stats = status.stats or {}
                stats["tasks_completed"] = stats.get("tasks_completed", 0) + 1
                
                # Update average duration
                durations = stats.get("_durations", [])
                durations.append(duration_seconds)
                if len(durations) > 50:
                    durations = durations[-50:]
                stats["_durations"] = durations
                stats["avg_duration_seconds"] = int(sum(durations) / len(durations))
                
                status.stats = stats

                await self.db.commit()

                logger.info(
                    f"[AGENT_TRACKER] {agent_type} completed task {task_id[:8]} "
                    f"({duration_seconds:.0f}s)"
                )
                return

            except Exception as e:
                if _attempt < 4:
                    logger.debug(f"[AGENT_TRACKER] Failed to mark completed (attempt {_attempt + 1}/5): {e}, retrying...")
                    await self.db.rollback()
                else:
                    logger.error(f"Failed to mark completed after 5 attempts: {e}", exc_info=True)
                    try:
                        await self.db.rollback()
                    except Exception:
                        pass

    async def mark_failed(self, agent_type: str, task_id: str) -> None:
        """Mark a task as failed (with retry-on-lock)."""
        for _attempt in range(5):
            try:
                if _attempt > 0:
                    await asyncio.sleep(_attempt * 0.5)
                    
                status = await self._get_or_create_status(agent_type)
                
                status.last_active_at = datetime.now(timezone.utc)
                
                # Update stats
                stats = status.stats or {}
                stats["tasks_failed"] = stats.get("tasks_failed", 0) + 1
                status.stats = stats

                await self.db.commit()

                logger.info(f"[AGENT_TRACKER] {agent_type} failed task {task_id[:8]}")
                return

            except Exception as e:
                if _attempt < 4:
                    logger.debug(f"[AGENT_TRACKER] Failed to mark failed (attempt {_attempt + 1}/5): {e}, retrying...")
                    await self.db.rollback()
                else:
                    logger.error(f"Failed to mark failed after 5 attempts: {e}", exc_info=True)
                    try:
                        await self.db.rollback()
                    except Exception:
                        pass

    async def mark_idle(self, agent_type: str) -> None:
        """Mark an agent as idle (no active work) (with retry-on-lock)."""
        for _attempt in range(5):
            try:
                if _attempt > 0:
                    await asyncio.sleep(_attempt * 0.5)
                    
                status = await self._get_or_create_status(agent_type)
                
                status.status = "idle"
                status.activity = None
                status.thinking = None
                status.current_task_id = None
                status.current_project_id = None

                await self.db.commit()

                logger.debug(f"[AGENT_TRACKER] {agent_type} -> idle")
                return

            except Exception as e:
                if _attempt < 4:
                    logger.debug(f"[AGENT_TRACKER] Failed to mark idle (attempt {_attempt + 1}/5): {e}, retrying...")
                    await self.db.rollback()
                else:
                    logger.error(f"Failed to mark idle after 5 attempts: {e}")
                    try:
                        await self.db.rollback()
                    except Exception:
                        pass  # Session may already be in an unrecoverable state

    async def get_status(self, agent_type: str) -> Optional[dict[str, Any]]:
        """Get status for a specific agent type."""
        try:
            result = await self.db.execute(
                select(AgentStatus).where(AgentStatus.agent_type == agent_type)
            )
            status = result.scalar_one_or_none()

            if not status:
                return None

            return {
                "agent_type": status.agent_type,
                "status": status.status,
                "activity": status.activity,
                "thinking": status.thinking,
                "current_task_id": status.current_task_id,
                "current_project_id": status.current_project_id,
                "last_active_at": status.last_active_at.isoformat() if status.last_active_at else None,
                "last_completed_task_id": status.last_completed_task_id,
                "last_completed_at": status.last_completed_at.isoformat() if status.last_completed_at else None,
                "stats": status.stats or {}
            }

        except Exception as e:
            logger.error(f"Failed to get agent status: {e}", exc_info=True)
            return None

    async def get_all_statuses(self) -> dict[str, dict[str, Any]]:
        """Get statuses for all agent types."""
        try:
            result = await self.db.execute(select(AgentStatus))
            statuses = result.scalars().all()

            return {
                status.agent_type: {
                    "agent_type": status.agent_type,
                    "status": status.status,
                    "activity": status.activity,
                    "thinking": status.thinking,
                    "current_task_id": status.current_task_id,
                    "current_project_id": status.current_project_id,
                    "last_active_at": status.last_active_at.isoformat() if status.last_active_at else None,
                    "last_completed_task_id": status.last_completed_task_id,
                    "last_completed_at": status.last_completed_at.isoformat() if status.last_completed_at else None,
                    "stats": status.stats or {}
                }
                for status in statuses
            }

        except Exception as e:
            logger.error(f"Failed to get all agent statuses: {e}", exc_info=True)
            return {}

    async def _get_or_create_status(self, agent_type: str) -> AgentStatus:
        """Get existing status or create new one."""
        result = await self.db.execute(
            select(AgentStatus).where(AgentStatus.agent_type == agent_type)
        )
        status = result.scalar_one_or_none()

        if not status:
            status = AgentStatus(
                agent_type=agent_type,
                status="idle",
                stats={}
            )
            self.db.add(status)
            await self.db.flush()

        return status
