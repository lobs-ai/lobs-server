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

import asyncio
import json
import logging
import os
import random
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Task,
    WorkerStatus,
    WorkerRun,
    Project,
    AgentReflection,
    AgentInitiative,
    DiagnosticTriggerEvent,
    OrchestratorSetting,
)
from app.orchestrator.config import (
    BASE_DIR,
    WORKER_RESULTS_DIR,
    MAX_WORKERS,
    WORKER_WARNING_TIMEOUT,
    WORKER_KILL_TIMEOUT,
    GATEWAY_URL,
    GATEWAY_TOKEN,
    GATEWAY_SESSION_KEY,
)
from app.orchestrator.model_chooser import ModelChooser
from app.orchestrator.token_extractor import extract_usage_from_transcript
from app.orchestrator.escalation_enhanced import EscalationManagerEnhanced
from app.orchestrator.circuit_breaker import CircuitBreaker
from app.orchestrator.agent_tracker import AgentTracker
from app.orchestrator.prompter import Prompter
from app.orchestrator.policy_engine import PolicyEngine
from app.orchestrator.run_validity import (
    RunValidityChecker,
    FIRST_RESPONSE_SLA_SECONDS,
    RVC_MISSING_LIFECYCLE,
    RVC_NO_FIRST_RESPONSE,
    RVC_SLA_BREACH,
    RVC_NO_TRANSCRIPT,
    RVC_NO_EVIDENCE,
)
from app.orchestrator.runtime_settings import (
    DEFAULT_RUNTIME_SETTINGS,
    SETTINGS_KEY_DIAG_AUTO_REMEDIATION,
    SETTINGS_KEY_DIAG_REMEDIATION_MAX_TASKS,
)
from app.services.usage import log_usage_event, resolve_route_type, infer_provider

logger = logging.getLogger(__name__)


def classify_error_type(error_message: str, response_data: dict | None = None) -> str:
    """
    Classify error type for provider health tracking.
    
    Returns one of: rate_limit, auth_error, quota_exceeded, timeout, 
                    server_error, unknown
    """
    error_lower = error_message.lower()
    
    # Check response data for specific error codes
    if response_data:
        error_code = str(response_data.get("error", "")).lower()
        status = response_data.get("status")
        
        if status == 429 or "429" in error_code:
            return "rate_limit"
        if status in (401, 403) or any(k in error_code for k in ("unauthorized", "forbidden", "auth")):
            return "auth_error"
        if (status is not None and status >= 500) or any(k in error_code for k in ("server_error", "internal_error", "service_unavailable")):
            return "server_error"
    
    # Pattern matching on error message
    if any(k in error_lower for k in ("rate limit", "429", "too many requests", "rate_limit")):
        return "rate_limit"
    if any(k in error_lower for k in ("auth", "unauthorized", "forbidden", "401", "403", "api key")):
        return "auth_error"
    if any(k in error_lower for k in ("quota", "billing", "insufficient_quota", "limit exceeded")):
        return "quota_exceeded"
    if any(k in error_lower for k in ("timeout", "timed out", "etimedout", "deadline")):
        return "timeout"
    if any(k in error_lower for k in ("500", "502", "503", "server error", "internal error", "service unavailable")):
        return "server_error"
    
    return "unknown"


async def _safe_log_usage_event(db: AsyncSession, **kwargs: Any) -> None:
    """Best-effort usage logging that never poisons the caller DB session."""
    try:
        await log_usage_event(db, **kwargs)
    except Exception as e:
        logger.warning("[USAGE] Skipping usage event due to DB/logging error: %s", e)
        try:
            await db.rollback()
        except Exception:
            pass


