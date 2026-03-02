"""Workflow node type handlers.

Each node type (spawn_agent, tool_call, branch, etc.) has an execute()
and optionally a check() method for async operations.

## Adding a New Node Type

    from app.orchestrator.workflow_nodes import register_node

    @register_node("my_node_type")
    async def exec_my_node(config, context, run, *, db, worker_manager):
        # ... do work ...
        return NodeResult(status="completed", output={"key": "value"})

    # Optional: register a checker for async nodes
    @register_node_checker("my_node_type")
    async def check_my_node(node_def, run, *, db, worker_manager):
        # Return None if still running, NodeResult when done
        return None
"""

import asyncio
import json
import logging
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import aiohttp

from app.orchestrator.config import GATEWAY_URL, GATEWAY_TOKEN, GATEWAY_SESSION_KEY

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════
# Node Result
# ══════════════════════════════════════════════════════════════════════

@dataclass
class NodeResult:
    """Result of executing or checking a workflow node."""
    status: str  # "completed" | "running" | "failed"
    output: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    error_type: str = ""
    session_key: Optional[str] = None

# ══════════════════════════════════════════════════════════════════════
# Node Registry — the plugin system
# ══════════════════════════════════════════════════════════════════════

# Handler signature: async (config, context, run, *, db, worker_manager) -> NodeResult
# Checker signature: async (node_def, run, *, db, worker_manager) -> Optional[NodeResult]

_NODE_EXECUTORS: dict[str, Callable] = {}
_NODE_CHECKERS: dict[str, Callable] = {}

def register_node(node_type: str):
    """Decorator to register a node executor.

    Usage:
        @register_node("my_type")
        async def exec_my_type(config, context, run, *, db, worker_manager):
            return NodeResult(status="completed", output={})
    """
    def decorator(fn: Callable) -> Callable:
        _NODE_EXECUTORS[node_type] = fn
        return fn
    return decorator

def register_node_checker(node_type: str):
    """Decorator to register a node checker for async nodes.

    Usage:
        @register_node_checker("my_type")
        async def check_my_type(node_def, run, *, db, worker_manager):
            return None  # still running
    """
    def decorator(fn: Callable) -> Callable:
        _NODE_CHECKERS[node_type] = fn
        return fn
    return decorator

def get_registered_node_types() -> list[str]:
    """Return all registered node type names."""
    return sorted(_NODE_EXECUTORS.keys())

# ══════════════════════════════════════════════════════════════════════
# Template rendering
# ══════════════════════════════════════════════════════════════════════

def _render_template(template: str, context: dict[str, Any]) -> str:
    """Render a template string with {path.to.value} substitutions from context."""
    def replacer(match: re.Match) -> str:
        path = match.group(1)
        value = context
        for part in path.split("."):
            if isinstance(value, dict):
                value = value.get(part, "")
            else:
                return match.group(0)  # Leave unreplaced
        if isinstance(value, (dict, list)):
            return json.dumps(value, indent=2, default=str)
        return str(value) if value is not None else ""

    return re.sub(r"\{([a-zA-Z0-9_.]+)\}", replacer, template)

def _resolve_context_path(context: dict, path: str) -> Any:
    """Walk a dotted path in context, returning the value or None."""
    value = context
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value

def _evaluate_condition(expr: str, context: dict) -> bool:
    """Evaluate a simple condition expression.

    Supports: "path == value", "path != value", "path" (truthiness),
    "path > N", "path < N", "path >= N", "path <= N", "path in [a,b,c]"
    """
    expr = expr.strip()

    # Comparison operators (order matters — check multi-char first)
    for op in ("!=", ">=", "<=", "==", ">", "<"):
        if op in expr:
            parts = expr.split(op, 1)
            path = parts[0].strip()
            expected = parts[1].strip().strip("'\"")
            value = _resolve_context_path(context, path)

            if op == "==":
                return str(value) == expected
            elif op == "!=":
                return str(value) != expected
            elif op in (">", "<", ">=", "<="):
                try:
                    fval = float(value) if value is not None else 0
                    fexp = float(expected)
                    if op == ">": return fval > fexp
                    if op == "<": return fval < fexp
                    if op == ">=": return fval >= fexp
                    if op == "<=": return fval <= fexp
                except (ValueError, TypeError):
                    return False

    # "path in [a, b, c]"
    if " in " in expr:
        parts = expr.split(" in ", 1)
        path = parts[0].strip()
        list_str = parts[1].strip()
        value = _resolve_context_path(context, path)
        if list_str.startswith("[") and list_str.endswith("]"):
            items = [i.strip().strip("'\"") for i in list_str[1:-1].split(",")]
            return str(value) in items

    # Truthiness check
    value = _resolve_context_path(context, expr)
    return bool(value)

# ══════════════════════════════════════════════════════════════════════
# NodeHandlers — dispatcher (backward compat + registry)
# ══════════════════════════════════════════════════════════════════════

class NodeHandlers:
    """Registry of node-type handlers. Dispatches to registered functions."""

    def __init__(self, db: Any, worker_manager: Any = None):
        self.db = db
        self.worker_manager = worker_manager

    async def execute(self, node_def: dict, run: Any) -> NodeResult:
        """Dispatch execution to the correct handler."""
        node_type = node_def.get("type", "")
        config = node_def.get("config", {})
        context = dict(run.context or {})

        executor = _NODE_EXECUTORS.get(node_type)
        if executor is None:
            return NodeResult(status="failed", error=f"Unknown node type: {node_type}")

        try:
            return await executor(config, context, run, db=self.db, worker_manager=self.worker_manager)
        except Exception as e:
            logger.error("[NODE:%s] Execution error: %s", node_def.get("id"), e, exc_info=True)
            return NodeResult(status="failed", error=str(e))

    async def check(self, node_def: dict, run: Any) -> Optional[NodeResult]:
        """Check if an async node has completed. Returns None if still running."""
        node_type = node_def.get("type", "")
        checker = _NODE_CHECKERS.get(node_type)
        if checker is None:
            return None
        return await checker(node_def, run, db=self.db, worker_manager=self.worker_manager)

    async def delete_session(self, session_key: str) -> None:
        """Delete a worker session via Gateway WebSocket API.

        Delegates to WorkerManager.gateway.delete_session() which uses the
        sessions.delete JSON-RPC method. Falls back gracefully if gateway is
        unavailable (session will be auto-archived by OpenClaw as a safety net).
        """
        if self.worker_manager and hasattr(self.worker_manager, "gateway") and self.worker_manager.gateway:
            try:
                result = await self.worker_manager.gateway.delete_session(session_key)
                if result:
                    logger.info("[NODE] Deleted session %s via Gateway", session_key)
                else:
                    logger.warning("[NODE] Gateway could not delete session %s (will auto-archive)", session_key)
            except Exception as exc:
                logger.warning("[NODE] Error deleting session %s: %s (will auto-archive)", session_key, exc)
        else:
            logger.debug("[NODE] No gateway available; session %s will auto-archive", session_key)

# ══════════════════════════════════════════════════════════════════════
# Helper: model tier resolution
# ══════════════════════════════════════════════════════════════════════

