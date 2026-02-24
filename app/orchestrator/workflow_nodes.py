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

    # ── Node Type Handlers ───────────────────────────────────────────

    async def _exec_spawn_agent(self, config: dict, context: dict, run: Any) -> NodeResult:
        """Spawn an OpenClaw agent session."""
        agent_type = config.get("agent_type", "programmer")
        prompt_template = config.get("prompt_template", "")
        model_tier = config.get("model_tier")
        prompt = _render_template(prompt_template, context)

        if not prompt.strip():
            return NodeResult(status="failed", error="Empty prompt after template rendering")

        # Use Gateway API to spawn
        label = f"wf-{run.id[:8]}"
        model = model_tier or "sonnet"  # Default

        try:
            async with aiohttp.ClientSession() as session:
                parent_key = f"{GATEWAY_SESSION_KEY}-wf-spawn-{uuid.uuid4().hex[:6]}"
                resp = await session.post(
                    f"{GATEWAY_URL}/tools/invoke",
                    headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                    json={
                        "tool": "sessions_spawn",
                        "sessionKey": parent_key,
                        "args": {
                            "task": prompt,
                            "agentId": agent_type,
                            "model": model,
                            "runTimeoutSeconds": config.get("timeout_seconds", 900),
                            "cleanup": "keep",
                            "label": label,
                        },
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                )
                data = await resp.json()

            if not data.get("ok"):
                return NodeResult(status="failed", error=f"Spawn failed: {data}", error_type="spawn_error")

            result = data.get("result", {})
            details = result.get("details", result)
            if details.get("status") != "accepted":
                return NodeResult(status="failed", error=f"Spawn not accepted: {details}", error_type="spawn_error")

            child_key = details["childSessionKey"]
            run_id = details["runId"]

            return NodeResult(
                status="running",
                output={"runId": run_id, "childSessionKey": child_key},
                session_key=child_key,
            )

        except Exception as e:
            return NodeResult(status="failed", error=str(e), error_type="spawn_error")

    async def _check_spawn_agent(self, node_def: dict, run: Any) -> Optional[NodeResult]:
        """Check if a spawned agent session has completed."""
        node_id = node_def["id"]
        ns = (run.node_states or {}).get(node_id, {})
        output = ns.get("output", {})
        session_key = output.get("childSessionKey")

        if not session_key:
            return NodeResult(status="failed", error="No session key to check")

        try:
            # Check via sessions_history
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"{GATEWAY_URL}/tools/invoke",
                    headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                    json={
                        "tool": "sessions_history",
                        "sessionKey": f"{GATEWAY_SESSION_KEY}-wf-check-{uuid.uuid4().hex[:6]}",
                        "args": {"sessionKey": session_key, "limit": 3, "includeTools": False},
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                data = await resp.json()

            if not data.get("ok"):
                # Session might be gone (completed and cleaned up)
                return NodeResult(status="completed", output={"session_result": "session not found"})

            result = data.get("result", {})
            details = result.get("details", result)
            messages = details.get("messages", [])

            # Check for assistant responses
            for msg in reversed(messages):
                if msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        content = "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
                    if content:
                        return NodeResult(
                            status="completed",
                            output={"session_result": content[:8000], "session_key": session_key},
                        )

            # Also check if session is still in sessions_list
            resp2 = await aiohttp.ClientSession().post(
                f"{GATEWAY_URL}/tools/invoke",
                headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                json={
                    "tool": "sessions_list",
                    "sessionKey": f"{GATEWAY_SESSION_KEY}-wf-list-{uuid.uuid4().hex[:6]}",
                    "args": {"limit": 50, "messageLimit": 0},
                },
                timeout=aiohttp.ClientTimeout(total=10),
            )
            list_data = await resp2.json()
            if list_data.get("ok"):
                sessions = list_data.get("result", {}).get("details", {}).get("sessions", [])
                found = any(s.get("key") == session_key for s in sessions)
                if not found:
                    # Session gone — treat as completed
                    return NodeResult(status="completed", output={"session_result": "(session ended)"})

            return None  # Still running

        except Exception as e:
            logger.debug("[NODE] Check spawn_agent error: %s", e)
            return None

    async def _exec_send_to_session(self, config: dict, context: dict, run: Any) -> NodeResult:
        """Send a message to an existing session (for fix loops)."""
        session_ref = config.get("session_ref", "")
        message_template = config.get("message_template", "")

        # Resolve session key from context
        session_key = context.get(session_ref)
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

    @staticmethod
    def _evaluate_condition(expr: str, context: dict) -> bool:
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
