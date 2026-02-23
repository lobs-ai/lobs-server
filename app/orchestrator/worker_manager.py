"""Worker manager - spawns and manages OpenClaw workers via Gateway API.

Refactored to use OpenClaw Gateway /tools/invoke with sessions_spawn instead
of subprocess.Popen. This enables per-task model control and removes the need
for git branch management (sub-agents handle their own workspace).

Key changes from subprocess version:
- HTTP calls to Gateway API instead of subprocess.Popen
- Track workers by runId and childSessionKey instead of PID
- Use sessions_list to poll status instead of process.poll()
- Remove git_manager integration (sub-agents work in their own workspace)
- Keep: DB tracking, domain locks, circuit breaker, escalation
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Task,
    WorkerStatus,
    Project,
)
from app.orchestrator.config import (
    BASE_DIR,
    WORKER_RESULTS_DIR,
    MAX_WORKERS,
)
from app.orchestrator.model_chooser import ModelChooser
from app.orchestrator.agent_tracker import AgentTracker
from app.orchestrator.prompter import Prompter
from app.orchestrator.worker_models import WorkerInfo
from app.orchestrator.worker_gateway import WorkerGateway
from app.orchestrator.worker_monitor import WorkerMonitor
from app.services.usage import infer_provider

logger = logging.getLogger(__name__)


class WorkerManager:
    """
    Manages spawning and tracking concurrent workers via OpenClaw Gateway.
    
    Tracks active workers in memory and syncs state to DB.
    Enforces domain locks (one worker per project). Multiple instances of
    the same agent type can run concurrently on different projects.
    """

    def __init__(self, db: AsyncSession, provider_health: Optional[Any] = None, session_factory: Optional[Any] = None):
        self.db = db
        self.provider_health = provider_health  # ProviderHealthRegistry instance
        self._session_factory = session_factory  # Override for independent DB sessions (testing)
        
        # In-memory tracking: worker_id -> WorkerInfo
        self.active_workers: dict[str, WorkerInfo] = {}
        
        # Domain locks: one worker per project (prevents repo conflicts)
        self.project_locks: dict[str, str] = {}  # project_id -> task_id

        self.max_workers = MAX_WORKERS

        # Initialize gateway and monitor
        self.gateway = WorkerGateway(db)
        self.monitor = WorkerMonitor(
            db=db,
            active_workers=self.active_workers,
            project_locks=self.project_locks,
            gateway=self.gateway,
            provider_health=provider_health,
            session_factory=session_factory,
        )

    @property
    def sweep_requested(self) -> bool:
        """Check if monitor has requested a sweep."""
        return self.monitor.sweep_requested
    
    @sweep_requested.setter
    def sweep_requested(self, value: bool):
        """Set sweep request flag."""
        self.monitor.sweep_requested = value

    async def spawn_worker(
        self,
        task: dict[str, Any],
        project_id: str,
        agent_type: str,
        rules: Optional[dict[str, Any]] = None
    ) -> bool:
        """
        Spawn an OpenClaw worker via Gateway API for the given task.
        
        Args:
            task: Task dict (from scanner)
            project_id: Project ID
            agent_type: Agent type (programmer/researcher/etc)
            rules: Optional engineering rules (unused, kept for API compat)
            
        Returns:
            True if worker spawned, False if queued/blocked
        """
        task_id = task.get("id")
        if not task_id:
            logger.warning("Cannot spawn worker: task missing ID")
            return False

        # Check capacity
        if len(self.active_workers) >= self.max_workers:
            logger.debug(
                f"[WORKER] Max workers ({self.max_workers}) reached. "
                f"Task {task_id[:8]} queued."
            )
            return False

        # Check project lock (one worker per project)
        if project_id in self.project_locks:
            logger.debug(
                f"[WORKER] Project {project_id} locked. "
                f"Task {task_id[:8]} queued."
            )
            return False

        # Note: agent type lock removed — multiple instances of the same
        # agent can now run concurrently (Gateway sessions are isolated).
        # Project lock still enforced to prevent repo conflicts.

        task_id_short = task_id[:8]
        
        try:
            # Get project details
            project = await self.db.get(Project, project_id)
            if not project:
                logger.error(f"Project {project_id} not found")
                return False

            # Resolve repo path for context (sub-agent will use its own workspace)
            # This is only used for building the prompt context
            if project.repo_path:
                repo_path = Path(project.repo_path)
            else:
                repo_path = BASE_DIR / project_id

            # Create worker ID and label
            worker_id = f"worker_{int(time.time())}_{task_id_short}"
            label = f"task-{task_id_short}"
            
            # Build task prompt using Prompter
            task_title = task.get("title", task_id_short)
            prompt_file = WORKER_RESULTS_DIR / f"{task_id}.prompt.txt"
            
            try:
                # Build structured prompt with agent context
                global_rules = ""  # TODO: Load from config/DB if needed
                prompt_content = Prompter.build_task_prompt(
                    item=task,
                    project_path=repo_path,
                    agent_type=agent_type,
                    rules=global_rules
                )
                prompt_file.write_text(prompt_content, encoding="utf-8")
                logger.info(
                    f"[WORKER] Built structured prompt for {task_id_short} "
                    f"(agent={agent_type})"
                )
            except Exception as e:
                # Fallback to simple prompt
                logger.warning(
                    f"[WORKER] Prompter failed for {task_id_short}: {e}. "
                    f"Using fallback."
                )
                task_notes = task.get("notes", "")
                prompt_content = f"{task_title}\n\n{task_notes}".strip()
                prompt_file.write_text(prompt_content, encoding="utf-8")

            # Select model preference list + audit
            chooser = ModelChooser(self.db, provider_health=self.provider_health)
            choice = await chooser.choose(
                agent_type=agent_type,
                task=task,
                purpose="execution",
            )
            logger.info("[MODEL_ROUTER] decision", extra={"model_router": choice.audit})

            chosen_model: str | None = None
            spawn_result: Optional[dict[str, str]] = None
            attempts: list[dict[str, Any]] = []

            candidate_models = list(choice.candidates)
            strict_coding_tier = bool(choice.strict_coding_tier)

            # Call Gateway API: sessions_spawn with fallback chain
            for idx, candidate in enumerate(candidate_models):
                spawn_result, err, err_type = await self.gateway.spawn_session(
                    task_prompt=prompt_content,
                    agent_id=agent_type,
                    model=candidate,
                    label=label,
                    routing_policy=choice.routing_policy or {},
                )
                attempts.append(
                    {
                        "index": idx,
                        "model": candidate,
                        "ok": bool(spawn_result),
                        "error": err,
                        "error_type": err_type,
                    }
                )
                
                # Record outcome in provider health
                if self.provider_health:
                    provider = infer_provider(candidate)
                    if spawn_result:
                        self.provider_health.record_outcome(
                            provider=provider,
                            model=candidate,
                            success=True,
                        )
                        chosen_model = candidate
                        break
                    else:
                        self.provider_health.record_outcome(
                            provider=provider,
                            model=candidate,
                            success=False,
                            error_type=err_type,
                        )
                else:
                    if spawn_result:
                        chosen_model = candidate
                        break

            if not spawn_result or not chosen_model:
                if agent_type == "programmer" and strict_coding_tier:
                    logger.error(
                        "[MODEL_ROUTER] strict coding tier prevented model downgrade after spawn failure",
                        extra={"model_router": {**choice.audit, "attempts": attempts}},
                    )
                logger.error(
                    f"[WORKER] Failed to spawn session for {task_id_short}",
                    extra={"model_router": {**choice.audit, "attempts": attempts}},
                )
                return False

            run_id = spawn_result["runId"]
            child_session_key = spawn_result["childSessionKey"]
            start_time = time.time()
            
            # Track worker
            worker_info = WorkerInfo(
                run_id=run_id,
                child_session_key=child_session_key,
                task_id=task_id,
                project_id=project_id,
                agent_type=agent_type,
                model=chosen_model,
                start_time=start_time,
                label=label,
                model_audit={
                    **choice.audit,
                    "attempts": attempts,
                    "chosen_model": chosen_model,
                    "fallback_used": chosen_model != candidate_models[0],
                    "fallback_reason": (
                        "provider_failure" if chosen_model != candidate_models[0] else None
                    ),
                    "strict_coding_tier": strict_coding_tier,
                    "degrade_on_quota": bool(choice.degrade_on_quota),
                    "subscription_models": (choice.routing_policy or {}).get("subscription_models", []),
                    "subscription_providers": (choice.routing_policy or {}).get("subscription_providers", []),
                },
            )
            self.active_workers[worker_id] = worker_info
            self.project_locks[project_id] = task_id

            # Update DB: worker status
            await self._update_worker_status(
                active=True,
                worker_id=worker_id,
                task_id=task_id,
                project_id=project_id,
                started_at=datetime.fromtimestamp(start_time, tz=timezone.utc)
            )

            # Update DB: task status
            db_task = await self.db.get(Task, task_id)
            if db_task:
                db_task.work_state = "in_progress"
                db_task.started_at = datetime.now(timezone.utc)
                db_task.updated_at = datetime.now(timezone.utc)
                await self.db.commit()

            # Update agent tracker
            await AgentTracker(self.db).mark_working(
                agent_type=agent_type,
                task_id=task_id,
                project_id=project_id,
                activity=task_title
            )

            logger.info(
                f"[WORKER] Spawned worker {worker_id} for task {task_id_short} "
                f"(project={project_id}, agent={agent_type}, model={chosen_model}, "
                f"runId={run_id[:12]}...)" ,
                extra={"model_router": worker_info.model_audit},
            )

            return True

        except Exception as e:
            logger.error(
                f"Failed to spawn worker for task {task_id_short}: {e}",
                exc_info=True
            )
            return False

    def register_external_worker(
        self,
        spawn_result: dict[str, str],
        *,
        agent_type: str,
        model: str,
        label: str,
        task_id: str | None = None,
        project_id: str | None = None,
    ) -> str:
        """Register a worker spawned outside of spawn_worker() (e.g. reflections, diagnostics).

        This ensures check_workers() polls and handles completion for these sessions.
        Returns the generated worker_id.
        """
        import time as _time

        run_id = spawn_result["runId"]
        child_session_key = spawn_result["childSessionKey"]
        worker_id = f"ext_{int(_time.time())}_{label}"
        self.active_workers[worker_id] = WorkerInfo(
            run_id=run_id,
            child_session_key=child_session_key,
            task_id=task_id or label,
            project_id=project_id or "",
            agent_type=agent_type,
            model=model,
            start_time=_time.time(),
            label=label,
        )
        logger.info("[WORKER] Registered external worker %s (label=%s, agent=%s)", worker_id, label, agent_type)
        return worker_id

    async def check_workers(self) -> None:
        """
        Check all active workers and handle completed/failed ones.
        
        Called by engine on each tick.
        """
        await self.monitor.check_workers()

    async def _update_worker_status(
        self,
        active: bool,
        worker_id: Optional[str] = None,
        task_id: Optional[str] = None,
        project_id: Optional[str] = None,
        started_at: Optional[datetime] = None
    ) -> None:
        """Update worker_status table (singleton record)."""
        try:
            result = await self.db.execute(
                select(WorkerStatus).where(WorkerStatus.id == 1)
            )
            status = result.scalar_one_or_none()

            if not status:
                status = WorkerStatus(id=1)
                self.db.add(status)

            status.active = active
            
            if active:
                status.worker_id = worker_id
                status.current_task = task_id
                status.current_project = project_id
                status.started_at = started_at
                status.last_heartbeat = datetime.now(timezone.utc)
            else:
                status.worker_id = None
                status.current_task = None
                status.current_project = None
                status.ended_at = datetime.now(timezone.utc)

            await self.db.commit()

        except Exception as e:
            logger.error(f"Failed to update worker status: {e}", exc_info=True)
            await self.db.rollback()

    async def get_worker_status(self) -> dict[str, Any]:
        """Get current worker status summary."""
        try:
            result = await self.db.execute(
                select(WorkerStatus).where(WorkerStatus.id == 1)
            )
            status = result.scalar_one_or_none()

            if not status or not status.active:
                return {
                    "busy": False,
                    "active_count": 0,
                    "current_task": None,
                    "state": "idle"
                }

            return {
                "busy": True,
                "active_count": len(self.active_workers),
                "current_task": status.current_task,
                "current_project": status.current_project,
                "worker_id": status.worker_id,
                "state": "working",
                "started_at": (
                    status.started_at.isoformat() if status.started_at else None
                )
            }

        except Exception as e:
            logger.error(f"Failed to get worker status: {e}", exc_info=True)
            return {
                "busy": False,
                "active_count": 0,
                "error": str(e)
            }

    async def shutdown(self, timeout: float = 300.0) -> None:
        """Gracefully shutdown all workers."""
        if not self.active_workers:
            logger.info("[WORKER] No active workers to shutdown")
            return

        logger.info(f"[WORKER] Shutting down {len(self.active_workers)} workers...")

        # For now, just mark all workers as failed
        # TODO: Implement graceful session termination via Gateway API
        for worker_id in list(self.active_workers.keys()):
            worker_info = self.active_workers[worker_id]
            await self.monitor.handle_worker_completion(
                worker_id=worker_id,
                worker_info=worker_info,
                succeeded=False,
                error_log="Orchestrator shutdown"
            )

        # Clear state
        self.active_workers.clear()
        self.project_locks.clear()

        # Update DB
        await self._update_worker_status(active=False)

        logger.info("[WORKER] Worker shutdown complete")