async def _resolve_model_tier(tier: str, agent_type: str, context: dict, db: Any) -> str | None:
    """Resolve a model tier name to an actual model using ModelChooser."""
    KNOWN_TIERS = {"micro", "small", "medium", "standard", "strong"}
    if tier not in KNOWN_TIERS:
        return tier  # Treat as explicit model/alias
    try:
        from app.orchestrator.model_chooser import ModelChooser
        chooser = ModelChooser(db, provider_health=None)
        task_ctx = context.get("task", {})
        if not isinstance(task_ctx, dict):
            task_ctx = {}
        task_for_chooser = {**task_ctx, "model_tier": tier}
        choice = await chooser.choose(agent_type=agent_type, task=task_for_chooser)
        return choice.model
    except Exception as e:
        logger.warning("[NODE] Model tier resolution failed for tier=%s: %s", tier, e)
        return None

# ══════════════════════════════════════════════════════════════════════
# Built-in Node Types
# ══════════════════════════════════════════════════════════════════════

# ── spawn_agent ──────────────────────────────────────────────────────

@register_node("spawn_agent")
async def _exec_spawn_agent(config, context, run, *, db, worker_manager):
    """Spawn an agent via the WorkerManager.

    Config:
        agent_type: str — agent template id (programmer, researcher, writer, etc.)
        prompt_template: str — optional override template
        model_tier: str — optional tier name (micro/small/medium/standard/strong)
        model: str — explicit model override
        timeout_seconds: int — max session runtime (default 900)
    """
    agent_type = config.get("agent_type", "programmer")
    model_tier = config.get("model_tier")
    prompt_template = config.get("prompt_template")

    task_ctx = context.get("task", {})
    if not isinstance(task_ctx, dict):
        return NodeResult(status="failed", error="No task in workflow context")

    task_id = task_ctx.get("id")
    project_id = task_ctx.get("project_id")
    if not task_id or not project_id:
        return NodeResult(status="failed", error="Task missing id or project_id")

    task_for_spawn = dict(task_ctx)
    if model_tier:
        task_for_spawn["model_tier"] = model_tier
    elif config.get("model"):
        task_for_spawn["model_tier"] = config["model"]
    elif not task_for_spawn.get("model_tier"):
        # Check if classify_tier node ran and set a tier
        classify_output = context.get("classify_tier", {})
        if isinstance(classify_output, dict) and classify_output.get("classified_tier"):
            task_for_spawn["model_tier"] = classify_output["classified_tier"]
    logger.info("[NODE:spawn_agent] task_id=%s model_tier_in_ctx=%s classify_output=%s model_tier_for_spawn=%s",
                task_id, task_ctx.get("model_tier"), context.get("classify_tier", {}).get("classified_tier"), task_for_spawn.get("model_tier"))

    if isinstance(prompt_template, str) and prompt_template.strip():
        rendered = _render_template(prompt_template, context).strip()
        if rendered:
            task_for_spawn["notes"] = rendered

    if not worker_manager:
        return NodeResult(status="failed", error="No worker_manager available — cannot spawn agent")

    timeout_seconds = config.get("timeout_seconds", 7200)

    try:
        spawned = await worker_manager.spawn_worker(
            task=task_for_spawn,
            project_id=project_id,
            agent_type=agent_type,
            timeout_seconds=timeout_seconds,
        )

        if not spawned:
            return NodeResult(
                status="running",
                output={"queued": True, "reason": "capacity or project lock"},
            )

        child_key = None
        run_id = None
        for wid, winfo in worker_manager.active_workers.items():
            if winfo.task_id == task_id:
                child_key = winfo.child_session_key
                run_id = winfo.run_id
                break

        return NodeResult(
            status="running",
            output={
                "runId": run_id or "",
                "childSessionKey": child_key or "",
                "spawned_via": "worker_manager",
            },
            session_key=child_key,
        )
    except Exception as e:
        return NodeResult(status="failed", error=str(e), error_type="spawn_error")

@register_node_checker("spawn_agent")
async def _check_spawn_agent(node_def, run, *, db, worker_manager):
    """Check if a spawned agent has completed."""
    node_id = node_def["id"]
    ns = (run.node_states or {}).get(node_id, {})
    output = ns.get("output", {})

    # If queued, retry spawn
    if output.get("queued"):
        task_ctx = (run.context or {}).get("task", {})
        if not isinstance(task_ctx, dict):
            return NodeResult(status="failed", error="No task in workflow context")
        task_id = task_ctx.get("id")
        project_id = task_ctx.get("project_id")
        if not task_id or not project_id:
            return NodeResult(status="failed", error="Task missing id or project_id")
        if not worker_manager:
            return NodeResult(status="failed", error="No worker_manager available")

        # Ensure model_tier from classify_tier is propagated
        task_for_retry = dict(task_ctx)
        if not task_for_retry.get("model_tier"):
            classify_output = (run.context or {}).get("classify_tier", {})
            if isinstance(classify_output, dict) and classify_output.get("classified_tier"):
                task_for_retry["model_tier"] = classify_output["classified_tier"]

        spawned = await worker_manager.spawn_worker(
            task=task_for_retry,
            project_id=project_id,
            agent_type=node_def.get("config", {}).get("agent_type", "programmer"),
        )
        if not spawned:
            return None

        child_key = None
        run_id = None
        for _, winfo in worker_manager.active_workers.items():
            if winfo.task_id == task_id:
                child_key = winfo.child_session_key
                run_id = winfo.run_id
                break

        return NodeResult(
            status="running",
            output={
                "queued": False,
                "runId": run_id or "",
                "childSessionKey": child_key or "",
                "spawned_via": "worker_manager",
            },
            session_key=child_key,
        )

    task_id = (run.context or {}).get("task", {}).get("id")
    if not task_id:
        return NodeResult(status="failed", error="No task_id in run context")
    if not worker_manager:
        return NodeResult(status="failed", error="No worker_manager")

    worker_active = any(
        w.task_id == task_id
        for w in worker_manager.active_workers.values()
    )
    if worker_active:
        return None

    from app.models import Task, WorkerRun
    from sqlalchemy import select
    db_task = await db.get(Task, task_id)

    if db_task and db_task.work_state == "completed":
        return NodeResult(status="completed", output={"task_status": "completed", "task_id": task_id})
    elif db_task and db_task.work_state == "blocked":
        return NodeResult(status="failed", output={"task_status": "blocked", "task_id": task_id}, error="Task blocked after worker failure")
    elif db_task and db_task.work_state == "in_progress":
        # Worker not in active_workers (e.g. after server restart) but task still in_progress.
        # Check if there's a live persisted session before declaring failure.
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=8)
        stub_result = await db.execute(
            select(WorkerRun).where(
                WorkerRun.task_id == task_id,
                WorkerRun.child_session_key.isnot(None),
                WorkerRun.succeeded.is_(None),
                WorkerRun.started_at >= cutoff,
            )
        )
        stub = stub_result.scalars().first()
        if stub and stub.child_session_key:
            alive = await worker_manager.check_session_alive(stub.child_session_key)
            if alive:
                # Re-register so future checks find it in active_workers
                worker_manager.register_external_worker(
                    {"runId": stub.worker_id, "childSessionKey": stub.child_session_key},
                    agent_type=stub.agent_type or "programmer",
                    model=stub.model or "",
                    label=f"task-{task_id}",
                )
                logger.info("[NODE:spawn_agent] Re-attached live session %s for task %s", stub.child_session_key[:20], task_id[:8])
                return None  # still running
            else:
                # Session dead — reset task and let it re-queue
                if db_task:
                    db_task.work_state = "not_started"
                    db_task.status = "active"
                    await db.commit()
                return NodeResult(status="failed", error="Session dead after restart, re-queuing task")
        # No persisted session and not in active_workers.
        # Before declaring failure, check two safety conditions:

        # 1) Recently-succeeded WorkerRun: DB commit may have finished by now
        recent_cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
        succeeded_result = await db.execute(
            select(WorkerRun).where(
                WorkerRun.task_id == task_id,
                WorkerRun.succeeded.is_(True),
                WorkerRun.ended_at >= recent_cutoff,
            )
        )
        succeeded_run = succeeded_result.scalars().first()
        if succeeded_run:
            # Worker finished successfully but task state has not been committed yet.
            # Treat as completed so we do not trigger escalation.
            logger.info(
                "[NODE:spawn_agent] Found recently-succeeded WorkerRun for task %s — treating as completed",
                task_id[:8],
            )
            return NodeResult(status="completed", output={"task_status": "completed", "task_id": task_id})

        # 2) Grace period: task was updated very recently — DB commit may still be in flight.
        grace_cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
        if db_task.updated_at and db_task.updated_at >= grace_cutoff:
            logger.info(
                "[NODE:spawn_agent] Task %s updated_at is recent (< 60s) — waiting for DB commit to settle",
                task_id[:8],
            )
            return None  # still waiting

        # Worker is truly gone
        return NodeResult(
            status="failed",
            output={"task_status": "in_progress_orphaned", "task_id": task_id},
            error="Worker lost (no active session or persisted key), re-queuing",
        )
    else:
        return NodeResult(
            status="failed",
            output={"task_status": db_task.work_state if db_task else "unknown", "task_id": task_id},
            error=f"Worker completed but task state is {db_task.work_state if db_task else 'unknown'}",
        )

