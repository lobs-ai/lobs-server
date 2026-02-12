"""Main orchestration engine - runs as asyncio background task."""

import asyncio
import logging
import time
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .config import POLL_INTERVAL
from .scanner import Scanner
from .router import Router
from .worker import WorkerManager
from .agent_tracker import AgentTracker

logger = logging.getLogger(__name__)


class OrchestratorEngine:
    """Main orchestration engine.
    
    Runs as an asyncio background task, polling for work and spawning workers.
    """

    def __init__(self, db_session_factory):
        """Initialize the orchestrator.
        
        Args:
            db_session_factory: Async callable that returns AsyncSession
        """
        self.db_session_factory = db_session_factory
        self.router = Router()
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.start_time = time.time()

    async def start(self):
        """Start the orchestrator background task."""
        if self.running:
            logger.warning("Orchestrator already running")
            return

        self.running = True
        self.task = asyncio.create_task(self._run_loop())
        logger.info("=" * 60)
        logger.info("Orchestrator started")
        logger.info("=" * 60)

    async def stop(self, timeout: float = 30.0):
        """Stop the orchestrator gracefully."""
        if not self.running:
            return

        logger.info("Stopping orchestrator...")
        self.running = False

        if self.task:
            try:
                await asyncio.wait_for(self.task, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"Orchestrator did not stop within {timeout}s, cancelling")
                self.task.cancel()
                try:
                    await self.task
                except asyncio.CancelledError:
                    pass

        logger.info("Orchestrator stopped")

    async def _run_loop(self):
        """Main orchestration loop."""
        iteration = 0
        current_interval = POLL_INTERVAL

        while self.running:
            try:
                iteration += 1
                async with self.db_session_factory() as db:
                    activity = await self._run_once(db)

                if activity:
                    current_interval = POLL_INTERVAL
                else:
                    # Adaptive backoff when idle
                    current_interval = min(current_interval + 2, POLL_INTERVAL * 6, 60)

            except Exception as e:
                logger.error(f"Error in orchestrator loop: {e}", exc_info=True)
                current_interval = POLL_INTERVAL

            if current_interval > POLL_INTERVAL:
                logger.debug(f"Idle, sleeping for {current_interval}s (iteration {iteration})")

            await asyncio.sleep(current_interval)

    async def _run_once(self, db: AsyncSession) -> bool:
        """Execute one iteration of the orchestration loop.
        
        Returns True if there was activity.
        """
        activity = False

        # Initialize components with database session
        scanner = Scanner(db)
        agent_tracker = AgentTracker(db)
        await agent_tracker.init_statuses()
        worker_manager = WorkerManager(db, agent_tracker)

        # 1. Check active workers
        initial_active = len(worker_manager.active_workers)
        await worker_manager.check_workers()
        if len(worker_manager.active_workers) != initial_active:
            activity = True

        # 2. Sync agent status to DB (rate-limited internally)
        await agent_tracker.sync_to_db()

        # 3. Scan for new work
        projects = await scanner.get_projects()
        eligible_work = await scanner.get_eligible_tasks()

        if not eligible_work:
            return activity

        # 4. Assign work
        for task in eligible_work:
            project_id = task.get("project_id") or "default"

            # Validate project exists
            project_ids = [p["id"] for p in projects]
            if project_id not in project_ids:
                logger.warning(f"Task {task['id']} has invalid projectId '{project_id}'. Skipping.")
                continue

            task_id = task["id"]
            task_title = task.get("title", task_id[:8])

            # Route to agent
            agent_type = self.router.route(task)
            logger.info(f"[ROUTER] Selected agent '{agent_type}' for {task_id[:8]}")

            # Try to spawn worker
            spawned = await worker_manager.spawn_worker(task, project_id, agent_type)
            if spawned:
                activity = True
                logger.info(f"Spawned worker for {task_id[:8]} (project={project_id}, agent={agent_type})")

        return activity

    def get_status(self) -> dict[str, Any]:
        """Get current orchestrator status."""
        return {
            "running": self.running,
            "uptime_seconds": int(time.time() - self.start_time),
        }

    async def get_worker_status(self) -> dict[str, Any]:
        """Get current worker status."""
        async with self.db_session_factory() as db:
            agent_tracker = AgentTracker(db)
            await agent_tracker.init_statuses()
            worker_manager = WorkerManager(db, agent_tracker)
            return worker_manager.get_worker_status()

    async def pause(self):
        """Pause the orchestrator (stop accepting new work)."""
        # TODO: Implement pause logic
        logger.info("Orchestrator pause requested (not yet implemented)")

    async def resume(self):
        """Resume the orchestrator."""
        # TODO: Implement resume logic
        logger.info("Orchestrator resume requested (not yet implemented)")
