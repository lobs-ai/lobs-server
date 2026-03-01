"""Auto-assign agents to unassigned active tasks.

Uses llm_direct for classification — direct API call, no agent spawn,
no workspace context, no tools. Much faster and cheaper than session-based approach.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task
from app.orchestrator.llm_direct import complete

logger = logging.getLogger(__name__)

ALLOWED_AGENTS = {"programmer", "writer", "reviewer", "researcher", "architect"}

SYSTEM_PROMPT = """You are an agent router for a multi-agent AI system. Given a task, output the correct agent as JSON. Output only JSON, no explanation outside the object.

Agents:
- programmer: Code, bugs, features, tests, refactors
- researcher: Research, analysis, investigation, information gathering
- writer: Docs, summaries, reports, written content
- architect: System design, technical strategy, architecture
- reviewer: Code review, quality checks, feedback"""

USER_TEMPLATE = """Task:
Title: {title}
Notes: {notes}
Project: {project_id}

JSON only: {{"agent": "<type>", "reason": "<brief>"}}"""


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
                chosen = await self._choose_agent(task)
                if chosen is None:
                    result.skipped += 1
                    continue
                if chosen not in ALLOWED_AGENTS:
                    logger.warning("[AUTO_ASSIGN] Invalid agent %s for task %s", chosen, task.id)
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

    async def _choose_agent(self, task: Task) -> Optional[str]:
        user_msg = USER_TEMPLATE.format(
            title=task.title or "Untitled",
            notes=(task.notes or "")[:800],
            project_id=task.project_id or "unknown",
        )

        output = await complete(
            system=SYSTEM_PROMPT,
            user=user_msg,
            max_tokens=100,
            temperature=0.0,
            timeout=20.0,
        )

        if not output:
            logger.warning("[AUTO_ASSIGN] No LLM response for task %s", task.id[:8])
            return None

        return _parse_agent(output)


def _parse_agent(text: str) -> Optional[str]:
    if not text:
        return None
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
            agent = parsed.get("agent", "").strip().lower()
            if agent in ALLOWED_AGENTS:
                return agent
    except (json.JSONDecodeError, AttributeError):
        pass
    for agent in ALLOWED_AGENTS:
        if agent in text.lower():
            return agent
    return None