# ── send_to_session ──────────────────────────────────────────────────

@register_node("send_to_session")
async def _exec_send_to_session(config, context, run, *, db, worker_manager):
    """Send a message to an existing session (for fix loops)."""
    session_ref = config.get("session_ref", "")
    message_template = config.get("message_template", "")

    session_key = _resolve_context_path(context, session_ref)
    if not session_key:
        return NodeResult(status="failed", error=f"Session ref '{session_ref}' not found in context")

    message = _render_template(message_template, context)
    if not message.strip():
        return NodeResult(status="failed", error="Empty message after rendering")

    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                f"{GATEWAY_URL}/tools/invoke",
                headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                json={
                    "tool": "sessions_send",
                    "sessionKey": f"{GATEWAY_SESSION_KEY}-wf-send-{uuid.uuid4().hex[:6]}",
                    "args": {"sessionKey": session_key, "message": message},
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
            data = await resp.json()

        if not data.get("ok"):
            return NodeResult(status="failed", error=f"sessions_send failed: {data}")

        wait_seconds = int(config.get("wait_seconds", 20))
        return NodeResult(
            status="running",
            output={
                "message_sent": True,
                "session_key": session_key,
                "wait_until": (datetime.now(timezone.utc).timestamp() + max(1, wait_seconds)),
            },
        )
    except Exception as e:
        return NodeResult(status="failed", error=str(e))

@register_node_checker("send_to_session")
async def _check_send_to_session(node_def, run, *, db, worker_manager):
    """Wait a short grace period after sending follow-up instructions."""
    node_id = node_def["id"]
    ns = (run.node_states or {}).get(node_id, {})
    output = ns.get("output", {}) if isinstance(ns, dict) else {}
    wait_until = output.get("wait_until")
    if not wait_until:
        return NodeResult(status="completed", output={"message_sent": True})
    if datetime.now(timezone.utc).timestamp() < float(wait_until):
        return None
    return NodeResult(status="completed", output={"message_sent": True, "wait_elapsed": True})

# ── tool_call ────────────────────────────────────────────────────────

@register_node("tool_call")
async def _exec_tool_call(config, context, run, *, db, worker_manager):
    """Execute a shell command directly (no LLM)."""
    command_template = config.get("command", "")
    timeout_seconds = config.get("timeout_seconds", 300)
    command = _render_template(command_template, context)

    if not command.strip():
        return NodeResult(status="failed", error="Empty command")

    try:
        import asyncio
        result = await asyncio.to_thread(
            subprocess.run,
            command, shell=True, capture_output=True, text=True, timeout=timeout_seconds,
        )
        output = {
            "stdout": result.stdout[-4000:] if result.stdout else "",
            "stderr": result.stderr[-4000:] if result.stderr else "",
            "returncode": result.returncode,
        }
        if result.returncode == 0:
            return NodeResult(status="completed", output=output)
        else:
            return NodeResult(status="failed", output=output, error=f"Command exited with code {result.returncode}", error_type="command_failed")
    except subprocess.TimeoutExpired:
        return NodeResult(status="failed", error="Command timed out", error_type="timeout")
    except Exception as e:
        return NodeResult(status="failed", error=str(e))

# ── branch ───────────────────────────────────────────────────────────

@register_node("branch")
async def _exec_branch(config, context, run, *, db, worker_manager):
    """Evaluate conditions and determine next node.

    Supports both simple conditions (legacy) and rich expressions with
    built-in functions. If a condition contains function calls (parentheses),
    it uses the async expression engine; otherwise falls back to simple eval.

    Config:
        conditions: list of {match: "expression", goto: "node_id"}
        default: str — fallback node_id if no condition matches

    Examples:
        - match: "task.agent == programmer"           (simple, legacy)
        - match: "numTasks('pending') > 0"            (function call)
        - match: "agentStatus('programmer') == 'idle'" (function call)
        - match: "workerCapacity() > 0 and numTasks('open') > 0" (compound)
    """
    conditions = config.get("conditions", [])
    default = config.get("default")

    for cond in conditions:
        match_expr = cond.get("match", "")
        goto = cond.get("goto")
        if not goto:
            continue

        # Use async expression engine if expression has function calls or complex syntax
        if "(" in match_expr:
            from app.orchestrator.workflow_functions import evaluate_condition_async
            matched = await evaluate_condition_async(match_expr, context, db, worker_manager)
        else:
            matched = _evaluate_condition(match_expr, context)

        if matched:
            return NodeResult(status="completed", output={"goto": goto, "matched_expr": match_expr})

    if default:
        return NodeResult(status="completed", output={"goto": default})

    return NodeResult(status="failed", error="No branch condition matched and no default")

# ── gate ─────────────────────────────────────────────────────────────

@register_node("gate")
async def _exec_gate(config, context, run, *, db, worker_manager):
    """Create an inbox item and wait for human approval."""
    from app.models import InboxItem

    prompt = _render_template(config.get("prompt", "Workflow requires approval"), context)

    item = InboxItem(
        id=str(uuid.uuid4()),
        title=f"[WORKFLOW GATE] {prompt[:80]}",
        content=f"Workflow run {run.id} is waiting for approval.\n\n{prompt}",
        is_read=False,
        summary=f"workflow_gate:{run.id}",
        modified_at=datetime.now(timezone.utc),
    )
    db.add(item)
    await db.commit()

    return NodeResult(status="running", output={"inbox_item_id": item.id, "gate_type": "human_approval"})

@register_node_checker("gate")
async def _check_gate(node_def, run, *, db, worker_manager):
    """Check if a gate has been approved (inbox item read = approved)."""
    from app.models import InboxItem

    node_id = node_def["id"]
    ns = (run.node_states or {}).get(node_id, {})
    output = ns.get("output", {})
    item_id = output.get("inbox_item_id")

    if not item_id:
        return NodeResult(status="failed", error="No inbox item to check")

    item = await db.get(InboxItem, item_id)
    if item and item.is_read:
        return NodeResult(status="completed", output={"approved": True})

    timeout_hours = node_def.get("config", {}).get("timeout_hours", 24)
    started = ns.get("started_at")
    if started:
        start_dt = datetime.fromisoformat(started)
        elapsed = (datetime.now(timezone.utc) - start_dt.replace(tzinfo=timezone.utc)).total_seconds() / 3600
        if elapsed > timeout_hours:
            auto_approve = node_def.get("config", {}).get("auto_approve_after")
            if auto_approve:
                return NodeResult(status="completed", output={"approved": True, "auto_approved": True})
            return NodeResult(status="failed", error="Gate timed out without approval")

    return None

# ── notify ───────────────────────────────────────────────────────────

@register_node("notify")
async def _exec_notify(config, context, run, *, db, worker_manager):
    """Send a notification. Supports internal, discord, and inbox channels."""
    channel = config.get("channel", "internal")
    message_template = config.get("message_template", "")
    message = _render_template(message_template, context)

    if channel == "internal":
        logger.info("[WORKFLOW NOTIFY] %s", message[:200])
        return NodeResult(status="completed", output={"notified": True, "channel": channel})

    if channel == "discord":
        # Create an inbox item so it surfaces to the user
        from app.models import InboxItem
        db.add(InboxItem(
            id=str(uuid.uuid4()),
            title=f"[Workflow] {message[:80]}",
            content=message,
            is_read=False,
            summary=f"workflow_notify:{run.id}",
            modified_at=datetime.now(timezone.utc),
        ))
        await db.commit()
        return NodeResult(status="completed", output={"notified": True, "channel": "discord", "surfaced_as": "inbox_item"})

    if channel == "inbox":
        from app.models import InboxItem
        db.add(InboxItem(
            id=str(uuid.uuid4()),
            title=config.get("title", f"[Workflow] Notification"),
            content=message,
            is_read=False,
            summary=f"workflow_notify:{run.id}",
            modified_at=datetime.now(timezone.utc),
        ))
        await db.commit()
        return NodeResult(status="completed", output={"notified": True, "channel": "inbox"})

    # Fallback: emit workflow event
    from app.models import WorkflowEvent
    db.add(WorkflowEvent(
        id=str(uuid.uuid4()),
        event_type="workflow.notification",
        payload={"channel": channel, "message": message, "run_id": run.id},
        source="workflow_executor",
    ))
    await db.commit()
    return NodeResult(status="completed", output={"notified": True, "channel": channel})

# ── cleanup ──────────────────────────────────────────────────────────

@register_node("cleanup")
async def _exec_cleanup(config, context, run, *, db, worker_manager):
    """Clean up sessions and artifacts.

    Config:
        session_ref: str — dotted path in context to the worker session key to delete
                          (e.g. "write_code.output.childSessionKey"). Preferred over
                          delete_session + run.session_key since run.session_key is
                          typically null for subagent workers.
        delete_session: bool — if True and no session_ref, fall back to run.session_key
        session_refs: list[str] — delete multiple sessions by context path (for parallel spawns)
    """
    handlers = NodeHandlers(db, worker_manager)
    deleted = []

    # Primary: delete by session_ref (dotted path to childSessionKey in context)
    session_ref = config.get("session_ref")
    if session_ref:
        session_key = _resolve_context_path(context, session_ref)
        if session_key and isinstance(session_key, str):
            await handlers.delete_session(session_key)
            deleted.append(session_key)
        else:
            logger.debug("[NODE:cleanup] session_ref '%s' resolved to nothing", session_ref)

    # Multiple refs (for parallel spawns)
    for ref in config.get("session_refs", []):
        session_key = _resolve_context_path(context, ref)
        if session_key and isinstance(session_key, str) and session_key not in deleted:
            await handlers.delete_session(session_key)
            deleted.append(session_key)

    # Fallback: delete run.session_key if explicitly requested and no ref was given
    if not deleted and config.get("delete_session", False) and run.session_key:
        await handlers.delete_session(run.session_key)
        deleted.append(run.session_key)

    return NodeResult(status="completed", output={"cleaned_up": True, "deleted_sessions": deleted})

# ── sub_workflow ─────────────────────────────────────────────────────

@register_node("sub_workflow")
async def _exec_sub_workflow(config, context, run, *, db, worker_manager):
    """Start a child workflow run and wait for it to complete.

    Config:
        workflow_id: str — ID or name of the workflow to run
        context_merge: dict — extra context to merge into the child run
    """
    workflow_id = config.get("workflow_id")
    if not workflow_id:
        return NodeResult(status="failed", error="No workflow_id specified for sub_workflow")

    from app.models import WorkflowDefinition
    from app.orchestrator.workflow_executor import WorkflowExecutor

    # Try by ID first, then by name
    wf = await db.get(WorkflowDefinition, workflow_id)
    if not wf:
        from sqlalchemy import select
        result = await db.execute(
            select(WorkflowDefinition).where(WorkflowDefinition.name == workflow_id).where(WorkflowDefinition.is_active == True)
        )
        wf = result.scalar_one_or_none()

    if not wf:
        return NodeResult(status="failed", error=f"Workflow '{workflow_id}' not found")

    # Merge parent context + any extra context
    child_context = dict(context)
    extra = config.get("context_merge", {})
    if isinstance(extra, dict):
        for k, v in extra.items():
            if isinstance(v, str) and "{" in v:
                child_context[k] = _render_template(v, context)
            else:
                child_context[k] = v

    executor = WorkflowExecutor(db, worker_manager=worker_manager)
    child_run = await executor.start_run(
        wf,
        trigger_type="sub_workflow",
        trigger_payload={"parent_run_id": run.id},
        initial_context=child_context,
    )

    return NodeResult(
        status="running",
        output={"child_run_id": child_run.id, "child_workflow": wf.name},
    )

@register_node_checker("sub_workflow")
async def _check_sub_workflow(node_def, run, *, db, worker_manager):
    """Check if the child workflow run has completed."""
    from app.models import WorkflowRun as WfRun

    node_id = node_def["id"]
    ns = (run.node_states or {}).get(node_id, {})
    output = ns.get("output", {})
    child_run_id = output.get("child_run_id")

    if not child_run_id:
        return NodeResult(status="failed", error="No child_run_id found")

    child_run = await db.get(WfRun, child_run_id)
    if not child_run:
        return NodeResult(status="failed", error=f"Child run {child_run_id} not found")

    if child_run.status in ("pending", "running"):
        return None  # Still going

    if child_run.status == "completed":
        return NodeResult(
            status="completed",
            output={
                "child_run_id": child_run_id,
                "child_status": "completed",
                "child_context": child_run.context or {},
            },
        )

    return NodeResult(
        status="failed",
        output={"child_run_id": child_run_id, "child_status": child_run.status},
        error=child_run.error or f"Child workflow {child_run.status}",
    )

# ── python_call ──────────────────────────────────────────────────────

@register_node("python_call")
async def _exec_python_call(config, context, run, *, db, worker_manager):
    """Execute a registered Python callable by name.

    Config:
        callable: "reflection_cycle.run_strategic"
        args_template: {key: "{context.path}"}
        poll: true — if result.completed is False, mark as "running"
    """
    callable_name = config.get("callable", "")
    args_template = config.get("args_template", {})
    is_poll = config.get("poll", False)

    handler = _PYTHON_CALL_REGISTRY.get(callable_name)
    if not handler:
        return NodeResult(status="failed", error=f"Unknown callable: {callable_name}")

    rendered_args = {}
    for k, v in args_template.items():
        if isinstance(v, str):
            rendered_args[k] = _render_template(v, context)
        else:
            rendered_args[k] = v

    try:
        result = await handler(db=db, worker_manager=worker_manager, context=context, **rendered_args)
        if isinstance(result, dict):
            if is_poll and result.get("completed") is False:
                return NodeResult(status="running", output=result)
            return NodeResult(status="completed", output=result)
        return NodeResult(status="completed", output={"result": str(result)})
    except Exception as e:
        logger.error("[NODE:python_call] %s failed: %s", callable_name, e, exc_info=True)
        return NodeResult(status="failed", error=str(e), error_type="python_error")

@register_node_checker("python_call")
async def _check_python_call(node_def, run, *, db, worker_manager):
    """Re-check a polling python_call node."""
    config = node_def.get("config", {})
    if not config.get("poll", False):
        return None

    callable_name = config.get("callable", "")
    handler = _PYTHON_CALL_REGISTRY.get(callable_name)
    if not handler:
        return NodeResult(status="failed", error=f"Unknown callable: {callable_name}")

    context = dict(run.context or {})
    try:
        result = await handler(db=db, worker_manager=worker_manager, context=context)
        if isinstance(result, dict):
            if result.get("completed") is False:
                return None
            return NodeResult(status="completed", output=result)
        return NodeResult(status="completed", output={"result": str(result)})
    except Exception as e:
        return NodeResult(status="failed", error=str(e), error_type="python_error")

# ── for_each ─────────────────────────────────────────────────────────

@register_node("for_each")
async def _exec_for_each(config, context, run, *, db, worker_manager):
    """Iterate over a context list and execute a node template for each item.

    Config:
        items_ref: "path.to.list" — dotted path to a list in context
        item_key: "item" — key name to inject each item as (default: "item")
        node_template: {type: "tool_call", config: {command: "echo {item}"}}
        collect_key: "results" — key to store collected results (default: "results")
    """
    items_ref = config.get("items_ref", "")
    item_key = config.get("item_key", "item")
    node_template = config.get("node_template")
    collect_key = config.get("collect_key", "results")

    items = _resolve_context_path(context, items_ref)
    if not isinstance(items, list):
        return NodeResult(status="completed", output={"items_processed": 0, "note": "items_ref did not resolve to a list"})

    if not node_template:
        return NodeResult(status="completed", output={"items": items, "count": len(items), "note": "no node_template provided"})

    # Execute the template node for each item sequentially
    results = []
    handlers = NodeHandlers(db, worker_manager)

    for i, item in enumerate(items):
        # Build a per-item context
        item_context = dict(context)
        item_context[item_key] = item
        item_context["_for_each_index"] = i

        # Create a virtual node def from the template
        virtual_node = {
            "id": f"for_each_{run.id}_{i}",
            "type": node_template.get("type", "tool_call"),
            "config": node_template.get("config", {}),
        }

        # Render any templates in the config with item context
        rendered_config = {}
        for k, v in virtual_node["config"].items():
            if isinstance(v, str):
                rendered_config[k] = _render_template(v, item_context)
            else:
                rendered_config[k] = v
        virtual_node["config"] = rendered_config

        # Create a temporary run-like object with item context
        class _VirtualRun:
            def __init__(self, ctx, node_states_dict, rid, sk):
                self.context = ctx
                self.node_states = node_states_dict
                self.id = rid
                self.session_key = sk

        virtual_run = _VirtualRun(item_context, {}, run.id, run.session_key)

        result = await handlers.execute(virtual_node, virtual_run)
        results.append({
            "index": i,
            "item": item if not isinstance(item, dict) else item,
            "status": result.status,
            "output": result.output,
            "error": result.error,
        })

        # If a sub-node fails, we still continue (collect all results)

    succeeded = sum(1 for r in results if r["status"] == "completed")
    return NodeResult(
        status="completed",
        output={
            "items_processed": len(items),
            "succeeded": succeeded,
            "failed": len(items) - succeeded,
            collect_key: results,
        },
    )

# ── http_request ─────────────────────────────────────────────────────

@register_node("http_request")
async def _exec_http_request(config, context, run, *, db, worker_manager):
    """Make an HTTP request and capture the response.

    Config:
        url: str — URL (supports template rendering)
        method: str — GET, POST, PUT, DELETE, PATCH (default: GET)
        headers: dict — optional headers
        body: dict|str — optional JSON body (for POST/PUT/PATCH)
        timeout_seconds: int — request timeout (default: 30)
        capture_body: bool — capture response body (default: true, max 10KB)
    """
    url = _render_template(config.get("url", ""), context)
    method = config.get("method", "GET").upper()
    headers = config.get("headers", {})
    body = config.get("body")
    timeout_seconds = config.get("timeout_seconds", 30)
    capture_body = config.get("capture_body", True)

    if not url:
        return NodeResult(status="failed", error="No URL provided")

    # Render header values
    rendered_headers = {}
    for k, v in headers.items():
        rendered_headers[k] = _render_template(str(v), context) if isinstance(v, str) else str(v)

    # Render body if string template
    json_body = None
    if body is not None:
        if isinstance(body, str):
            rendered = _render_template(body, context)
            try:
                json_body = json.loads(rendered)
            except json.JSONDecodeError:
                json_body = {"raw": rendered}
        elif isinstance(body, dict):
            # Render template values in body dict
            json_body = {}
            for k, v in body.items():
                if isinstance(v, str):
                    json_body[k] = _render_template(v, context)
                else:
                    json_body[k] = v

    try:
        async with aiohttp.ClientSession() as session:
            kwargs = {
                "headers": rendered_headers,
                "timeout": aiohttp.ClientTimeout(total=timeout_seconds),
            }
            if json_body and method in ("POST", "PUT", "PATCH"):
                kwargs["json"] = json_body

            async with session.request(method, url, **kwargs) as resp:
                status_code = resp.status
                resp_headers = dict(resp.headers)
                resp_body = ""
                if capture_body:
                    raw = await resp.read()
                    resp_body = raw[:10240].decode("utf-8", errors="replace")

        output = {
            "status_code": status_code,
            "headers": {k: v for k, v in resp_headers.items() if k.lower() in ("content-type", "location", "x-request-id")},
        }
        if capture_body:
            # Try to parse as JSON
            try:
                output["body"] = json.loads(resp_body)
            except (json.JSONDecodeError, ValueError):
                output["body_text"] = resp_body[:4000]

        if 200 <= status_code < 300:
            return NodeResult(status="completed", output=output)
        else:
            return NodeResult(status="failed", output=output, error=f"HTTP {status_code}", error_type="http_error")

    except asyncio.TimeoutError:
        return NodeResult(status="failed", error="HTTP request timed out", error_type="timeout")
    except Exception as e:
        return NodeResult(status="failed", error=str(e), error_type="http_error")

# ── transform ────────────────────────────────────────────────────────

@register_node("transform")
async def _exec_transform(config, context, run, *, db, worker_manager):
    """Transform context data using simple expressions.

    Config:
        mappings: dict — {output_key: "input.path"} or {output_key: {"expr": "..."}}
        template: str — optional Jinja-like template string to render
    """
    mappings = config.get("mappings", {})
    template = config.get("template")

    output = {}

    for out_key, source in mappings.items():
        if isinstance(source, str):
            # Simple path reference
            output[out_key] = _resolve_context_path(context, source)
        elif isinstance(source, dict):
            expr = source.get("expr", "")
            if expr == "len" and "of" in source:
                val = _resolve_context_path(context, source["of"])
                output[out_key] = len(val) if isinstance(val, (list, dict, str)) else 0
            elif expr == "join":
                val = _resolve_context_path(context, source.get("of", ""))
                sep = source.get("sep", ", ")
                output[out_key] = sep.join(str(v) for v in val) if isinstance(val, list) else str(val)
            elif expr == "filter":
                val = _resolve_context_path(context, source.get("of", ""))
                condition = source.get("where", "")
                if isinstance(val, list) and condition:
                    output[out_key] = [
                        item for item in val
                        if _evaluate_condition(condition, {**context, "item": item})
                    ]
                else:
                    output[out_key] = val
            elif expr == "default":
                val = _resolve_context_path(context, source.get("of", ""))
                output[out_key] = val if val is not None else source.get("value")
            elif expr == "concat":
                parts = source.get("parts", [])
                output[out_key] = "".join(
                    _render_template(p, context) if isinstance(p, str) else str(p)
                    for p in parts
                )
            else:
                # Unknown expr, try as template
                output[out_key] = _render_template(expr, context)

    if template:
        output["rendered"] = _render_template(template, context)

    return NodeResult(status="completed", output=output)

# ── parallel ─────────────────────────────────────────────────────────

@register_node("parallel")
async def _exec_parallel(config, context, run, *, db, worker_manager):
    """Run multiple node definitions concurrently and wait for all.

    Config:
        branches: list of {id: str, type: str, config: dict}
        fail_fast: bool — abort all on first failure (default: false)
    """
    branches = config.get("branches", [])
    fail_fast = config.get("fail_fast", False)

    if not branches:
        return NodeResult(status="completed", output={"branches": 0})

    handlers = NodeHandlers(db, worker_manager)

    async def run_branch(branch_def):
        node_def = {
            "id": branch_def.get("id", f"parallel_{uuid.uuid4().hex[:6]}"),
            "type": branch_def.get("type", "tool_call"),
            "config": branch_def.get("config", {}),
        }

        class _VirtualRun:
            def __init__(self):
                self.context = dict(context)
                self.node_states = {}
                self.id = run.id
                self.session_key = run.session_key

        return await handlers.execute(node_def, _VirtualRun())

    tasks = [run_branch(b) for b in branches]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    branch_results = []
    any_failed = False
    for i, (branch, result) in enumerate(zip(branches, results)):
        bid = branch.get("id", f"branch_{i}")
        if isinstance(result, Exception):
            branch_results.append({"id": bid, "status": "failed", "error": str(result)})
            any_failed = True
        else:
            branch_results.append({"id": bid, "status": result.status, "output": result.output, "error": result.error})
            if result.status == "failed":
                any_failed = True

    if fail_fast and any_failed:
        return NodeResult(
            status="failed",
            output={"branches": branch_results},
            error="One or more parallel branches failed (fail_fast=true)",
        )

    return NodeResult(
        status="completed",
        output={
            "branches": branch_results,
            "total": len(branches),
            "succeeded": sum(1 for r in branch_results if r["status"] == "completed"),
            "failed": sum(1 for r in branch_results if r["status"] == "failed"),
        },
    )

# ── delay ────────────────────────────────────────────────────────────

@register_node("expression")
async def _exec_expression(config, context, run, *, db, worker_manager):
    """Evaluate one or more expressions and store results in run context.

    This is the "eval" node — runs expressions using the function registry
    and feeds results into the context for downstream nodes to use.

    Config:
        expressions: dict — {output_key: "expression string"}
        goto_if: list — optional conditional routing based on results
            [{match: "result_key > 0", goto: "node_id"}]
        default: str — optional default next node

    Examples:
        expressions:
            pending_count: "numTasks('pending')"
            has_capacity: "workerCapacity() > 0"
            agent_ready: "agentStatus('programmer') == 'idle'"
        goto_if:
            - match: "has_capacity and pending_count > 0"
              goto: "spawn_worker"
        default: "wait_node"
    """
    from app.orchestrator.workflow_functions import evaluate_expression, evaluate_condition_async

    expressions = config.get("expressions", {})
    goto_if = config.get("goto_if", [])
    default = config.get("default")

    output = {}

    # Evaluate all expressions
    for key, expr in expressions.items():
        try:
            result = await evaluate_expression(expr, context, db, worker_manager)
            output[key] = result
        except Exception as e:
            logger.warning("[NODE:expression] Failed to evaluate '%s': %s", key, e)
            output[key] = None
            output[f"{key}_error"] = str(e)

    # Build a merged context for goto_if evaluation
    eval_context = {**context, **output}

    # Conditional routing
    if goto_if:
        for cond in goto_if:
            match_expr = cond.get("match", "")
            goto = cond.get("goto")
            if not goto:
                continue
            if "(" in match_expr:
                matched = await evaluate_condition_async(match_expr, eval_context, db, worker_manager)
            else:
                matched = _evaluate_condition(match_expr, eval_context)
            if matched:
                output["goto"] = goto
                output["matched_expr"] = match_expr
                return NodeResult(status="completed", output=output)

    if default:
        output["goto"] = default

    return NodeResult(status="completed", output=output)

@register_node("llm_route")
async def _exec_llm_route(config, context, run, *, db, worker_manager):
    """Use an LLM to decide which node to go to next.

    The LLM receives a prompt with context and a list of candidate nodes,
    and returns its choice. This enables dynamic, intelligent routing
    that goes beyond static conditions.

    Config:
        prompt_template: str — template describing the decision to make
        candidates: list — [{id: "node_id", description: "what this path does"}]
        model_tier: str — model tier to use (default: "micro")
        model: str — explicit model override
        context_keys: list[str] — which context keys to include in LLM prompt
        system_prompt: str — optional system prompt override
        temperature: float — sampling temperature (default: 0)

    Example:
        type: llm_route
        config:
            prompt_template: |
                Task: {task.title}
                Description: {task.notes}
                Agent: {task.agent}
                Current status: {task.status}

                Decide the best next step.
            candidates:
                - id: run_tests
                  description: "Run the test suite to validate changes"
                - id: skip_tests
                  description: "Skip tests — task is documentation-only"
                - id: needs_review
                  description: "Changes are risky, needs human review"
    """
    prompt_template = config.get("prompt_template", "")
    candidates = config.get("candidates", [])
    model_tier = config.get("model_tier", "micro")
    model = config.get("model")
    context_keys = config.get("context_keys")
    system_prompt = config.get("system_prompt")
    temperature = config.get("temperature", 0)

    if not candidates:
        return NodeResult(status="failed", error="No candidates provided for llm_route")

    # Build prompt
    rendered_prompt = _render_template(prompt_template, context)

    # Build context summary if context_keys specified
    context_summary = ""
    if context_keys:
        ctx_parts = []
        for key in context_keys:
            val = _resolve_context_path(context, key)
            if val is not None:
                ctx_parts.append(f"{key}: {json.dumps(val, default=str)[:500]}")
        context_summary = "\n".join(ctx_parts)

    # Build candidates description
    candidates_text = "\n".join(
        f"- **{c['id']}**: {c.get('description', 'No description')}"
        for c in candidates
    )
    candidate_ids = [c["id"] for c in candidates]

    full_prompt = f"""You are a workflow router. Based on the context below, choose exactly ONE of the candidate nodes to route to.

{f"Context:{chr(10)}{context_summary}{chr(10)}" if context_summary else ""}
{f"Situation:{chr(10)}{rendered_prompt}{chr(10)}" if rendered_prompt else ""}
Available routes:
{candidates_text}

Respond with ONLY the id of the chosen route. Nothing else."""

    if not system_prompt:
        system_prompt = "You are a precise workflow routing agent. Respond with only the chosen route id."

    # Resolve model
    resolved_model = model
    if not resolved_model and model_tier:
        resolved_model = await _resolve_model_tier(model_tier, "router", context, db)
    if not resolved_model:
        resolved_model = "lmstudio/qwen/qwen3.5-35b-a3b"  # Local first, haiku fallback via ModelChooser

    # Call LLM via Gateway
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                f"{GATEWAY_URL}/tools/invoke",
                headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                json={
                    "tool": "sessions_spawn",
                    "sessionKey": f"{GATEWAY_SESSION_KEY}-wf-llmroute-{uuid.uuid4().hex[:6]}",
                    "args": {
                        "task": full_prompt,
                        "mode": "run",
                        "model": resolved_model,
                        "runTimeoutSeconds": 30,
                        "cleanup": "delete",
                    },
                },
                timeout=aiohttp.ClientTimeout(total=60),
            )
            data = await resp.json()

        if not data.get("ok"):
            return NodeResult(status="failed", error=f"LLM route call failed: {data}")

        # Extract the response
        result_text = ""
        content = data.get("content", "")
        if isinstance(content, str):
            result_text = content.strip()
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    result_text += part.get("text", "")
            result_text = result_text.strip()
        elif isinstance(content, dict):
            result_text = content.get("text", str(content)).strip()

        # Parse the chosen route — find which candidate id appears in the response
        chosen = None
        result_lower = result_text.lower().strip()

        # Exact match first
        for cid in candidate_ids:
            if result_lower == cid.lower():
                chosen = cid
                break

        # Substring match if no exact
        if not chosen:
            for cid in candidate_ids:
                if cid.lower() in result_lower:
                    chosen = cid
                    break

        if not chosen:
            # Default to first candidate with a warning
            chosen = candidate_ids[0]
            logger.warning(
                "[NODE:llm_route] LLM response '%s' didn't match any candidate, defaulting to '%s'",
                result_text[:100], chosen,
            )

        return NodeResult(
            status="completed",
            output={
                "goto": chosen,
                "llm_response": result_text[:200],
                "model": resolved_model,
                "candidates": candidate_ids,
            },
        )
    except Exception as e:
        logger.error("[NODE:llm_route] Failed: %s", e, exc_info=True)
        # Fallback to first candidate on error
        fallback = candidate_ids[0] if candidate_ids else None
        if fallback:
            return NodeResult(
                status="completed",
                output={"goto": fallback, "error": str(e), "fallback": True},
            )
        return NodeResult(status="failed", error=str(e))

