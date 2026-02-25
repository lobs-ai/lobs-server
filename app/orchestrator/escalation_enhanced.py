"""Enhanced escalation manager - multi-tier failure handling.

Port of ~/lobs-orchestrator/orchestrator/core/escalation.py
Implements 4-tier escalation:
1. Auto-retry with same agent
2. Agent switch (try different agent type)
3. Diagnostic run (spawn reviewer to analyze)
4. Human escalation (inbox alert)
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, InboxItem
from app.services.failure_explainer import explain_failure_markdown

logger = logging.getLogger(__name__)


class EscalationManagerEnhanced:
    """
    Multi-tier escalation system for task failures.
    
    Escalation Tiers:
    0. None - initial state
    1. Auto-retry - same agent, max 2 retries
    2. Agent switch - try different agent type
    3. Diagnostic - spawn reviewer to analyze
    4. Human - create inbox alert, wait for manual intervention
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.max_tier_1_retries = 2
        self.agent_alternatives = {
            "programmer": ["architect", "reviewer"],
            "architect": ["programmer", "researcher"],
            "researcher": ["writer", "programmer"],
            "writer": ["researcher", "programmer"],
            "reviewer": ["architect", "programmer"],
        }

    async def handle_failure(
        self,
        task_id: str,
        project_id: str,
        agent_type: str,
        error_log: str,
        exit_code: int = -1
    ) -> dict[str, Any]:
        """
        Main entry point for handling a task failure.
        
        Determines appropriate escalation tier and takes action.
        
        Args:
            task_id: Failed task ID
            project_id: Project ID
            agent_type: Agent type that failed
            error_log: Error log from worker
            exit_code: Process exit code
            
        Returns:
            Dict with action taken and new state
        """
        try:
            task = await self.db.get(Task, task_id)
            if not task:
                logger.error(f"Task {task_id} not found for escalation")
                return {"action": "error", "reason": "task_not_found"}
            
            current_tier = task.escalation_tier or 0
            retry_count = task.retry_count or 0
            
            logger.info(
                f"[ESCALATION] Handling failure for {task_id[:8]} "
                f"(tier={current_tier}, retries={retry_count}, agent={agent_type})"
            )
            
            # Tier 1: Auto-retry with same agent (tier 0 or 1, retries < max)
            if current_tier <= 1 and retry_count < self.max_tier_1_retries:
                return await self._tier_1_auto_retry(task, agent_type, error_log)
            
            # Tier 2: Switch agent type (tier 1 exhausted)
            elif current_tier <= 1:
                return await self._tier_2_agent_switch(task, agent_type, error_log)
            
            # Tier 3: Diagnostic run (reviewer analysis)
            elif current_tier == 2:
                return await self._tier_3_diagnostic(task, agent_type, error_log)
            
            # Tier 4: Human escalation
            else:
                return await self._tier_4_human_escalation(task, agent_type, error_log)
        
        except Exception as e:
            logger.error(f"Escalation failed for task {task_id}: {e}", exc_info=True)
            return {"action": "error", "reason": str(e)}

    async def _tier_1_auto_retry(
        self,
        task: Task,
        agent_type: str,
        error_log: str
    ) -> dict[str, Any]:
        """
        Tier 1: Auto-retry with same agent (max 2 retries).
        
        Args:
            task: Task model
            agent_type: Current agent type
            error_log: Error log from worker
            
        Returns:
            Dict with action taken
        """
        try:
            task.escalation_tier = 1
            task.retry_count = (task.retry_count or 0) + 1
            task.failure_reason = error_log[:1000]
            task.last_retry_reason = "tier_1_auto_retry"
            task.work_state = "not_started"
            task.status = "active"
            task.updated_at = datetime.now(timezone.utc)
            
            # Add note to task
            retry_note = (
                f"\n\n---\n**Auto-retry #{task.retry_count}:** "
                f"{datetime.now(timezone.utc).isoformat()}\n"
                f"Tier: 1 (auto-retry with same agent)\n"
                f"Agent: {agent_type}\n"
            )
            task.notes = (task.notes or "") + retry_note
            
            await self.db.commit()
            
            logger.info(
                f"[ESCALATION] Tier 1: Auto-retry {task.id[:8]} "
                f"(attempt {task.retry_count}/{self.max_tier_1_retries})"
            )
            
            return {
                "action": "retry",
                "tier": 1,
                "agent_type": agent_type,
                "retry_count": task.retry_count
            }
        
        except Exception as e:
            logger.error(f"Tier 1 escalation failed: {e}", exc_info=True)
            await self.db.rollback()
            return {"action": "error", "reason": str(e)}

    async def _tier_2_agent_switch(
        self,
        task: Task,
        current_agent: str,
        error_log: str
    ) -> dict[str, Any]:
        """
        Tier 2: Switch to a different agent type.
        
        Args:
            task: Task model
            current_agent: Current agent type that failed
            error_log: Error log from worker
            
        Returns:
            Dict with action taken
        """
        try:
            # Get alternative agent types
            alternatives = self.agent_alternatives.get(current_agent, ["programmer"])
            
            # Pick first alternative (or second if we already tried first)
            last_retry = task.last_retry_reason or ""
            if "tier_2_switch:" in last_retry:
                # Already tried one alternative, try next
                tried_agent = last_retry.split(":")[-1]
                remaining = [a for a in alternatives if a != tried_agent]
                new_agent = remaining[0] if remaining else "programmer"
            else:
                new_agent = alternatives[0]
            
            task.escalation_tier = 2
            task.retry_count = (task.retry_count or 0) + 1
            task.failure_reason = error_log[:1000]
            task.last_retry_reason = f"tier_2_switch:{new_agent}"
            task.agent = new_agent
            task.work_state = "not_started"
            task.status = "active"
            task.updated_at = datetime.now(timezone.utc)
            
            # Add note to task
            switch_note = (
                f"\n\n---\n**Agent Switch:** {datetime.now(timezone.utc).isoformat()}\n"
                f"Tier: 2 (agent switch)\n"
                f"Switched from {current_agent} to {new_agent}\n"
                f"Reason: Previous agent failed {task.retry_count} time(s)\n"
            )
            task.notes = (task.notes or "") + switch_note
            
            await self.db.commit()
            
            logger.info(
                f"[ESCALATION] Tier 2: Agent switch {task.id[:8]} "
                f"({current_agent} → {new_agent})"
            )
            
            return {
                "action": "agent_switch",
                "tier": 2,
                "old_agent": current_agent,
                "new_agent": new_agent,
                "retry_count": task.retry_count
            }
        
        except Exception as e:
            logger.error(f"Tier 2 escalation failed: {e}", exc_info=True)
            await self.db.rollback()
            return {"action": "error", "reason": str(e)}

    async def _tier_3_diagnostic(
        self,
        task: Task,
        agent_type: str,
        error_log: str
    ) -> dict[str, Any]:
        """
        Tier 3: Spawn reviewer agent to diagnose the failure.
        
        Args:
            task: Task model
            agent_type: Current agent type
            error_log: Error log from worker
            
        Returns:
            Dict with action taken
        """
        try:
            # Create diagnostic task for reviewer
            diagnostic_id = f"diag_{task.id}_{int(datetime.now(timezone.utc).timestamp())}"
            
            diagnostic_notes = (
                f"# Diagnostic Task - Failure Analysis\n\n"
                f"**Original Task:** `{task.id}`\n"
                f"**Project:** `{task.project_id}`\n"
                f"**Failed Agent:** {agent_type}\n"
                f"**Retry Count:** {task.retry_count}\n\n"
                f"## Original Task\n"
                f"**Title:** {task.title}\n\n"
                f"**Notes:**\n{task.notes}\n\n"
                f"## Failure Analysis\n\n"
                f"Please analyze why this task is failing and provide:\n"
                f"1. Root cause of the failure\n"
                f"2. Recommended fix or workaround\n"
                f"3. Whether task should be retried, modified, or escalated\n\n"
                f"## Error Log (last 2000 chars)\n"
                f"```\n{error_log[-2000:]}\n```\n"
            )
            
            diagnostic_task = Task(
                id=diagnostic_id,
                title=f"Diagnose failure: {task.title[:50]}",
                notes=diagnostic_notes,
                project_id=task.project_id,
                agent="reviewer",
                status="active",
                work_state="not_started",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            
            self.db.add(diagnostic_task)
            
            # Update original task
            task.escalation_tier = 3
            task.failure_reason = error_log[:1000]
            task.last_retry_reason = f"tier_3_diagnostic:{diagnostic_id}"
            task.work_state = "blocked"
            task.status = "active"
            task.updated_at = datetime.now(timezone.utc)
            
            # Add note to original task
            diagnostic_note = (
                f"\n\n---\n**Diagnostic Spawned:** {datetime.now(timezone.utc).isoformat()}\n"
                f"Tier: 3 (diagnostic run)\n"
                f"Diagnostic Task: {diagnostic_id}\n"
                f"Waiting for reviewer analysis...\n"
            )
            task.notes = (task.notes or "") + diagnostic_note
            
            await self.db.commit()
            
            logger.info(
                f"[ESCALATION] Tier 3: Diagnostic spawned for {task.id[:8]} "
                f"(diagnostic_id={diagnostic_id})"
            )
            
            return {
                "action": "diagnostic",
                "tier": 3,
                "diagnostic_task_id": diagnostic_id,
                "reviewer_agent": "reviewer"
            }
        
        except Exception as e:
            logger.error(f"Tier 3 escalation failed: {e}", exc_info=True)
            await self.db.rollback()
            return {"action": "error", "reason": str(e)}

    async def _tier_4_human_escalation(
        self,
        task: Task,
        agent_type: str,
        error_log: str
    ) -> dict[str, Any]:
        """
        Tier 4: Create inbox alert for human intervention.
        
        Args:
            task: Task model
            agent_type: Current agent type
            error_log: Error log from worker
            
        Returns:
            Dict with action taken
        """
        try:
            now = datetime.now(timezone.utc)
            alert_id = f"escalation_{task.id}_{int(now.timestamp())}"
            
            # Create inbox alert
            # Build runbook section if a matching runbook exists
            runbook_section = explain_failure_markdown(
                task.failure_reason, include_runbook_link=True
            )

            alert = InboxItem(
                id=alert_id,
                title=f"🚨 Task Escalation: {task.title[:50]}",
                filename=None,
                relative_path=None,
                content=(
                    f"# Task Escalation - Human Intervention Required\n\n"
                    f"**Task ID:** `{task.id}`\n"
                    f"**Project:** `{task.project_id}`\n"
                    f"**Agent:** {agent_type}\n"
                    f"**Retry Count:** {task.retry_count}\n"
                    f"**Escalation Tier:** 4 (Human)\n\n"
                    f"## Summary\n\n"
                    f"This task has failed through all automatic escalation tiers:\n"
                    f"1. ✗ Auto-retry ({self.max_tier_1_retries} attempts)\n"
                    f"2. ✗ Agent switch\n"
                    f"3. ✗ Diagnostic analysis\n"
                    f"4. → **Manual intervention required**\n\n"
                    f"## Failure Diagnosis\n\n"
                    f"{runbook_section}\n\n"
                    f"## Task Details\n\n"
                    f"**Title:** {task.title}\n\n"
                    f"**Last Failure Reason:**\n{task.failure_reason or 'Unknown'}\n\n"
                    f"## Error Log (last 1000 chars)\n\n"
                    f"```\n{error_log[-1000:]}\n```\n\n"
                    f"## Recommended Actions\n\n"
                    f"1. Review task requirements and error logs\n"
                    f"2. Check if task is feasible or needs redesign\n"
                    f"3. Update task notes with guidance or split into subtasks\n"
                    f"4. Reset task to `not_started` when ready to retry\n"
                    f"5. Or mark as `rejected` if not viable\n"
                ),
                modified_at=now,
                is_read=False,
                summary=f"Task {task.id[:8]} needs manual intervention after {task.retry_count} failures"
            )
            
            self.db.add(alert)
            
            # Update task
            task.escalation_tier = 4
            task.failure_reason = error_log[:1000]
            task.last_retry_reason = f"tier_4_human:{alert_id}"
            task.work_state = "blocked"
            task.status = "active"
            task.updated_at = now
            
            # Add note to task
            escalation_note = (
                f"\n\n---\n**Human Escalation:** {now.isoformat()}\n"
                f"Tier: 4 (human intervention)\n"
                f"Alert: {alert_id}\n"
                f"All automatic escalation tiers exhausted.\n"
            )
            task.notes = (task.notes or "") + escalation_note
            
            await self.db.commit()
            
            logger.warning(
                f"[ESCALATION] Tier 4: Human escalation for {task.id[:8]} "
                f"(alert_id={alert_id})"
            )
            
            return {
                "action": "human_escalation",
                "tier": 4,
                "alert_id": alert_id,
                "retry_count": task.retry_count
            }
        
        except Exception as e:
            logger.error(f"Tier 4 escalation failed: {e}", exc_info=True)
            await self.db.rollback()
            return {"action": "error", "reason": str(e)}

    async def create_simple_alert(
        self,
        task_id: str,
        project_id: str,
        error_log: str,
        severity: str = "medium"
    ) -> Optional[str]:
        """
        Create a simple inbox alert for a failed task.
        
        Used for non-escalation failures (e.g., first failure, timeout).
        
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
            await self.db.commit()

            logger.info(
                f"[ESCALATION] Created alert {alert_id} for task {task_id[:8]} "
                f"(severity={severity})"
            )

            return alert_id

        except Exception as e:
            logger.error(f"Failed to create failure alert: {e}", exc_info=True)
            await self.db.rollback()
            return None
