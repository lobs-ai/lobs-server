"""Lobs sweep phase: collect agent proposals, filter obvious junk, and use an LLM
to intelligently review and decide on all remaining initiatives.

Flow:
1. Server-side first pass: quality filter (too-short, junk), dedup by title+desc
2. Spawn a single LLM session to review ALL remaining proposals as a batch
3. Process LLM decisions: approve → create task, defer, reject
4. Only risk_tier C / truly dangerous items also create Rafe inbox items
"""

from __future__ import annotations

import json
import logging
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
from app.orchestrator.initiative_decisions import InitiativeDecisionEngine
from app.orchestrator.registry import AgentRegistry

logger = logging.getLogger(__name__)

DEFAULT_DAILY_BUDGET = {
    "writer": 4,
    "researcher": 4,
    "programmer": 2,
    "reviewer": 3,
    "architect": 2,
}

# Categories that should never be auto-approved regardless of LLM recommendation
HARD_GATE_CATEGORIES = {
    "architecture_change",
    "destructive_operation",
    "cross_project_migration",
    "agent_recruitment",
}


class SweepArbitrator:
    """Global initiative arbitration using LLM-based review.

    After server-side prefiltering (quality, dedup), all remaining proposals
    are sent to a single LLM session for intelligent batch review.
    """

    def __init__(self, db: AsyncSession, worker_manager: Any | None = None):
        self.db = db
        self.worker_manager = worker_manager
        self.decision_engine = InitiativeDecisionEngine(db)
        self.registry = AgentRegistry()

    async def run_once(self) -> dict[str, Any]:
        initiatives = await self._load_proposed_initiatives()
        if not initiatives:
            return {"proposed": 0, "approved": 0, "deferred": 0, "rejected": 0}

        start = datetime.now(timezone.utc)
        sweep_id = str(uuid.uuid4())
        budgets = await self._load_daily_budgets()
        usage = await self._daily_budget_usage()

        rejected = 0

        # --- Server-side prefilter: quality gate ---
        for initiative in initiatives:
            bad_reason = self._bad_idea_reason(initiative)
            if bad_reason:
                initiative.status = "rejected"
                initiative.rationale = (initiative.rationale or "") + f" | Quality gate: {bad_reason}"
                rejected += 1

        # --- Server-side prefilter: dedup ---
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
                dup.rationale = (dup.rationale or "") + " | Duplicate initiative"
                rejected += 1

        # --- Collect remaining proposals for LLM review ---
        remaining = [i for i in initiatives if i.status == "proposed"]

        if not remaining:
            sweep = self._create_sweep_record(
                sweep_id, start, len(initiatives), 0, 0, rejected, budgets, usage
            )
            self.db.add(sweep)
            await self.db.commit()
            return {"proposed": len(initiatives), "approved": 0, "deferred": 0, "rejected": rejected}

        # --- Spawn LLM to review all proposals ---
        approved = 0
        deferred = 0

        if self.worker_manager is not None:
            try:
                await self._spawn_llm_review(remaining, budgets, usage, sweep_id)
                # Mark all remaining as "lobs_review" — the LLM session will
                # process them asynchronously via _process_sweep_review_results
                for initiative in remaining:
                    initiative.status = "lobs_review"
            except Exception as e:
                logger.warning("[SWEEP] Failed to spawn LLM review: %s", e)
                # Fallback: defer everything so nothing is lost
                for initiative in remaining:
                    await self.decision_engine.decide(
                        initiative,
                        decision="defer",
                        decision_summary=f"LLM review spawn failed: {e}",
                        decided_by="lobs",
                        sweep_id=sweep_id,
                    )
                    deferred += 1
        else:
            # No worker manager — defer everything
            for initiative in remaining:
                await self.decision_engine.decide(
                    initiative,
                    decision="defer",
                    decision_summary="No worker manager available for LLM review",
                    decided_by="lobs",
                    sweep_id=sweep_id,
                )
                deferred += 1

        # --- Create high-risk inbox items for Rafe ---
        for initiative in remaining:
            if (initiative.category or "").strip().lower() in HARD_GATE_CATEGORIES:
                await self._create_rafe_inbox_item(initiative)

        sweep = self._create_sweep_record(
            sweep_id, start, len(initiatives), approved, deferred, rejected, budgets, usage
        )
        self.db.add(sweep)
        await self.db.commit()

        return {
            "proposed": len(initiatives),
            "approved": approved,
            "deferred": deferred,
            "rejected": rejected,
            "llm_review": len(remaining),
        }

    # ------------------------------------------------------------------
    # LLM review
    # ------------------------------------------------------------------

    async def _spawn_llm_review(
        self,
        initiatives: list[AgentInitiative],
        budgets: dict[str, int],
        usage: dict[str, int],
        sweep_id: str,
    ) -> None:
        """Spawn a single LLM session to review all proposals intelligently."""
        batch = []
        for i in initiatives:
            batch.append({
                "id": i.id,
                "title": i.title,
                "description": i.description,
                "category": i.category,
                "proposed_by": i.proposed_by_agent,
                "suggested_owner": i.owner_agent,
                "estimated_effort": int(i.score) if i.score is not None else None,
            })

        budget_summary = {
            agent: f"{usage.get(agent, 0)}/{limit}"
            for agent, limit in budgets.items()
        }

        prompt = f"""## Initiative Sweep Review — Lobs PM Decision

You are Lobs, the main agent and project manager. You know Rafe's priorities, the active projects, and the system architecture. Use that context to make real decisions about these {len(batch)} initiative proposals from your agent team's reflections.

### Context
- These proposals come from agents reflecting on their recent work
- Daily agent budgets (used/limit): {json.dumps(budget_summary)}
- Sweep ID: {sweep_id}
- Read your MEMORY.md and project context to inform decisions — you have full access to Rafe's priorities and system state

### Your job
1. Evaluate each proposal against what you know about current priorities, active work, and what Rafe actually cares about
2. Check for overlapping or contradictory proposals across agents
3. Decide: **approve** (create a task), **defer** (not now but maybe later), or **reject** (not worth doing)
4. For approved items, assign the best agent and set priority

### Decision guidelines
- Be selective. Approve only proposals that deliver clear, concrete value aligned with current priorities.
- Reject vague, speculative, or low-impact proposals. Reject things that duplicate existing work.
- Defer things that are reasonable but not urgent or would exceed budget.
- Respect daily budgets — don't approve more work for an agent than their remaining budget allows.
- High-risk categories (architecture changes, destructive operations, cross-project migrations, agent recruitment) should be deferred for human review, not approved.
- Think about what Rafe would want — he values correctness, leverage, and systems that work autonomously. Don't approve busywork.

### Proposals
{json.dumps(batch, indent=2)}

### Output format
Return STRICT JSON only (no prose outside JSON):
```json
{{
  "decisions": [
    {{
      "initiative_id": "<id>",
      "decision": "approve|defer|reject",
      "reason": "brief rationale",
      "owner_agent": "agent-type (for approved items)",
      "task_title": "refined title (for approved items)",
      "task_notes": "notes for task (for approved items)",
      "priority": "low|medium|high",
      "project_id": "optional project id"
    }}
  ],
  "observations": ["any cross-cutting observations about the proposals"]
}}
```
"""
        # Use the main agent's standard-tier model — this is Lobs making real
        # project decisions and needs full context awareness + reasoning quality.
        from app.orchestrator.model_chooser import ModelChooser
        chooser = ModelChooser(self.db)
        choice = await chooser.choose(
            agent_type="lobs",
            task={"id": "sweep-review", "title": "Initiative sweep review", "notes": "Project management decisions requiring full context"},
            purpose="execution",
        )

        result, error, _error_type = await self.worker_manager._spawn_session(
            task_prompt=prompt,
            agent_id="main",
            model=choice.model,
            label=f"sweep-review-{sweep_id[:8]}",
        )
        if not result:
            raise RuntimeError(f"LLM sweep spawn failed: {error}")

        logger.info(
            "[SWEEP] Spawned LLM review for %d initiatives (model=%s, runId=%s)",
            len(batch),
            choice.model,
            result.get("runId", "?")[:12],
        )

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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

    async def _create_rafe_inbox_item(self, initiative: AgentInitiative) -> None:
        title = initiative.title or "Untitled initiative"
        content = (
            "High-risk initiative requires human review.\n\n"
            f"Initiative ID: {initiative.id}\n"
            f"Title: {title}\n"
            f"Category: {initiative.category}\n"
            f"Proposed by: {initiative.proposed_by_agent}\n\n"
            f"Description:\n{initiative.description or '(none)'}\n\n"
            "Action: approve / defer / reject"
        )

        self.db.add(
            InboxItem(
                id=str(uuid.uuid4()),
                title=f"[HIGH] Initiative review: {title[:80]}",
                content=content,
                is_read=False,
                summary=f"High-risk category: {initiative.category}",
                modified_at=datetime.now(timezone.utc),
            )
        )

    def _create_sweep_record(
        self,
        sweep_id: str,
        start: datetime,
        total: int,
        approved: int,
        deferred: int,
        rejected: int,
        budgets: dict[str, int],
        usage: dict[str, int],
    ) -> SystemSweep:
        return SystemSweep(
            id=sweep_id,
            sweep_type="initiative_sweep",
            status="completed",
            window_start=start,
            window_end=datetime.now(timezone.utc),
            summary={
                "proposed": total,
                "approved": approved,
                "deferred": deferred,
                "rejected": rejected,
            },
            decisions={
                "budgets": budgets,
                "usage": usage,
                "mode": "llm_reviewed",
            },
            completed_at=datetime.now(timezone.utc),
        )
