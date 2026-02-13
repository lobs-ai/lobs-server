"""Orchestrator engine - main async polling loop.

Port of ~/lobs-orchestrator/orchestrator/core/engine.py
Key changes:
- Replace all git operations with SQLAlchemy queries
- Use scanner.py to find eligible tasks
- Use router.py to route tasks to agents
- Run as asyncio background task
- Support pause/resume
"""

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.database import AsyncSessionLocal
from app.orchestrator.scanner import Scanner
from app.orchestrator.router import Router
from app.orchestrator.worker import WorkerManager
from app.orchestrator.monitor import Monitor
from app.orchestrator.monitor_enhanced import MonitorEnhanced
from app.orchestrator.escalation_enhanced import EscalationManagerEnhanced
from app.orchestrator.circuit_breaker import CircuitBreaker
from app.orchestrator.agent_tracker import AgentTracker
from app.orchestrator.scheduler import EventScheduler
from app.orchestrator.inbox_processor import InboxProcessor
from app.orchestrator.config import POLL_INTERVAL

logger = logging.getLogger(__name__)


class OrchestratorEngine:
    """
    Main orchestration engine.
    
    Responsibilities:
    - Poll for work and spawn workers
    - Track worker lifecycle
    - Monitor system health
    - Handle task routing
    """

    def __init__(
        self,
        session_factory: Optional[Callable[[], Any]] = None,
    ):
        self._session_factory = session_factory or AsyncSessionLocal
        self.router = Router()
        self._running = False
        self._paused = False
        self._task: Optional[asyncio.Task] = None
        self.last_poll = 0.0
        self._last_scheduler_check = 0.0
        self._scheduler_interval = 60  # Check events every 60 seconds
        self._last_inbox_check = 0.0
        self._inbox_interval = 45  # Process inbox every 45 seconds
        # Persistent worker manager (survives across ticks)
        self._worker_manager: Optional[WorkerManager] = None

    async def start(self) -> None:
        """Start the orchestrator engine as a background task."""
        if self._running:
            logger.warning("[ENGINE] Already running")
            return

        # Check if OpenClaw is available
        self._openclaw_available = shutil.which("openclaw") is not None
        if not self._openclaw_available:
            logger.warning("[ENGINE] OpenClaw not found on PATH — orchestrator will run in monitoring-only mode (no worker spawning)")
        
        self._running = True
        self._paused = False
        self._task = asyncio.create_task(self._run_loop())
        
        logger.info("=" * 60)
        logger.info("[ENGINE] Orchestrator started%s", " (monitoring-only, no OpenClaw)" if not self._openclaw_available else "")
        logger.info("=" * 60)

    async def stop(self, timeout: Optional[float] = None) -> None:
        """Stop the orchestrator engine."""
        if not self._running:
            return

        logger.info("[ENGINE] Stopping orchestrator...")
        self._running = False
        
        if self._task:
            self._task.cancel()
            try:
                if timeout is not None:
                    await asyncio.wait_for(self._task, timeout=timeout)
                else:
                    await self._task
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                logger.warning(
                    "[ENGINE] Timed out waiting for orchestrator loop to stop"
                )

        # Shutdown workers
        if self._worker_manager:
            async with self._session_factory() as db:
                self._worker_manager.db = db
                await self._worker_manager.shutdown()

        logger.info("[ENGINE] Orchestrator stopped")

    def pause(self) -> None:
        """Pause the orchestrator (stop spawning new workers)."""
        self._paused = True
        logger.info("[ENGINE] Orchestrator paused")

    def resume(self) -> None:
        """Resume the orchestrator."""
        self._paused = False
        logger.info("[ENGINE] Orchestrator resumed")

    def is_running(self) -> bool:
        """Check if orchestrator is running."""
        return self._running

    def is_paused(self) -> bool:
        """Check if orchestrator is paused."""
        return self._paused

    async def _run_loop(self) -> None:
        """Main orchestration loop."""
        current_interval = POLL_INTERVAL
        iteration = 0

        while self._running:
            try:
                iteration += 1
                activity = await self._run_once()

                if activity:
                    current_interval = POLL_INTERVAL
                else:
                    # Adaptive backoff when idle
                    current_interval = min(current_interval + 2, POLL_INTERVAL * 6, 60)

            except Exception as e:
                logger.error(f"[ENGINE] Error in orchestration loop: {e}", exc_info=True)
                current_interval = POLL_INTERVAL

            if current_interval > POLL_INTERVAL:
                logger.debug(
                    f"[ENGINE] Idle, sleeping for {current_interval}s "
                    f"(iteration {iteration})"
                )

            await asyncio.sleep(current_interval)

    async def _run_once(self) -> bool:
        """
        Execute one iteration of the orchestration loop.
        
        Returns True if there was activity.
        """
        activity = False

        async with self._session_factory() as db:
            scanner = Scanner(db)
            # Reuse persistent worker manager, just update its db session
            if self._worker_manager is None:
                self._worker_manager = WorkerManager(db)
            else:
                self._worker_manager.db = db
            worker_manager = self._worker_manager
            monitor = Monitor(db)
            monitor_enhanced = MonitorEnhanced(db)
            circuit_breaker = CircuitBreaker(db)
            escalation = EscalationManagerEnhanced(db)
            agent_tracker = AgentTracker(db)
            scheduler = EventScheduler(db)

            # 1. Check scheduled events (every 60 seconds)
            import time
            current_time = time.time()
            if current_time - self._last_scheduler_check >= self._scheduler_interval:
                try:
                    result = await scheduler.check_due_events()
                    if result["total_fired"] > 0:
                        activity = True
                        logger.info(
                            f"[ENGINE] Scheduler fired {result['total_fired']} event(s)"
                        )
                    self._last_scheduler_check = current_time
                    await db.commit()  # Commit scheduler changes
                except Exception as e:
                    logger.error(f"[ENGINE] Scheduler check failed: {e}", exc_info=True)
                    await db.rollback()

            # 2. Process inbox threads (every 45 seconds, only if not paused)
            if not self._paused and current_time - self._last_inbox_check >= self._inbox_interval:
                try:
                    inbox_processor = InboxProcessor(db)
                    result = await inbox_processor.process_threads()
                    if result["threads_processed"] > 0:
                        activity = True
                    self._last_inbox_check = current_time
                    # Commit happens inside process_threads()
                except Exception as e:
                    logger.error(f"[ENGINE] Inbox processing failed: {e}", exc_info=True)
                    await db.rollback()

            # 3. Check active workers
            initial_active = len(worker_manager.active_workers)
            await worker_manager.check_workers()
            if len(worker_manager.active_workers) != initial_active:
                activity = True

            # 4. Enhanced monitoring (includes auto-unblock, failure detection, etc.)
            try:
                monitor_result = await monitor_enhanced.run_full_check()
                if monitor_result.get("issues_found", 0) > 0:
                    activity = True
                    logger.info(
                        f"[ENGINE] Monitor found {monitor_result.get('issues_found')} issue(s): "
                        f"stuck={monitor_result.get('stuck_tasks', 0)}, "
                        f"unblocked={monitor_result.get('unblocked_tasks', 0)}, "
                        f"patterns={monitor_result.get('failure_patterns', 0)}"
                    )
            except Exception as e:
                logger.error(f"[ENGINE] Enhanced monitor check failed: {e}", exc_info=True)

            # 5. Skip work assignment if paused or OpenClaw unavailable
            if self._paused:
                return activity
            
            if not self._openclaw_available:
                return activity

            # 6. Scan for eligible tasks
            eligible_tasks = await scanner.get_eligible_tasks()
            
            if not eligible_tasks:
                return activity

            # Log queue depth if worker is busy
            worker_status = await worker_manager.get_worker_status()
            if worker_status.get("busy") and len(eligible_tasks) > 0:
                current = (worker_status.get("current_task") or "unknown")[:8]
                logger.info(
                    f"[ENGINE] Worker busy (current: {current}). "
                    f"{len(eligible_tasks)} task(s) queued."
                )

            # 7. Process eligible tasks
            for task_dict in eligible_tasks:
                activity = True
                
                task_id = task_dict.get("id")
                project_id = task_dict.get("project_id")
                task_title = task_dict.get("title", task_id[:8] if task_id else "unknown")

                if not task_id or not project_id:
                    logger.warning("[ENGINE] Task missing ID or project_id, skipping")
                    continue

                # Route task to agent
                try:
                    agent_type = self.router.route(task_dict)
                    logger.info(
                        f"[ENGINE] Routing task {task_id[:8]} to {agent_type} agent"
                    )
                except Exception as e:
                    logger.warning(
                        f"[ENGINE] Failed to route task {task_id[:8]}: {e}. "
                        f"Using default (programmer)"
                    )
                    agent_type = "programmer"

                # Check circuit breaker before spawning
                allowed, reason = await circuit_breaker.should_allow_spawn(
                    project_id=project_id,
                    agent_type=agent_type
                )
                
                if not allowed:
                    logger.warning(
                        f"[ENGINE] Circuit breaker blocked spawn for {task_id[:8]}: {reason}"
                    )
                    continue

                # Try to spawn worker
                spawned = await worker_manager.spawn_worker(
                    task=task_dict,
                    project_id=project_id,
                    agent_type=agent_type
                )

                if spawned:
                    logger.info(
                        f"[ENGINE] Spawned worker for task {task_id[:8]} "
                        f"(project={project_id}, agent={agent_type})"
                    )
                    # Only process one task per iteration
                    break
                else:
                    logger.debug(
                        f"[ENGINE] Worker not spawned for task {task_id[:8]} "
                        f"(likely queued due to locks/capacity)"
                    )

        return activity

    async def get_status(self) -> dict[str, Any]:
        """Get current orchestrator status."""
        async with self._session_factory() as db:
            if self._worker_manager is None:
                self._worker_manager = WorkerManager(db)
            else:
                self._worker_manager.db = db
            agent_tracker = AgentTracker(db)
            
            worker_status = await self._worker_manager.get_worker_status()
            agent_statuses = await agent_tracker.get_all_statuses()

            return {
                "running": self._running,
                "paused": self._paused,
                "worker": worker_status,
                "agents": agent_statuses,
                "poll_interval": POLL_INTERVAL
            }

    async def get_worker_details(self) -> list[dict[str, Any]]:
        """Get details of all active workers."""
        async with self._session_factory() as db:
            if self._worker_manager is None:
                self._worker_manager = WorkerManager(db)
            else:
                self._worker_manager.db = db
            
            workers = []
            for worker_id, (process, task_id, project_id, agent_type, start_time, log_file) in self._worker_manager.active_workers.items():
                import time
                runtime = time.time() - start_time
                
                workers.append({
                    "worker_id": worker_id,
                    "task_id": task_id,
                    "project_id": project_id,
                    "agent_type": agent_type,
                    "pid": process.pid,
                    "runtime_seconds": int(runtime),
                    "started_at": datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat(),
                    "log_file": str(log_file)
                })

            return workers
