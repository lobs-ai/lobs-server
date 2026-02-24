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
import aiohttp
import logging
import shutil
import uuid
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Callable, Optional

from app.database import AsyncSessionLocal
from app.orchestrator.scanner import Scanner
from app.orchestrator.capability_registry import CapabilityRegistrySync
from app.orchestrator.worker import WorkerManager
from app.orchestrator.workflow_executor import WorkflowExecutor
from app.orchestrator.monitor_enhanced import MonitorEnhanced
from app.orchestrator.circuit_breaker import CircuitBreaker
from app.orchestrator.agent_tracker import AgentTracker
from app.orchestrator.scheduler import EventScheduler
from app.orchestrator.routine_runner import RoutineRunner
# Inbox processing now handled by inbox-processing workflow
from app.orchestrator.provider_health import ProviderHealthRegistry
from app.orchestrator.config import POLL_INTERVAL, GATEWAY_URL, GATEWAY_TOKEN, GATEWAY_SESSION_KEY
from app.models import Project as ProjectModel, Task as TaskModel, OrchestratorSetting, InboxItem, ControlLoopHeartbeat, AgentReflection
from app.services.github_sync import GitHubSyncService
from app.services.openclaw_models import fetch_openclaw_model_catalog
from sqlalchemy import select
from app.orchestrator.runtime_settings import (
    DEFAULT_RUNTIME_SETTINGS,
    SETTINGS_KEY_OPENCLAW_MODEL_SYNC_INTERVAL_SECONDS,
)

logger = logging.getLogger(__name__)