@dataclass
class WorkerInfo:
    """Information about an active worker spawned via Gateway API."""
    run_id: str
    child_session_key: str
    task_id: str
    project_id: str
    agent_type: str
    model: str
    start_time: float
    label: str
    model_audit: dict[str, Any] | None = None
    transcript_path: str | None = None


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

        # Signal to engine: set True when all reflection workers from a batch
        # have completed, indicating the sweep arbitrator should run.
        self.sweep_requested = False

    def _get_independent_session(self):
        """Get an independent DB session for operations that must not conflict with the engine's session."""
        if self._session_factory:
            return self._session_factory()
        from app.database import AsyncSessionLocal
        return AsyncSessionLocal()

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
            logger.info(
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
                logger.warning(f"Project {project_id} not found")
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
            
            # Build task prompt using Prompter (with learning enhancement)
            task_title = task.get("title", task_id_short)
            prompt_file = WORKER_RESULTS_DIR / f"{task_id}.prompt.txt"
            
            # Determine control group for A/B testing
            control_group_pct = float(os.getenv("LEARNING_CONTROL_GROUP_PCT", "0.2"))
            learning_disabled = random.random() < control_group_pct
            
            applied_learning_ids = []
            
            try:
                # Build structured prompt with agent context and learning enhancement
                global_rules = ""  # TODO: Load from config/DB if needed
                prompt_content, applied_learning_ids = await Prompter.build_task_prompt_enhanced(
                    db=self.db,
                    item=task,
                    project_path=repo_path,
                    agent_type=agent_type,
                    rules=global_rules,
                    learning_disabled=learning_disabled,
                )
                prompt_file.write_text(prompt_content, encoding="utf-8")
                logger.info(
                    f"[WORKER] Built {'enhanced' if applied_learning_ids else 'standard'} prompt for {task_id_short} "
                    f"(agent={agent_type}, learnings={len(applied_learning_ids)}, control_group={learning_disabled})"
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
                spawn_result, err, err_type = await self._spawn_session(
                    task_prompt=prompt_content,
                    agent_id=agent_type,
                    model=candidate,
                    label=label,
                    routing_policy=choice.routing_policy or {},
                    budget_lane=choice.budget_lane,
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

            # Best-effort: persist applied learnings to TaskOutcome
            if applied_learning_ids or learning_disabled:
                try:
                    from app.models import TaskOutcome
                    
                    # Check if outcome already exists for this task
                    stmt = select(TaskOutcome).where(TaskOutcome.task_id == task_id)
                    result = await self.db.execute(stmt)
                    outcome = result.scalar_one_or_none()
                    
                    if outcome:
                        # Update existing outcome
                        outcome.applied_learnings = applied_learning_ids
                        outcome.learning_disabled = learning_disabled
                        outcome.updated_at = datetime.now(timezone.utc)
                        await self.db.commit()
                        logger.debug(
                            f"[LEARNING] Updated TaskOutcome for task {task_id_short} "
                            f"(learnings={len(applied_learning_ids)}, control_group={learning_disabled})"
                        )
                    else:
                        # No outcome exists yet - this is fine, it may be created later
                        logger.debug(
                            f"[LEARNING] No TaskOutcome found for task {task_id_short} yet, "
                            f"will persist learnings when outcome is created"
                        )
                except Exception as e:
                    # Never fail task execution due to learning metadata persistence
                    logger.warning(
                        f"[LEARNING] Failed to persist applied learnings for task {task_id_short}: {e}"
                    )

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

    async def _spawn_session(
        self,
        task_prompt: str,
        agent_id: str,
        model: str,
        label: str,
        routing_policy: dict[str, Any] | None = None,
        budget_lane: str | None = None,
    ) -> tuple[Optional[dict[str, str]], Optional[str], str]:
        """
        Call Gateway API to spawn a new session.
        
        Uses cleanup=keep so session history remains available for result
        extraction. Sessions are spawned from the internal sink session key;
        sink is a control-plane routing identity, not an execution agent.
        
        Returns:
            (Dict with runId and childSessionKey, error_string, error_type)
        """
        try:
            async with aiohttp.ClientSession() as session:
                parent_session_key = f"{GATEWAY_SESSION_KEY}-spawn-{uuid.uuid4().hex[:8]}"
                resp = await session.post(
                    f"{GATEWAY_URL}/tools/invoke",
                    headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                    json={
                        "tool": "sessions_spawn",
                        "sessionKey": parent_session_key,
                        "args": {
                            "task": task_prompt,
                            "agentId": agent_id,
                            "model": model,
                            "runTimeoutSeconds": 1800,
                            "cleanup": "keep",
                            "label": label
                        }
                    },
                    timeout=aiohttp.ClientTimeout(total=30)
                )
                
                data = await resp.json()
                
                if not data.get("ok"):
                    error_msg = f"sessions_spawn_failed: {data}"
                    error_type = classify_error_type(error_msg, data)
                    
                    await _safe_log_usage_event(
                        self.db,
                        source="orchestrator-spawn",
                        model=model,
                        route_type=resolve_route_type(model, subscription_models=(routing_policy or {}).get("subscription_models", []), subscription_providers=(routing_policy or {}).get("subscription_providers", [])),
                        task_type="inbox" if "inbox" in label else "task_execution",
                        budget_lane=budget_lane,
                        status="error",
                        error_code="sessions_spawn_failed",
                        metadata={"label": label, "agent_id": agent_id, "error_type": error_type},
                    )
                    logger.error(
                        "[GATEWAY] sessions_spawn failed",
                        extra={"gateway": {"model": model, "response": data, "error_type": error_type}},
                    )
                    return None, error_msg, error_type
                
                result = data.get("result", {})
                # Gateway wraps tool results in {content, details}
                details = result.get("details", result)
                if details.get("status") != "accepted":
                    error_msg = f"sessions_spawn_not_accepted: {result}"
                    error_type = classify_error_type(error_msg, result)
                    
                    await _safe_log_usage_event(
                        self.db,
                        source="orchestrator-spawn",
                        model=model,
                        route_type=resolve_route_type(model, subscription_models=(routing_policy or {}).get("subscription_models", []), subscription_providers=(routing_policy or {}).get("subscription_providers", [])),
                        task_type="inbox" if "inbox" in label else "task_execution",
                        budget_lane=budget_lane,
                        status="error",
                        error_code="sessions_spawn_not_accepted",
                        metadata={"label": label, "agent_id": agent_id, "details": details, "error_type": error_type},
                    )
                    logger.error(
                        "[GATEWAY] sessions_spawn not accepted",
                        extra={"gateway": {"model": model, "result": result, "error_type": error_type}},
                    )
                    return None, error_msg, error_type

                await _safe_log_usage_event(
                    self.db,
                    source="orchestrator-spawn",
                    model=model,
                    route_type=resolve_route_type(model, subscription_models=(routing_policy or {}).get("subscription_models", []), subscription_providers=(routing_policy or {}).get("subscription_providers", [])),
                    task_type="inbox" if "inbox" in label else "task_execution",
                    budget_lane=budget_lane,
                    status="success",
                    metadata={"label": label, "agent_id": agent_id, "run_id": details.get("runId")},
                )

                return (
                    {
                        "runId": details["runId"],
                        "childSessionKey": details["childSessionKey"],
                    },
                    None,
                    "none",  # No error
                )

        except Exception as e:
            error_msg = str(e)
            error_type = classify_error_type(error_msg)
            
            logger.error(
                "[GATEWAY] Error calling sessions_spawn",
                extra={"gateway": {"model": model, "error": error_msg, "error_type": error_type}},
                exc_info=True,
            )
            return None, error_msg, error_type

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
            await self._kill_worker(worker_id, reason="timeout")
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
            worker_info.transcript_path = await self._resolve_transcript_path(worker_info.child_session_key)

        # Check session status
        session_status = await self._check_session_status(
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
            result_summary = await self._fetch_session_summary(
                worker_info.child_session_key,
                transcript_hint=worker_info.transcript_path
            )
            
            # Check if the model requested escalation (local model punt)
            escalate_reason = self._detect_escalation(
                result_summary, worker_info, worker_id
            )
            if escalate_reason:
                await self._handle_escalation(
                    worker_id=worker_id,
                    worker_info=worker_info,
                    reason=escalate_reason,
                )
                return
            
            await self._handle_worker_completion(
                worker_id=worker_id,
                worker_info=worker_info,
                succeeded=success,
                error_log=session_status.get("error", ""),
                result_summary=result_summary
            )

    def _find_transcript_file(self, session_key: str, transcript_hint: Optional[str] = None) -> Optional["pathlib.Path"]:
        """Find a transcript file on disk for the given session key.
        
        Searches agent session directories for JSONL files matching the session UUID,
        including files that have been marked as deleted (.deleted.*).
        
        The session key UUID (subagent UUID) often differs from the transcript filename
        (sessionId), so we try both the subagent UUID and any hint from sessions_list.
        """
        import pathlib

        parts = session_key.split(":")
        if len(parts) < 2:
            return None
        agent_id = parts[1]
        subagent_uuid = parts[3] if len(parts) >= 4 else None

        # Collect UUIDs to search for
        search_uuids = []
        if subagent_uuid:
            search_uuids.append(subagent_uuid)

        # If we have a transcript path hint, extract its sessionId
        if transcript_hint:
            hint_path = pathlib.Path(transcript_hint)
            # Extract UUID from filename like "6c51b07a-2a06-4e3f-b1d9-f05ecf156ad2.jsonl"
            stem = hint_path.name.split(".")[0]
            if stem and stem not in search_uuids:
                search_uuids.append(stem)
            # Also check if the hint path itself exists (or its .deleted version)
            if hint_path.exists():
                return hint_path
            deleted = list(hint_path.parent.glob(f"{hint_path.name}.deleted.*")) if hint_path.parent.exists() else []
            if deleted:
                return sorted(deleted, key=lambda p: p.stat().st_mtime, reverse=True)[0]

        # Search known locations
        search_dirs = [
            pathlib.Path.home() / ".openclaw" / "agents" / agent_id / "sessions",
            pathlib.Path.home() / ".openclaw" / "workspace",
        ]

        for base in search_dirs:
            if not base.exists():
                continue
            for uuid in search_uuids:
                exact = base / f"{uuid}.jsonl"
                if exact.exists():
                    return exact
                deleted = list(base.glob(f"{uuid}.jsonl.deleted.*"))
                if deleted:
                    return sorted(deleted, key=lambda p: p.stat().st_mtime, reverse=True)[0]

        return None

    def _read_transcript_assistant_messages(self, transcript_path: "pathlib.Path") -> list[str]:
        """Read all assistant message texts from a JSONL transcript file."""
        messages = []
        try:
            with open(transcript_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("type") != "message":
                        continue
                    msg = entry.get("message", {})
                    if msg.get("role") != "assistant":
                        continue
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        # Extract text blocks, skip thinking blocks
                        text_parts = []
                        for block in content:
                            if block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                        content = "\n".join(text_parts)
                    if content:
                        messages.append(content)
        except Exception as e:
            logger.debug("[WORKER] Error reading transcript %s: %s", transcript_path, e)
        return messages

    async def _resolve_transcript_path(self, session_key: str) -> Optional[str]:
        """Query Gateway sessions_list to get the transcript path for a session."""
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"{GATEWAY_URL}/tools/invoke",
                    headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                    json={
                        "tool": "sessions_list",
                        "sessionKey": f"{GATEWAY_SESSION_KEY}-resolve-transcript",
                        "args": {"limit": 50, "messageLimit": 0}
                    },
                    timeout=aiohttp.ClientTimeout(total=10)
                )
                data = await resp.json()
                if data.get("ok"):
                    result = data.get("result", {})
                    details = result.get("details", result)
                    for s in details.get("sessions", []):
                        if s.get("key") == session_key:
                            return s.get("transcriptPath")
        except Exception as e:
            logger.debug("[WORKER] Error resolving transcript path: %s", e)
        return None

    async def _check_session_status(
        self,
        session_key: str,
        spawn_time: Optional[float] = None,
        transcript_hint: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Check if a worker session has completed.

        Primary method: find transcript file on disk (including .deleted files).
        Fallback: Gateway sessions_history API.
        """
        import pathlib

        try:
            # Method 1: Find transcript on disk (most reliable)
            transcript = self._find_transcript_file(session_key, transcript_hint=transcript_hint)
            if transcript:
                mtime = transcript.stat().st_mtime
                age_seconds = time.time() - mtime

                # .deleted files are always completed
                is_deleted = ".deleted." in transcript.name
                if is_deleted:
                    messages = self._read_transcript_assistant_messages(transcript)
                    return {
                        "completed": True,
                        "success": len(messages) > 0,
                        "error": "" if messages else "No assistant response in deleted transcript",
                    }

                # Live transcript — check if still being written
                if age_seconds < 15:
                    return {"completed": False, "success": False, "error": ""}

                messages = self._read_transcript_assistant_messages(transcript)
                if messages:
                    return {"completed": True, "success": True, "error": ""}
                if age_seconds > 300:
                    return {"completed": True, "success": False, "error": "Session stale (no response)"}
                return {"completed": False, "success": False, "error": ""}

            # Method 2: Try Gateway sessions_history
            history = await self._get_session_history(session_key)
            if history is not None and len(history) > 0:
                has_assistant = any(msg.get("role") == "assistant" for msg in history)
                if has_assistant:
                    return {"completed": True, "success": True, "error": ""}
                if spawn_time and (time.time() - spawn_time) / 60 > 15:
                    return {"completed": True, "success": False, "error": "Session stale"}
                return {"completed": False, "success": False, "error": ""}

            # Method 3: Check age-based fallback
            if spawn_time is not None:
                age_minutes = (time.time() - spawn_time) / 60
                if age_minutes < 5:
                    return {"completed": False, "success": False, "error": ""}
                return {"completed": True, "success": False, "error": "Session not found"}
            return {"completed": True, "success": False, "error": "Session not found"}

        except Exception as e:
            logger.warning("[WORKER] Error checking session status: %s", e)
            return None

    async def _get_session_history(self, session_key: str) -> Optional[list[dict]]:
        """Get session message history via Gateway sessions_history API."""
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"{GATEWAY_URL}/tools/invoke",
                    headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                    json={
                        "tool": "sessions_history",
                        "sessionKey": f"{GATEWAY_SESSION_KEY}-status-check",
                        "args": {
                            "sessionKey": session_key,
                            "limit": 3,
                            "includeTools": False,
                        }
                    },
                    timeout=aiohttp.ClientTimeout(total=10)
                )
                data = await resp.json()
                if data.get("ok"):
                    result = data.get("result", {})
                    details = result.get("details", result)
                    return details.get("messages", [])
                return None
        except Exception as e:
            logger.debug("[WORKER] Error querying Gateway sessions_history: %s", e)
            return None

    async def _fetch_session_summary(
        self, session_key: str, transcript_hint: Optional[str] = None
    ) -> Optional[str]:
        """Fetch the last assistant message from a completed session as its summary.

        Primary: read transcript file on disk (including .deleted files).
        Fallback: Gateway sessions_history API.
        """
        # --- Attempt 1: Read transcript directly from disk ---
        transcript = self._find_transcript_file(session_key, transcript_hint=transcript_hint)
        if transcript:
            messages = self._read_transcript_assistant_messages(transcript)
            if messages:
                # Use the longest assistant message (the actual output, not preamble)
                text = max(messages, key=len)
                if len(text) > 16000:
                    text = text[:16000] + "..."
                logger.info(
                    "[WORKER] Read transcript summary for %s (len=%d, file=%s)",
                    session_key, len(text), transcript.name,
                )
                return text

        # --- Attempt 2: Gateway sessions_history API ---
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"{GATEWAY_URL}/tools/invoke",
                    headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                    json={
                        "tool": "sessions_history",
                        "sessionKey": f"{GATEWAY_SESSION_KEY}-fetch-summary",
                        "args": {
                            "sessionKey": session_key,
                            "limit": 3,
                            "includeTools": False
                        }
                    },
                    timeout=aiohttp.ClientTimeout(total=10)
                )
                data = await resp.json()
                if data.get("ok"):
                    result = data.get("result", {})
                    details = result.get("details", result)
                    messages = details.get("messages", [])

                    for msg in reversed(messages):
                        if msg.get("role") == "assistant":
                            text = msg.get("content", "")
                            if isinstance(text, list):
                                text = " ".join(
                                    b.get("text", "") for b in text
                                    if b.get("type") == "text"
                                )
                            if text and len(text) > 16000:
                                text = text[:16000] + "..."
                            if text:
                                return text
        except Exception as e:
            logger.debug("[WORKER] Gateway sessions_history failed: %s", e)

        return None

    def _detect_escalation(
        self, result_summary: Optional[str], worker_info: WorkerInfo, worker_id: str
    ) -> Optional[str]:
        """Check if the model requested escalation via ESCALATE: signal.
        
        Returns the reason string if escalation detected, None otherwise.
        Local models can punt on complex tasks by outputting:
            ESCALATE: <reason>
        """
        if not result_summary:
            return None
        
        # Check first few hundred chars — ESCALATE should be early in output
        check_text = result_summary[:1000].strip()
        
        # Look for ESCALATE: pattern (case-insensitive, may have markdown backticks)
        import re
        match = re.search(r'ESCALATE:\s*(.+)', check_text, re.IGNORECASE)
        if match:
            reason = match.group(1).strip()
            logger.info(
                "[WORKER] Model requested escalation for %s: %s (model=%s)",
                worker_info.task_id[:8], reason, worker_info.model
            )
            return reason
        
        return None

    async def _handle_escalation(
        self,
        worker_id: str,
        worker_info: WorkerInfo,
        reason: str,
    ) -> None:
        """Handle model escalation — respawn task on a higher-tier model.
        
        When a local model determines a task is too complex, it punts via ESCALATE.
        We mark the task for retry with model_tier bumped to 'standard'.
        """
        task_id = worker_info.task_id
        project_id = worker_info.project_id
        duration = time.time() - worker_info.start_time
        
        # Remove from tracking
        self.active_workers.pop(worker_id, None)
        self.project_locks.pop(project_id, None)
        
        logger.info(
            "[WORKER] Escalating %s from %s to standard tier (reason: %s, punt_time=%.0fs)",
            task_id[:8], worker_info.model, reason, duration
        )
        
        # Update task: reset to not_started with standard tier
        db_task = await self.db.get(Task, task_id)
        if db_task:
            db_task.work_state = "not_started"
            db_task.status = "active"
            db_task.model_tier = "standard"
            db_task.failure_reason = f"Escalated by local model: {reason}"
            db_task.updated_at = datetime.now(timezone.utc)
            await self.db.commit()
        
        # Record the escalation in worker_runs
        await self._record_worker_run(
            task_id=task_id,
            worker_id=worker_id,
            model=worker_info.model,
            agent_type=worker_info.agent_type,
            succeeded=False,
            duration_seconds=duration,
            error_log=f"ESCALATED: {reason}",
            result_summary=f"Model punted to higher tier: {reason}",
        )
        
        # Cancel the workflow run so it restarts with new tier
        from sqlalchemy import text as sa_text
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            sa_text("UPDATE workflow_runs SET status = 'cancelled', finished_at = :now WHERE task_id = :tid AND status IN ('running', 'pending')"),
            {"now": now, "tid": task_id}
        )
        await self.db.commit()
        
        logger.info("[WORKER] Task %s ready for re-pickup at standard tier", task_id[:8])

    async def _handle_worker_completion(
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

            # Auto-commit and push any changes the worker produced.
            db_task_for_title = await self.db.get(Task, task_id)
            _task_title = db_task_for_title.title if db_task_for_title else ""
            commit_sha, modified_files = await self._push_project_repo_if_needed(
                project_id=project_id,
                task_id=task_id,
                agent_type=agent_type,
                task_title=_task_title,
            )

            # If worker produced no file changes and no commits, mark as failed
            # (the agent ran but didn't actually do anything)
            # Skip this check for: reflections, sweeps, non-code agents, diagnostic tasks
            is_internal_task = (
                worker_info.label.startswith("reflection-")
                or worker_info.label.startswith("sweep-")
                or worker_info.label.startswith("diagnostic-")
                or worker_info.label.startswith("inbox-")
            )
            if not commit_sha and not modified_files and not is_internal_task:
                # Check if it's a non-code task (writer/researcher docs go to shared memory)
                if agent_type not in ("writer", "researcher", "reviewer", "architect"):
                    logger.warning(
                        "[WORKER] Worker %s completed but produced no file changes "
                        "(task=%s). Marking as failed.",
                        worker_id, task_id_short,
                    )
                    # Revert task to todo so it can be retried
                    if db_task_for_title:
                        db_task_for_title.work_state = "not_started"
                        db_task_for_title.status = "active"
                        db_task_for_title.finished_at = None
                        db_task_for_title.updated_at = datetime.now(timezone.utc)
                        db_task_for_title.failure_reason = "No file changes produced"
                        await self.db.commit()
                    succeeded = False

            # ── Run Validity Contract check ───────────────────────────────────────
            # Only run for non-internal tasks that are still considered successful.
            # This is fail-closed: any contract violation reverts the task so it can
            # be retried rather than silently marked done without evidence of completion.
            validity_result: dict | None = None
            if succeeded and not is_internal_task:
                _started_at: Optional[datetime] = None
                if db_task_for_title:
                    _started_at = db_task_for_title.started_at

                checker = RunValidityChecker(
                    task_id=task_id,
                    started_at=_started_at,
                    session_key=worker_info.child_session_key,
                    transcript_path=worker_info.transcript_path,
                    result_summary=result_summary,
                    files_modified=list(modified_files) if modified_files else [],
                    find_transcript_fn=self._find_transcript_file,
                    read_messages_fn=self._read_transcript_assistant_messages,
                    first_response_sla_seconds=FIRST_RESPONSE_SLA_SECONDS,
                )
                rvc = checker.validate()
                validity_result = rvc.to_dict()

                if not rvc.passed:
                    violation_codes = [v.code for v in rvc.violations]
                    violation_details = "; ".join(v.detail for v in rvc.violations)
                    logger.warning(
                        "[RVC] Run validity contract FAILED for task %s "
                        "(worker=%s violations=%s): %s",
                        task_id_short, worker_id, violation_codes, violation_details,
                    )
                    # Revert task — fail-closed: requires human or retry to re-run
                    if db_task_for_title:
                        db_task_for_title.work_state = "not_started"
                        db_task_for_title.status = "active"
                        db_task_for_title.finished_at = None
                        db_task_for_title.updated_at = datetime.now(timezone.utc)
                        db_task_for_title.failure_reason = (
                            f"Run validity contract failed: {', '.join(violation_codes)}"
                        )
                        await self.db.commit()
                    succeeded = False
                else:
                    logger.info(
                        "[RVC] Run validity contract PASSED for task %s "
                        "(first_response=%.1fs, transcript=%s, evidence=%s)",
                        task_id_short,
                        rvc.first_response_time_seconds or 0.0,
                        rvc.transcript_found,
                        rvc.evidence_ok,
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
                provider = infer_provider(worker_info.model)
                error_type = classify_error_type(error_log)
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
        
        # Record worker run with actual commit/file info
        _commit_sha = locals().get("commit_sha")
        _modified_files = locals().get("modified_files")
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
            commit_sha=_commit_sha,
            files_modified=_modified_files,
            session_key=worker_info.child_session_key,
        )

        # Update worker status (mark inactive if no other workers)
        if not self.active_workers:
            await self._update_worker_status(active=False)

        # Mark agent idle
        await AgentTracker(self.db).mark_idle(agent_type)

    async def _terminate_session(self, session_key: str, reason: str) -> bool:
        """
        Terminate a session via Gateway API.
        
        Attempts to gracefully terminate the session using sessions_kill.
        Returns True if termination was successful or the session is already gone.
        """
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"{GATEWAY_URL}/tools/invoke",
                    headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                    json={
                        "tool": "sessions_kill",
                        "sessionKey": f"{GATEWAY_SESSION_KEY}-kill-{uuid.uuid4().hex[:8]}",
                        "args": {
                            "sessionKey": session_key,
                            "reason": reason,
                        }
                    },
                    timeout=aiohttp.ClientTimeout(total=10)
                )
                
                data = await resp.json()
                
                if data.get("ok"):
                    logger.info(
                        "[GATEWAY] Session terminated successfully: %s (reason: %s)",
                        session_key, reason
                    )
                    return True
                else:
                    # Session might already be gone or API doesn't support sessions_kill
                    error = data.get("error", {})
                    error_msg = error.get("message", str(data))
                    
                    # If session not found, treat as success (already terminated)
                    if "not found" in error_msg.lower() or "unknown session" in error_msg.lower():
                        logger.info(
                            "[GATEWAY] Session already terminated: %s",
                            session_key
                        )
                        return True
                    
                    logger.warning(
                        "[GATEWAY] Failed to terminate session %s: %s",
                        session_key, error_msg
                    )
                    return False
                    
        except Exception as e:
            logger.warning(
                "[GATEWAY] Error terminating session %s: %s",
                session_key, e
            )
            return False

    async def _kill_worker(self, worker_id: str, reason: str) -> None:
        """Kill a worker session via Gateway API."""
        worker_info = self.active_workers.get(worker_id)
        if not worker_info:
            return

        logger.warning(f"[WORKER] Killing worker {worker_id} (reason={reason})")

        # Attempt to terminate the session gracefully
        terminated = await self._terminate_session(
            worker_info.child_session_key,
            reason=reason
        )
        
        if terminated:
            logger.info(f"[WORKER] Session terminated for worker {worker_id}")
        else:
            logger.warning(
                f"[WORKER] Could not terminate session for {worker_id}, "
                f"marking as failed anyway"
            )

        # Handle as failed completion
        await self._handle_worker_completion(
            worker_id=worker_id,
            worker_info=worker_info,
            succeeded=False,
            error_log=f"Worker killed: {reason}"
        )

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

                    # Extract budget_lane from model audit for accurate lane spend tracking
                    _lane_guard = (model_audit or {}).get("lane_guard") or {}
                    _budget_lane: str | None = _lane_guard.get("lane") if isinstance(_lane_guard, dict) else None

                    await _safe_log_usage_event(
                        db,
                        source="orchestrator-worker",
                        model=token_usage.model or model if token_usage else model,
                        provider=token_usage.provider if token_usage and token_usage.provider else None,
                        route_type=route_type,
                        task_type="task_execution",
                        budget_lane=_budget_lane,
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

        payload = self._extract_json(summary)

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
        reflection.inefficiencies = self._json_list(payload.get("inefficiencies_detected"))
        reflection.system_risks = self._json_list(payload.get("system_risks"))
        reflection.missed_opportunities = self._json_list(payload.get("missed_opportunities"))
        reflection.identity_adjustments = self._json_list(payload.get("identity_adjustments"))
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
            
            payload = self._extract_json(summary)
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
                
                if not initiative_id or decision not in {"approve", "defer", "reject"}:
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
    def _json_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if isinstance(item, (str, int, float))]

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Best-effort extraction of JSON object from assistant summary text.
        
        Handles:
        - Plain JSON objects
        - JSON wrapped in markdown code blocks (```json ... ```)
        - Nested braces
        """
        candidate = text.strip()
        
        # Try direct JSON parse first
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else {"raw": candidate}
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown code blocks (```json ... ```)
        import re
        json_block_pattern = r"```(?:json)?\s*\n(.*?)\n```"
        matches = re.findall(json_block_pattern, candidate, re.DOTALL | re.IGNORECASE)
        for match in matches:
            try:
                parsed = json.loads(match.strip())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue

        # Try finding JSON object boundaries
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end != -1 and end > start:
            snippet = candidate[start : end + 1]
            try:
                parsed = json.loads(snippet)
                return parsed if isinstance(parsed, dict) else {"raw": candidate}
            except json.JSONDecodeError:
                pass

        return {"raw": candidate}

    @staticmethod
    def _git_commit_and_push_sync(
        *,
        repo_path: Path,
        project_id: str,
        task_id: str,
        agent_type: str,
        task_title: str = "",
    ) -> tuple[Optional[str], Optional[list[str]]]:
        """Synchronous git commit+push. Designed to run in asyncio.to_thread."""
        commit_sha = None
        files_modified = None

        def run_git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git", "-C", str(repo_path), *args],
                capture_output=True,
                text=True,
                check=check,
            )

        inside = run_git("rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return None, None

        dirty = run_git("status", "--porcelain")
        if dirty.returncode == 0 and dirty.stdout.strip():
            changed_lines = [
                line[3:].strip() for line in dirty.stdout.strip().split("\n")
                if line.strip()
            ]
            files_modified = changed_lines
            run_git("add", "-A")

            short_title = (task_title or task_id[:8])[:72]
            commit_msg = (
                f"agent({agent_type}): {short_title}\n\n"
                f"Task: {task_id}\n"
                f"Agent: {agent_type}\n"
                f"Auto-committed by orchestrator after task completion."
            )

            commit_result = run_git(
                "commit", "-m", commit_msg,
                "--author", f"lobs-{agent_type} <thelobsbot@gmail.com>",
            )
            if commit_result.returncode != 0:
                logger.warning(
                    "[WORKER] Auto-commit failed for project %s: %s",
                    project_id, commit_result.stderr.strip(),
                )
                return None, files_modified
            else:
                logger.info(
                    "[WORKER] Auto-committed %d changed files for task %s",
                    len(files_modified), task_id[:8],
                )

        sha_result = run_git("rev-parse", "HEAD")
        if sha_result.returncode == 0:
            commit_sha = sha_result.stdout.strip()

        upstream = run_git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if upstream.returncode != 0:
            return commit_sha, files_modified

        run_git("fetch", "--prune", "origin")

        counts = run_git("rev-list", "--left-right", "--count", "@{u}...HEAD")
        if counts.returncode != 0:
            return commit_sha, files_modified

        behind_str, ahead_str = (counts.stdout.strip().split() + ["0", "0"])[:2]
        behind = int(behind_str)
        ahead = int(ahead_str)

        if ahead <= 0 and behind <= 0:
            return commit_sha, files_modified

        if behind > 0:
            pull = run_git("pull", "--rebase")
            if pull.returncode != 0:
                raise RuntimeError(pull.stderr.strip() or pull.stdout.strip() or "git pull --rebase failed")

        push = run_git("push")
        if push.returncode != 0:
            raise RuntimeError(push.stderr.strip() or push.stdout.strip() or "git push failed")

        logger.info(
            "[WORKER] Auto-pushed repo after completion (project=%s task=%s ahead=%s)",
            project_id,
            task_id[:8],
            ahead,
        )

        return commit_sha, files_modified

    async def _push_project_repo_if_needed(
        self,
        *,
        project_id: str,
        task_id: str,
        agent_type: str,
        task_title: str = "",
    ) -> tuple[Optional[str], Optional[list[str]]]:
        """Auto-commit and push worker-produced changes to origin.

        Workers operate in the project repo; they write files but often don't
        commit. This method:
        1. Auto-commits any uncommitted changes with a descriptive message.
        2. Pushes to origin (rebasing if behind).
        3. Returns (commit_sha, files_modified) for tracking.

        Returns:
            Tuple of (commit_sha, files_modified) or (None, None) if no changes.
        """
        commit_sha = None
        files_modified = None

        try:
            project = await self.db.get(Project, project_id)
            if not project:
                return None, None

            repo_path = Path(project.repo_path) if project.repo_path else (BASE_DIR / project_id)
            if not repo_path.exists():
                return None, None

            # Run all git operations in a thread to avoid blocking the event loop
            import asyncio
            commit_sha, files_modified = await asyncio.to_thread(
                self._git_commit_and_push_sync,
                repo_path=repo_path,
                project_id=project_id,
                task_id=task_id,
                agent_type=agent_type,
                task_title=task_title,
            )

            return commit_sha, files_modified

        except Exception as e:
            logger.warning(
                "[WORKER] Auto-commit/push failed for project %s after task %s: %s",
                project_id,
                task_id[:8],
                e,
            )
            try:
                await EscalationManagerEnhanced(self.db).create_simple_alert(
                    task_id=task_id,
                    project_id=project_id,
                    error_log=f"Auto-commit/push failed: {e}",
                    severity="medium",
                )
            except Exception:
                # Never let alerting failures break completion handling.
                logger.debug("[WORKER] Failed to create auto-push alert", exc_info=True)

            return commit_sha, files_modified

    async def _read_work_summary(self, project_id: str) -> str | None:
        """Read .work-summary file from project directory."""
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
            await self._handle_worker_completion(
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
