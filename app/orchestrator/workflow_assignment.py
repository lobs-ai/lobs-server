"""Workflow python_call handlers for LLM-based agent assignment.

Two callables:
  - assignment.scan_unassigned: finds tasks without agents, emits events
  - assignment.assign_agent: uses LLM to pick the right agent for a task
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, WorkflowEvent, OrchestratorSetting
from app.orchestrator.config import GATEWAY_URL, GATEWAY_TOKEN, GATEWAY_SESSION_KEY

logger = logging.getLogger(__name__)

# Valid agent types that can be assigned
VALID_AGENTS = ["programmer", "researcher", "writer", "architect", "reviewer", "inbox-responder"]

ASSIGNMENT_PROMPT = """You are an agent router for a multi-agent system. Given a task, determine which specialist agent should handle it.

Available agents:
- **programmer**: Write code, fix bugs, implement features, run tests, refactor. Use for anything that involves writing or modifying code.
- **researcher**: Investigate topics, compare options, analyze information, synthesize findings. Use for research, analysis, and information gathering.
- **writer**: Create documentation, write-ups, summaries, reports, content. Use for prose, docs, and written deliverables.
- **architect**: System design, technical strategy, design docs, architecture planning. Use for high-level design decisions and technical planning.
- **reviewer**: Code review, quality checks, feedback on existing work. Use for reviewing PRs, code quality, and providing feedback.
- **inbox-responder**: Quick responses, triage, simple actions. Use for lightweight tasks that need a fast response.

Task to assign:
- Title: {title}
- Notes: {notes}
- Project: {project_id}

Respond with ONLY a JSON object:
{{"agent": "<agent_type>", "reasoning": "<brief explanation>"}}
"""


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

    emitted = 0
    for task in tasks:
        # Check if we already have a pending event for this task
        existing = await db.execute(
            select(WorkflowEvent).where(
                WorkflowEvent.event_type == "task.needs_assignment",
                WorkflowEvent.processed == False,
            )
        )
        existing_events = existing.scalars().all()
        already_pending = any(
            (e.payload or {}).get("task_id") == task.id
            for e in existing_events
        )
        if already_pending:
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
        logger.info("[ASSIGNMENT] Emitted %d assignment events for unassigned tasks", emitted)

    return {"scanned": len(tasks), "emitted": emitted}


async def assign_agent(db: AsyncSession, worker_manager: Any, context: dict, **kw) -> dict:
    """Use an LLM to assign the correct agent to a task."""
    task_id = kw.get("task_id") or context.get("trigger", {}).get("task_id")
    if not task_id:
        return {"assigned": False, "reason": "no task_id provided", "task_title": "unknown"}

    task = await db.get(Task, task_id)
    if not task:
        return {"assigned": False, "reason": "task not found", "task_title": "unknown"}

    if task.agent:
        return {"assigned": True, "agent": task.agent, "reason": "already assigned", "task_title": task.title}

    # Build the prompt
    prompt = ASSIGNMENT_PROMPT.format(
        title=task.title or "Untitled",
        notes=(task.notes or "")[:2000],
        project_id=task.project_id or "unknown",
    )

    # Call LLM via Gateway sessions_spawn (one-shot)
    try:
        async with aiohttp.ClientSession() as session:
            parent_key = f"{GATEWAY_SESSION_KEY}-assign-{uuid.uuid4().hex[:6]}"
            resp = await session.post(
                f"{GATEWAY_URL}/tools/invoke",
                headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                json={
                    "tool": "sessions_spawn",
                    "sessionKey": parent_key,
                    "args": {
                        "task": prompt,
                        "mode": "run",
                        "model": "haiku",  # Cheap + fast for routing decisions
                        "runTimeoutSeconds": 60,
                        "cleanup": "delete",
                    },
                },
                timeout=aiohttp.ClientTimeout(total=90),
            )
            data = await resp.json()

        if not data.get("ok"):
            logger.warning("[ASSIGNMENT] LLM spawn failed: %s", data)
            return {"assigned": False, "reason": f"LLM spawn failed: {data}", "task_title": task.title}

        # Extract the result
        result = data.get("result", {})
        details = result.get("details", result)
        output = details.get("output", "")

        # Parse JSON from LLM response
        agent_type = _parse_agent_response(output)
        if not agent_type:
            logger.warning("[ASSIGNMENT] Could not parse agent from LLM response: %s", output[:200])
            return {"assigned": False, "reason": f"unparseable LLM response", "task_title": task.title}

        # Apply the assignment
        task.agent = agent_type
        task.updated_at = datetime.now(timezone.utc)
        await db.commit()

        logger.info("[ASSIGNMENT] Assigned agent '%s' to task %s (%s)", agent_type, task.id[:8], task.title[:50])
        return {"assigned": True, "agent": agent_type, "task_title": task.title, "task_id": task.id}

    except Exception as e:
        logger.error("[ASSIGNMENT] Error assigning agent: %s", e, exc_info=True)
        return {"assigned": False, "reason": str(e), "task_title": task.title}


def _parse_agent_response(text: str) -> str | None:
    """Extract agent type from LLM JSON response."""
    if not text:
        return None

    # Try to find JSON in the response
    try:
        # Look for JSON object
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
            agent = parsed.get("agent", "").strip().lower()
            if agent in VALID_AGENTS:
                return agent
    except (json.JSONDecodeError, AttributeError):
        pass

    # Fallback: look for agent name directly in text
    text_lower = text.lower()
    for agent in VALID_AGENTS:
        if agent in text_lower:
            return agent

    return None