USAGE_ROUTING_POLICY_KEY = "usage.routing_policy"
OPENCLAW_MODEL_CATALOG_KEY = "usage.openclaw_model_catalog"


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
        self._running = False
        self._paused = False
        self._task: Optional[asyncio.Task] = None
        self.last_poll = 0.0
        # Timer-based checks (all recurring work now driven by workflow scheduler)
        self._last_scheduler_check = 0.0
        self._scheduler_interval = 60
        self._last_routine_check = 0.0
        self._routine_interval = 60
        # Inbox processing moved to inbox-processing workflow
        self._last_capability_sync = 0.0
        self._capability_sync_interval = 3600
        self._last_runtime_settings_refresh = 0.0
        self._runtime_settings_refresh_interval = 60
        self._last_openclaw_model_sync = 0.0
        self._openclaw_model_sync_interval = 900
        # Persistent worker manager (survives across ticks)
        self._worker_manager: Optional[WorkerManager] = None
        # Provider health tracking (persistent across ticks)
        self.provider_health: Optional[ProviderHealthRegistry] = None

    async def start(self) -> None:
        """Start the orchestrator engine as a background task."""
        if self._running:
            logger.warning("[ENGINE] Already running")
            return

        # Check if OpenClaw is available
        self._openclaw_available = shutil.which("openclaw") is not None
        if not self._openclaw_available:
            logger.warning("[ENGINE] OpenClaw not found on PATH — orchestrator will run in monitoring-only mode (no worker spawning)")
        
        # Startup recovery: ensure default project exists and reset orphaned tasks
        await self._startup_recovery()
        
        # Seed default workflow definitions
        try:
            from app.orchestrator.workflow_seeds import seed_default_workflows
            async with self._session_factory() as db:
                count = await seed_default_workflows(db)
                if count:
                    logger.info("[ENGINE] Seeded %d default workflow(s)", count)
        except Exception as e:
            logger.warning("[ENGINE] Workflow seeding failed: %s", e)
        
        self._running = True
        self._paused = False
        self._task = asyncio.create_task(self._run_loop())
        
        logger.info("=" * 60)
        logger.info("[ENGINE] Orchestrator started%s", " (monitoring-only, no OpenClaw)" if not self._openclaw_available else "")
        logger.info("=" * 60)

    async def _startup_recovery(self) -> None:
        """Run once on startup: ensure default project exists and reset orphaned in_progress tasks."""
        try:
            async with self._session_factory() as db:
                # 1. Ensure default project exists
                default_project = await db.get(ProjectModel, "default")
                if not default_project:
                    default_project = ProjectModel(
                        id="default",
                        title="General",
                        notes="Default project for tasks not tied to a specific project",
                        archived=False,
                        type="kanban",
                        sort_order=99,
                        tracking="local",
                    )
                    db.add(default_project)
                    await db.commit()
                    logger.info("[ENGINE] Created default project 'General'")

                # 2. Reset orphaned in_progress tasks (no worker alive after restart)
                result = await db.execute(
                    select(TaskModel).where(TaskModel.work_state == "in_progress")
                )
                orphaned = result.scalars().all()

                if orphaned:
                    for task in orphaned:
                        task.work_state = "not_started"
                        task.updated_at = datetime.now(timezone.utc)
                    await db.commit()
                    logger.info(
                        "[ENGINE] Startup recovery: reset %d orphaned in_progress task(s) to not_started",
                        len(orphaned),
                    )

                # 3. Cancel stale workflow runs (no workers alive after restart)
                from app.models import WorkflowRun
                wf_result = await db.execute(
                    select(WorkflowRun).where(WorkflowRun.status == "running")
                )
                stale_runs = wf_result.scalars().all()
                if stale_runs:
                    for wf_run in stale_runs:
                        wf_run.status = "failed"
                        wf_run.error = "Stale: cancelled during startup recovery"
                        wf_run.finished_at = datetime.now(timezone.utc)
                        wf_run.updated_at = datetime.now(timezone.utc)
                    await db.commit()
                    logger.info(
                        "[ENGINE] Startup recovery: cancelled %d stale workflow run(s)",
                        len(stale_runs),
                    )

                # 4. Unblock tasks that were blocked due to transient errors
                blocked_result = await db.execute(
                    select(TaskModel).where(
                        TaskModel.work_state == "blocked",
                        TaskModel.failure_reason.isnot(None),
                    )
                )
                blocked_tasks = blocked_result.scalars().all()
                transient_reasons = [
                    "No matching workflow for agent",
                    "Stuck - no progress",
                    "Infrastructure failure detected",
                    "Stale:",
                ]
                unblocked = 0
                for task in blocked_tasks:
                    reason = task.failure_reason or ""
                    if any(r in reason for r in transient_reasons):
                        task.work_state = "not_started"
                        task.failure_reason = None
                        task.updated_at = datetime.now(timezone.utc)
                        unblocked += 1
                if unblocked:
                    await db.commit()
                    logger.info(
                        "[ENGINE] Startup recovery: unblocked %d task(s) blocked by transient errors",
                        unblocked,
                    )

                # 5. Clean up stale pending reflections (>2h old = never going to complete)
                from app.models import AgentReflection
                stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
                stale_refl_result = await db.execute(
                    select(AgentReflection).where(
                        AgentReflection.status == "pending",
                        AgentReflection.created_at < stale_cutoff,
                    )
                )
                stale_reflections = stale_refl_result.scalars().all()
                if stale_reflections:
                    for refl in stale_reflections:
                        refl.status = "failed"
                        refl.result = {"error": "stale: never completed (server restart)"}
                    await db.commit()
                    logger.info(
                        "[ENGINE] Startup recovery: marked %d stale pending reflections as failed",
                        len(stale_reflections),
                    )

                # 6. Clear stale worker_status
                from app.models import WorkerStatus
                ws_result = await db.execute(
                    select(WorkerStatus).where(WorkerStatus.id == 1)
                )
                ws = ws_result.scalar_one_or_none()
                if ws and ws.active:
                    ws.active = False
                    ws.worker_id = None
                    ws.current_task = None
                    ws.current_project = None
                    ws.ended_at = datetime.now(timezone.utc)
                    await db.commit()
                    logger.info("[ENGINE] Startup recovery: cleared stale worker_status")

        except Exception as e:
            logger.error("[ENGINE] Startup recovery failed: %s", e, exc_info=True)

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
            
            # Initialize provider health registry (persistent, shared across ticks)
            if self.provider_health is None:
                self.provider_health = ProviderHealthRegistry(db)
                await self.provider_health.initialize()
                logger.info("[ENGINE] Provider health registry initialized")
            else:
                # Update DB session reference
                self.provider_health.db = db
            
            # Reuse persistent worker manager, just update its db session
            if self._worker_manager is None:
                self._worker_manager = WorkerManager(db, provider_health=self.provider_health)
            else:
                self._worker_manager.db = db
                self._worker_manager.provider_health = self.provider_health
            worker_manager = self._worker_manager
            monitor_enhanced = MonitorEnhanced(db, worker_manager=worker_manager)
            circuit_breaker = CircuitBreaker(db)
            scheduler = EventScheduler(db)

            import time
            current_time = time.time()

            # 0. Refresh runtime-configurable settings from DB
            if current_time - self._last_runtime_settings_refresh >= self._runtime_settings_refresh_interval:
                try:
                    await self._refresh_runtime_settings(db)
                except Exception as e:
                    logger.warning("[ENGINE] Runtime settings refresh failed: %s", e)
                self._last_runtime_settings_refresh = current_time

            # 1. Check scheduled events (every 60 seconds)
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

            # 1a. Check routine registry (every 60 seconds)
            if current_time - self._last_routine_check >= self._routine_interval:
                try:
                    runner = RoutineRunner(db, hooks={
                        # Built-in no-op hook for smoke tests and manual triggers.
                        "noop": (lambda _r: asyncio.sleep(0, result={"hook": "noop", "status": "ok"})),
                        # Memory maintenance: audit and clean all agent workspaces.
                        "memory_maintenance": (lambda _r: run_memory_maintenance(_r)),
                    })
                    routine_result = await runner.process_due_routines(limit=10)
                    if routine_result.executed or routine_result.notified or routine_result.confirmation_requested:
                        activity = True
                        logger.info(
                            "[ENGINE] Routines processed: executed=%s notified=%s confirm=%s errors=%s",
                            routine_result.executed,
                            routine_result.notified,
                            routine_result.confirmation_requested,
                            routine_result.errors,
                        )
                    self._last_routine_check = current_time
                    await db.commit()
                except Exception as e:
                    logger.error("[ENGINE] Routine registry check failed: %s", e, exc_info=True)
                    await db.rollback()

            # 1b. GitHub sync now handled by github-sync workflow (every 15 min)

            # 1c. Sync OpenClaw model/auth/billing catalog (every 15 minutes)
            if current_time - self._last_openclaw_model_sync >= self._openclaw_model_sync_interval:
                try:
                    catalog = await fetch_openclaw_model_catalog()
                    setting = await db.get(OrchestratorSetting, OPENCLAW_MODEL_CATALOG_KEY)
                    if setting is None:
                        setting = OrchestratorSetting(key=OPENCLAW_MODEL_CATALOG_KEY, value=catalog)
                        db.add(setting)
                    else:
                        setting.value = catalog

                    models = catalog.get("models") if isinstance(catalog, dict) else []
                    subscription_models: list[str] = []
                    subscription_providers: list[str] = []
                    if isinstance(models, list):
                        for item in models:
                            if not isinstance(item, dict):
                                continue
                            if str(item.get("billing_type", "")).lower() != "subscription":
                                continue
                            model = item.get("model")
                            provider = item.get("provider")
                            if isinstance(model, str) and model not in subscription_models:
                                subscription_models.append(model)
                            if isinstance(provider, str) and provider not in subscription_providers:
                                subscription_providers.append(provider)

                    policy_setting = await db.get(OrchestratorSetting, USAGE_ROUTING_POLICY_KEY)
                    policy = policy_setting.value if (policy_setting and isinstance(policy_setting.value, dict)) else {}
                    policy.setdefault("subscription_first_task_types", ["inbox", "quick_summary", "triage", "inbox_item"])
                    policy.setdefault("fallback_chains", {
                        "inbox": ["subscription", "kimi", "minimax", "openai", "claude"],
                        "quick_summary": ["subscription", "kimi", "minimax", "openai", "claude"],
                        "triage": ["subscription", "kimi", "minimax", "openai", "claude"],
                        "default": ["openai", "claude", "kimi", "minimax", "subscription"],
                    })
                    policy["subscription_models"] = subscription_models
                    policy["subscription_providers"] = subscription_providers

                    if policy_setting is None:
                        policy_setting = OrchestratorSetting(key=USAGE_ROUTING_POLICY_KEY, value=policy)
                        db.add(policy_setting)
                    else:
                        policy_setting.value = policy

                    await db.commit()
                    self._last_openclaw_model_sync = current_time
                    if subscription_models or subscription_providers:
                        activity = True
                        logger.info(
                            "[ENGINE] OpenClaw model catalog sync complete: models=%s subscriptions=%s",
                            catalog.get("count", 0) if isinstance(catalog, dict) else 0,
                            len(subscription_models),
                        )
                except Exception as e:
                    logger.error("[ENGINE] OpenClaw model sync failed: %s", e, exc_info=True)
                    await db.rollback()

            # 2. Inbox processing now handled by inbox-processing workflow (every minute)

            # 3. Capability registry sync (hourly)
            if current_time - self._last_capability_sync >= self._capability_sync_interval:
                try:
                    sync_result = await CapabilityRegistrySync(db).sync()
                    self._last_capability_sync = current_time
                    if sync_result.get("added", 0) > 0:
                        activity = True
                    logger.debug(
                        "[ENGINE] Capability registry sync: added=%s updated=%s",
                        sync_result.get("added", 0),
                        sync_result.get("updated", 0),
                    )
                except Exception as e:
                    logger.error("[ENGINE] Capability sync failed: %s", e, exc_info=True)

            # 4. Reflection, compression, diagnostics, sweeps, GitHub sync, memory sync
            #    ALL now handled by workflow scheduler (cron-triggered workflows).
            #    No legacy tick-based paths remain.

            # 5. Check active workers
            initial_active = len(worker_manager.active_workers)
            await worker_manager.check_workers()
            if len(worker_manager.active_workers) != initial_active:
                activity = True

            # 6. Advance active workflow runs
            try:
                workflow_executor = WorkflowExecutor(db, worker_manager=worker_manager)
                active_runs = await workflow_executor.get_active_runs()
                for wf_run in active_runs:
                    advanced = await workflow_executor.advance(wf_run)
                    if advanced:
                        activity = True
                # Process pending workflow events
                events_started = await workflow_executor.process_events(limit=10)
                if events_started > 0:
                    activity = True
                # Process schedule-triggered workflows (cron)
                schedules_started = await workflow_executor.process_schedules()
                if schedules_started > 0:
                    activity = True
            except Exception as e:
                logger.error("[ENGINE] Workflow executor error: %s", e, exc_info=True)

            # 7. Enhanced monitoring (includes auto-unblock, failure detection, etc.)
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

            # 8. Skip work assignment if paused or OpenClaw unavailable
            if self._paused:
                return activity
            
            if not self._openclaw_available:
                return activity

            # 9. Scan for eligible tasks
            eligible_tasks = await scanner.get_eligible_tasks()
            
            if not eligible_tasks:
                return activity

            # Log queue depth if worker is busy (debug to avoid spam)
            worker_status = await worker_manager.get_worker_status()
            if worker_status.get("busy") and len(eligible_tasks) > 0:
                current = (worker_status.get("current_task") or "unknown")[:8]
                logger.debug(
                    f"[ENGINE] Worker busy (current: {current}). "
                    f"{len(eligible_tasks)} task(s) queued."
                )

            # 10. Process eligible tasks — ALL tasks go through workflow engine
            workflow_executor = WorkflowExecutor(db, worker_manager=worker_manager)
            for task_dict in eligible_tasks:
                activity = True
                
                task_id = task_dict.get("id")
                project_id = task_dict.get("project_id")

                if not task_id or not project_id:
                    logger.warning("[ENGINE] Task missing ID or project_id, skipping")
                    continue

                agent_type = task_dict.get("agent")

                # Tasks without an agent: emit assignment event for the
                # workflow-based LLM assigner (replaces old inbox-item approach)
                if not agent_type:
                    try:
                        await workflow_executor.emit_event(
                            "task.needs_assignment",
                            {
                                "task_id": task_id,
                                "title": task_dict.get("title", ""),
                                "notes": (task_dict.get("notes") or "")[:500],
                                "project_id": project_id,
                            },
                            source="engine",
                        )
                        logger.info(
                            "[ENGINE] Task %s has no agent; emitted assignment event",
                            task_id[:8],
                        )
                    except Exception as e:
                        logger.warning("[ENGINE] Failed to emit assignment event for %s: %s", task_id[:8], e)
                    continue

                # GitHub claim handshake before workflow start
                if task_dict.get("external_source") == "github":
                    project = await db.get(ProjectModel, project_id)
                    db_task = await db.get(TaskModel, task_id)
                    if not project or not db_task:
                        logger.warning("[ENGINE] Missing project/task for GitHub claim handshake: %s", task_id[:8])
                        continue
                    try:
                        claimed, claim_reason = await GitHubSyncService(db).claim_issue_for_task(project, db_task)
                        if not claimed:
                            logger.info(
                                "[ENGINE] Skipping GitHub task %s; claim handshake failed (%s)",
                                task_id[:8],
                                claim_reason,
                            )
                            continue
                        db_task.sync_state = "synced"
                        await db.commit()
                    except Exception as claim_err:
                        logger.warning(
                            "[ENGINE] GitHub claim handshake error for %s: %s",
                            task_id[:8],
                            claim_err,
                        )
                        await db.rollback()
                        continue

                # Check circuit breaker before starting workflow
                allowed, reason = await circuit_breaker.should_allow_spawn(
                    project_id=project_id,
                    agent_type=agent_type,
                )
                if not allowed:
                    logger.warning("[ENGINE] Circuit breaker blocked task %s: %s", task_id[:8], reason)
                    continue

                # Route through workflow engine — match task to workflow
                try:
                    matched_workflow = await workflow_executor.match_workflow(task_dict)
                    if matched_workflow:
                        await workflow_executor.start_run(matched_workflow, task=task_dict, trigger_type="task")
                        logger.info(
                            "[ENGINE] Task %s → workflow '%s'",
                            task_id[:8], matched_workflow.name,
                        )
                        continue
                except Exception as e:
                    # Transient error — skip this tick, don't block the task
                    logger.warning("[ENGINE] Workflow match failed for %s (transient, will retry): %s", task_id[:8], e)
                    continue

                # No matching workflow — only block if this is genuinely unroutable
                # (agent type not in any active workflow trigger)
                logger.error(
                    "[ENGINE] Task %s (agent=%s) has no matching workflow; blocking task (legacy spawn disabled)",
                    task_id[:8], agent_type,
                )
                db_task = await db.get(TaskModel, task_id)
                if db_task:
                    db_task.work_state = "blocked"
                    db_task.failure_reason = f"No matching workflow for agent '{agent_type}'"
                    db_task.updated_at = datetime.now(timezone.utc)
                    await db.commit()

        return activity

    # Legacy _request_lobs_assignment removed — agent-assignment workflow handles this

    async def _refresh_runtime_settings(self, db: Any) -> None:
        """Load runtime loop intervals from DB without restart."""
        # Only load the OpenClaw model sync interval — everything else is workflow-managed
        keys = (SETTINGS_KEY_OPENCLAW_MODEL_SYNC_INTERVAL_SECONDS,)
        result = await db.execute(select(OrchestratorSetting).where(OrchestratorSetting.key.in_(keys)))
        rows = {row.key: row.value for row in result.scalars().all()}

        raw = rows.get(SETTINGS_KEY_OPENCLAW_MODEL_SYNC_INTERVAL_SECONDS, DEFAULT_RUNTIME_SETTINGS.get(SETTINGS_KEY_OPENCLAW_MODEL_SYNC_INTERVAL_SECONDS, 900))
        try:
            self._openclaw_model_sync_interval = max(30, int(raw))
        except Exception:
            self._openclaw_model_sync_interval = 900

    async def get_status(self) -> dict[str, Any]:
        """Get current orchestrator status."""
        async with self._session_factory() as db:
            # Initialize provider health if needed
            if self.provider_health is None:
                self.provider_health = ProviderHealthRegistry(db)
                await self.provider_health.initialize()
            else:
                self.provider_health.db = db
            
            if self._worker_manager is None:
                self._worker_manager = WorkerManager(db, provider_health=self.provider_health)
            else:
                self._worker_manager.db = db
                self._worker_manager.provider_health = self.provider_health
            agent_tracker = AgentTracker(db)
            
            worker_status = await self._worker_manager.get_worker_status()
            agent_statuses = await agent_tracker.get_all_statuses()

            heartbeat = await db.get(ControlLoopHeartbeat, "main")

            return {
                "running": self._running,
                "paused": self._paused,
                "worker": worker_status,
                "agents": agent_statuses,
                "poll_interval": POLL_INTERVAL,
                "control_loop": {
                    "mode": "workflow",
                    "note": "All recurring work (reflections, compression, diagnostics, syncs) driven by workflow scheduler",
                    "last_heartbeat": heartbeat.last_heartbeat_at.isoformat() if heartbeat else None,
                    "heartbeat_phase": heartbeat.phase if heartbeat else None,
                },
            }

    async def get_worker_details(self) -> list[dict[str, Any]]:
        """Get details of all active workers."""
        async with self._session_factory() as db:
            # Initialize provider health if needed
            if self.provider_health is None:
                self.provider_health = ProviderHealthRegistry(db)
                await self.provider_health.initialize()
            else:
                self.provider_health.db = db
            
            if self._worker_manager is None:
                self._worker_manager = WorkerManager(db, provider_health=self.provider_health)
            else:
                self._worker_manager.db = db
                self._worker_manager.provider_health = self.provider_health
            
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
