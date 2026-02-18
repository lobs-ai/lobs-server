"""Lobs sweep phase: dedupe initiatives, apply policy gates, and emit actions."""

from __future__ import annotations

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
    SystemSweep,
)
from app.orchestrator.policy_engine import PolicyEngine

DEFAULT_DAILY_BUDGET = {
    "writer": 4,
    "researcher": 4,
    "programmer": 2,
    "reviewer": 3,
    "architect": 2,
}


class SweepArbitrator:
    """Global initiative arbitration with Lobs as final decision authority."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.policy = PolicyEngine()

    async def run_once(self) -> dict[str, Any]:
        initiatives = await self._load_proposed_initiatives()
        if not initiatives:
            return {"proposed": 0, "lobs_review": 0, "rejected": 0}

        start = datetime.now(timezone.utc)
        budgets = await self._load_daily_budgets()
        usage = await self._daily_budget_usage()

        lobs_review = 0
        rejected = 0

        dedupe_map: dict[tuple[str, str], list[AgentInitiative]] = defaultdict(list)
        for i in initiatives:
            dedupe_map[(self._norm(i.category), self._norm(i.title))].append(i)

        # reject duplicates except newest
        for _k, bucket in dedupe_map.items():
            if len(bucket) <= 1:
                continue
            bucket_sorted = sorted(
                bucket,
                key=lambda x: x.created_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            for dup in bucket_sorted[1:]:
                dup.status = "rejected"
                dup.rationale = (dup.rationale or "") + " | Rejected as duplicate initiative"
                rejected += 1

        for initiative in initiatives:
            if initiative.status != "proposed":
                continue

            owner = (initiative.owner_agent or initiative.proposed_by_agent or "programmer").lower()
            decision = self.policy.decide(initiative.category)
            initiative.risk_tier = decision.risk_tier

            daily_limit = budgets.get(owner, 2)
            used = usage.get(owner, 0)
            budget_remaining = max(0, daily_limit - used)

            recommendation = self._recommendation_for(decision.approval_mode, budget_remaining)
            initiative.status = "lobs_review"
            initiative.rationale = (
                f"Lobs decision required. Recommendation={recommendation}. "
                f"Policy={decision.approval_mode}. Budget={used}/{daily_limit}. "
                f"Reason={decision.reason}"
            )

            await self._create_lobs_decision_item(
                initiative,
                recommendation=recommendation,
                budget=f"{used}/{daily_limit}",
                decision_reason=decision.reason,
                approval_mode=decision.approval_mode,
            )
            lobs_review += 1

        sweep = SystemSweep(
            id=str(uuid.uuid4()),
            sweep_type="initiative_sweep",
            status="completed",
            window_start=start,
            window_end=datetime.now(timezone.utc),
            summary={
                "proposed": len(initiatives),
                "lobs_review": lobs_review,
                "rejected": rejected,
            },
            decisions={"budgets": budgets, "usage": usage, "mode": "lobs_governed"},
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

    async def _create_lobs_decision_item(
        self,
        initiative: AgentInitiative,
        *,
        recommendation: str,
        budget: str,
        decision_reason: str,
        approval_mode: str,
    ) -> None:
        title = initiative.title or "Untitled initiative"
        severity = "HIGH" if initiative.risk_tier == "C" else "MEDIUM"

        content = (
            "Lobs initiative decision required.\n\n"
            f"Recommendation: {recommendation}\n"
            f"Policy mode: {approval_mode}\n"
            f"Budget usage: {budget}\n"
            f"Reason: {decision_reason}\n\n"
            f"Initiative ID: {initiative.id}\n"
            f"Title: {title}\n"
            f"Category: {initiative.category}\n"
            f"Risk tier: {initiative.risk_tier}\n"
            f"Proposed by: {initiative.proposed_by_agent}\n"
            f"Owner agent: {initiative.owner_agent}\n\n"
            f"Description:\n{initiative.description or '(none)'}\n\n"
            "Expected decision by Lobs: approve / defer / reject"
        )

        self.db.add(
            InboxItem(
                id=str(uuid.uuid4()),
                title=f"[{severity}] Lobs decision: {title[:80]}",
                content=content,
                is_read=False,
                summary=f"Recommendation={recommendation} | {initiative.category}",
                modified_at=datetime.now(timezone.utc),
            )
        )

    @staticmethod
    def _recommendation_for(approval_mode: str, budget_remaining: int) -> str:
        if approval_mode == "hard_gate":
            return "review"
        if budget_remaining <= 0:
            return "defer"
        if approval_mode == "auto":
            return "approve"
        return "review"

    @staticmethod
    def _norm(value: str | None) -> str:
        return " ".join((value or "").strip().lower().split())
