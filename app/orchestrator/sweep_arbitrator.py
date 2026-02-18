"""Lobs sweep phase: dedupe initiatives, apply policy gates, and emit actions."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentInitiative,
    InboxItem,
    OrchestratorSetting,
    Project,
    SystemSweep,
    Task,
)
from app.orchestrator.policy_engine import PolicyEngine

logger = logging.getLogger(__name__)

DEFAULT_DAILY_BUDGET = {
    "writer": 4,
    "researcher": 4,
    "programmer": 2,
    "reviewer": 3,
    "architect": 2,
}


class SweepArbitrator:
    """Global initiative arbitration and bounded autonomy enforcement."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.policy = PolicyEngine()

    async def run_once(self) -> dict[str, Any]:
        initiatives = await self._load_proposed_initiatives()
        if not initiatives:
            return {"proposed": 0, "approved": 0, "deferred": 0, "rejected": 0}

        start = datetime.now(timezone.utc)
        budgets = await self._load_daily_budgets()
        usage = await self._daily_budget_usage()

        approved = 0
        deferred = 0
        rejected = 0
        soft_gated = 0

        dedupe_map: dict[tuple[str, str], list[AgentInitiative]] = defaultdict(list)
        for i in initiatives:
            dedupe_map[(self._norm(i.category), self._norm(i.title))].append(i)

        # reject duplicates except newest
        for _k, bucket in dedupe_map.items():
            if len(bucket) <= 1:
                continue
            bucket_sorted = sorted(bucket, key=lambda x: x.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            winner = bucket_sorted[0]
            for dup in bucket_sorted[1:]:
                dup.status = "rejected"
                dup.rationale = (dup.rationale or "") + " | Rejected as duplicate initiative"
                rejected += 1
            # keep winner for normal processing

        for initiative in initiatives:
            if initiative.status != "proposed":
                continue

            decision = self.policy.decide(initiative.category)
            initiative.risk_tier = decision.risk_tier

            if decision.approval_mode == "auto":
                owner = (initiative.owner_agent or initiative.proposed_by_agent or "programmer").lower()
                daily_limit = budgets.get(owner, 2)
                used = usage.get(owner, 0)

                if used >= daily_limit:
                    initiative.status = "deferred"
                    initiative.rationale = f"Autonomy budget reached ({used}/{daily_limit})"
                    deferred += 1
                    continue

                created = await self._maybe_create_task_for_initiative(initiative)
                usage[owner] = used + 1
                initiative.status = "approved" if created else "deferred"
                initiative.rationale = decision.reason
                if created:
                    approved += 1
                else:
                    deferred += 1
                continue

            if decision.approval_mode == "soft_gate":
                await self._create_inbox_gate_item(initiative, hard=False)
                initiative.status = "needs_review"
                initiative.rationale = decision.reason
                soft_gated += 1
                continue

            await self._create_inbox_gate_item(initiative, hard=True)
            initiative.status = "needs_review"
            initiative.rationale = decision.reason
            soft_gated += 1

        sweep = SystemSweep(
            id=str(uuid.uuid4()),
            sweep_type="initiative_sweep",
            status="completed",
            window_start=start,
            window_end=datetime.now(timezone.utc),
            summary={
                "proposed": len(initiatives),
                "approved": approved,
                "deferred": deferred,
                "rejected": rejected,
                "needs_review": soft_gated,
            },
            decisions={"budgets": budgets, "usage": usage},
            completed_at=datetime.now(timezone.utc),
        )
        self.db.add(sweep)
        await self.db.commit()

        return sweep.summary or {}

    async def _load_proposed_initiatives(self) -> list[AgentInitiative]:
        result = await self.db.execute(
            select(AgentInitiative)
            .where(AgentInitiative.status == "proposed")
            .order_by(AgentInitiative.created_at.asc())
            .limit(300)
        )
        return result.scalars().all()

    async def _load_daily_budgets(self) -> dict[str, int]:
        row = await self.db.get(OrchestratorSetting, "autonomy_budget.daily")
        if not row or not isinstance(row.value, dict):
            return dict(DEFAULT_DAILY_BUDGET)

        budgets: dict[str, int] = {}
        for agent, raw in row.value.items():
            try:
                budgets[str(agent).lower()] = max(0, int(raw))
            except (TypeError, ValueError):
                continue

        for k, v in DEFAULT_DAILY_BUDGET.items():
            budgets.setdefault(k, v)

        return budgets

    async def _daily_budget_usage(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        result = await self.db.execute(
            select(AgentInitiative).where(
                AgentInitiative.status.in_(["approved", "active"]),
                AgentInitiative.updated_at >= day_start,
            )
        )
        rows = result.scalars().all()
        usage: dict[str, int] = defaultdict(int)
        for row in rows:
            owner = (row.owner_agent or row.proposed_by_agent or "programmer").lower()
            usage[owner] += 1
        return dict(usage)

    async def _maybe_create_task_for_initiative(self, initiative: AgentInitiative) -> bool:
        title = initiative.title.strip() if initiative.title else "Untitled initiative"
        if not title:
            return False

        # Avoid duplicate active tasks with same title.
        dup_result = await self.db.execute(
            select(Task).where(Task.title == title, Task.status == "active")
        )
        if dup_result.scalar_one_or_none() is not None:
            return False

        project_id = await self._default_project_id()
        if not project_id:
            logger.warning("[SWEEP] No project found to create initiative task")
            return False

        notes = (
            f"Auto-created from initiative {initiative.id}.\n\n"
            f"Category: {initiative.category}\n"
            f"Risk tier: {initiative.risk_tier}\n"
            f"Proposed by: {initiative.proposed_by_agent}\n\n"
            f"{initiative.description or ''}"
        )

        task = Task(
            id=str(uuid.uuid4()),
            title=title,
            status="active",
            work_state="not_started",
            project_id=project_id,
            notes=notes,
            agent=initiative.owner_agent or initiative.proposed_by_agent,
            owner="lobs",
        )
        self.db.add(task)
        return True

    async def _create_inbox_gate_item(self, initiative: AgentInitiative, *, hard: bool) -> None:
        title = initiative.title or "Untitled initiative"
        severity = "HIGH" if hard else "MEDIUM"

        content = (
            f"Initiative requires {'hard' if hard else 'soft'} review.\n\n"
            f"Title: {title}\n"
            f"Category: {initiative.category}\n"
            f"Risk tier: {initiative.risk_tier}\n"
            f"Proposed by: {initiative.proposed_by_agent}\n"
            f"Owner agent: {initiative.owner_agent}\n\n"
            f"Description:\n{initiative.description or '(none)'}\n"
        )

        self.db.add(
            InboxItem(
                id=str(uuid.uuid4()),
                title=f"[{severity}] Initiative review: {title[:80]}",
                content=content,
                is_read=False,
                summary=f"{initiative.category} initiative needs review",
                modified_at=datetime.now(timezone.utc),
            )
        )

    async def _default_project_id(self) -> str | None:
        row = await self.db.get(OrchestratorSetting, "initiative.default_project")
        if row and isinstance(row.value, str) and row.value.strip():
            return row.value.strip()

        preferred = await self.db.get(Project, "lobs-server")
        if preferred:
            return preferred.id

        result = await self.db.execute(select(Project).order_by(Project.created_at.asc()).limit(1))
        first = result.scalar_one_or_none()
        return first.id if first else None

    @staticmethod
    def _norm(value: str | None) -> str:
        return " ".join((value or "").strip().lower().split())
