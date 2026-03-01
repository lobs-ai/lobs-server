"""Workflow python_call handlers for LLM-based agent assignment.

Two callables:
  - assignment.scan_unassigned: finds tasks without agents, emits events
  - assignment.assign_agent: uses LLM to pick the right agent for a task

Uses llm_direct for classification — no full agent spawn, no workspace context.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, WorkflowEvent
from app.orchestrator.llm_direct import complete

logger = logging.getLogger(__name__)

VALID_AGENTS = ["programmer", "researcher", "writer", "architect", "reviewer", "inbox-responder"]
ASSIGNABLE_AGENTS = ["programmer", "researcher", "writer", "architect", "reviewer"]

ASSIGNMENT_SYSTEM = """You are an agent router for a multi-agent AI system. Given a task title and notes, return the correct agent type as JSON. Be concise — only output the JSON object, nothing else.

Available agents:
- programmer: Write/fix/refactor code, implement features, run tests. Use for anything requiring code changes.
- researcher: Investigate topics, compare options, analyze, synthesize findings.
- writer: Documentation, summaries, reports, written content.
- architect: System design, technical strategy, architecture planning.
- reviewer: Code review, quality checks, feedback on existing work."""

ASSIGNMENT_USER_TEMPLATE = """Task to assign:
Title: {title}
Notes: {notes}
Project: {project_id}

Respond ONLY with JSON: {{"agent": "<agent_type>", "reasoning": "<one sentence>"}}"""


async def scan_unassigned(db: AsyncSession, worker_manager: Any, context: dict, **kw) -> dict:
    """Find active tasks without assigned agents and emit assignment events."""
    result = await db.execute(
        select(Task).where(
            Task.status.in_(["todo", "active", "inbox"]),
            Task.work_state.in_(["not_started", None]),
            Task.agent.is_(None),
        ).limit(10)
    )
    tasks = result.scalars().all()

    existing = await db.execute(
        select(WorkflowEvent).where(
            WorkflowEvent.event_type == "task.needs_assignment",
            WorkflowEvent.processed == False,
        )
    )
    pending_task_ids = {
        (e.payload or {}).get("task_id")
        for e in existing.scalars().all()
    }

    emitted = 0
    for task in tasks:
        if task.id in pending_task_ids:
            continue
        db.add(WorkflowEvent(
            id=str(uuid.uuid4()),
            event_type="task.needs_assignment",
            payload={
                "task_id": task.id,
                "title": task.title,
                "notes": (task.notes or "")[:500],
                "project_id": task.project_id,
            },
            source="scan_unassigned",
        ))
        emitted += 1

    if emitted:
        await db.commit()
        logger.info("[ASSIGNMENT] Emitted %d assignment events", emitted)

    return {"scanned": len(tasks), "emitted": emitted}


async def assign_agent(db: AsyncSession, worker_manager: Any, context: dict, **kw) -> dict:
    """Use a direct LLM call to assign the correct agent to a task."""
    task_id = kw.get("task_id") or context.get("trigger", {}).get("task_id")
    if not task_id:
        return {"assigned": False, "reason": "no task_id", "task_title": "unknown"}

    task = await db.get(Task, task_id)
    if not task:
        return {"assigned": False, "reason": "task not found", "task_title": "unknown"}

    if task.agent:
        return {"assigned": True, "agent": task.agent, "reason": "already assigned", "task_title": task.title}

    user_msg = ASSIGNMENT_USER_TEMPLATE.format(
        title=task.title or "Untitled",
        notes=(task.notes or "")[:1000],
        project_id=task.project_id or "unknown",
    )

    try:
        output = await complete(
            system=ASSIGNMENT_SYSTEM,
            user=user_msg,
            max_tokens=128,
            temperature=0.0,
            timeout=25.0,
        )
    except Exception as e:
        logger.error("[ASSIGNMENT] LLM call failed for %s: %s", task_id[:8], e)
        return {"assigned": False, "reason": str(e), "task_title": task.title}

    if not output:
        logger.warning("[ASSIGNMENT] No LLM response for task %s", task_id[:8])
        return {"assigned": False, "reason": "no LLM response", "task_title": task.title}

    logger.info("[ASSIGNMENT] LLM response for %s: %s", task_id[:8], output[:200])

    agent_type = _parse_agent_response(output)
    if not agent_type:
        logger.warning("[ASSIGNMENT] Could not parse agent from: %s", output[:300])
        return {"assigned": False, "reason": "unparseable response", "task_title": task.title, "raw": output[:300]}

    task.agent = agent_type
    task.updated_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info("[ASSIGNMENT] Assigned '%s' to task %s (%s)", agent_type, task_id[:8], task.title[:50])
    return {"assigned": True, "agent": agent_type, "task_title": task.title, "task_id": task.id}


def _parse_agent_response(text: str) -> str | None:
    if not text:
        return None
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
            agent = parsed.get("agent", "").strip().lower()
            if agent in ASSIGNABLE_AGENTS:
                return agent
    except (json.JSONDecodeError, AttributeError):
        pass
    text_lower = text.lower()
    for agent in ASSIGNABLE_AGENTS:
        if agent in text_lower:
            return agent
    return None
