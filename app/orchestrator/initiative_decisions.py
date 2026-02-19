"""Lobs-driven initiative decisioning and conversion to executable work."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentCapability, AgentInitiative, AgentReflection, InitiativeDecisionRecord, Project, Task


class InitiativeDecisionEngine:
    """Applies Lobs decisions to initiatives and converts approved ideas into tasks."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def suggest_agent(self, initiative: AgentInitiative) -> str:
        agent, _has_capability_match = await self.suggest_agent_with_diagnostics(initiative)
        return agent

    async def suggest_agent_with_diagnostics(self, initiative: AgentInitiative) -> tuple[str, bool]:
        text = "\n".join(
            [
                initiative.title or "",
                initiative.description or "",
                initiative.category or "",
            ]
        ).lower()

        result = await self.db.execute(select(AgentCapability))
        rows = result.scalars().all()
        fallback = initiative.owner_agent or initiative.proposed_by_agent or "programmer"
        if not rows:
            return fallback, False

        scores: dict[str, float] = defaultdict(float)
        for row in rows:
            capability = (row.capability or "").strip().lower()
            if not capability:
                continue
            tokens = [tok for tok in capability.split() if tok]
            if not tokens:
                continue
            matches = sum(1 for tok in tokens if tok in text)
            if matches == 0:
                continue
            overlap = matches / len(tokens)
            scores[row.agent_type] += overlap * float(row.confidence or 0.5)

        if not scores:
            return fallback, False

        best_agent, _score = max(scores.items(), key=lambda item: item[1])
        return best_agent, True

    async def decide(
        self,
        initiative: AgentInitiative,
        *,
        decision: str,
        revised_title: str | None = None,
        revised_description: str | None = None,
        selected_agent: str | None = None,
        selected_project_id: str | None = None,
        decision_summary: str | None = None,
        learning_feedback: str | None = None,
        decided_by: str = "lobs",
        sweep_id: str | None = None,
        overlap_with_ids: list[str] | None = None,
        contradiction_with_ids: list[str] | None = None,
        capability_gap: bool = False,
    ) -> dict[str, Any]:
        decision = decision.strip().lower()
        if decision not in {"approve", "defer", "reject"}:
            raise ValueError("decision must be approve|defer|reject")

        if (decided_by or "").strip().lower() != "lobs":
            raise PermissionError("initiative decisions must be finalized by lobs")

        if decision == "approve" and initiative.status not in {"lobs_review", "proposed"}:
            raise ValueError(f"initiative in status '{initiative.status}' is not approvable")

        title = (revised_title or initiative.title or "Untitled initiative").strip()
        description = (revised_description or initiative.description or "").strip()

        initiative.selected_agent = selected_agent or await self.suggest_agent(initiative)
        initiative.selected_project_id = selected_project_id or await self._default_project_id()
        initiative.approved_by = decided_by
        initiative.decision_summary = decision_summary
        initiative.learning_feedback = learning_feedback

        if decision == "approve":
            task = await self._create_task_from_initiative(
                initiative,
                title=title,
                description=description,
            )
            initiative.task_id = task.id
            initiative.status = "approved"
        elif decision == "defer":
            initiative.status = "deferred"
        else:
            initiative.status = "rejected"

        self.db.add(
            InitiativeDecisionRecord(
                id=str(uuid.uuid4()),
                initiative_id=initiative.id,
                sweep_id=sweep_id,
                decision=decision,
                decided_by=decided_by,
                decision_summary=decision_summary,
                overlap_with_ids=overlap_with_ids or [],
                contradiction_with_ids=contradiction_with_ids or [],
                capability_gap=bool(capability_gap),
                source_reflection_ids=[initiative.source_reflection_id] if initiative.source_reflection_id else [],
                task_id=initiative.task_id,
            )
        )

        await self._record_feedback_reflection(
            initiative,
            decision=decision,
            title=title,
            description=description,
            learning_feedback=learning_feedback,
            decision_summary=decision_summary,
            decided_by=decided_by,
        )

        await self.db.commit()

        return {
            "initiative_id": initiative.id,
            "status": initiative.status,
            "task_id": initiative.task_id,
            "selected_agent": initiative.selected_agent,
            "selected_project_id": initiative.selected_project_id,
        }

    async def _create_task_from_initiative(
        self,
        initiative: AgentInitiative,
        *,
        title: str,
        description: str,
    ) -> Task:
        project_id = initiative.selected_project_id or await self._default_project_id()
        if not project_id:
            raise ValueError("No project available for initiative task creation")

        notes = (
            f"Converted by Lobs from initiative {initiative.id}.\n"
            f"Source reflection: {initiative.source_reflection_id or 'unknown'}\n\n"
            f"Original proposer: {initiative.proposed_by_agent}\n"
            f"Category: {initiative.category}\n"
            f"Risk tier: {initiative.risk_tier}\n"
            f"Selected agent: {initiative.selected_agent}\n\n"
            f"{description}"
        )

        task = Task(
            id=str(uuid.uuid4()),
            title=title,
            status="active",
            work_state="not_started",
            project_id=project_id,
            notes=notes,
            owner="lobs",
            agent=initiative.selected_agent,
        )
        self.db.add(task)
        return task

    async def _record_feedback_reflection(
        self,
        initiative: AgentInitiative,
        *,
        decision: str,
        title: str,
        description: str,
        learning_feedback: str | None,
        decision_summary: str | None,
        decided_by: str,
    ) -> None:
        feedback_payload = {
            "initiative_id": initiative.id,
            "decision": decision,
            "selected_agent": initiative.selected_agent,
            "selected_project_id": initiative.selected_project_id,
            "task_id": initiative.task_id,
            "title_used": title,
            "description_used": description,
            "decision_summary": decision_summary,
            "learning_feedback": learning_feedback,
            "decided_by": decided_by,
        }

        self.db.add(
            AgentReflection(
                id=str(uuid.uuid4()),
                agent_type=initiative.proposed_by_agent,
                reflection_type="initiative_feedback",
                status="completed",
                window_start=datetime.now(timezone.utc),
                window_end=datetime.now(timezone.utc),
                context_packet={"source": "lobs_decision"},
                result=feedback_payload,
                completed_at=datetime.now(timezone.utc),
            )
        )

    async def _default_project_id(self) -> str | None:
        preferred = await self.db.get(Project, "lobs-server")
        if preferred:
            return preferred.id
        result = await self.db.execute(select(Project).order_by(Project.created_at.asc()).limit(1))
        first = result.scalar_one_or_none()
        return first.id if first else None
