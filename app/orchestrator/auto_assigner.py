"""Auto-assign agents to unassigned active tasks.

Rationale:
- We require an explicit agent assignment for execution.
- Some task creation paths (clients, imports) may create tasks with agent=NULL.
- This routine detects those tasks and uses an LLM classification pass to choose
  an agent (programmer/writer/reviewer/researcher/architect).

Design:
- Runs periodically from the orchestrator engine.
- Uses OpenClaw Gateway tools/invoke:
  - sessions_spawn (agentTurn) to run a short classification prompt
  - sessions_history to retrieve the result
- Updates Task.agent in DB for tasks it can classify confidently.

Note: This is best-effort; failures should not block the orchestrator loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task
from app.orchestrator.config import GATEWAY_SESSION_KEY, GATEWAY_TOKEN, GATEWAY_URL

logger = logging.getLogger(__name__)


ALLOWED_AGENTS = {"programmer", "writer", "reviewer", "researcher", "architect"}


@dataclass
class AutoAssignResult:
    scanned: int = 0
    assigned: int = 0
    skipped: int = 0
    failed: int = 0


class TaskAutoAssigner:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run_once(self, limit: int = 20) -> AutoAssignResult:
        result = AutoAssignResult()

        q = await self.db.execute(
            select(Task)
            .where(Task.status == "active")
            .where(Task.agent.is_(None))
            .order_by(Task.created_at.asc())
            .limit(limit)
        )
        tasks = q.scalars().all()
        result.scanned = len(tasks)

        for task in tasks:
            try:
                chosen = await self._choose_agent_llm(task)
                if chosen is None:
                    result.skipped += 1
                    continue
                if chosen not in ALLOWED_AGENTS:
                    logger.warning("[AUTO_ASSIGN] Invalid agent choice %s for task %s", chosen, task.id)
                    result.failed += 1
                    continue

                task.agent = chosen
                task.updated_at = datetime.now(timezone.utc)
                await self.db.commit()
                result.assigned += 1
                logger.info("[AUTO_ASSIGN] task=%s agent=%s", task.id, chosen)
            except Exception as e:
                await self.db.rollback()
                result.failed += 1
                logger.error("[AUTO_ASSIGN] Failed for task=%s: %s", task.id, e, exc_info=True)

        return result

    async def _choose_agent_llm(self, task: Task) -> Optional[str]:
        """Return the chosen agent type or None."""

        prompt = self._build_prompt(task)

        # Use a non-programming agent persona for classification; we only need a short JSON.
        agent_id = "reviewer"
        model = "anthropic/claude-haiku-4-5"
        label = f"auto-assign-{task.id[:8].lower()}"

        spawn = await _gateway_invoke(
            tool="sessions_spawn",
            session_key=f"{GATEWAY_SESSION_KEY}-autoassign-{uuid.uuid4().hex[:8]}",
            args={
                "task": prompt,
                "agentId": agent_id,
                "model": model,
                "runTimeoutSeconds": 60,
                # Important: give Gateway more time than the default 10s so we reliably
                # receive the accepted response, especially on cold starts.
                "timeoutSeconds": 30,
                "cleanup": "delete",
                "label": label,
            },
        )

        # If Gateway timed out but still returned a childSessionKey, we can still
        # recover by reading history from that child session.
        child_session_key = (spawn or {}).get("childSessionKey")
        if not child_session_key:
            logger.warning("[AUTO_ASSIGN] sessions_spawn missing childSessionKey for task=%s: %s", task.id, spawn)
            return None
        if not child_session_key:
            return None

        # Poll the session history briefly for the model output.
        # Keep this short so the orchestrator loop stays responsive.
        for _ in range(8):
            await asyncio.sleep(1)
            hist = await _gateway_invoke(
                tool="sessions_history",
                session_key=f"{GATEWAY_SESSION_KEY}-autoassign-hist-{uuid.uuid4().hex[:8]}",
                args={
                    "sessionKey": child_session_key,
                    "limit": 20,
                    "includeTools": False,
                    "timeoutSeconds": 20,
                },
            )
            chosen = _extract_choice_from_history(hist)
            if chosen:
                return chosen

        return None

    @staticmethod
    def _build_prompt(task: Task) -> str:
        title = (task.title or "").strip()
        notes = (task.notes or "").strip()
        project = (task.project_id or "").strip()

        return (
            "You are a routing classifier for a multi-agent system.\n\n"
            "Choose exactly ONE agent type from: programmer, writer, reviewer, researcher, architect.\n"
            "Return STRICT JSON only, no prose.\n\n"
            "Decision rules (high level):\n"
            "- programmer: code changes, debugging, tests, build failures, performance fixes\n"
            "- writer: docs, summaries, handoffs, user-facing writeups\n"
            "- reviewer: code review, risk assessment, QA plans, verification strategy\n"
            "- researcher: external research, comparisons, investigations, options analysis\n"
            "- architect: system design, refactors, API design, data model design\n\n"
            "Task context:\n"
            f"- project_id: {project}\n"
            f"- title: {title}\n"
            f"- notes: {notes}\n\n"
            "JSON schema:\n"
            "{\n"
            "  \"agent\": \"programmer|writer|reviewer|researcher|architect\",\n"
            "  \"confidence\": 0.0,\n"
            "  \"reason\": \"short\"\n"
            "}\n"
        )


async def _gateway_invoke(tool: str, session_key: str, args: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Invoke an OpenClaw tool via the Gateway HTTP API.

    Returns the tool details dict when ok, else None.
    """

    if not GATEWAY_URL or not GATEWAY_TOKEN:
        return None

    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                f"{GATEWAY_URL}/tools/invoke",
                headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                json={"tool": tool, "sessionKey": session_key, "args": args},
                timeout=aiohttp.ClientTimeout(total=30),
            )
            data = await resp.json()
            if not data.get("ok"):
                return None
            result = data.get("result", {})
            # Gateway wraps tool results in {content, details}
            return result.get("details", result)
    except Exception:
        logger.exception("[AUTO_ASSIGN] gateway invoke failed tool=%s", tool)
        return None


def _extract_choice_from_history(history_details: Optional[dict[str, Any]]) -> Optional[str]:
    """Parse sessions_history output and return agent if present."""
    if not history_details:
        return None

    # sessions_history returns: {messages:[{role,content,...}, ...]}
    messages = history_details.get("messages") if isinstance(history_details, dict) else None
    if not isinstance(messages, list):
        return None

    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            continue

        text = content.strip()
        # Attempt to parse JSON.
        try:
            payload = json.loads(text)
        except Exception:
            # Some models may wrap in ```json ...```
            if "```" in text:
                cleaned = text
                cleaned = cleaned.replace("```json", "```")
                if cleaned.startswith("```") and cleaned.endswith("```"):
                    cleaned = cleaned.strip("`").strip()
                try:
                    payload = json.loads(cleaned)
                except Exception:
                    continue
            else:
                continue

        agent = payload.get("agent") if isinstance(payload, dict) else None
        if isinstance(agent, str) and agent.strip():
            return agent.strip()

    return None
