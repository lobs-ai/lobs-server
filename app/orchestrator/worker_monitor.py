"""Worker monitoring and lifecycle management.

Handles checking worker status, completion handling, and cleanup.
"""

import logging
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Task,
    WorkerRun,
    AgentReflection,
    AgentInitiative,
    DiagnosticTriggerEvent,
    OrchestratorSetting,
)
from app.orchestrator.config import (
    BASE_DIR,
    WORKER_WARNING_TIMEOUT,
    WORKER_KILL_TIMEOUT,
)
from app.orchestrator.worker_models import (
    WorkerInfo,
    extract_json,
    json_list,
    safe_log_usage_event,
)
from app.orchestrator.token_extractor import extract_usage_from_transcript
from app.orchestrator.escalation_enhanced import EscalationManagerEnhanced
from app.orchestrator.circuit_breaker import CircuitBreaker
from app.orchestrator.agent_tracker import AgentTracker
from app.orchestrator.runtime_settings import (
    DEFAULT_RUNTIME_SETTINGS,
    SETTINGS_KEY_DIAG_AUTO_REMEDIATION,
    SETTINGS_KEY_DIAG_REMEDIATION_MAX_TASKS,
)
from app.services.usage import resolve_route_type, infer_provider

logger = logging.getLogger(__name__)


