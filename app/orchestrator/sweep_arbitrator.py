"""Lobs sweep phase: wait for full agent proposal set, filter bad ideas, dedupe, and route."""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentInitiative,
    AgentReflection,
    InboxItem,
    OrchestratorSetting,
    SystemSweep,
)
from app.orchestrator.config import CONTROL_PLANE_AGENTS
from app.orchestrator.policy_engine import PolicyEngine
from app.orchestrator.initiative_decisions import InitiativeDecisionEngine
from app.orchestrator.registry import AgentRegistry

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
        self.decision_engine = InitiativeDecisionEngine(db)
        self.registry = AgentRegistry()

    async def run_once(self) -> dict[str, Any]:
        batch_ready = await self._is_reflection_batch_ready()
        if not batch_ready["ready"]:
            return {
                "proposed": 0,
                "approved": 0,
                "deferred": 0,
                "lobs_review": 0,
                "rejected": 0,
                "waiting_for_agents": batch_ready.get("missing_agents", []),
            }

        initiatives = await self._load_proposed_initiatives()
        if not initiatives:
            return {
                "proposed": 0,
                "approved": 0,
                "deferred": 0,
                "lobs_review": 0,
                "rejected": 0,
            }

        start = datetime.now(timezone.utc)
        sweep_id = str(uuid.uuid4())
        budgets = await self._load_daily_budgets()
        usage = await self._daily_budget_usage()
        overlap_map, contradiction_map = self._detect_relationships(initiatives)
        capability_gap_map: dict[str, bool] = {}

        lobs_review = 0
        rejected = 0

        # First-pass quality filter: reject low-signal/bad ideas.
        for initiative in initiatives:
            bad_reason = self._bad_idea_reason(initiative)
            if bad_reason:
                initiative.status = "rejected"
                initiative.rationale = (initiative.rationale or "") + f" | Rejected by quality gate: {bad_reason}"
                rejected += 1

        # Dedupe by normalized title+description signature. Keep newest.
        dedupe_map: dict[str, list[AgentInitiative]] = defaultdict(list)
        for i in initiatives:
            if i.status != "proposed":
                continue
            dedupe_map[self._dedupe_key(i)].append(i)

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

        approved = 0
        deferred = 0

        for initiative in initiatives:
            if initiative.status != "proposed":
                continue

            suggested_agent, has_capability_match = await self.decision_engine.suggest_agent_with_diagnostics(initiative)
            capability_gap = not has_capability_match
            capability_gap_map[initiative.id] = capability_gap
            initiative.owner_agent = suggested_agent
            owner = (suggested_agent or initiative.proposed_by_agent or "programmer").lower()
            decision = self.policy.decide(
                initiative.category,
                estimated_effort=int(initiative.score) if initiative.score is not None else None,
            )
            initiative.risk_tier = decision.risk_tier
            initiative.policy_lane = decision.lane
            initiative.policy_reason = decision.reason

            daily_limit = budgets.get(owner, 2)
            used = usage.get(owner, 0)
            budget_remaining = max(0, daily_limit - used)

            size = self._size_bucket(initiative, decision.approval_mode)
            recommendation = self._recommendation_for(size=size, budget_remaining=budget_remaining)
            contradiction_ids = contradiction_map.get(initiative.id, [])
            overlap_ids = overlap_map.get(initiative.id, [])

            if contradiction_ids:
                initiative.status = "lobs_review"
                initiative.rationale = (
                    (initiative.rationale or "")
                    + f" | Contradiction detected with initiatives: {', '.join(contradiction_ids)}"
                )
                await self._create_lobs_decision_item(
                    initiative,
                    recommendation="review",
                    budget=f"{used}/{daily_limit}",
                    decision_reason="Contradictory initiatives detected in reflection sweep",
                    approval_mode=decision.approval_mode,
                    suggested_agent=suggested_agent,
                    size=size,
                )
                await self.decision_engine.decide(
                    initiative,
                    decision="defer",
                    selected_agent=suggested_agent,
                    decision_summary="Deferred for human review due to contradiction detection.",
                    decided_by="lobs",
                    sweep_id=sweep_id,
                    overlap_with_ids=overlap_ids,
                    contradiction_with_ids=contradiction_ids,
                    capability_gap=capability_gap,
                )
                lobs_review += 1
                continue

            if decision.lane == "blocked":
                await self.decision_engine.decide(
                    initiative,
                    decision="reject",
                    selected_agent=suggested_agent,
                    decision_summary=(
                        f"Rejected by policy lane. category={initiative.category}; "
                        f"reason={decision.reason}; size={size}; budget={used}/{daily_limit}"
                    ),
                    decided_by="lobs",
                    sweep_id=sweep_id,
                    overlap_with_ids=overlap_ids,
                    contradiction_with_ids=contradiction_ids,
                    capability_gap=capability_gap,
                )
                rejected += 1
                continue

            if decision.lane == "auto_allowed":
                if budget_remaining > 0:
                    await self.decision_engine.decide(
                        initiative,
                        decision="approve",
                        selected_agent=suggested_agent,
                        decision_summary=(
                            f"Auto-approved by Lobs sweep ({size}); lane={decision.lane}; "
                            f"budget {used}/{daily_limit}; reason={decision.reason}"
                        ),
                        decided_by="lobs",
                        sweep_id=sweep_id,
                        overlap_with_ids=overlap_ids,
                        contradiction_with_ids=contradiction_ids,
                        capability_gap=capability_gap,
                    )
                    usage[owner] = used + 1
                    approved += 1
                else:
                    await self.decision_engine.decide(
                        initiative,
                        decision="defer",
                        selected_agent=suggested_agent,
                        decision_summary=(
                            f"Deferred by Lobs sweep due to budget cap {used}/{daily_limit}; "
                            f"lane={decision.lane}; size={size}"
                        ),
                        decided_by="lobs",
                        sweep_id=sweep_id,
                        overlap_with_ids=overlap_ids,
                        contradiction_with_ids=contradiction_ids,
                        capability_gap=capability_gap,
                    )
                    deferred += 1
                continue

            await self.decision_engine.decide(
                initiative,
                decision="defer",
                selected_agent=suggested_agent,
                decision_summary=(
                    f"Deferred for Lobs review. Recommendation={recommendation}. "
                    f"Lane={decision.lane}. Budget={used}/{daily_limit}. Reason={decision.reason}"
                ),
                decided_by="lobs",
                sweep_id=sweep_id,
                overlap_with_ids=overlap_ids,
                contradiction_with_ids=contradiction_ids,
                capability_gap=capability_gap,
            )

            await self._create_lobs_decision_item(
                initiative,
                recommendation=recommendation,
                budget=f"{used}/{daily_limit}",
                decision_reason=decision.reason,
                approval_mode=decision.approval_mode,
                suggested_agent=suggested_agent,
                size=size,
            )
            lobs_review += 1

        capability_gaps = [i.id for i, is_gap in capability_gap_map.items() if is_gap]

        sweep = SystemSweep(
            id=sweep_id,
            sweep_type="initiative_sweep",
            status="completed",
            window_start=start,
            window_end=datetime.now(timezone.utc),
            summary={
                "proposed": len(initiatives),
                "approved": approved,
                "deferred": deferred,
                "lobs_review": lobs_review,
                "rejected": rejected,
                "contradictions": sum(1 for v in contradiction_map.values() if v),
                "overlap_candidates": sum(1 for v in overlap_map.values() if v),
                "capability_gaps": len(capability_gaps),
            },
            decisions={
                "budgets": budgets,
                "usage": usage,
                "mode": "lobs_governed",
                "overlap_map": {k: v for k, v in overlap_map.items() if v},
                "contradiction_map": {k: v for k, v in contradiction_map.items() if v},
                "capability_gaps": capability_gaps,
            },
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

    async def _is_reflection_batch_ready(self) -> dict[str, Any]:
        """Only sweep once every execution agent has completed a recent strategic reflection."""
        execution_agents = [
            a for a in self.registry.available_types() if a not in CONTROL_PLANE_AGENTS
        ]
        if not execution_agents:
            return {"ready": True, "missing_agents": []}

        since = datetime.now(timezone.utc) - timedelta(hours=8)
        result = await self.db.execute(
            select(AgentReflection).where(
                AgentReflection.reflection_type == "strategic",
                AgentReflection.created_at >= since,
            )
        )
        rows = result.scalars().all()

        # Backward-compatible fallback: if there are no recent strategic reflections,
        # allow manual/testing initiative sweeps to proceed.
        if not rows:
            return {"ready": True, "missing_agents": []}

        completed_agents = {
            r.agent_type for r in rows if r.status == "completed" and (r.agent_type in execution_agents)
        }

        missing = [a for a in execution_agents if a not in completed_agents]
        return {"ready": len(missing) == 0, "missing_agents": missing}

    async def _create_lobs_decision_item(
        self,
        initiative: AgentInitiative,
        *,
        recommendation: str,
        budget: str,
        decision_reason: str,
        approval_mode: str,
        suggested_agent: str,
        size: str,
    ) -> None:
        title = initiative.title or "Untitled initiative"
        severity = "HIGH" if initiative.risk_tier == "C" else "MEDIUM"

        content = (
            "Rafe decision required for policy-gated initiative.\n\n"
            f"Estimated size: {size}\n"
            f"Recommendation: {recommendation}\n"
            f"Policy mode: {approval_mode}\n"
            f"Policy lane: {initiative.policy_lane or 'review_required'}\n"
            f"Budget usage: {budget}\n"
            f"Reason: {decision_reason}\n"
            f"Suggested execution agent: {suggested_agent}\n\n"
            f"Initiative ID: {initiative.id}\n"
            f"Title: {title}\n"
            f"Category: {initiative.category}\n"
            f"Risk tier: {initiative.risk_tier}\n"
            f"Proposed by: {initiative.proposed_by_agent}\n"
            f"Owner agent: {initiative.owner_agent}\n\n"
            f"Description:\n{initiative.description or '(none)'}\n\n"
            "Expected decision by Rafe: approve / defer / reject"
        )

        self.db.add(
            InboxItem(
                id=str(uuid.uuid4()),
                title=f"[{severity}] Rafe decision: {title[:80]}",
                content=content,
                is_read=False,
                summary=f"Recommendation={recommendation} | {initiative.category}",
                modified_at=datetime.now(timezone.utc),
            )
        )

    @staticmethod
    def _recommendation_for(*, size: str, budget_remaining: int) -> str:
        if size == "large":
            return "review"
        if budget_remaining <= 0:
            return "defer"
        return "approve"

    @staticmethod
    def _size_bucket(initiative: AgentInitiative, approval_mode: str) -> str:
        effort = int(initiative.score) if initiative.score is not None else None
        if effort is not None:
            if effort >= 7:
                return "large"
            if effort >= 4:
                return "medium"
            return "small"

        if approval_mode == "hard_gate":
            return "large"
        if approval_mode == "soft_gate":
            return "medium"
        return "small"

    def _detect_relationships(self, initiatives: list[AgentInitiative]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        overlap_map: dict[str, list[str]] = defaultdict(list)
        contradiction_map: dict[str, list[str]] = defaultdict(list)

        for i, left in enumerate(initiatives):
            left_text = self._norm(f"{left.title or ''} {left.description or ''}")
            left_tokens = set(tok for tok in left_text.split() if len(tok) > 3)
            left_dir = self._direction(left_text)
            for right in initiatives[i + 1 :]:
                if left.id == right.id:
                    continue
                right_text = self._norm(f"{right.title or ''} {right.description or ''}")
                right_tokens = set(tok for tok in right_text.split() if len(tok) > 3)
                if not left_tokens or not right_tokens:
                    continue

                overlap = len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))
                same_category = self._norm(left.category) == self._norm(right.category)
                if same_category and overlap >= 0.5:
                    overlap_map[left.id].append(right.id)
                    overlap_map[right.id].append(left.id)

                right_dir = self._direction(right_text)
                if same_category and overlap >= 0.3 and left_dir and right_dir and left_dir != right_dir:
                    contradiction_map[left.id].append(right.id)
                    contradiction_map[right.id].append(left.id)

        return dict(overlap_map), dict(contradiction_map)

    @staticmethod
    def _direction(text: str) -> str | None:
        positive = ["increase", "expand", "add", "enable", "raise", "more"]
        negative = ["decrease", "reduce", "remove", "disable", "lower", "less"]
        pos = any(tok in text for tok in positive)
        neg = any(tok in text for tok in negative)
        if pos and not neg:
            return "up"
        if neg and not pos:
            return "down"
        return None

    @staticmethod
    def _bad_idea_reason(initiative: AgentInitiative) -> str | None:
        title = (initiative.title or "").strip()
        desc = (initiative.description or "").strip()
        joined = f"{title} {desc}".lower()

        if len(title) < 6:
            return "title too short"
        if len(desc) < 12:
            return "description too short"

        blocked_patterns = [
            r"\b(tbd|todo|n/a|none|unknown)\b",
            r"^\?+$",
            r"\bdo\s+nothing\b",
            r"\bjust\s+vibes\b",
        ]
        for pattern in blocked_patterns:
            if re.search(pattern, joined):
                return f"low-signal content matched '{pattern}'"

        return None

    @staticmethod
    def _dedupe_key(initiative: AgentInitiative) -> str:
        normalized = " ".join(
            re.sub(r"[^a-z0-9\s]", " ", f"{initiative.title or ''} {initiative.description or ''}".lower()).split()
        )
        category = " ".join((initiative.category or "").strip().lower().split())
        return f"{category}|{normalized[:220]}"

    @staticmethod
    def _norm(value: str | None) -> str:
        return " ".join((value or "").strip().lower().split())
