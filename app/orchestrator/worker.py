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
        if status >= 500 or any(k in error_code for k in ("server_error", "internal_error", "service_unavailable")):
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


class WorkerManager:
    """
    Manages spawning and tracking concurrent workers via OpenClaw Gateway.
    
    Tracks active workers in memory and syncs state to DB.
    Enforces domain locks (one worker per project). Multiple instances of
    the same agent type can run concurrently on different projects.
    """

    def __init__(self, db: AsyncSession, provider_health: Optional[Any] = None):
        self.db = db
        self.provider_health = provider_health  # ProviderHealthRegistry instance
        
        # In-memory tracking: worker_id -> WorkerInfo
        self.active_workers: dict[str, WorkerInfo] = {}
        
        # Domain locks: one worker per project (prevents repo conflicts)
        self.project_locks: dict[str, str] = {}  # project_id -> task_id

        self.max_workers = MAX_WORKERS

        # Signal to engine: set True when all reflection workers from a batch
        # have completed, indicating the sweep arbitrator should run.
        self.sweep_requested = False

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
            locked_task = self.project_locks[project_id]
            logger.info(
                f"[WORKER] Project {project_id} locked by task {locked_task[:8]}. "
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
                spawn_result, err, err_type = await self._spawn_session(
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

    async def _spawn_session(
        self,
        task_prompt: str,
        agent_id: str,
        model: str,
        label: str,
        routing_policy: dict[str, Any] | None = None,
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
                            "runTimeoutSeconds": 900,
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

        # Check session status via Gateway API
        session_status = await self._check_session_status(
            worker_info.child_session_key,
            spawn_time=worker_info.start_time
        )
        
        if not session_status:
            # Unable to get status - skip this check
            return
        
        # Check if session completed
        if session_status.get("completed"):
            success = session_status.get("success", False)
            
            # Try to fetch the session result summary
            result_summary = await self._fetch_session_summary(
                worker_info.child_session_key
            )
            
            await self._handle_worker_completion(
                worker_id=worker_id,
                worker_info=worker_info,
                succeeded=success,
                error_log=session_status.get("error", ""),
                result_summary=result_summary
            )

    async def _check_session_status(
        self,
        session_key: str,
        spawn_time: Optional[float] = None
    ) -> Optional[dict[str, Any]]:
        """Check session status by reading the JSONL transcript file directly.

        Gateway's sessions_list doesn't expose a "completed" status field, so we
        detect completion by checking whether the transcript contains a final
        assistant message and is no longer being written to (mtime > 15s ago).
        
        Args:
            session_key: The session key to check
            spawn_time: Unix timestamp when the worker was spawned (for timeout handling)
        """
        import pathlib

        try:
            # Extract agent ID from session key: "agent:<agentId>:subagent:<uuid>"
            parts = session_key.split(":")
            if len(parts) < 2:
                return None
            agent_id = parts[1]

            # Find the session transcript
            base = pathlib.Path.home() / ".openclaw" / "agents" / agent_id / "sessions"
            if not base.exists():
                # Might be under the workspace directory
                return None

            # Match by looking for the session UUID in the key
            # Key format: agent:<agentId>:subagent:<uuid>
            session_uuid = parts[3] if len(parts) >= 4 else None

            transcript = None
            if session_uuid:
                # Try exact match first
                candidate = base / f"{session_uuid}.jsonl"
                if candidate.exists():
                    transcript = candidate

            if not transcript and not session_uuid:
                # Only fall back to most recent transcript if we don't have a UUID
                # (with a UUID, falling back picks up unrelated sessions)
                transcripts = sorted(base.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
                transcript = transcripts[0] if transcripts else None

            if not transcript:
                # No transcript found - check how long ago the session was spawned
                if spawn_time is not None:
                    age_minutes = (time.time() - spawn_time) / 60
                    if age_minutes < 15:
                        # Session is recent, transcript might not be created yet
                        return {"completed": False, "success": False, "error": ""}
                    else:
                        # Session is stale and transcript is missing - mark as failed
                        return {"completed": True, "success": False, "error": "Session stale (no transcript found)"}
                
                # No spawn time provided, can't determine - mark as failed
                return {"completed": True, "success": False, "error": "Transcript not found"}

            # Check if file is still being written to
            mtime = transcript.stat().st_mtime
            age_seconds = time.time() - mtime
            if age_seconds < 15:
                # Still being written to — not done yet
                return {"completed": False, "success": False, "error": ""}

            # Check if there's an assistant message in the transcript
            has_assistant = False
            with open(transcript, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("type") == "message":
                        msg = entry.get("message", {})
                        if msg.get("role") == "assistant":
                            has_assistant = True

            if has_assistant:
                return {"completed": True, "success": True, "error": ""}

            # No assistant message but file is stale — might be stuck
            if age_seconds > 300:  # 5 minutes with no activity
                return {"completed": True, "success": False, "error": "Session stale (no assistant response)"}

            return {"completed": False, "success": False, "error": ""}

        except Exception as e:
            logger.warning("[WORKER] Error checking session status via transcript: %s", e)
            return None

    async def _fetch_session_summary(
        self, session_key: str
    ) -> Optional[str]:
        """Fetch the last assistant message from a completed session as its summary.

        Tries Gateway sessions_history first, then falls back to reading the
        JSONL transcript file directly (avoids cross-agent permission issues).
        """
        # --- Attempt 1: Gateway API ---
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"{GATEWAY_URL}/tools/invoke",
                    headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                    json={
                        "tool": "sessions_history",
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
                        role = msg.get("role", "")
                        if role == "assistant":
                            text = msg.get("content", "")
                            if isinstance(text, list):
                                text = " ".join(
                                    b.get("text", "") for b in text
                                    if b.get("type") == "text"
                                )
                            if text and len(text) > 8000:
                                text = text[:8000] + "..."
                            if text:
                                return text
        except Exception as e:
            logger.debug("[WORKER] Gateway sessions_history failed: %s", e)

        # --- Attempt 2: Read JSONL transcript directly ---
        return await self._read_transcript_summary(session_key)

    async def _read_transcript_summary(self, session_key: str) -> Optional[str]:
        """Read the last assistant message from a session's JSONL transcript file.

        Session keys follow the pattern: agent:<agentId>:<label>
        Transcripts live at: ~/.openclaw/agents/<agentId>/sessions/<sessionId>.jsonl
        """
        import glob
        import pathlib

        try:
            # Extract agent ID from session key (e.g. "agent:architect:main-spawn-xxx")
            parts = session_key.split(":")
            if len(parts) < 2:
                return None
            agent_id = parts[1]

            # Find the most recent transcript for this agent
            base = pathlib.Path.home() / ".openclaw" / "agents" / agent_id / "sessions"
            if not base.exists():
                return None

            # Get all transcript files sorted by modification time (newest first)
            transcripts = sorted(base.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not transcripts:
                return None

            # Read the newest transcript and find the last assistant message
            transcript = transcripts[0]
            last_assistant_text = None

            with open(transcript, "r") as f:
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
                        content = " ".join(
                            b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                    if content:
                        last_assistant_text = content

            if last_assistant_text and len(last_assistant_text) > 8000:
                last_assistant_text = last_assistant_text[:8000] + "..."

            if last_assistant_text:
                logger.info(
                    "[WORKER] Read transcript summary for %s (len=%d, file=%s)",
                    session_key, len(last_assistant_text), transcript.name,
                )

            return last_assistant_text

        except Exception as e:
            logger.debug("[WORKER] Failed to read transcript: %s", e)
            return None

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

            # Auto-push any commits the worker produced.
            #
            # Sub-agents (OpenClaw sessions) generally commit directly in the repo,
            # but the server used to *not* push those commits, leaving origin behind.
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

        # Update worker status (mark inactive if no other workers)
        if not self.active_workers:
            await self._update_worker_status(active=False)

        # Mark agent idle
        await AgentTracker(self.db).mark_idle(agent_type)

    async def _kill_worker(self, worker_id: str, reason: str) -> None:
        """Kill a worker session via Gateway API."""
        worker_info = self.active_workers.get(worker_id)
        if not worker_info:
            return

        logger.warning(f"[WORKER] Killing worker {worker_id} (reason={reason})")

        # TODO: Implement session termination via Gateway API if available
        # For now, just handle as failed completion
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

            self.db.add(run)

            if model:
                route_type = resolve_route_type(
                    model,
                    subscription_models=(model_audit or {}).get("subscription_models", []),
                    subscription_providers=(model_audit or {}).get("subscription_providers", []),
                )
                # Use actual token counts if available
                input_tokens = token_usage.input_tokens if token_usage else 0
                output_tokens = token_usage.output_tokens if token_usage else 0
                cached_tokens = token_usage.cache_read_tokens if token_usage else 0
                cost = token_usage.estimated_cost_usd if token_usage else 0.0

                await _safe_log_usage_event(
                    self.db,
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

            await self.db.commit()

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
        """Persist strategic/diagnostic outputs and derive initiatives for strategic runs."""
        try:
            reflection_result = await self.db.execute(
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
            # Some workers return diagnostic-style schemas, normalize them
            if isinstance(payload, dict) and "raw" not in payload:
                # Map: issue_summary/summary → inefficiencies_detected
                if "inefficiencies_detected" not in payload:
                    issue_summary = payload.get("issue_summary") or payload.get("summary")
                    if issue_summary:
                        payload["inefficiencies_detected"] = [issue_summary] if isinstance(issue_summary, str) else issue_summary
                
                # Map: root_causes → inefficiencies_detected (append)
                root_causes = payload.get("root_causes")
                if root_causes:
                    existing = payload.get("inefficiencies_detected", [])
                    if not isinstance(existing, list):
                        existing = [existing] if existing else []
                    if isinstance(root_causes, list):
                        payload["inefficiencies_detected"] = existing + root_causes
                    elif isinstance(root_causes, str):
                        payload["inefficiencies_detected"] = existing + [root_causes]
                
                # Map: recommended_actions → missed_opportunities
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
                    self.db.add(
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

            await self.db.commit()

        except Exception as e:
            logger.warning("[WORKER] Failed to persist reflection output: %s", e, exc_info=True)
            await self.db.rollback()

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
        try:
            project = await self.db.get(Project, project_id)
            if not project:
                return

            repo_path = Path(project.repo_path) if project.repo_path else (BASE_DIR / project_id)
            if not repo_path.exists():
                return

            def run_git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
                return subprocess.run(
                    ["git", "-C", str(repo_path), *args],
                    capture_output=True,
                    text=True,
                    check=check,
                )

            inside = run_git("rev-parse", "--is-inside-work-tree")
            if inside.returncode != 0 or inside.stdout.strip() != "true":
                return

            # Don't attempt to push if the repo is dirty.
            dirty = run_git("status", "--porcelain")
            if dirty.returncode == 0 and dirty.stdout.strip():
                logger.info(
                    "[WORKER] Repo has uncommitted changes; skipping auto-push (%s)",
                    repo_path,
                )
                return

            # Ensure upstream exists; if not, skip.
            upstream = run_git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
            if upstream.returncode != 0:
                return

            run_git("fetch", "--prune", "origin")

            counts = run_git("rev-list", "--left-right", "--count", "@{u}...HEAD")
            if counts.returncode != 0:
                return

            behind_str, ahead_str = (counts.stdout.strip().split() + ["0", "0"])[:2]
            behind = int(behind_str)
            ahead = int(ahead_str)

            if ahead <= 0 and behind <= 0:
                return

            # If we're behind, try to rebase first.
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