class WorkerMonitor:
    """Monitors active workers and handles their lifecycle."""
    
    def __init__(
        self,
        db: AsyncSession,
        active_workers: dict[str, WorkerInfo],
        project_locks: dict[str, str],
        gateway,
        provider_health=None,
        session_factory=None,
    ):
        self.db = db
        self.active_workers = active_workers
        self.project_locks = project_locks
        self.gateway = gateway
        self.provider_health = provider_health
        self._session_factory = session_factory
        
        # Signal to engine: set True when all reflection workers from a batch
        # have completed, indicating the sweep arbitrator should run.
        self.sweep_requested = False
    
    def _get_independent_session(self):
        """Get an independent DB session for operations that must not conflict with the engine's session."""
        if self._session_factory:
            return self._session_factory()
        from app.database import AsyncSessionLocal
        return AsyncSessionLocal()
    
    async def check_workers(self) -> None:
        """
        Check all active workers and handle completed/failed ones.
        
        Called by engine on each tick.
        """
        if not self.active_workers:
            return

        for worker_id in list(self.active_workers.keys()):
            await self._check_worker(worker_id)

    async def _check_worker(self, worker_id: str) -> None:
        """Check a specific worker and handle completion/timeout."""
        worker_info = self.active_workers.get(worker_id)
        if not worker_info:
            return

        # Check timeout first (before API call)
        runtime = time.time() - worker_info.start_time
        
        if runtime > WORKER_KILL_TIMEOUT:
            logger.warning(
                f"[WORKER] Worker {worker_id} exceeded timeout "
                f"({int(runtime/60)}m). Killing."
            )
            await self.kill_worker(worker_id, reason="timeout")
            return

        elif runtime > WORKER_WARNING_TIMEOUT:
            # Log warning periodically
            if int(runtime) % 300 == 0:  # Every 5 minutes
                logger.warning(
                    f"[WORKER] Worker {worker_id} running long "
                    f"({int(runtime/60)}m)"
                )

        # Resolve transcript path hint on first check (one-time lookup)
        if not worker_info.transcript_path:
            worker_info.transcript_path = await self.gateway.resolve_transcript_path(
                worker_info.child_session_key
            )

        # Check session status
        session_status = await self.gateway.check_session_status(
            worker_info.child_session_key,
            spawn_time=worker_info.start_time,
            transcript_hint=worker_info.transcript_path
        )
        
        if not session_status:
            # Unable to get status - skip this check
            return
        
        # Check if session completed
        if session_status.get("completed"):
            success = session_status.get("success", False)
            
            # Try to fetch the session result summary
            result_summary = await self.gateway.fetch_session_summary(
                worker_info.child_session_key,
                transcript_hint=worker_info.transcript_path
            )
            
            await self.handle_worker_completion(
                worker_id=worker_id,
                worker_info=worker_info,
                succeeded=success,
                error_log=session_status.get("error", ""),
                result_summary=result_summary
            )

    async def handle_worker_completion(
        self,
        worker_id: str,
        worker_info: WorkerInfo,
        succeeded: bool,
        error_log: str,
        result_summary: Optional[str] = None
    ) -> None:
        """Handle worker completion (success or failure)."""
        duration = time.time() - worker_info.start_time
        task_id = worker_info.task_id
        project_id = worker_info.project_id
        agent_type = worker_info.agent_type
        task_id_short = task_id[:8]
        
        # Remove from tracking
        self.active_workers.pop(worker_id, None)
        self.project_locks.pop(project_id, None)

        if succeeded:
            # Success
            logger.info(
                f"[WORKER] Worker {worker_id} completed successfully "
                f"(task={task_id_short}, duration={int(duration)}s)"
            )

            # Update task
            db_task = await self.db.get(Task, task_id)
            if db_task:
                db_task.work_state = "completed"
                db_task.status = "completed"
                db_task.finished_at = datetime.now(timezone.utc)
                db_task.updated_at = datetime.now(timezone.utc)
                # Reset escalation on success
                db_task.escalation_tier = 0
                db_task.retry_count = 0
                await self.db.commit()

            # Update agent tracker
            await AgentTracker(self.db).mark_completed(
                agent_type=agent_type,
                task_id=task_id,
                duration_seconds=duration
            )
            
            # Record success in circuit breaker
            circuit_breaker = CircuitBreaker(self.db)
            await circuit_breaker.record_success(project_id, agent_type)
            
            # Record success in provider health
            if self.provider_health:
                provider = infer_provider(worker_info.model)
                self.provider_health.record_outcome(
                    provider=provider,
                    model=worker_info.model,
                    success=True,
                )

            # Auto-push any commits the worker produced.
            await self._push_project_repo_if_needed(
                project_id=project_id,
                task_id=task_id,
                agent_type=agent_type,
            )

        else:
            # Failure
            logger.warning(
                f"[WORKER] Worker {worker_id} failed "
                f"(task={task_id_short})"
            )

            # Update agent tracker
            await AgentTracker(self.db).mark_failed(agent_type, task_id)
            
            # Check if this is infrastructure failure
            circuit_breaker = CircuitBreaker(self.db)
            is_infra_failure = await circuit_breaker.record_failure(
                task_id=task_id,
                project_id=project_id,
                agent_type=agent_type,
                error_log=error_log,
                failure_reason="worker_failed"
            )
            
            # Record failure in provider health
            if self.provider_health:
                from app.orchestrator.worker_models import classify_error_type
                provider = infer_provider(worker_info.model)
                error_type = classify_error_type(error_log)
                # Local model sessions killed by runTimeoutSeconds produce
                # "no assistant response" / "session stale" errors — these
                # look like "unknown" to classify_error_type but are actually
                # timeouts.  Detect this case explicitly so the cooldown fires.
                _local_prefixes = ("lmstudio/", "ollama/")
                _is_local = worker_info.model.startswith(_local_prefixes)
                _timeout_stale_msgs = (
                    "no assistant response",
                    "session stale",
                    "session not found",
                    "deleted transcript",
                )
                _looks_like_timeout = any(m in error_log.lower() for m in _timeout_stale_msgs)
                if _is_local and _looks_like_timeout and error_type == "unknown":
                    error_type = "timeout"
                    logger.info(
                        "[WORKER] Local model %s classified as timeout (stale/deleted session)",
                        worker_info.model,
                    )
                self.provider_health.record_outcome(
                    provider=provider,
                    model=worker_info.model,
                    success=False,
                    error_type=error_type,
                )
            
            # Use enhanced escalation manager
            escalation_enhanced = EscalationManagerEnhanced(self.db)
            
            if is_infra_failure:
                # Infrastructure failure - create alert, don't escalate
                logger.warning(
                    f"[WORKER] Infrastructure failure detected for {task_id_short}, "
                    f"pausing further spawning"
                )
                await escalation_enhanced.create_simple_alert(
                    task_id=task_id,
                    project_id=project_id,
                    error_log=error_log,
                    severity="high"
                )
                
                # Mark task as blocked
                db_task = await self.db.get(Task, task_id)
                if db_task:
                    db_task.work_state = "blocked"
                    db_task.status = "active"
                    db_task.failure_reason = "Infrastructure failure detected"
                    db_task.updated_at = datetime.now(timezone.utc)
                    await self.db.commit()
            else:
                # Task-level failure - use multi-tier escalation
                escalation_result = await escalation_enhanced.handle_failure(
                    task_id=task_id,
                    project_id=project_id,
                    agent_type=agent_type,
                    error_log=error_log,
                    exit_code=-1  # No exit code from Gateway sessions
                )
                
                logger.info(
                    f"[WORKER] Escalation result for {task_id_short}: "
                    f"{escalation_result}"
                )

        # Get work summary: prefer session result, fall back to .work-summary file
        summary = result_summary
        if not summary and succeeded:
            summary = await self._read_work_summary(project_id)

        if worker_info.label.startswith("reflection-") or worker_info.label.startswith("diagnostic-"):
            logger.info(
                "[WORKER] Reflection output for %s: summary_len=%s, succeeded=%s",
                worker_info.label,
                len(summary) if summary else 0,
                succeeded,
            )

        # Reflection/diagnostic runs return structured JSON; persist outputs
        if (worker_info.label.startswith("reflection-") or worker_info.label.startswith("diagnostic-")) and summary:
            reflection_type = "diagnostic" if worker_info.label.startswith("diagnostic-") else "strategic"
            await self._persist_reflection_output(
                agent_type=agent_type,
                reflection_label=worker_info.label,
                reflection_type=reflection_type,
                summary=summary,
                succeeded=succeeded,
            )

            # If this was a strategic reflection, check whether all reflection
            # workers from this batch have finished.  If so, signal the engine
            # to run the initiative sweep immediately.
            if reflection_type == "strategic":
                remaining = any(
                    w.label.startswith("reflection-")
                    for w in self.active_workers.values()
                )
                if not remaining:
                    self.sweep_requested = True
                    logger.info("[WORKER] All reflection workers done — requesting initiative sweep")

        # Lobs-PM sweep review results: create tasks from approved initiatives
        if worker_info.label.startswith("sweep-review-") and summary and succeeded:
            await self._process_sweep_review_results(summary)
        
        # Record worker run
        await self._record_worker_run(
            worker_id=worker_id,
            task_id=task_id,
            start_time=worker_info.start_time,
            duration=duration,
            succeeded=succeeded,
            exit_code=0 if succeeded else -1,
            summary=summary,
            model=worker_info.model,
            model_audit=worker_info.model_audit,
            commit_sha=None,  # Sub-agents handle their own commits
            files_modified=None,
            session_key=worker_info.child_session_key,
        )

        # Clean up the completed session to prevent session leak
        if worker_info.child_session_key:
            try:
                await self.gateway.delete_session(worker_info.child_session_key)
            except Exception as e:
                logger.warning(
                    "[WORKER] Failed to clean up session %s: %s",
                    worker_info.child_session_key, e,
                )

        # Mark agent idle
        await AgentTracker(self.db).mark_idle(agent_type)

    async def kill_worker(self, worker_id: str, reason: str) -> None:
        """Kill a worker session via Gateway API."""
        worker_info = self.active_workers.get(worker_id)
        if not worker_info:
            return

        logger.warning(f"[WORKER] Killing worker {worker_id} (reason={reason})")

        # TODO: Implement session termination via Gateway API if available
        # For now, just handle as failed completion
        await self.handle_worker_completion(
            worker_id=worker_id,
            worker_info=worker_info,
            succeeded=False,
            error_log=f"Worker killed: {reason}"
        )

    async def _record_worker_run(
        self,
        worker_id: str,
        task_id: str,
        start_time: float,
        duration: float,
        succeeded: bool,
        exit_code: int,
        summary: str | None = None,
        model: str | None = None,
        model_audit: dict[str, Any] | None = None,
        commit_sha: str | None = None,
        files_modified: list[str] | None = None,
        session_key: str | None = None,
    ) -> None:
        """Record worker run to history table with actual token usage."""
        try:
            # Extract token usage from session transcript
            token_usage = None
            if session_key:
                try:
                    token_usage = extract_usage_from_transcript(session_key)
                    if token_usage and token_usage.has_data:
                        logger.info(
                            "[WORKER] Token usage for %s: %d in, %d out, $%.4f",
                            worker_id, token_usage.input_tokens,
                            token_usage.output_tokens, token_usage.estimated_cost_usd,
                        )
                except Exception as e:
                    logger.warning("[WORKER] Failed to extract token usage: %s", e)

            task_log = {"model_router": model_audit} if model_audit else {}
            if token_usage and token_usage.has_data:
                task_log["token_usage"] = {
                    "input_tokens": token_usage.input_tokens,
                    "output_tokens": token_usage.output_tokens,
                    "cache_read_tokens": token_usage.cache_read_tokens,
                    "cache_write_tokens": token_usage.cache_write_tokens,
                    "total_tokens": token_usage.total_tokens,
                    "estimated_cost_usd": round(token_usage.estimated_cost_usd, 6),
                    "message_count": token_usage.message_count,
                    "actual_model": token_usage.model,
                    "actual_provider": token_usage.provider,
                }

            run = WorkerRun(
                worker_id=worker_id,
                task_id=task_id,
                started_at=datetime.fromtimestamp(start_time, tz=timezone.utc),
                ended_at=datetime.now(timezone.utc),
                tasks_completed=1 if succeeded else 0,
                succeeded=succeeded,
                timeout_reason="exit_code_" + str(exit_code) if not succeeded else None,
                source="orchestrator-gateway",
                model=model,
                task_log=task_log or None,
                summary=summary,
                commit_shas=[commit_sha] if commit_sha else None,
                files_modified=files_modified,
            )

            async with self._get_independent_session() as db:
                db.add(run)

                if model:
                    route_type = resolve_route_type(
                        model,
                        subscription_models=(model_audit or {}).get("subscription_models", []),
                        subscription_providers=(model_audit or {}).get("subscription_providers", []),
                    )
                    input_tokens = token_usage.input_tokens if token_usage else 0
                    output_tokens = token_usage.output_tokens if token_usage else 0
                    cached_tokens = token_usage.cache_read_tokens if token_usage else 0
                    cost = token_usage.estimated_cost_usd if token_usage else 0.0

                    await safe_log_usage_event(
                        db,
                        source="orchestrator-worker",
                        model=token_usage.model or model if token_usage else model,
                        provider=token_usage.provider if token_usage and token_usage.provider else None,
                        route_type=route_type,
                        task_type="task_execution",
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cached_tokens=cached_tokens,
                        requests=token_usage.message_count if token_usage and token_usage.message_count else 1,
                        status="success" if succeeded else "error",
                        estimated_cost_usd=cost,
                        error_code=None if succeeded else f"exit_code_{exit_code}",
                        metadata={
                            "task_id": task_id,
                            "worker_id": worker_id,
                            "model_router": model_audit,
                            "duration_seconds": duration,
                            "session_key": session_key,
                        },
                    )

                await db.commit()

        except Exception as e:
            logger.error(f"Failed to record worker run: {e}", exc_info=True)
            await self.db.rollback()
    
    async def _persist_reflection_output(
        self,
        *,
        agent_type: str,
        reflection_label: str,
        reflection_type: str,
        summary: str,
        succeeded: bool,
    ) -> None:
        """Persist strategic/diagnostic outputs and derive initiatives for strategic runs.
        
        Uses an independent DB session to avoid conflicts with the engine's session
        (which may be in a 'prepared' state during concurrent worker completions).
        """
        try:
            async with self._get_independent_session() as db:
                await self._persist_reflection_output_impl(
                    db=db,
                    agent_type=agent_type,
                    reflection_label=reflection_label,
                    reflection_type=reflection_type,
                    summary=summary,
                    succeeded=succeeded,
                )
                await db.commit()
        except Exception as e:
            logger.warning("[WORKER] Failed to persist reflection output: %s", e, exc_info=True)

    async def _persist_reflection_output_impl(
        self,
        *,
        db: AsyncSession,
        agent_type: str,
        reflection_label: str,
        reflection_type: str,
        summary: str,
        succeeded: bool,
    ) -> None:
        """Inner implementation using the provided DB session."""
        from app.orchestrator.policy_engine import PolicyEngine
        
        reflection_result = await db.execute(
            select(AgentReflection)
            .where(
                AgentReflection.agent_type == agent_type,
                AgentReflection.status == "pending",
                AgentReflection.reflection_type == reflection_type,
            )
            .order_by(AgentReflection.created_at.desc())
            .limit(1)
        )
        reflection = reflection_result.scalar_one_or_none()
        if reflection is None:
            return

        payload = extract_json(summary)

        # Map alternative field names from different worker response formats
        if isinstance(payload, dict) and "raw" not in payload:
            if "inefficiencies_detected" not in payload:
                issue_summary = payload.get("issue_summary") or payload.get("summary")
                if issue_summary:
                    payload["inefficiencies_detected"] = [issue_summary] if isinstance(issue_summary, str) else issue_summary

            root_causes = payload.get("root_causes")
            if root_causes:
                existing = payload.get("inefficiencies_detected", [])
                if not isinstance(existing, list):
                    existing = [existing] if existing else []
                if isinstance(root_causes, list):
                    payload["inefficiencies_detected"] = existing + root_causes
                elif isinstance(root_causes, str):
                    payload["inefficiencies_detected"] = existing + [root_causes]

            if "missed_opportunities" not in payload:
                recommended = payload.get("recommended_actions")
                if recommended:
                    payload["missed_opportunities"] = recommended if isinstance(recommended, list) else [recommended]

        reflection.status = "completed" if succeeded else "failed"
        reflection.result = payload
        reflection.inefficiencies = json_list(payload.get("inefficiencies_detected"))
        reflection.system_risks = json_list(payload.get("system_risks"))
        reflection.missed_opportunities = json_list(payload.get("missed_opportunities"))
        reflection.identity_adjustments = json_list(payload.get("identity_adjustments"))
        reflection.completed_at = datetime.now(timezone.utc)

        if reflection_type == "diagnostic":
            await self._persist_diagnostic_event_outcome(
                reflection=reflection,
                payload=payload,
                succeeded=succeeded,
            )

        if reflection_type == "strategic" and succeeded and isinstance(payload, dict):
            initiatives = payload.get("proposed_initiatives") or []
            policy = PolicyEngine()
            for raw in initiatives:
                if not isinstance(raw, dict):
                    continue
                category = str(raw.get("category", "")).strip().lower()
                effort = raw.get("estimated_effort")
                effort_int = int(effort) if isinstance(effort, (int, float)) else None
                decision = policy.decide(category, estimated_effort=effort_int)

                proposed_owner = raw.get("owner_agent") or raw.get("suggested_owner_agent")
                db.add(
                    AgentInitiative(
                        id=str(uuid.uuid4()),
                        proposed_by_agent=agent_type,
                        source_reflection_id=reflection.id,
                        owner_agent=(str(proposed_owner).strip().lower() if proposed_owner else None),
                        title=str(raw.get("title") or "Untitled initiative"),
                        description=str(raw.get("description") or ""),
                        category=category or "unknown",
                        risk_tier=decision.risk_tier,
                        policy_lane=decision.lane,
                        policy_reason=decision.reason,
                        status="proposed",
                        score=float(effort_int) if effort_int is not None else None,
                        rationale=(
                            f"Proposed by {agent_type}. Initial policy lane={decision.lane} "
                            f"(mode={decision.approval_mode}). Reason={decision.reason}"
                        ),
                    )
                )

    async def _persist_diagnostic_event_outcome(
        self,
        *,
        reflection: AgentReflection,
        payload: dict[str, Any],
        succeeded: bool,
    ) -> None:
        trigger = ((reflection.context_packet or {}).get("trigger") or {}) if isinstance(reflection.context_packet, dict) else {}
        trigger_event_id = trigger.get("trigger_event_id")
        if not trigger_event_id:
            return

        event = await self.db.get(DiagnosticTriggerEvent, str(trigger_event_id))
        if event is None:
            return

        event.status = "completed" if succeeded else "failed"
        event.diagnostic_result = payload

        remediation_ids: list[str] = []
        if succeeded and await self._auto_remediation_enabled() and isinstance(payload, dict):
            remediation_ids = await self._create_remediation_tasks(event=event, payload=payload)

        event.remediation_task_ids = remediation_ids
        event.outcome = {
            "succeeded": succeeded,
            "recommended_actions": payload.get("recommended_actions") if isinstance(payload, dict) else None,
            "remediation_tasks_created": len(remediation_ids),
        }

    async def _auto_remediation_enabled(self) -> bool:
        result = await self.db.execute(
            select(OrchestratorSetting).where(
                OrchestratorSetting.key.in_([
                    SETTINGS_KEY_DIAG_AUTO_REMEDIATION,
                    SETTINGS_KEY_DIAG_REMEDIATION_MAX_TASKS,
                ])
            )
        )
        settings = {row.key: row.value for row in result.scalars().all()}
        merged = {**DEFAULT_RUNTIME_SETTINGS, **settings}
        return bool(merged.get(SETTINGS_KEY_DIAG_AUTO_REMEDIATION, False))

    async def _create_remediation_tasks(
        self,
        *,
        event: DiagnosticTriggerEvent,
        payload: dict[str, Any],
    ) -> list[str]:
        actions = payload.get("recommended_actions")
        if not isinstance(actions, list):
            return []

        result = await self.db.execute(
            select(OrchestratorSetting).where(OrchestratorSetting.key == SETTINGS_KEY_DIAG_REMEDIATION_MAX_TASKS)
        )
        setting = result.scalar_one_or_none()
        max_tasks = int((setting.value if setting else DEFAULT_RUNTIME_SETTINGS[SETTINGS_KEY_DIAG_REMEDIATION_MAX_TASKS]) or 0)
        max_tasks = max(0, max_tasks)
        created: list[str] = []

        for raw in actions[:max_tasks]:
            action = str(raw).strip()
            if not action:
                continue
            task_id = str(uuid.uuid4())
            self.db.add(
                Task(
                    id=task_id,
                    title=f"Remediation: {action[:80]}",
                    status="inbox",
                    work_state="not_started",
                    project_id=event.project_id,
                    agent=event.agent_type,
                    notes=(
                        "Auto-created from diagnostic trigger event.\n"
                        f"Trigger: {event.trigger_type}\n"
                        f"Action: {action}\n"
                    ),
                )
            )
            created.append(task_id)

        return created

    async def _process_sweep_review_results(self, summary: str) -> None:
        """Process Lobs sweep review output: create tasks from approved initiatives.
        
        Uses InitiativeDecisionEngine to ensure proper audit trail, feedback reflections,
        and task creation with full governance.
        """
        try:
            from app.orchestrator.initiative_decisions import InitiativeDecisionEngine
            
            payload = extract_json(summary)
            decisions = payload.get("decisions", [])
            if not isinstance(decisions, list):
                logger.warning("[SWEEP_REVIEW] No decisions array found in LLM output")
                return

            engine = InitiativeDecisionEngine(self.db)
            processed = 0
            approved = 0
            deferred = 0
            rejected = 0

            for d in decisions:
                if not isinstance(d, dict):
                    continue
                    
                initiative_id = d.get("initiative_id")
                decision = d.get("decision", "").lower()
                
                if not initiative_id or decision not in {"approve", "defer", "reject", "escalate"}:
                    logger.warning(
                        "[SWEEP_REVIEW] Skipping invalid decision: id=%s decision=%s",
                        initiative_id, decision
                    )
                    continue

                initiative = await self.db.get(AgentInitiative, initiative_id)
                if not initiative:
                    logger.warning("[SWEEP_REVIEW] Initiative not found: %s", initiative_id)
                    continue

                # Use the decision engine to ensure full governance
                try:
                    result = await engine.decide(
                        initiative,
                        decision=decision,
                        revised_title=d.get("task_title"),
                        revised_description=d.get("task_notes"),
                        selected_agent=d.get("owner_agent"),
                        selected_project_id=d.get("project_id"),
                        decision_summary=d.get("reason"),
                        learning_feedback=None,  # LLM didn't provide learning feedback
                        decided_by="lobs",
                    )
                    
                    processed += 1
                    if decision == "approve":
                        approved += 1
                    elif decision == "defer":
                        deferred += 1
                    elif decision == "reject":
                        rejected += 1
                    
                    logger.debug(
                        "[SWEEP_REVIEW] Processed initiative %s: decision=%s task_id=%s",
                        initiative_id[:8], decision, result.get("task_id")
                    )
                    
                except Exception as e:
                    logger.error(
                        "[SWEEP_REVIEW] Failed to process initiative %s: %s",
                        initiative_id[:8], e, exc_info=True
                    )

            # Commit happens inside engine.decide(), but we should commit the batch
            await self.db.commit()
            
            logger.info(
                "[SWEEP_REVIEW] Processed %d decisions: approved=%d deferred=%d rejected=%d",
                processed, approved, deferred, rejected
            )
            
        except Exception as e:
            logger.error("[SWEEP_REVIEW] Failed to process sweep review results: %s", e, exc_info=True)
            await self.db.rollback()

    @staticmethod
    def _git_push_sync(*, repo_path: Path, project_id: str, task_id: str) -> None:
        """Synchronous git push. Designed to run in asyncio.to_thread."""
        def run_git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git", "-C", str(repo_path), *args],
                capture_output=True, text=True, check=check,
            )

        inside = run_git("rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return

        dirty = run_git("status", "--porcelain")
        if dirty.returncode == 0 and dirty.stdout.strip():
            logger.info("[WORKER] Repo has uncommitted changes; skipping auto-push (%s)", repo_path)
            return

        upstream = run_git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if upstream.returncode != 0:
            return

        run_git("fetch", "--prune", "origin")
        counts = run_git("rev-list", "--left-right", "--count", "@{u}...HEAD")
        if counts.returncode != 0:
            return

        behind_str, ahead_str = (counts.stdout.strip().split() + ["0", "0"])[:2]
        behind, ahead = int(behind_str), int(ahead_str)

        if ahead <= 0 and behind <= 0:
            return

        if behind > 0:
            pull = run_git("pull", "--rebase")
            if pull.returncode != 0:
                raise RuntimeError(pull.stderr.strip() or pull.stdout.strip() or "git pull --rebase failed")

        push = run_git("push")
        if push.returncode != 0:
            raise RuntimeError(push.stderr.strip() or push.stdout.strip() or "git push failed")

        logger.info("[WORKER] Auto-pushed repo (project=%s task=%s ahead=%s)", project_id, task_id[:8], ahead)

    async def _push_project_repo_if_needed(
        self,
        *,
        project_id: str,
        task_id: str,
        agent_type: str,
    ) -> None:
        """Push worker-produced commits to origin.

        Workers operate in the project repo; they may commit changes, but without
        an explicit push step the remote never updates.

        Behavior:
        - If there are uncommitted changes, we do nothing (don't guess).
        - If HEAD is ahead of upstream, attempt `git pull --rebase` then push.
        - On failure, create a simple alert so it shows up for the human.
        """
        from app.models import Project
        
        try:
            project = await self.db.get(Project, project_id)
            if not project:
                return

            repo_path = Path(project.repo_path) if project.repo_path else (BASE_DIR / project_id)
            if not repo_path.exists():
                return

            import asyncio
            await asyncio.to_thread(
                self._git_push_sync,
                repo_path=repo_path,
                project_id=project_id,
                task_id=task_id,
            )

        except Exception as e:
            logger.warning(
                "[WORKER] Auto-push failed for project %s after task %s: %s",
                project_id,
                task_id[:8],
                e,
            )
            try:
                await EscalationManagerEnhanced(self.db).create_simple_alert(
                    task_id=task_id,
                    project_id=project_id,
                    error_log=f"Auto-push failed: {e}",
                    severity="medium",
                )
            except Exception:
                # Never let alerting failures break completion handling.
                logger.debug("[WORKER] Failed to create auto-push alert", exc_info=True)

    async def _read_work_summary(self, project_id: str) -> str | None:
        """Read .work-summary file from project directory."""
        from app.models import Project
        
        try:
            # Get project from database to find its path
            project = await self.db.get(Project, project_id)
            if not project:
                return None
            
            # Construct path to .work-summary
            project_dir = (
                Path(project.repo_path) if project.repo_path
                else BASE_DIR / project_id
            )
            summary_file = project_dir / ".work-summary"
            
            if not summary_file.exists():
                return None
            
            # Read summary file
            with open(summary_file, "r", encoding="utf-8", errors="ignore") as f:
                summary = f.read().strip()
                return summary if summary else None
        
        except Exception as e:
            logger.warning(f"Failed to read work summary for {project_id}: {e}")
            return None
