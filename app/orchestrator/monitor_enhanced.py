"""Enhanced monitor module - health checks, stuck task detection, auto-unblock, failure patterns.

Port of ~/lobs-orchestrator/orchestrator/core/monitor.py enhanced features.
Replaces file reads with DB queries, stores monitoring state in DB.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, WorkerStatus, InboxItem, WorkflowRun

logger = logging.getLogger(__name__)


class MonitorEnhanced:
    """
    Enhanced monitor with advanced health checks and auto-remediation.
    
    Features:
    - Stuck task detection with heartbeat checking
    - Auto-unblock for tasks blocked by completed dependencies
    - Failure pattern detection (same task failing repeatedly)
    - Worker health monitoring
    - Worker termination for stuck/timed-out workers
    - System health summary
    """

    def __init__(self, db: AsyncSession, worker_manager: Optional[Any] = None):
        self.db = db
        self.worker_manager = worker_manager
        self.stuck_timeout = 1800  # 30 minutes (allows for slow local models)
        self.warning_timeout = 3600  # 1 hour
        self.kill_timeout = 7200  # 2 hours
        self.max_retry_count = 3  # Maximum auto-retries before escalation

    async def check_stuck_tasks(self) -> list[dict[str, Any]]:
        """
        Find tasks stuck in 'in_progress' state with no worker heartbeat.
        
        Returns list of stuck task details.
        """
        try:
            now = datetime.now(timezone.utc)
            stuck_cutoff = now - timedelta(seconds=self.stuck_timeout)
            warning_cutoff = now - timedelta(seconds=self.warning_timeout)
            
            # Find tasks in progress that haven't been updated recently
            result = await self.db.execute(
                select(Task).where(
                    Task.work_state == "in_progress",
                    Task.updated_at < stuck_cutoff
                )
            )
            tasks = result.scalars().all()
            
            stuck = []
            for task in tasks:
                # Ensure timezone-aware datetime before subtraction
                updated_at = task.updated_at.replace(tzinfo=timezone.utc) if task.updated_at.tzinfo is None else task.updated_at
                age_seconds = (now - updated_at).total_seconds()
                
                # Check if worker is still active for this task
                worker_result = await self.db.execute(
                    select(WorkerStatus).where(
                        WorkerStatus.current_task == task.id,
                        WorkerStatus.active == True
                    )
                )
                worker = worker_result.scalar_one_or_none()
                
                # If worker exists and has recent heartbeat, task is not stuck
                if worker and worker.last_heartbeat:
                    hb = worker.last_heartbeat.replace(tzinfo=timezone.utc) if worker.last_heartbeat.tzinfo is None else worker.last_heartbeat
                    heartbeat_age = (now - hb).total_seconds()
                    if heartbeat_age < self.stuck_timeout:
                        continue
                
                # If a workflow run is still active for this task, don't auto-block it.
                run_q = await self.db.execute(
                    select(WorkflowRun).where(
                        WorkflowRun.task_id == task.id,
                        WorkflowRun.status.in_(["pending", "running"]),
                    ).limit(1)
                )
                if run_q.scalar_one_or_none() is not None:
                    continue

                # Task is stuck - no worker and no active workflow run
                severity = "critical" if age_seconds > self.kill_timeout else \
                          "high" if age_seconds > self.warning_timeout else "medium"
                
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
                
                # If critical severity and worker manager available, kill the worker
                if severity == "critical" and self.worker_manager:
                    killed = await self._kill_stuck_worker(task.id, age_seconds)
                    if killed:
                        logger.info(
                            f"[MONITOR] Killed stuck worker for task {task.id[:8]} "
                            f"(running for {int(age_seconds/60)}m)"
                        )
                else:
                    # Reset task to not_started so orchestrator can retry
                    # For medium severity (15-30min), just reset silently
                    # For high severity (30-60min), reset and log warning
                    task.work_state = "not_started"
                    task.updated_at = datetime.now(timezone.utc)
                    await self.db.commit()
                    logger.info(
                        f"[MONITOR] Reset stuck task {task.id[:8]} to not_started "
                        f"(was in_progress for {int(age_seconds/60)}m, no worker/workflow)"
                    )
            
            return stuck
        
        except Exception as e:
            logger.error(f"Failed to check stuck tasks: {e}", exc_info=True)
            return []

    async def _kill_stuck_worker(self, task_id: str, age_seconds: float) -> bool:
        """
        Kill a worker that's stuck on a task.
        
        Returns True if worker was found and killed.
        """
        if not self.worker_manager:
            return False
        
        try:
            # Find the worker handling this task
            worker_id = None
            for wid, worker_info in self.worker_manager.active_workers.items():
                if worker_info.task_id == task_id:
                    worker_id = wid
                    break
            
            if not worker_id:
                logger.debug(
                    f"[MONITOR] No active worker found for stuck task {task_id[:8]}"
                )
                return False
            
            # Kill the worker
            reason = f"stuck_timeout_{int(age_seconds/60)}m"
            await self.worker_manager._kill_worker(worker_id, reason)
            
            logger.info(
                f"[MONITOR] Killed stuck worker {worker_id} "
                f"(task={task_id[:8]}, age={int(age_seconds/60)}m)"
            )
            return True
            
        except Exception as e:
            logger.error(
                f"[MONITOR] Failed to kill stuck worker for task {task_id[:8]}: {e}",
                exc_info=True
            )
            return False

    async def _mark_task_stuck(self, task: Task, age_seconds: float) -> None:
        """Mark a task as stuck and create an inbox alert."""
        try:
            # Update task to blocked state
            task.work_state = "blocked"
            task.failure_reason = f"Stuck - no progress for {int(age_seconds/60)} minutes"
            task.updated_at = datetime.now(timezone.utc)
            await self.db.commit()
            
            # Create inbox alert
            alert_id = f"stuck_{task.id}_{int(datetime.now(timezone.utc).timestamp())}"
            alert = InboxItem(
                id=alert_id,
                title=f"⏰ Task Stuck: {task.title[:50]}",
                filename=None,
                relative_path=None,
                content=(
                    f"**Task ID:** `{task.id}`\n"
                    f"**Project:** `{task.project_id}`\n"
                    f"**Duration:** {int(age_seconds/60)} minutes\n\n"
                    f"This task has been in progress with no updates. "
                    f"It may be stuck or the worker may have crashed.\n\n"
                    f"**Actions:**\n"
                    f"- Check worker logs\n"
                    f"- Restart worker if needed\n"
                    f"- Reset task to not_started if safe\n"
                ),
                modified_at=datetime.now(timezone.utc),
                is_read=False,
                summary=f"Task {task.id[:8]} stuck for {int(age_seconds/60)}m"
            )
            
            self.db.add(alert)
            await self.db.commit()
            
            logger.info(f"[MONITOR] Created stuck task alert {alert_id}")
        
        except Exception as e:
            logger.error(f"Failed to mark task as stuck: {e}", exc_info=True)
            await self.db.rollback()

    async def auto_unblock_tasks(self) -> int:
        """
        Automatically unblock tasks that are blocked by completed dependencies.
        
        Returns count of unblocked tasks.
        """
        try:
            # Find tasks in blocked state
            result = await self.db.execute(
                select(Task).where(Task.work_state == "blocked")
            )
            blocked_tasks = result.scalars().all()
            
            unblocked_count = 0
            
            for task in blocked_tasks:
                # Check if task has blocked_by dependencies
                blocked_by = task.blocked_by
                if not blocked_by or not isinstance(blocked_by, list):
                    continue
                
                # Check if all dependencies are completed
                all_completed = True
                for dep_id in blocked_by:
                    dep = await self.db.get(Task, dep_id)
                    if not dep or dep.work_state != "completed":
                        all_completed = False
                        break
                
                if all_completed:
                    # Unblock the task
                    task.work_state = "not_started"
                    task.status = "active"
                    task.updated_at = datetime.now(timezone.utc)
                    
                    # Add note to task
                    unblock_note = (
                        f"\n\n---\n**Auto-unblocked:** {datetime.now(timezone.utc).isoformat()}\n"
                        f"All dependencies completed.\n"
                    )
                    task.notes = (task.notes or "") + unblock_note
                    
                    await self.db.commit()
                    unblocked_count += 1
                    
                    logger.info(
                        f"[MONITOR] Auto-unblocked task {task.id[:8]} "
                        f"(dependencies completed)"
                    )
            
            if unblocked_count > 0:
                logger.info(f"[MONITOR] Auto-unblocked {unblocked_count} task(s)")
            
            return unblocked_count
        
        except Exception as e:
            logger.error(f"Failed to auto-unblock tasks: {e}", exc_info=True)
            await self.db.rollback()
            return 0

    async def recover_escalation_blocked_tasks(self) -> int:
        """
        Recover tasks stuck in escalation-blocked state (tier 3+).
        
        After a cooldown period (1 hour), reset blocked tasks so they can
        be retried fresh. Transient failures (gateway timeouts, model stalls)
        will likely succeed on retry.
        
        Returns count of recovered tasks.
        """
        try:
            cooldown = timedelta(hours=1)
            cutoff = datetime.now(timezone.utc) - cooldown

            result = await self.db.execute(
                select(Task).where(
                    Task.work_state == "blocked",
                    Task.status == "active",
                    Task.escalation_tier >= 3,
                    Task.updated_at < cutoff,
                )
            )
            blocked_tasks = result.scalars().all()

            recovered = 0
            for task in blocked_tasks:
                task.work_state = "not_started"
                task.escalation_tier = 0
                task.retry_count = 0
                task.failure_reason = None
                task.updated_at = datetime.now(timezone.utc)
                recovered += 1

                logger.info(
                    "[MONITOR] Recovered escalation-blocked task %s "
                    "(was tier %s, retries %s)",
                    task.id[:8],
                    task.escalation_tier,
                    task.retry_count,
                )

            if recovered > 0:
                await self.db.commit()
                logger.info("[MONITOR] Recovered %d escalation-blocked task(s)", recovered)

            return recovered

        except Exception as e:
            logger.error("Failed to recover escalation-blocked tasks: %s", e, exc_info=True)
            await self.db.rollback()
            return 0

    async def detect_failure_patterns(self) -> list[dict[str, Any]]:
        """
        Detect tasks that have failed repeatedly and should be escalated.
        
        Returns list of tasks with failure patterns.
        """
        try:
            # Find tasks with high retry counts
            result = await self.db.execute(
                select(Task).where(
                    Task.retry_count >= self.max_retry_count,
                    Task.work_state.in_(["blocked", "not_started"])
                )
            )
            tasks = result.scalars().all()
            
            patterns = []
            
            for task in tasks:
                # Check if already escalated to human
                if task.escalation_tier >= 4:
                    continue
                
                patterns.append({
                    "id": task.id,
                    "title": task.title,
                    "project_id": task.project_id,
                    "retry_count": task.retry_count,
                    "escalation_tier": task.escalation_tier,
                    "failure_reason": task.failure_reason,
                    "last_retry_reason": task.last_retry_reason,
                })
                
                logger.warning(
                    f"[MONITOR] Failure pattern detected: {task.id[:8]} "
                    f"({task.retry_count} retries, tier {task.escalation_tier})"
                )
            
            return patterns
        
        except Exception as e:
            logger.error(f"Failed to detect failure patterns: {e}", exc_info=True)
            return []

    async def check_worker_health(self) -> dict[str, Any]:
        """
        Check health of active workers.
        
        Returns dict with worker health status.
        """
        try:
            result = await self.db.execute(
                select(WorkerStatus).where(WorkerStatus.active == True)
            )
            workers = result.scalars().all()
            
            if not workers:
                return {
                    "healthy": True,
                    "active": False,
                    "message": "No active workers",
                    "workers": []
                }
            
            now = datetime.now(timezone.utc)
            unhealthy_workers = []
            
            for worker in workers:
                worker_health = {
                    "worker_id": worker.worker_id,
                    "current_task": worker.current_task,
                    "current_project": worker.current_project,
                    "healthy": True,
                    "issues": []
                }
                
                # Check heartbeat timeout
                if worker.last_heartbeat:
                    hb = worker.last_heartbeat.replace(tzinfo=timezone.utc) if worker.last_heartbeat.tzinfo is None else worker.last_heartbeat
                    heartbeat_age = (now - hb).total_seconds()
                    if heartbeat_age > 300:  # 5 minutes
                        worker_health["healthy"] = False
                        worker_health["issues"].append(
                            f"Heartbeat stale ({int(heartbeat_age/60)}m)"
                        )
                
                # Check task duration
                if worker.started_at:
                    started = worker.started_at.replace(tzinfo=timezone.utc) if worker.started_at.tzinfo is None else worker.started_at
                    duration = (now - started).total_seconds()
                    if duration > self.kill_timeout:
                        worker_health["healthy"] = False
                        worker_health["issues"].append(
                            f"Running too long ({int(duration/60)}m)"
                        )
                
                if not worker_health["healthy"]:
                    unhealthy_workers.append(worker_health)
            
            return {
                "healthy": len(unhealthy_workers) == 0,
                "active": True,
                "total_workers": len(workers),
                "unhealthy_count": len(unhealthy_workers),
                "workers": unhealthy_workers,
                "message": f"{len(workers)} active worker(s), {len(unhealthy_workers)} unhealthy"
            }
        
        except Exception as e:
            logger.error(f"Failed to check worker health: {e}", exc_info=True)
            return {
                "healthy": False,
                "error": str(e)
            }

    async def get_system_health_summary(self) -> dict[str, Any]:
        """
        Get overall system health summary with statistics.
        
        Returns dict with system stats and health indicators.
        """
        try:
            now = datetime.now(timezone.utc)
            
            # Count tasks by state
            result = await self.db.execute(
                select(Task.work_state, Task.status).where(
                    Task.status == "active"
                )
            )
            tasks = result.all()
            
            stats = {
                "not_started": 0,
                "in_progress": 0,
                "blocked": 0,
                "completed_today": 0,
            }
            
            for work_state, status in tasks:
                if work_state in stats:
                    stats[work_state] += 1
            
            # Count completed tasks today
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            completed_result = await self.db.execute(
                select(Task).where(
                    Task.work_state == "completed",
                    Task.finished_at >= today_start
                )
            )
            stats["completed_today"] = len(completed_result.scalars().all())
            
            # Get worker status
            worker_health = await self.check_worker_health()
            
            # Get stuck tasks
            stuck_tasks = await self.check_stuck_tasks()
            
            # Get failure patterns
            failure_patterns = await self.detect_failure_patterns()
            
            # Calculate overall health
            healthy = (
                worker_health.get("healthy", True) and
                len(stuck_tasks) == 0 and
                len(failure_patterns) == 0
            )
            
            return {
                "timestamp": now.isoformat(),
                "healthy": healthy,
                "stats": stats,
                "worker": worker_health,
                "stuck_tasks": {
                    "count": len(stuck_tasks),
                    "tasks": stuck_tasks
                },
                "failure_patterns": {
                    "count": len(failure_patterns),
                    "tasks": failure_patterns
                }
            }
        
        except Exception as e:
            logger.error(f"Failed to get system health summary: {e}", exc_info=True)
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "healthy": False,
                "error": str(e)
            }

    async def run_full_check(self) -> dict[str, Any]:
        """
        Run all monitoring checks and return comprehensive results.
        
        This is called by the engine on each poll cycle.
        """
        try:
            # Run all checks
            stuck_tasks = await self.check_stuck_tasks()
            unblocked_count = await self.auto_unblock_tasks()
            recovered_count = await self.recover_escalation_blocked_tasks()
            failure_patterns = await self.detect_failure_patterns()
            worker_health = await self.check_worker_health()
            
            return {
                "stuck_tasks": len(stuck_tasks),
                "unblocked_tasks": unblocked_count,
                "recovered_tasks": recovered_count,
                "failure_patterns": len(failure_patterns),
                "worker_healthy": worker_health.get("healthy", True),
                "issues_found": (
                    len(stuck_tasks) + 
                    len(failure_patterns) + 
                    (0 if worker_health.get("healthy", True) else 1)
                )
            }
        
        except Exception as e:
            logger.error(f"Failed to run full monitoring check: {e}", exc_info=True)
            return {
                "error": str(e)
            }
