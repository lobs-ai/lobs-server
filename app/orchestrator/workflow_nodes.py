"""Workflow node type handlers.

Each node type (spawn_agent, tool_call, branch, etc.) has an execute()
and optionally a check() method for async operations.
"""

import asyncio
import json
import logging
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from app.orchestrator.config import GATEWAY_URL, GATEWAY_TOKEN, GATEWAY_SESSION_KEY

logger = logging.getLogger(__name__)


@dataclass
class NodeResult:
    """Result of executing or checking a workflow node."""
    status: str  # "completed" | "running" | "failed"
    output: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    error_type: str = ""
    session_key: Optional[str] = None


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


class NodeHandlers:
    """Registry of node-type handlers."""

    def __init__(self, db: Any, worker_manager: Any = None):
        self.db = db
        self.worker_manager = worker_manager

    async def execute(self, node_def: dict, run: Any) -> NodeResult:
        """Dispatch execution to the correct handler."""
        node_type = node_def.get("type", "")
        config = node_def.get("config", {})
        context = dict(run.context or {})

        handler = getattr(self, f"_exec_{node_type}", None)
        if handler is None:
            return NodeResult(status="failed", error=f"Unknown node type: {node_type}")

        try:
            return await handler(config, context, run)
        except Exception as e:
            logger.error("[NODE:%s] Execution error: %s", node_def.get("id"), e, exc_info=True)
            return NodeResult(status="failed", error=str(e))

    async def check(self, node_def: dict, run: Any) -> Optional[NodeResult]:
        """Check if an async node has completed.  Returns None if still running."""
        node_type = node_def.get("type", "")
        checker = getattr(self, f"_check_{node_type}", None)
        if checker is None:
            # Node types without a checker complete synchronously
            return None
        return await checker(node_def, run)

    async def delete_session(self, session_key: str) -> None:
        """Delete an OpenClaw session via Gateway."""
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"{GATEWAY_URL}/tools/invoke",
                    headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                    json={
                        "tool": "sessions_kill",
                        "sessionKey": f"{GATEWAY_SESSION_KEY}-wf-cleanup-{uuid.uuid4().hex[:6]}",
                        "args": {"sessionKey": session_key, "reason": "workflow cleanup"},
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                data = await resp.json()
                if data.get("ok"):
                    logger.info("[NODE] Deleted session %s", session_key)
                else:
                    logger.debug("[NODE] Session delete response: %s", data)
        except Exception as e:
            logger.debug("[NODE] Session delete failed: %s", e)

    async def _resolve_model_tier(self, tier: str, agent_type: str, context: dict) -> str | None:
        """Resolve a model tier name to an actual model using ModelChooser.

        If the tier is already a model id (contains '/' or is an alias like 'sonnet'),
        return it as-is. Otherwise use the full ModelChooser pipeline.
        """
        # If it looks like an explicit model or alias, pass through
        KNOWN_TIERS = {"micro", "small", "medium", "standard", "strong"}
        if tier not in KNOWN_TIERS:
            return tier  # Treat as explicit model/alias

        try:
            from app.orchestrator.model_chooser import ModelChooser
            chooser = ModelChooser(self.db, provider_health=None)
            task_ctx = context.get("task", {})
            if not isinstance(task_ctx, dict):
                task_ctx = {}
            # Inject model_tier into the task dict for decide_models
            task_for_chooser = {**task_ctx, "model_tier": tier}
            choice = await chooser.choose(agent_type=agent_type, task=task_for_chooser)
            return choice.model
        except Exception as e:
            logger.warning("[NODE] Model tier resolution failed for tier=%s: %s", tier, e)
            return None

    # ── Node Type Handlers ───────────────────────────────────────────

    async def _exec_spawn_agent(self, config: dict, context: dict, run: Any) -> NodeResult:
        """Spawn an agent via the WorkerManager, which handles:
        - Model selection (ModelChooser with fallback chain)
        - Prompt building (Prompter with learning enhancement)
        - Worker tracking (active_workers, project_locks)
        - Completion handling (task status, agent tracker, circuit breaker,
          git auto-commit, escalation, provider health)

        Config:
            agent_type: str — agent template id (programmer, researcher, writer, etc.)
            prompt_template: str — optional override template (if empty, WorkerManager builds prompt)
            model_tier: str — optional tier name (micro/small/medium/standard/strong)
            model: str — explicit model override (takes priority over model_tier)
            timeout_seconds: int — max session runtime (default 900)
        """
        agent_type = config.get("agent_type", "programmer")
        model_tier = config.get("model_tier")

        task_ctx = context.get("task", {})
        if not isinstance(task_ctx, dict):
            return NodeResult(status="failed", error="No task in workflow context")

        task_id = task_ctx.get("id")
        project_id = task_ctx.get("project_id")
        if not task_id or not project_id:
            return NodeResult(status="failed", error="Task missing id or project_id")

        # Apply model_tier to the task dict so ModelChooser picks it up
        task_for_spawn = dict(task_ctx)
        if model_tier:
            task_for_spawn["model_tier"] = model_tier
        elif config.get("model"):
            # Explicit model — store so worker can use it
            task_for_spawn["model_tier"] = config["model"]

        if not self.worker_manager:
            return NodeResult(status="failed", error="No worker_manager available — cannot spawn agent")

        try:
            spawned = await self.worker_manager.spawn_worker(
                task=task_for_spawn,
                project_id=project_id,
                agent_type=agent_type,
            )

            if not spawned:
                # Queued due to capacity/locks — not a hard failure
                return NodeResult(
                    status="running",
                    output={"queued": True, "reason": "capacity or project lock"},
                )

            # Find the worker info that was just created
            child_key = None
            run_id = None
            for wid, winfo in self.worker_manager.active_workers.items():
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

    async def _check_spawn_agent(self, node_def: dict, run: Any) -> Optional[NodeResult]:
        """Check if a spawned agent has completed.

        Since we now spawn via worker_manager, it handles all completion
        processing (task status, git commit, escalation, etc.) automatically.
        We just need to check if the worker is still active.
        """
        node_id = node_def["id"]
        ns = (run.node_states or {}).get(node_id, {})
        output = ns.get("output", {})

        # If queued (capacity/lock), just wait
        if output.get("queued"):
            return None  # Still waiting

        task_id = (run.context or {}).get("task", {}).get("id")
        if not task_id:
            return NodeResult(status="failed", error="No task_id in run context")

        if not self.worker_manager:
            return NodeResult(status="failed", error="No worker_manager — can't check status")

        # Check if the worker is still active
        worker_active = any(
            w.task_id == task_id
            for w in self.worker_manager.active_workers.values()
        )

        if worker_active:
            return None  # Still running — worker_manager handles everything

        # Worker is no longer active — it completed (success or failure).
        # worker_manager._handle_worker_completion already handled:
        # - Task status update (completed/failed)
        # - Agent tracker update
        # - Circuit breaker recording
        # - Git auto-commit and push
        # - Failure escalation
        # - Provider health recording

        # Check the task's final state to determine our result
        from app.models import Task
        db_task = await self.db.get(Task, task_id)
        if db_task and db_task.work_state == "completed":
            return NodeResult(
                status="completed",
                output={"task_status": "completed", "task_id": task_id},
            )
        elif db_task and db_task.work_state == "blocked":
            return NodeResult(
                status="failed",
                output={"task_status": "blocked", "task_id": task_id},
                error="Task blocked after worker failure",
            )
        else:
            # Task reverted to not_started or other state — worker failed
            return NodeResult(
                status="failed",
                output={"task_status": db_task.work_state if db_task else "unknown", "task_id": task_id},
                error=f"Worker completed but task state is {db_task.work_state if db_task else 'unknown'}",
            )

    async def _exec_send_to_session(self, config: dict, context: dict, run: Any) -> NodeResult:
        """Send a message to an existing session (for fix loops)."""
        session_ref = config.get("session_ref", "")
        message_template = config.get("message_template", "")

        # Resolve session key from context — try flat key first, then dot-path
        session_key = context.get(session_ref)
        if not session_key and "." in session_ref:
            # Navigate dot path: "run_code_task.session_key" → context["run_code_task"]["session_key"]
            parts = session_ref.split(".")
            val = context
            for p in parts:
                if isinstance(val, dict):
                    val = val.get(p)
                else:
                    val = None
                    break
            session_key = val
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

            return NodeResult(status="running", output={"message_sent": True, "session_key": session_key})

        except Exception as e:
            return NodeResult(status="failed", error=str(e))

    async def _check_send_to_session(self, node_def: dict, run: Any) -> Optional[NodeResult]:
        """Check if the session has responded after a send."""
        # Reuse spawn_agent checker — same pattern (wait for assistant response)
        return await self._check_spawn_agent(node_def, run)

    async def _exec_tool_call(self, config: dict, context: dict, run: Any) -> NodeResult:
        """Execute a shell command directly (no LLM)."""
        command_template = config.get("command", "")
        timeout_seconds = config.get("timeout_seconds", 300)
        command = _render_template(command_template, context)

        if not command.strip():
            return NodeResult(status="failed", error="Empty command")

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )

            output = {
                "stdout": result.stdout[-4000:] if result.stdout else "",
                "stderr": result.stderr[-4000:] if result.stderr else "",
                "returncode": result.returncode,
            }

            if result.returncode == 0:
                return NodeResult(status="completed", output=output)
            else:
                return NodeResult(
                    status="failed",
                    output=output,
                    error=f"Command exited with code {result.returncode}",
                    error_type="command_failed",
                )

        except subprocess.TimeoutExpired:
            return NodeResult(status="failed", error="Command timed out", error_type="timeout")
        except Exception as e:
            return NodeResult(status="failed", error=str(e))

    async def _exec_branch(self, config: dict, context: dict, run: Any) -> NodeResult:
        """Evaluate conditions and determine next node."""
        conditions = config.get("conditions", [])
        default = config.get("default")

        for cond in conditions:
            match_expr = cond.get("match", "")
            goto = cond.get("goto")
            if self._evaluate_condition(match_expr, context) and goto:
                return NodeResult(status="completed", output={"goto": goto})

        if default:
            return NodeResult(status="completed", output={"goto": default})

        return NodeResult(status="failed", error="No branch condition matched and no default")

    async def _exec_gate(self, config: dict, context: dict, run: Any) -> NodeResult:
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
        self.db.add(item)
        await self.db.commit()

        return NodeResult(status="running", output={"inbox_item_id": item.id, "gate_type": "human_approval"})

    async def _check_gate(self, node_def: dict, run: Any) -> Optional[NodeResult]:
        """Check if a gate has been approved (inbox item read = approved for now)."""
        from app.models import InboxItem

        node_id = node_def["id"]
        ns = (run.node_states or {}).get(node_id, {})
        output = ns.get("output", {})
        item_id = output.get("inbox_item_id")

        if not item_id:
            return NodeResult(status="failed", error="No inbox item to check")

        item = await self.db.get(InboxItem, item_id)
        if item and item.is_read:
            return NodeResult(status="completed", output={"approved": True})

        # Check timeout
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

        return None  # Still waiting

    async def _exec_notify(self, config: dict, context: dict, run: Any) -> NodeResult:
        """Send a notification."""
        channel = config.get("channel", "internal")
        message_template = config.get("message_template", "")
        message = _render_template(message_template, context)

        if channel == "internal":
            # Just log it
            logger.info("[WORKFLOW NOTIFY] %s", message[:200])
            return NodeResult(status="completed", output={"notified": True, "channel": channel})

        # For Discord/external, we'd integrate with messaging here
        # For now, emit a workflow event that can be picked up
        from app.models import WorkflowEvent
        self.db.add(WorkflowEvent(
            id=str(uuid.uuid4()),
            event_type="workflow.notification",
            payload={"channel": channel, "message": message, "run_id": run.id},
            source="workflow_executor",
        ))
        await self.db.commit()

        return NodeResult(status="completed", output={"notified": True, "channel": channel})

    async def _exec_cleanup(self, config: dict, context: dict, run: Any) -> NodeResult:
        """Clean up sessions and artifacts."""
        delete_session = config.get("delete_session", True)

        if delete_session and run.session_key:
            await self.delete_session(run.session_key)

        return NodeResult(status="completed", output={"cleaned_up": True})

    async def _exec_sub_workflow(self, config: dict, context: dict, run: Any) -> NodeResult:
        """Start a sub-workflow (placeholder — advanced feature)."""
        workflow_id = config.get("workflow_id")
        if not workflow_id:
            return NodeResult(status="failed", error="No workflow_id specified for sub_workflow")

        # For now, just mark as completed — full sub-workflow support is Phase 4
        return NodeResult(status="completed", output={"sub_workflow_id": workflow_id, "note": "sub_workflow not yet implemented"})

    async def _exec_python_call(self, config: dict, context: dict, run: Any) -> NodeResult:
        """Execute a registered Python callable by name.

        This is the bridge between workflow definitions and existing Python
        business logic (reflections, sweeps, diagnostics, compression, etc.).
        Instead of rewriting that logic, we call it as a workflow node.

        Config:
            callable: "reflection_cycle.run_strategic"  (dotted name → registry lookup)
            args_template: {key: "{context.path}"}  (optional, rendered from context)
            poll: true  — if result has completed=False, mark node as "running" for re-check
        """
        callable_name = config.get("callable", "")
        args_template = config.get("args_template", {})
        is_poll = config.get("poll", False)

        handler = _PYTHON_CALL_REGISTRY.get(callable_name)
        if not handler:
            return NodeResult(status="failed", error=f"Unknown callable: {callable_name}")

        # Render args from context
        rendered_args = {}
        for k, v in args_template.items():
            if isinstance(v, str):
                rendered_args[k] = _render_template(v, context)
            else:
                rendered_args[k] = v

        try:
            result = await handler(db=self.db, worker_manager=self.worker_manager, context=context, **rendered_args)
            if isinstance(result, dict):
                # Poll mode: if result says not completed, keep node in "running" for re-check
                if is_poll and result.get("completed") is False:
                    return NodeResult(status="running", output=result)
                return NodeResult(status="completed", output=result)
            return NodeResult(status="completed", output={"result": str(result)})
        except Exception as e:
            logger.error("[NODE:python_call] %s failed: %s", callable_name, e, exc_info=True)
            return NodeResult(status="failed", error=str(e), error_type="python_error")

    async def _check_python_call(self, node_def: dict, run: Any) -> Optional[NodeResult]:
        """Re-check a polling python_call node."""
        config = node_def.get("config", {})
        if not config.get("poll", False):
            return None  # Non-poll nodes complete synchronously

        callable_name = config.get("callable", "")
        handler = _PYTHON_CALL_REGISTRY.get(callable_name)
        if not handler:
            return NodeResult(status="failed", error=f"Unknown callable: {callable_name}")

        context = dict(run.context or {})
        try:
            result = await handler(db=self.db, worker_manager=self.worker_manager, context=context)
            if isinstance(result, dict):
                if result.get("completed") is False:
                    return None  # Still not done
                return NodeResult(status="completed", output=result)
            return NodeResult(status="completed", output={"result": str(result)})
        except Exception as e:
            return NodeResult(status="failed", error=str(e), error_type="python_error")

    async def _exec_for_each(self, config: dict, context: dict, run: Any) -> NodeResult:
        """Iterate over a context list and collect results.

        This is a synchronous fan-out — it runs sequentially for simplicity.
        For parallel fan-out, use multiple spawn_agent nodes.

        Config:
            items_ref: "context.path.to.list"
            node_template: {type: "spawn_agent", config: {..., prompt_template: "... {item} ..."}}
        """
        items_ref = config.get("items_ref", "")
        # Resolve items from context
        items = context
        for part in items_ref.split("."):
            if isinstance(items, dict):
                items = items.get(part, [])
            else:
                items = []
                break

        if not isinstance(items, list):
            return NodeResult(status="completed", output={"items_processed": 0, "note": "items_ref did not resolve to a list"})

        return NodeResult(status="completed", output={"items": items, "count": len(items)})

    @staticmethod
    def _evaluate_condition(expr: str, context: dict) -> bool:  # noqa: C901
        """Evaluate a simple condition expression."""
        # Support: "path.to.value == expected"
        # Support: "path.to.value != expected"
        # Support: "path.to.value" (truthiness)
        for op in ("!=", "=="):
            if op in expr:
                parts = expr.split(op, 1)
                path = parts[0].strip()
                expected = parts[1].strip().strip("'\"")

                value = context
                for p in path.split("."):
                    if isinstance(value, dict):
                        value = value.get(p)
                    else:
                        value = None
                        break

                if op == "==":
                    return str(value) == expected
                else:
                    return str(value) != expected

        # Truthiness check
        value = context
        for p in expr.strip().split("."):
            if isinstance(value, dict):
                value = value.get(p)
            else:
                return False
        return bool(value)