@register_node("delay")
async def _exec_delay(config, context, run, *, db, worker_manager):
    """Wait for a specified duration before proceeding.

    Config:
        seconds: int — duration in seconds
    """
    seconds = int(config.get("seconds", 60))
    wait_until = datetime.now(timezone.utc).timestamp() + seconds
    return NodeResult(
        status="running",
        output={"wait_until": wait_until, "delay_seconds": seconds},
    )

@register_node_checker("delay")
async def _check_delay(node_def, run, *, db, worker_manager):
    """Check if the delay period has elapsed."""
    node_id = node_def["id"]
    ns = (run.node_states or {}).get(node_id, {})
    output = ns.get("output", {})
    wait_until = output.get("wait_until")

    if not wait_until:
        return NodeResult(status="completed", output={"delayed": True})

    if datetime.now(timezone.utc).timestamp() >= float(wait_until):
        return NodeResult(status="completed", output={"delayed": True, "delay_seconds": output.get("delay_seconds", 0)})

    return None  # Still waiting

# ══════════════════════════════════════════════════════════════════════
# Python Call Registry — bridges workflow nodes to existing business logic
# ══════════════════════════════════════════════════════════════════════

async def _pcall_run_strategic_reflections(db, worker_manager, context, **kw):
    from app.orchestrator.reflection_cycle import ReflectionCycleManager
    mgr = ReflectionCycleManager(db, worker_manager)
    return await mgr.run_strategic_reflection_cycle()

