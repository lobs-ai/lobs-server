"""Lobs sweep phase: collect agent proposals, filter obvious junk, and route
all remaining initiatives to the Lobs main session for approval.

Flow:
1. Server-side first pass: quality filter (too-short, junk), dedup by title+desc
2. Mark all remaining initiatives as 'lobs_review'
3. Send formatted summary to Lobs main session via Gateway API
4. Lobs reviews and decides using lobs-api.sh initiatives/batch-decide
5. High-risk categories also create Rafe inbox items
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

        # --- Collect remaining proposals for Lobs main session review ---
        remaining = [i for i in initiatives if i.status == "proposed"]

        if not remaining:
            sweep = self._create_sweep_record(
                sweep_id, start, len(initiatives), 0, 0, rejected, budgets, usage
            )
            self.db.add(sweep)
            await self.db.commit()
            return {"proposed": len(initiatives), "approved": 0, "deferred": 0, "rejected": rejected}

        # --- Mark all remaining as "lobs_review" ---
        approved = 0
        deferred = 0

        for initiative in remaining:
            initiative.status = "lobs_review"

        # --- Send notification to Lobs main session via Gateway API ---
        notification_sent = False
        try:
            await self._notify_lobs_main_session(remaining, budgets, usage, sweep_id)
            notification_sent = True
            logger.info(
                "[SWEEP] Notified Lobs main session of %d pending initiatives",
                len(remaining)
            )
        except Exception as e:
            logger.warning(
                "[SWEEP] Failed to notify Lobs main session: %s — initiatives remain as lobs_review",
                e
            )
            # Create fallback inbox item so Lobs knows there are pending reviews
            await self._create_lobs_review_inbox_item(remaining, sweep_id)

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
    # Lobs main session notification
    # ------------------------------------------------------------------

    async def _notify_lobs_main_session(
        self,
        initiatives: list[AgentInitiative],
        budgets: dict[str, int],
        usage: dict[str, int],
        sweep_id: str,
    ) -> None:
        """Send a formatted message to Lobs main session listing all pending initiatives."""
        import aiohttp
        from app.orchestrator.config import GATEWAY_URL, GATEWAY_TOKEN, GATEWAY_SESSION_KEY

        # Build initiative summary
        budget_summary = {
            agent: f"{usage.get(agent, 0)}/{limit}"
            for agent, limit in budgets.items()
        }

        summary_lines = [
            f"## Initiative Sweep Review — {len(initiatives)} Proposal(s) Pending",
            "",
            f"**Sweep ID:** `{sweep_id}`",
            f"**Daily budgets:** {json.dumps(budget_summary)}",
            "",
            "The agent team has proposed the following initiatives from their strategic reflections. Review each and decide:",
            "- **Approve**: Create a task (use `lobs-api.sh create-task`)",
            "- **Defer**: Not now, maybe later",
            "- **Reject**: Not worth doing",
            "",
            "**Commands:**",
            "```bash",
            "# List all pending initiatives",
            "~/.openclaw/workspace/scripts/lobs-api.sh initiatives",
            "",
            "# Batch-decide after reviewing",
            "~/.openclaw/workspace/scripts/lobs-api.sh batch-decide",
            "```",
            "",
            "---",
            "",
        ]

        for i, init in enumerate(initiatives, 1):
            summary_lines.append(f"### {i}. {init.title}")
            summary_lines.append(f"**ID:** `{init.id}`")
            summary_lines.append(f"**Proposed by:** {init.proposed_by_agent}")
            summary_lines.append(f"**Category:** {init.category}")
            summary_lines.append(f"**Suggested owner:** {init.owner_agent or '(unspecified)'}")
            summary_lines.append(f"**Risk tier:** {init.risk_tier or 'B'}")
            if init.score is not None:
                summary_lines.append(f"**Estimated effort:** {int(init.score)}")
            summary_lines.append("")
            summary_lines.append(init.description or "(no description)")
            summary_lines.append("")
            summary_lines.append("---")
            summary_lines.append("")

        message_text = "\n".join(summary_lines)

        # Send via Gateway sessions_send to main session
        async with aiohttp.ClientSession() as session:
            caller_key = f"{GATEWAY_SESSION_KEY}-sweep-notify-{sweep_id[:8]}"
            resp = await session.post(
                f"{GATEWAY_URL}/tools/invoke",
                headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                json={
                    "tool": "sessions_send",
                    "sessionKey": caller_key,
                    "args": {
                        "sessionKey": "agent:main:main",
                        "message": message_text,
                    }
                },
                timeout=aiohttp.ClientTimeout(total=30)
            )

            data = await resp.json()

            if not data.get("ok"):
                raise RuntimeError(f"Gateway sessions_send failed: {data}")

    async def _create_lobs_review_inbox_item(
        self,
        initiatives: list[AgentInitiative],
        sweep_id: str,
    ) -> None:
        """Create an inbox item summarizing pending reviews (fallback if Gateway notification fails)."""
        title_preview = ", ".join(i.title[:40] for i in initiatives[:3])
        if len(initiatives) > 3:
            title_preview += f" + {len(initiatives) - 3} more"

        content = (
            f"Initiative sweep completed but Lobs notification failed.\n\n"
            f"Sweep ID: {sweep_id}\n"
            f"Pending initiatives: {len(initiatives)}\n\n"
            "Use the following commands to review:\n"
            "```bash\n"
            "~/.openclaw/workspace/scripts/lobs-api.sh initiatives\n"
            "~/.openclaw/workspace/scripts/lobs-api.sh batch-decide\n"
            "```\n\n"
            "Initiatives:\n"
        )

        for init in initiatives:
            content += f"- **{init.title}** (ID: {init.id[:8]}..., by: {init.proposed_by_agent})\n"

        self.db.add(
            InboxItem(
                id=str(uuid.uuid4()),
                title=f"[REVIEW] {len(initiatives)} initiative(s) pending: {title_preview[:80]}",
                content=content,
                is_read=False,
                summary=f"sweep_review_fallback:{sweep_id}",
                modified_at=datetime.now(timezone.utc),
            )
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