# ══════════════════════════════════════════════════════════════════════
# Python Call Registry — bridges workflow nodes to existing business logic
# ══════════════════════════════════════════════════════════════════════

async def _pcall_run_strategic_reflections(db, worker_manager, context, **kw):
    """Run the strategic reflection cycle for all agents (legacy monolithic)."""
    from app.orchestrator.reflection_cycle import ReflectionCycleManager
    mgr = ReflectionCycleManager(db, worker_manager)
    return await mgr.run_strategic_reflection_cycle()


async def _pcall_run_daily_compression(db, worker_manager, context, **kw):
    """Run daily identity compression across all agents."""
    from app.orchestrator.reflection_cycle import ReflectionCycleManager
    mgr = ReflectionCycleManager(db, worker_manager)
    return await mgr.run_daily_compression()


async def _pcall_run_sweep(db, worker_manager, context, **kw):
    """Run the initiative sweep arbitrator."""
    from app.orchestrator.sweep_arbitrator import SweepArbitrator
    arb = SweepArbitrator(db, worker_manager)
    return await arb.run_once()


async def _pcall_run_diagnostics(db, worker_manager, context, **kw):
    """Run the diagnostic trigger engine."""
    from app.orchestrator.diagnostic_triggers import DiagnosticTriggerEngine
    engine = DiagnosticTriggerEngine(db, worker_manager)
    return await engine.run_once()


