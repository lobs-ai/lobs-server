"""Escalation manager - tiered failure handling.

Port of ~/lobs-orchestrator/orchestrator/core/escalation.py
Swaps file I/O for DB operations.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, InboxItem

logger = logging.getLogger(__name__)


class EscalationManager:
    """
    Handles tiered escalation for worker failures.
    
    Levels:
    1. Auto-retry (transient failures) - handled by worker manager
    2. Alert creation (persistent failures)
    3. Human escalation (critical failures)
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_failure_alert(
        self,
        task_id: str,
        project_id: str,
        error_log: str,
        severity: str = "medium"
    ) -> Optional[str]:
        """
        Create an inbox alert for a failed task.
        
        Args:
            task_id: Failed task ID
            project_id: Project ID
            error_log: Error log excerpt
            severity: Alert severity (low/medium/high/critical)
            
        Returns:
            Alert ID if created, None on failure
        """
        try:
            now = datetime.now(timezone.utc)
            alert_id = f"alert_{task_id}_{int(now.timestamp())}"

            # Get task details
            task = await self.db.get(Task, task_id)
            task_title = task.title if task else task_id[:8]

            # Create inbox item as alert
            alert = InboxItem(
                id=alert_id,
                title=f"🚨 Task Failure: {task_title}",
                filename=None,
                relative_path=None,
                content=(
                    f"**Task ID:** `{task_id}`\n"
                    f"**Project:** `{project_id}`\n"
                    f"**Severity:** {severity}\n\n"
                    f"**Error Log (excerpt):**\n"
                    f"```\n{error_log[:1000]}\n```\n"
                ),
                modified_at=now,
                is_read=False,
                summary=f"Task {task_id[:8]} failed in {project_id}"
            )

            self.db.add(alert)
            # Commit with retry-on-lock logic (exponential backoff)
            for _attempt in range(5):
                try:
                    await self.db.commit()
                    break  # Success - exit the retry loop
                except Exception as _e:
                    if _attempt < 4:
                        await asyncio.sleep(_attempt * 0.5)
                        await self.db.rollback()
                    else:
                        logger.error("[ORCHESTRATOR] Failed to commit after 5 attempts: %s", _e, exc_info=True)
                        try:
                            await self.db.rollback()
                        except Exception:
                            pass

            logger.info(
                f"[ESCALATION] Created alert {alert_id} for task {task_id[:8]} "
                f"(severity={severity})"
            )

            return alert_id

        except Exception as e:
            logger.error(f"Failed to create failure alert: {e}", exc_info=True)
            await self.db.rollback()
            return None

    async def escalate_stuck_task(
        self,
        task_id: str,
        project_id: str,
        duration_minutes: int
    ) -> Optional[str]:
        """
        Escalate a task that has been running too long.
        
        Args:
            task_id: Stuck task ID
            project_id: Project ID
            duration_minutes: How long the task has been running
            
        Returns:
            Alert ID if created, None on failure
        """
        try:
            now = datetime.now(timezone.utc)
            alert_id = f"stuck_{task_id}_{int(now.timestamp())}"

            task = await self.db.get(Task, task_id)
            task_title = task.title if task else task_id[:8]

            severity = "critical" if duration_minutes > 60 else "high"

            alert = InboxItem(
                id=alert_id,
                title=f"⏰ Task Timeout: {task_title}",
                filename=None,
                relative_path=None,
                content=(
                    f"**Task ID:** `{task_id}`\n"
                    f"**Project:** `{project_id}`\n"
                    f"**Duration:** {duration_minutes} minutes\n\n"
                    f"This task has exceeded the expected runtime and may be stuck.\n"
                ),
                modified_at=now,
                is_read=False,
                summary=f"Task {task_id[:8]} stuck for {duration_minutes}m"
            )

            self.db.add(alert)
            # Commit with retry-on-lock logic (exponential backoff)
            for _attempt in range(5):
                try:
                    await self.db.commit()
                    break  # Success - exit the retry loop
                except Exception as _e:
                    if _attempt < 4:
                        await asyncio.sleep(_attempt * 0.5)
                        await self.db.rollback()
                    else:
                        logger.error("[ORCHESTRATOR] Failed to commit after 5 attempts: %s", _e, exc_info=True)
                        try:
                            await self.db.rollback()
                        except Exception:
                            pass

            logger.info(
                f"[ESCALATION] Escalated stuck task {task_id[:8]} "
                f"({duration_minutes}m runtime)"
            )

            return alert_id

        except Exception as e:
            logger.error(f"Failed to escalate stuck task: {e}", exc_info=True)
            await self.db.rollback()
            return None

    async def record_failure(
        self,
        task_id: str,
        project_id: str,
        agent_type: str,
        error_message: str
    ) -> None:
        """
        Record a task failure for pattern detection.
        
        Updates the task with failure metadata.
        """
        try:
            task = await self.db.get(Task, task_id)
            if not task:
                logger.warning(f"Task {task_id} not found for failure recording")
                return

            # Update task notes with failure info
            failure_note = (
                f"\n\n---\n**Failure recorded:** {datetime.now(timezone.utc).isoformat()}\n"
                f"Agent: {agent_type}\n"
                f"Error: {error_message[:500]}\n"
            )
            
            task.notes = (task.notes or "") + failure_note
            task.updated_at = datetime.now(timezone.utc)

            # Commit with retry-on-lock logic (exponential backoff)
            for _attempt in range(5):
                try:
                    await self.db.commit()
                    break  # Success - exit the retry loop
                except Exception as _e:
                    if _attempt < 4:
                        await asyncio.sleep(_attempt * 0.5)
                        await self.db.rollback()
                    else:
                        logger.error("[ORCHESTRATOR] Failed to commit after 5 attempts: %s", _e, exc_info=True)
                        try:
                            await self.db.rollback()
                        except Exception:
                            pass

            logger.info(
                f"[ESCALATION] Recorded failure for task {task_id[:8]} "
                f"(agent={agent_type})"
            )

        except Exception as e:
            logger.error(f"Failed to record task failure: {e}", exc_info=True)
            await self.db.rollback()