async def _pcall_run_daily_compression(db, worker_manager, context, **kw):
    from app.orchestrator.reflection_cycle import ReflectionCycleManager
    mgr = ReflectionCycleManager(db, worker_manager)
    return await mgr.run_daily_compression()

async def _pcall_run_sweep(db, worker_manager, context, **kw):
    from app.orchestrator.sweep_arbitrator import SweepArbitrator
    arb = SweepArbitrator(db, worker_manager)
    return await arb.run_once()

async def _pcall_run_diagnostics(db, worker_manager, context, **kw):
    from app.orchestrator.diagnostic_triggers import DiagnosticTriggerEngine
    engine = DiagnosticTriggerEngine(db, worker_manager)
    return await engine.run_once()

async def _pcall_run_scheduled_events(db, worker_manager, context, **kw):
    from app.orchestrator.scheduler import EventScheduler
    sched = EventScheduler(db)
    return await sched.check_due_events()

async def _pcall_run_github_sync(db, worker_manager, context, **kw):
    from app.services.github_sync import GitHubSyncService
    svc = GitHubSyncService(db)
    return await svc.sync_all()

async def _pcall_run_memory_sync(db, worker_manager, context, **kw):
    from app.services.memory_sync import sync_agent_memories
    return await sync_agent_memories(db)

