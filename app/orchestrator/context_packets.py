"""Server-side context packet builder for reflection and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentInitiative, AgentReflection, Task, WorkerRun


@dataclass
class AgentContextPacket:
    schema_version: str
    packet_type: str
    agent_type: str
    generated_at: str
    recent_tasks_summary: list[dict[str, Any]]
    active_initiatives: list[dict[str, Any]]
    recent_initiative_decisions: list[dict[str, Any]]
    backlog_summary: list[dict[str, Any]]
    performance_metrics: dict[str, Any]
    other_agent_activity_summary: list[dict[str, Any]]
    repo_change_summary: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ContextPacketBuilder:
    """Build bounded, deterministic packets to avoid context bloat."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def build_for_agent(self, agent_type: str, *, hours: int = 6) -> AgentContextPacket:
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=hours)

        recent_tasks = await self._recent_tasks(agent_type, since)
        backlog = await self._backlog(agent_type)
        metrics = await self._performance_metrics(agent_type, since)
        other_activity = await self._other_agent_activity(agent_type, since)
        initiatives = await self._active_initiatives(agent_type, since)
        decisions = await self._recent_initiative_decisions(agent_type)

        return AgentContextPacket(
            schema_version="agent-context-packet.v1",
            packet_type="strategic_reflection_input",
            agent_type=agent_type,
            generated_at=now.isoformat(),
            recent_tasks_summary=recent_tasks,
            active_initiatives=initiatives,
            recent_initiative_decisions=decisions,
            backlog_summary=backlog,
            performance_metrics=metrics,
            other_agent_activity_summary=other_activity,
            repo_change_summary=[],
        )

    async def _recent_tasks(self, agent_type: str, since: datetime) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(Task)
            .where(Task.agent == agent_type, Task.updated_at >= since)
            .order_by(Task.updated_at.desc())
            .limit(20)
        )
        tasks = result.scalars().all()
        return [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "work_state": t.work_state,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in tasks
        ]

    async def _backlog(self, agent_type: str) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(Task)
            .where(
                Task.agent == agent_type,
                Task.status == "active",
                Task.work_state.in_(["not_started", "ready", "in_progress"]),
            )
            .order_by(Task.updated_at.desc())
            .limit(20)
        )
        tasks = result.scalars().all()
        return [
            {
                "id": t.id,
                "title": t.title,
                "work_state": t.work_state,
                "project_id": t.project_id,
            }
            for t in tasks
        ]

    async def _performance_metrics(self, agent_type: str, since: datetime) -> dict[str, Any]:
        result = await self.db.execute(
            select(WorkerRun)
            .where(WorkerRun.started_at >= since)
            .order_by(WorkerRun.started_at.desc())
            .limit(50)
        )
        runs = [r for r in result.scalars().all() if self._run_matches_agent(r, agent_type)]

        if not runs:
            return {
                "runs": 0,
                "success_rate": None,
                "avg_duration_seconds": None,
            }

        success_count = sum(1 for r in runs if r.succeeded)
        durations = [
            (r.ended_at - r.started_at).total_seconds()
            for r in runs
            if r.started_at and r.ended_at
        ]

        return {
            "runs": len(runs),
            "success_rate": success_count / len(runs),
            "avg_duration_seconds": (sum(durations) / len(durations)) if durations else None,
        }

    async def _other_agent_activity(self, agent_type: str, since: datetime) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(Task)
            .where(Task.updated_at >= since, Task.agent.is_not(None), Task.agent != agent_type)
            .order_by(Task.updated_at.desc())
            .limit(20)
        )
        tasks = result.scalars().all()
        return [
            {
                "agent": t.agent,
                "task_id": t.id,
                "title": t.title,
                "status": t.status,
            }
            for t in tasks
        ]

    async def _active_initiatives(self, agent_type: str, since: datetime) -> list[dict[str, Any]]:
        reflections_result = await self.db.execute(
            select(AgentReflection)
            .where(
                AgentReflection.agent_type == agent_type,
                AgentReflection.reflection_type == "initiative_feedback",
                AgentReflection.created_at >= since,
            )
            .order_by(AgentReflection.created_at.desc())
            .limit(20)
        )
        reflections = reflections_result.scalars().all()

        initiatives_result = await self.db.execute(
            select(AgentInitiative)
            .where(
                AgentInitiative.proposed_by_agent == agent_type,
                AgentInitiative.status.in_(["proposed", "approved", "in_progress"]),
            )
            .order_by(AgentInitiative.updated_at.desc())
            .limit(20)
        )
        initiatives = initiatives_result.scalars().all()

        feedback_by_initiative: dict[str, dict[str, Any]] = {}
        for r in reflections:
            payload = r.result if isinstance(r.result, dict) else {}
            initiative_id = payload.get("initiative_id")
            if initiative_id and initiative_id not in feedback_by_initiative:
                feedback_by_initiative[initiative_id] = {
                    "decision": payload.get("decision"),
                    "selected_agent": payload.get("selected_agent"),
                    "task_id": payload.get("task_id"),
                    "learning_feedback": payload.get("learning_feedback"),
                }

        items: list[dict[str, Any]] = []
        for i in initiatives:
            items.append(
                {
                    "initiative_id": i.id,
                    "title": i.title,
                    "status": i.status,
                    "category": i.category,
                    "risk_tier": i.risk_tier,
                    "selected_agent": i.selected_agent,
                    "task_id": i.task_id,
                    "last_feedback": feedback_by_initiative.get(i.id),
                }
            )

        return items[:20]

    async def _recent_initiative_decisions(self, agent_type: str) -> list[dict[str, Any]]:
        """Load recent initiative decisions (approved/rejected/deferred) for this agent.

        Uses a 7-day window so agents can learn from recent feedback patterns.
        """
        since = datetime.now(timezone.utc) - timedelta(days=7)

        result = await self.db.execute(
            select(AgentInitiative)
            .where(
                AgentInitiative.proposed_by_agent == agent_type,
                AgentInitiative.status.in_(["approved", "rejected", "deferred"]),
                AgentInitiative.updated_at >= since,
            )
            .order_by(AgentInitiative.updated_at.desc())
            .limit(30)
        )
        initiatives = result.scalars().all()

        items: list[dict[str, Any]] = []
        for i in initiatives:
            item: dict[str, Any] = {
                "title": i.title,
                "category": i.category,
                "status": i.status,  # approved/rejected/deferred
                "decision_summary": i.decision_summary,
                "learning_feedback": i.learning_feedback,
            }
            # Include rationale for server-side rejections (quality gate, dedup)
            if i.rationale:
                item["rationale"] = i.rationale
            items.append(item)

        return items

    @staticmethod
    def _run_matches_agent(run: WorkerRun, agent_type: str) -> bool:
        worker_id = (run.worker_id or "").lower()
        return agent_type.lower() in worker_id
