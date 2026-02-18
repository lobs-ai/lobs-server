"""Reactive diagnostic triggers for stalls, failures, and idle drift."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentReflection, AgentStatus, Task
from app.orchestrator.context_packets import ContextPacketBuilder
from app.orchestrator.worker import WorkerManager

logger = logging.getLogger(__name__)


class DiagnosticTriggerEngine:
    """Detect trigger conditions and spawn small diagnostic sessions."""

    def __init__(self, db: AsyncSession, worker_manager: WorkerManager):
        self.db = db
        self.worker_manager = worker_manager
        self.packet_builder = ContextPacketBuilder(db)

    async def run_once(self) -> dict[str, int]:
        triggers = 0
        spawned = 0

        for trigger in await self._stalled_task_triggers():
            triggers += 1
            if await self._spawn_diagnostic(trigger):
                spawned += 1

        for trigger in await self._failure_pattern_triggers():
            triggers += 1
            if await self._spawn_diagnostic(trigger):
                spawned += 1

        for trigger in await self._idle_agent_triggers():
            triggers += 1
            if await self._spawn_diagnostic(trigger):
                spawned += 1

        return {"triggers": triggers, "spawned": spawned}

    async def _stalled_task_triggers(self) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        result = await self.db.execute(
            select(Task).where(Task.work_state == "in_progress", Task.updated_at <= cutoff)
        )
        tasks = result.scalars().all()
        out: list[dict[str, Any]] = []
        for task in tasks:
            out.append(
                {
                    "kind": "stalled_task",
                    "trigger_key": f"stalled:{task.id}",
                    "agent_type": (task.agent or "programmer"),
                    "task_id": task.id,
                    "details": {
                        "title": task.title,
                        "project_id": task.project_id,
                        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                    },
                }
            )
        return out

    async def _failure_pattern_triggers(self) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(Task).where(Task.retry_count >= 2, Task.work_state.in_(["blocked", "not_started"]))
        )
        tasks = result.scalars().all()
        out: list[dict[str, Any]] = []
        for task in tasks:
            out.append(
                {
                    "kind": "repeated_failure",
                    "trigger_key": f"failure:{task.id}",
                    "agent_type": (task.agent or "programmer"),
                    "task_id": task.id,
                    "details": {
                        "title": task.title,
                        "retry_count": task.retry_count,
                        "failure_reason": task.failure_reason,
                    },
                }
            )
        return out

    async def _idle_agent_triggers(self) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=8)
        result = await self.db.execute(
            select(AgentStatus).where(AgentStatus.last_active_at <= cutoff)
        )
        statuses = result.scalars().all()
        out: list[dict[str, Any]] = []
        for status in statuses:
            if (status.status or "").lower() in {"working", "busy", "active"}:
                continue
            out.append(
                {
                    "kind": "idle_drift",
                    "trigger_key": f"idle:{status.agent_type}",
                    "agent_type": status.agent_type,
                    "task_id": None,
                    "details": {
                        "last_active_at": status.last_active_at.isoformat() if status.last_active_at else None,
                        "activity": status.activity,
                    },
                }
            )
        return out

    async def _spawn_diagnostic(self, trigger: dict[str, Any]) -> bool:
        agent_type = trigger["agent_type"]
        trigger_key = trigger["trigger_key"]

        if await self._recent_duplicate_exists(agent_type, trigger_key):
            return False

        packet = await self.packet_builder.build_for_agent(agent_type, hours=2)
        context_packet = packet.to_dict()
        context_packet["trigger"] = trigger

        reflection = AgentReflection(
            id=str(uuid.uuid4()),
            agent_type=agent_type,
            reflection_type="diagnostic",
            status="pending",
            window_start=datetime.now(timezone.utc) - timedelta(hours=2),
            window_end=datetime.now(timezone.utc),
            context_packet=context_packet,
        )
        self.db.add(reflection)
        await self.db.commit()

        prompt = self._build_prompt(agent_type, reflection.id, context_packet)
        result, error = await self.worker_manager._spawn_session(
            task_prompt=prompt,
            agent_id=agent_type,
            model="anthropic/claude-haiku-4-5",
            label=f"diagnostic-{agent_type}",
        )

        if result:
            return True

        reflection.status = "failed"
        reflection.result = {"error": error or "spawn_failed"}
        reflection.completed_at = datetime.now(timezone.utc)
        await self.db.commit()
        logger.warning("[DIAGNOSTIC] Failed to spawn diagnostic for %s: %s", agent_type, error)
        return False

    async def _recent_duplicate_exists(self, agent_type: str, trigger_key: str) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        result = await self.db.execute(
            select(AgentReflection)
            .where(
                AgentReflection.agent_type == agent_type,
                AgentReflection.reflection_type == "diagnostic",
                AgentReflection.created_at >= cutoff,
            )
            .order_by(AgentReflection.created_at.desc())
            .limit(50)
        )
        rows = result.scalars().all()
        for row in rows:
            packet = row.context_packet if isinstance(row.context_packet, dict) else {}
            existing = ((packet.get("trigger") or {}).get("trigger_key"))
            if existing == trigger_key:
                return True
        return False

    @staticmethod
    def _build_prompt(agent_type: str, reflection_id: str, packet: dict[str, Any]) -> str:
        return f"""## Agent Diagnostic Mode

Agent: {agent_type}
Reflection record: {reflection_id}

Context packet JSON:
{packet}

Return STRICT JSON (no prose outside JSON):
{{
  "issue_summary": "...",
  "root_causes": ["..."],
  "recommended_actions": ["..."],
  "confidence": 0.0
}}
"""