# Reflection workflow discrete steps
async def _pcall_list_agents(db, worker_manager, context, **kw):
    from app.orchestrator.workflow_reflection import list_execution_agents
    return await list_execution_agents(db, worker_manager, context, **kw)

async def _pcall_build_contexts(db, worker_manager, context, **kw):
    from app.orchestrator.workflow_reflection import build_context_packets
    return await build_context_packets(db, worker_manager, context, **kw)

async def _pcall_spawn_reflections(db, worker_manager, context, **kw):
    from app.orchestrator.workflow_reflection import spawn_reflection_agents
    return await spawn_reflection_agents(db, worker_manager, context, **kw)

async def _pcall_check_reflections(db, worker_manager, context, **kw):
    from app.orchestrator.workflow_reflection import check_reflections_complete
    return await check_reflections_complete(db, worker_manager, context, **kw)

async def _pcall_run_initiative_sweep(db, worker_manager, context, **kw):
    from app.orchestrator.workflow_reflection import run_initiative_sweep
    return await run_initiative_sweep(db, worker_manager, context, **kw)

async def _pcall_run_compression(db, worker_manager, context, **kw):
    from app.orchestrator.workflow_reflection import run_daily_compression
    return await run_daily_compression(db, worker_manager, context, **kw)

async def _pcall_assign_agent(db, worker_manager, context, **kw):
    from app.orchestrator.workflow_assignment import assign_agent
    return await assign_agent(db, worker_manager, context, **kw)

