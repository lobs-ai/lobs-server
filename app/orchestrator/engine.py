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
from app.orchestrator.inbox_processor import InboxProcessor
from app.orchestrator.reflection_cycle import ReflectionCycleManager
from app.orchestrator.memory_maintenance import run_memory_maintenance
from app.orchestrator.sweep_arbitrator import SweepArbitrator
from app.orchestrator.auto_assigner import TaskAutoAssigner
from app.orchestrator.diagnostic_triggers import DiagnosticTriggerEngine
from app.orchestrator.control_loop import LobsControlLoopService
from app.orchestrator.provider_health import ProviderHealthRegistry
from app.orchestrator.config import POLL_INTERVAL, GATEWAY_URL, GATEWAY_TOKEN, GATEWAY_SESSION_KEY
from app.models import Project as ProjectModel, Task as TaskModel, OrchestratorSetting, InboxItem, ControlLoopHeartbeat, AgentReflection, AgentInitiative
from app.services.github_sync import GitHubSyncService
from app.services.openclaw_models import fetch_openclaw_model_catalog
from sqlalchemy import select
from app.orchestrator.runtime_settings import (
    DEFAULT_RUNTIME_SETTINGS,
    SETTINGS_KEY_REFLECTION_INTERVAL_SECONDS,
    SETTINGS_KEY_DIAGNOSTIC_INTERVAL_SECONDS,
    SETTINGS_KEY_GITHUB_SYNC_INTERVAL_SECONDS,
    SETTINGS_KEY_OPENCLAW_MODEL_SYNC_INTERVAL_SECONDS,
    SETTINGS_KEY_REFLECTION_LAST_RUN_AT,
    SETTINGS_KEY_DAILY_COMPRESSION_HOUR_UTC,
    SETTINGS_KEY_DAILY_COMPRESSION_HOUR_ET,
    SETTINGS_KEY_DAILY_COMPRESSION_LAST_DATE_ET,
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
        self._last_scheduler_check = 0.0
        self._scheduler_interval = 60  # Check events every 60 seconds
        self._last_routine_check = 0.0
        self._routine_interval = 60  # Check routine registry every 60 seconds
        self._last_inbox_check = 0.0
        self._inbox_interval = 45  # Process inbox every 45 seconds
        self._auto_assign_interval = 60  # Auto-assign unassigned tasks every 60 seconds
        self._last_auto_assign_check = 0.0
        self._last_reflection_check = 0.0
        self._reflection_interval = 10800  # every 3 hours
        self._daily_compression_hour_et = 3
        self._last_daily_compression_date_et: str | None = None
        self._last_memory_maintenance_date_et: str | None = None
        self._last_capability_sync = 0.0
        self._capability_sync_interval = 3600  # every hour
        # Sweep is now triggered by worker_manager.sweep_requested (no fixed timer)
        self._last_diagnostic_check = 0.0
        self._diagnostic_interval = 600  # every 10 minutes
        self._last_github_sync_check = 0.0
        self._github_sync_interval = 120  # every 2 minutes
        self._last_runtime_settings_refresh = 0.0
        self._runtime_settings_refresh_interval = 60  # refresh from DB every minute
        self._last_openclaw_model_sync = 0.0
        self._openclaw_model_sync_interval = 900  # every 15 minutes
        # Persistent worker manager (survives across ticks)
        self._worker_manager: Optional[WorkerManager] = None
        self._reflection_anchor_loaded = False
        # API-triggered reflection flag
        self._force_reflection = False
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

                # 3. Clear stale worker_status
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
            reflection_manager = ReflectionCycleManager(db, worker_manager)
            sweep_arbitrator = SweepArbitrator(db, worker_manager=worker_manager)
            diagnostic_engine = DiagnosticTriggerEngine(db, worker_manager)

            # 1. Check scheduled events (every 60 seconds)
            import time
            current_time = time.time()

            # 0. Refresh runtime-configurable loop intervals
            if current_time - self._last_runtime_settings_refresh >= self._runtime_settings_refresh_interval:
                try:
                    await self._refresh_runtime_settings(db)
                except Exception as e:
                    logger.warning("[ENGINE] Runtime settings refresh failed: %s", e)
                self._last_runtime_settings_refresh = current_time
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

            # 1b. Sync GitHub-backed projects (every 2 minutes)
            if current_time - self._last_github_sync_check >= self._github_sync_interval:
                try:
                    github_projects_q = await db.execute(
                        select(ProjectModel).where(
                            ProjectModel.archived == False,
                            ProjectModel.tracking == "github",
                            ProjectModel.github_repo.is_not(None),
                        )
                    )
                    github_projects = github_projects_q.scalars().all()
                    if github_projects:
                        sync_service = GitHubSyncService(db)
                        for project in github_projects:
                            try:
                                result = await sync_service.sync_project(project, push=True)
                                if result.get("imported", 0) > 0 or result.get("updated", 0) > 0:
                                    activity = True
                            except Exception as project_err:
                                logger.warning(
                                    "[ENGINE] GitHub sync failed for project %s: %s",
                                    project.id,
                                    project_err,
                                )
                        await db.commit()
                    self._last_github_sync_check = current_time
                except Exception as e:
                    logger.error("[ENGINE] GitHub sync check failed: %s", e, exc_info=True)
                    await db.rollback()

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

            # 3. Auto-assign agents for unassigned active tasks (best-effort)
            if not self._paused and current_time - self._last_auto_assign_check >= self._auto_assign_interval:
                try:
                    assigner = TaskAutoAssigner(db)
                    assign_result = await assigner.run_once(limit=20)
                    self._last_auto_assign_check = current_time
                    if assign_result.assigned > 0:
                        activity = True
                        logger.info(
                            "[ENGINE] Auto-assign: scanned=%s assigned=%s skipped=%s failed=%s",
                            assign_result.scanned,
                            assign_result.assigned,
                            assign_result.skipped,
                            assign_result.failed,
                        )
                except Exception as e:
                    logger.error("[ENGINE] Auto-assign failed: %s", e, exc_info=True)
                    await db.rollback()

            # 4. Capability registry sync (hourly)
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

            # 4. Lobs-as-PM control loop phases (event handling + reflection + daily compression)
            if not self._paused:
                # API-triggered reflection: reset timer so the control loop runs it
                if self._force_reflection:
                    self._last_reflection_check = 0.0
                    self._force_reflection = False
                    logger.info("[ENGINE] Force-reflection triggered via API")

                try:
                    async def _run_reflection() -> dict[str, Any]:
                        reflection_result = await reflection_manager.run_strategic_reflection_cycle()
                        self._last_reflection_check = current_time

                        # Persist reflection anchor across restarts.
                        last_run_iso = datetime.now(timezone.utc).isoformat()
                        anchor = await db.get(OrchestratorSetting, SETTINGS_KEY_REFLECTION_LAST_RUN_AT)
                        if anchor is None:
                            anchor = OrchestratorSetting(key=SETTINGS_KEY_REFLECTION_LAST_RUN_AT, value=last_run_iso)
                            db.add(anchor)
                        else:
                            anchor.value = last_run_iso
                        return reflection_result

                    async def _run_compression() -> dict[str, Any]:
                        daily_result = await reflection_manager.run_daily_compression()

                        # Persist "ran today" marker in ET so restarts don't lose the daily state.
                        now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
                        today_key = now_et.date().isoformat()
                        marker = await db.get(OrchestratorSetting, SETTINGS_KEY_DAILY_COMPRESSION_LAST_DATE_ET)
                        if marker is None:
                            marker = OrchestratorSetting(key=SETTINGS_KEY_DAILY_COMPRESSION_LAST_DATE_ET, value=today_key)
                            db.add(marker)
                        else:
                            marker.value = today_key

                        return daily_result

                    async def _route_task_created(payload: dict[str, Any]) -> bool:
                        task_id = payload.get("task_id")
                        if not task_id:
                            return False
                        db_task = await db.get(TaskModel, task_id)
                        if db_task is None or db_task.agent:
                            return False
                        return await self._request_lobs_assignment(db, {
                            "id": db_task.id,
                            "project_id": db_task.project_id,
                            "title": db_task.title,
                            "notes": db_task.notes,
                            "agent": db_task.agent,
                        })

                    control_loop = LobsControlLoopService(
                        db,
                        reflection_interval_seconds=self._reflection_interval,
                        reflection_last_run_at=self._last_reflection_check,
                        compression_hour_et=self._daily_compression_hour_et,
                        last_compression_date_et=self._last_daily_compression_date_et,
                        run_reflection=_run_reflection,
                        run_daily_compression=_run_compression,
                        route_task_created=_route_task_created,
                    )
                    loop_result = await control_loop.run_once()
                    self._last_reflection_check = control_loop.reflection_last_run_at
                    self._last_daily_compression_date_et = control_loop.last_compression_date_et

                    # Daily memory maintenance — runs once per day at same hour as compression.
                    now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
                    today_key_et = now_et.date().isoformat()
                    if (
                        self._last_memory_maintenance_date_et != today_key_et
                        and now_et.hour >= self._daily_compression_hour_et
                    ):
                        try:
                            maint_result = await run_memory_maintenance()
                            self._last_memory_maintenance_date_et = today_key_et
                            summary = maint_result.get("summary", {})
                            logger.info(
                                "[ENGINE] Memory maintenance: consolidated=%s pruned=%s sessions=%s",
                                summary.get("files_consolidated", 0),
                                summary.get("files_pruned", 0),
                                summary.get("sessions_removed", 0),
                            )
                            # Persist marker
                            maint_marker_key = "memory_maintenance_last_date_et"
                            maint_marker = await db.get(OrchestratorSetting, maint_marker_key)
                            if maint_marker is None:
                                maint_marker = OrchestratorSetting(key=maint_marker_key, value=today_key_et)
                                db.add(maint_marker)
                            else:
                                maint_marker.value = today_key_et
                        except Exception as e:
                            logger.error("[ENGINE] Memory maintenance failed: %s", e, exc_info=True)

                    # Important: commit here so heartbeat + any control-plane settings (reflection anchor,
                    # daily compression marker) actually persist.
                    await db.commit()

                    if loop_result.events_processed > 0 or loop_result.reflection_triggered or loop_result.compression_triggered:
                        activity = True
                except Exception as e:
                    logger.error("[ENGINE] Lobs control loop failed: %s", e, exc_info=True)
                    await db.rollback()

            # 5. Lobs sweep/arbitration — triggered after reflection batch completes
            #    (no longer on a fixed timer; the worker manager signals readiness)
            if worker_manager.sweep_requested:
                worker_manager.sweep_requested = False
                try:
                    sweep_result = await sweep_arbitrator.run_once()
                    if sweep_result.get("lobs_review", 0) > 0 or sweep_result.get("approved", 0) > 0:
                        activity = True
                    logger.info("[ENGINE] Post-reflection initiative sweep: %s", sweep_result)
                except Exception as e:
                    logger.error("[ENGINE] Initiative sweep failed: %s", e, exc_info=True)
                    await db.rollback()

            # 6. Reactive diagnostics (every 10 minutes)
            if not self._paused and self._openclaw_available and current_time - self._last_diagnostic_check >= self._diagnostic_interval:
                try:
                    diagnostic_result = await diagnostic_engine.run_once()
                    self._last_diagnostic_check = current_time
                    if diagnostic_result.get("spawned", 0) > 0:
                        activity = True
                    logger.debug("[ENGINE] Diagnostics: %s", diagnostic_result)
                except Exception as e:
                    logger.error("[ENGINE] Diagnostic triggers failed: %s", e, exc_info=True)
                    await db.rollback()

            # 4. Check active workers
            initial_active = len(worker_manager.active_workers)
            await worker_manager.check_workers()
            if len(worker_manager.active_workers) != initial_active:
                activity = True

            # 4a. Advance active workflow runs
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
            except Exception as e:
                logger.error("[ENGINE] Workflow executor error: %s", e, exc_info=True)

            # 5. Enhanced monitoring (includes auto-unblock, failure detection, etc.)
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

            # 6. Skip work assignment if paused or OpenClaw unavailable
            if self._paused:
                return activity
            
            if not self._openclaw_available:
                return activity

            # 7. Scan for eligible tasks
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

            # 8. Process eligible tasks
            workflow_executor = WorkflowExecutor(db, worker_manager=worker_manager)
            for task_dict in eligible_tasks:
                activity = True
                
                task_id = task_dict.get("id")
                project_id = task_dict.get("project_id")
                task_title = task_dict.get("title", task_id[:8] if task_id else "unknown")

                if not task_id or not project_id:
                    logger.warning("[ENGINE] Task missing ID or project_id, skipping")
                    continue

                # Strict assignment policy: never guess agent routing in engine.
                agent_type = task_dict.get("agent")
                if not agent_type:
                    created = await self._request_lobs_assignment(db, task_dict)
                    if created:
                        activity = True
                    logger.info(
                        "[ENGINE] Task %s has no assigned agent; queued Lobs assignment request",
                        task_id[:8],
                    )
                    continue

                # Check if a workflow matches this task
                try:
                    matched_workflow = await workflow_executor.match_workflow(task_dict)
                    if matched_workflow:
                        await workflow_executor.start_run(matched_workflow, task=task_dict, trigger_type="task")
                        logger.info(
                            "[ENGINE] Task %s matched workflow '%s', started run",
                            task_id[:8], matched_workflow.name,
                        )
                        continue
                except Exception as e:
                    logger.warning("[ENGINE] Workflow match failed for %s: %s", task_id[:8], e)

                # GitHub claim handshake before spawning work
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
                    # Continue to try spawning more workers (up to max_workers)
                else:
                    logger.debug(
                        f"[ENGINE] Worker not spawned for task {task_id[:8]} "
                        f"(likely queued due to locks/capacity)"
                    )

        return activity

    async def _request_lobs_assignment(self, db: Any, task: dict[str, Any]) -> bool:
        """Create a deduplicated inbox item requesting Lobs to assign an agent."""
        task_id = task.get("id")
        if not task_id:
            return False

        marker = f"assignment_request:{task_id}"
        existing = await db.execute(
            select(InboxItem).where(InboxItem.summary == marker)
        )
        if existing.scalar_one_or_none():
            return False

        title = task.get("title") or "Untitled task"
        project_id = task.get("project_id") or "unknown"
        notes = (task.get("notes") or "").strip()
        notes_preview = notes[:1200] + ("..." if len(notes) > 1200 else "")

        db.add(
            InboxItem(
                id=str(uuid.uuid4()),
                title=f"[ASSIGNMENT] Agent needed: {title[:80]}",
                content=(
                    "Lobs agent assignment required before execution.\n\n"
                    f"Task ID: {task_id}\n"
                    f"Project ID: {project_id}\n"
                    f"Title: {title}\n\n"
                    "Notes:\n"
                    f"{notes_preview or '(none)'}\n\n"
                    "Action: set the task `agent` field explicitly."
                ),
                is_read=False,
                summary=marker,
                modified_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
        return True

    async def _refresh_runtime_settings(self, db: Any) -> None:
        """Load runtime loop intervals from DB without restart."""
        SETTINGS_KEY_MEMORY_MAINTENANCE_LAST_DATE_ET = "memory_maintenance_last_date_et"
        keys = (
            SETTINGS_KEY_REFLECTION_INTERVAL_SECONDS,
            SETTINGS_KEY_DIAGNOSTIC_INTERVAL_SECONDS,
            SETTINGS_KEY_GITHUB_SYNC_INTERVAL_SECONDS,
            SETTINGS_KEY_OPENCLAW_MODEL_SYNC_INTERVAL_SECONDS,
            SETTINGS_KEY_REFLECTION_LAST_RUN_AT,
            SETTINGS_KEY_DAILY_COMPRESSION_HOUR_UTC,
            SETTINGS_KEY_DAILY_COMPRESSION_HOUR_ET,
            SETTINGS_KEY_DAILY_COMPRESSION_LAST_DATE_ET,
            SETTINGS_KEY_MEMORY_MAINTENANCE_LAST_DATE_ET,
        )
        result = await db.execute(select(OrchestratorSetting).where(OrchestratorSetting.key.in_(keys)))
        rows = {row.key: row.value for row in result.scalars().all()}

        def _as_int(key: str) -> int:
            default = int(DEFAULT_RUNTIME_SETTINGS[key])
            raw = rows.get(key, default)
            try:
                return max(30, int(raw))
            except Exception:
                return default

        self._reflection_interval = _as_int(SETTINGS_KEY_REFLECTION_INTERVAL_SECONDS)
        # Sweep interval no longer used — sweep is triggered after reflections complete
        self._diagnostic_interval = _as_int(SETTINGS_KEY_DIAGNOSTIC_INTERVAL_SECONDS)
        self._github_sync_interval = _as_int(SETTINGS_KEY_GITHUB_SYNC_INTERVAL_SECONDS)
        self._openclaw_model_sync_interval = _as_int(SETTINGS_KEY_OPENCLAW_MODEL_SYNC_INTERVAL_SECONDS)
        # Prefer ET-local setting; fall back to legacy UTC key for compatibility.
        daily_hour_raw = rows.get(
            SETTINGS_KEY_DAILY_COMPRESSION_HOUR_ET,
            rows.get(
                SETTINGS_KEY_DAILY_COMPRESSION_HOUR_UTC,
                DEFAULT_RUNTIME_SETTINGS[SETTINGS_KEY_DAILY_COMPRESSION_HOUR_ET],
            ),
        )
        try:
            self._daily_compression_hour_et = max(0, min(23, int(daily_hour_raw)))
        except Exception:
            self._daily_compression_hour_et = int(DEFAULT_RUNTIME_SETTINGS[SETTINGS_KEY_DAILY_COMPRESSION_HOUR_ET])

        # Load persistent daily compression marker so restarts don't lose "already ran today" state.
        raw_last_compression = rows.get(SETTINGS_KEY_DAILY_COMPRESSION_LAST_DATE_ET)
        if isinstance(raw_last_compression, str) and raw_last_compression.strip():
            self._last_daily_compression_date_et = raw_last_compression.strip()

        # Load persistent memory maintenance marker.
        raw_maint = rows.get(SETTINGS_KEY_MEMORY_MAINTENANCE_LAST_DATE_ET)
        if isinstance(raw_maint, str) and raw_maint.strip():
            self._last_memory_maintenance_date_et = raw_maint.strip()

        # Load persistent reflection anchor so restarts don't reset the 6h cadence.
        if not self._reflection_anchor_loaded:
            raw_last = rows.get(SETTINGS_KEY_REFLECTION_LAST_RUN_AT)
            if isinstance(raw_last, str) and raw_last:
                try:
                    last_dt = datetime.fromisoformat(raw_last.replace("Z", "+00:00"))
                    self._last_reflection_check = max(self._last_reflection_check, last_dt.timestamp())
                except Exception:
                    logger.warning("[ENGINE] Invalid reflection anchor timestamp: %s", raw_last)
            self._reflection_anchor_loaded = True

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
                    "reflection_interval_seconds": self._reflection_interval,
                    "daily_compression_hour_et": self._daily_compression_hour_et,
                    "last_reflection_check": datetime.fromtimestamp(self._last_reflection_check, tz=timezone.utc).isoformat() if self._last_reflection_check else None,
                    "last_daily_compression_date_et": self._last_daily_compression_date_et,
                    "last_memory_maintenance_date_et": self._last_memory_maintenance_date_et,
                    "last_heartbeat": heartbeat.last_heartbeat_at.isoformat() if heartbeat else None,
                    "heartbeat_phase": heartbeat.phase if heartbeat else None,
                    "heartbeat_metadata": heartbeat.heartbeat_metadata if heartbeat else None,
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