async def _pcall_run_scheduled_events(db, worker_manager, context, **kw):
    """Fire due scheduled events (calendar → tasks)."""
    from app.orchestrator.scheduler import EventScheduler
    sched = EventScheduler(db)
    return await sched.fire_due_events()


async def _pcall_run_github_sync(db, worker_manager, context, **kw):
    """Sync GitHub issues/PRs for all tracked projects."""
    from app.services.github_sync import GitHubSyncService
    svc = GitHubSyncService(db)
    return await svc.sync_all()


async def _pcall_run_memory_sync(db, worker_manager, context, **kw):
    """Sync agent memory files to DB."""
    from app.services.memory_sync import MemorySyncService
    svc = MemorySyncService(db)
    return await svc.sync_all()


# ── Reflection workflow discrete steps ────────────────────────────────

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
    "calendar.sync_google": lambda db, wm, ctx, **kw: _pcall_integration("sync_google_calendar", db, wm, ctx, **kw),
    "calendar.check_upcoming": lambda db, wm, ctx, **kw: _pcall_integration("check_upcoming_events", db, wm, ctx, **kw),
    # Email
    "email.check_inbox": lambda db, wm, ctx, **kw: _pcall_integration("check_email_inbox", db, wm, ctx, **kw),
    "email.send": lambda db, wm, ctx, **kw: _pcall_integration("send_email", db, wm, ctx, **kw),
    # Work Tracker
    "tracker.check_deadlines": lambda db, wm, ctx, **kw: _pcall_integration("check_deadlines", db, wm, ctx, **kw),
    "tracker.daily_summary": lambda db, wm, ctx, **kw: _pcall_integration("daily_work_summary", db, wm, ctx, **kw),
}


async def _pcall_integration(func_name: str, db, worker_manager, context, **kw):
    """Generic dispatcher for integration callables."""
    import app.orchestrator.workflow_integrations as integrations
    fn = getattr(integrations, func_name)
    return await fn(db, worker_manager, context, **kw)