async def _pcall_scan_unassigned(db, worker_manager, context, **kw):
    from app.orchestrator.workflow_assignment import scan_unassigned
    return await scan_unassigned(db, worker_manager, context, **kw)

async def _pcall_inbox_process_threads(db, worker_manager, context, **kw):
    """Process inbox threads with user responses."""
    from app.orchestrator.inbox_processor import InboxProcessor
    processor = InboxProcessor(db)
    return await processor.process_threads()


async def _pcall_system_cleanup(db, worker_manager, context, **kw):
    """Clean up old workflow runs, worker history, and stale state."""
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select, and_
    from app.models import WorkflowRun, WorkerRun

    now = datetime.now(timezone.utc)
    results = {}

    # 1. Delete terminal workflow runs older than 7 days
    cutoff_7d = now - timedelta(days=7)
    old_runs_result = await db.execute(
        select(WorkflowRun).where(
            and_(
                WorkflowRun.status.in_(["completed", "failed", "cancelled"]),
                WorkflowRun.finished_at < cutoff_7d,
            )
        )
    )
    old_runs = old_runs_result.scalars().all()
    deleted_wf = 0
    for run in old_runs:
        await db.delete(run)
        deleted_wf += 1
    results["workflow_runs_deleted"] = deleted_wf

    # 2. Clear context/node_states payloads from completed runs older than 2 days
    cutoff_2d = now - timedelta(days=2)
    old_context_result = await db.execute(
        select(WorkflowRun).where(
            and_(
                WorkflowRun.status.in_(["completed", "failed", "cancelled"]),
                WorkflowRun.finished_at < cutoff_2d,
            )
        )
    )
    old_context_runs = old_context_result.scalars().all()
    cleared_context = 0
    for run in old_context_runs:
        ctx = run.context or {}
        if ctx and not ctx.get("_cleared"):
            run.context = {"_cleared": True, "cleared_at": now.isoformat()}
            run.node_states = {}
            cleared_context += 1
    results["workflow_contexts_cleared"] = cleared_context

    # 3. Delete worker run history older than 14 days
    cutoff_14d = now - timedelta(days=14)
    old_worker_result = await db.execute(
        select(WorkerRun).where(WorkerRun.started_at < cutoff_14d)
    )
    old_worker_runs = old_worker_result.scalars().all()
    deleted_wr = 0
    for wr in old_worker_runs:
        await db.delete(wr)
        deleted_wr += 1
    results["worker_runs_deleted"] = deleted_wr

    # 4. Mark runs stuck in "running" for > 8 hours as failed
    cutoff_8h = now - timedelta(hours=8)
    stale_result = await db.execute(
        select(WorkflowRun).where(
            and_(
                WorkflowRun.status == "running",
                WorkflowRun.updated_at < cutoff_8h,
            )
        )
    )
    stale_runs = stale_result.scalars().all()
    marked_failed = 0
    for run in stale_runs:
        run.status = "failed"
        run.error = "Cleanup: stuck in running for >8 hours"
        run.finished_at = now
        marked_failed += 1
    results["stale_runs_failed"] = marked_failed

    await db.commit()
    logger.info(
        "[CLEANUP] Done: %d wf runs deleted, %d contexts cleared, %d worker runs deleted, %d stale runs failed",
        deleted_wf, cleared_context, deleted_wr, marked_failed
    )
    return results

_PYTHON_CALL_REGISTRY: dict[str, Any] = {
    # Legacy monolithic callables
    "reflection_cycle.run_strategic": _pcall_run_strategic_reflections,
    "reflection_cycle.run_daily_compression": _pcall_run_daily_compression,
    "sweep.run_once": _pcall_run_sweep,
    "diagnostics.run_once": _pcall_run_diagnostics,
    "scheduler.fire_due_events": _pcall_run_scheduled_events,
    "github_sync.sync_all": _pcall_run_github_sync,
    "memory_sync.sync_all": _pcall_run_memory_sync,
    # Discrete reflection workflow steps
    "reflection.list_agents": _pcall_list_agents,
    "reflection.build_contexts": _pcall_build_contexts,
    "reflection.spawn_agents": _pcall_spawn_reflections,
    "reflection.check_complete": _pcall_check_reflections,
    "reflection.run_sweep": _pcall_run_initiative_sweep,
    "reflection.run_compression": _pcall_run_compression,
    # Agent assignment
    "assignment.assign_agent": _pcall_assign_agent,
    "assignment.scan_unassigned": _pcall_scan_unassigned,
    # Calendar
    "calendar.sync_google": lambda db=None, worker_manager=None, context=None, **kw: _pcall_integration("sync_google_calendar", db, worker_manager, context, **kw),
    "calendar.check_upcoming": lambda db=None, worker_manager=None, context=None, **kw: _pcall_integration("check_upcoming_events", db, worker_manager, context, **kw),
    # Email
    "email.check_inbox": lambda db=None, worker_manager=None, context=None, **kw: _pcall_integration("check_email_inbox", db, worker_manager, context, **kw),
    "email.send": lambda db=None, worker_manager=None, context=None, **kw: _pcall_integration("send_email", db, worker_manager, context, **kw),
    # Work Tracker
    "tracker.check_deadlines": lambda db=None, worker_manager=None, context=None, **kw: _pcall_integration("check_deadlines", db, worker_manager, context, **kw),
    "tracker.daily_summary": lambda db=None, worker_manager=None, context=None, **kw: _pcall_integration("daily_work_summary", db, worker_manager, context, **kw),
    # Learning
    "learning.check_due": lambda db=None, worker_manager=None, context=None, **kw: _pcall_learning("check_due_lessons", db, worker_manager, context, **kw),
    "learning.create_plan": lambda db=None, worker_manager=None, context=None, **kw: _pcall_learning("create_plan_from_request", db, worker_manager, context, **kw),
    # Inbox processing
    "inbox.process_threads": _pcall_inbox_process_threads,
    # System maintenance
    "system.cleanup": _pcall_system_cleanup,
}

def register_python_callable(name: str, handler: Callable):
    """Register a new Python callable for use in python_call nodes.

    Usage:
        register_python_callable("my_service.do_thing", my_handler_fn)
    """
    _PYTHON_CALL_REGISTRY[name] = handler

async def _pcall_integration(func_name: str, db, worker_manager, context, **kw):
    """Generic dispatcher for integration callables."""
    import app.orchestrator.workflow_integrations as integrations
    fn = getattr(integrations, func_name)
    return await fn(db, worker_manager, context, **kw)

async def _pcall_learning(func_name: str, db, worker_manager, context, **kw):
    """Dispatcher for learning service callables."""
    import app.services.learning_service as learning
    fn = getattr(learning, func_name)
    return await fn(db, worker_manager, context, **kw)

