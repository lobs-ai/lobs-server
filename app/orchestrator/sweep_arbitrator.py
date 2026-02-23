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

# Import PolicyEngine for smart escalation routing
from app.orchestrator.policy_engine import PolicyEngine


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

        # --- Create inbox items for each initiative ---
        await self._create_initiative_inbox_items(remaining, sweep_id)
        logger.info(
            "[SWEEP] Created %d inbox items for initiative review",
            len(remaining)
        )

        # --- Send lightweight notification to Lobs main session via Gateway API ---
        try:
            await self._notify_lobs_main_session_lightweight(remaining, sweep_id)
            logger.info(
                "[SWEEP] Notified Lobs main session of %d pending initiatives",
                len(remaining)
            )
        except Exception as e:
            logger.warning(
                "[SWEEP] Failed to notify Lobs main session: %s — inbox items already created",
                e
            )

        # --- Create high-risk inbox items for Rafe using PolicyEngine ---
        policy_engine = PolicyEngine()
        for initiative in remaining:
            # Use initiative.score as estimated_effort (score represents effort in days)
            estimated_effort = int(initiative.score) if initiative.score else None
            decision = policy_engine.decide(initiative.category, estimated_effort=estimated_effort)
            
            if decision.escalate_to_rafe:
                await self._create_rafe_inbox_item(initiative, decision.reason)

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

    async def _create_initiative_inbox_items(
        self,
        initiatives: list[AgentInitiative],
        sweep_id: str,
    ) -> None:
        """Create an inbox item for each initiative needing review."""
        for initiative in initiatives:
            effort_str = f"{int(initiative.score)} day(s)" if initiative.score else "unspecified"
            
            content = (
                f"**Initiative ID:** `{initiative.id}`\n\n"
                f"**Proposed by:** {initiative.proposed_by_agent}\n"
                f"**Category:** {initiative.category}\n"
                f"**Suggested owner:** {initiative.owner_agent or '(unspecified)'}\n"
                f"**Risk tier:** {initiative.risk_tier or 'C'}\n"
                f"**Estimated effort:** {effort_str}\n\n"
                f"**Description:**\n{initiative.description or '(no description)'}\n\n"
                "---\n\n"
                "**Review and decide:**\n"
                "- **Approve**: Create a task from this initiative (use as-is or rescope first)\n"
                "- **Approve + Rescope**: Break big ideas into practical, buildable pieces — "
                "use `revised_title` and `revised_description` to reshape the scope before approving\n"
                "- **Escalate**: Tier-C items — send to Rafe for approval instead of approving directly\n"
                "- **Defer**: Not now, revisit later\n"
                "- **Reject**: Only if fundamentally wrong — prefer rescoping over rejecting\n\n"
                "**💡 Prefer rescoping over rejection.** If an idea is too broad, break it into a "
                "concrete first step. If it overlaps existing work, reshape it to complement.\n\n"
                "**Batch-decide JSON format:**\n"
                "```json\n"
                '{"initiative_id": "' + initiative.id + '", '
                '"decision": "approve", '
                '"revised_title": "Narrower practical title", '
                '"revised_description": "Concrete scope description"}\n'
                "```"
            )

            item_id = str(uuid.uuid4())
            filename = f"review_{initiative.id[:8]}_{int(datetime.now(timezone.utc).timestamp())}.md"
            self.db.add(
                InboxItem(
                    id=item_id,
                    title=f"[REVIEW] {initiative.title[:80]}",
                    filename=filename,
                    relative_path=f"inbox/{filename}",
                    content=content,
                    is_read=False,
                    summary=f"initiative_review:{initiative.id}",
                    modified_at=datetime.now(timezone.utc),
                )
            )

    async def _notify_lobs_main_session_lightweight(
        self,
        initiatives: list[AgentInitiative],
        sweep_id: str,
    ) -> None:
        """Send a lightweight ping to Lobs main session about pending initiatives."""
        import aiohttp
        from app.orchestrator.config import GATEWAY_URL, GATEWAY_TOKEN, GATEWAY_SESSION_KEY

        message_text = (
            f"📋 **Initiative Sweep Complete**\n\n"
            f"**{len(initiatives)} initiative(s)** are awaiting review in your inbox.\n\n"
            f"Check **Mission Control → Inbox** to review and decide on each initiative.\n\n"
            f"Sweep ID: `{sweep_id}`"
        )

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

    async def _create_rafe_inbox_item(self, initiative: AgentInitiative, escalation_reason: str) -> None:
        title = initiative.title or "Untitled initiative"
        effort_str = f"{int(initiative.score)} day(s)" if initiative.score else "unspecified"
        
        content = (
            "🚨 **High-impact initiative escalated for human review**\n\n"
            f"**Why escalated:** {escalation_reason}\n\n"
            f"**Initiative ID:** `{initiative.id}`\n"
            f"**Title:** {title}\n"
            f"**Category:** {initiative.category}\n"
            f"**Proposed by:** {initiative.proposed_by_agent}\n"
            f"**Suggested owner:** {initiative.owner_agent or '(unspecified)'}\n"
            f"**Estimated effort:** {effort_str}\n"
            f"**Risk tier:** {initiative.risk_tier or 'C'}\n\n"
            f"**Description:**\n{initiative.description or '(none)'}\n\n"
            "---\n\n"
            "**Action required:** Review and decide:\n"
            "- **Approve**: Use `lobs-api.sh create-task` to create from this initiative\n"
            "- **Defer**: Not now, revisit later\n"
            "- **Reject**: Not worth doing\n\n"
            f"Commands:\n"
            f"```bash\n"
            f"# View initiative details\n"
            f"~/.openclaw/workspace/scripts/lobs-api.sh get-initiative {initiative.id[:8]}\n\n"
            f"# Create task from initiative\n"
            f"~/.openclaw/workspace/scripts/lobs-api.sh create-task\n"
            f"```"
        )

        item_id = str(uuid.uuid4())
        filename = f"escalation_{initiative.id[:8]}_{int(datetime.now(timezone.utc).timestamp())}.md"
        self.db.add(
            InboxItem(
                id=item_id,
                title=f"🚨 [RAFE] {title[:70]}",
                filename=filename,
                relative_path=f"inbox/{filename}",
                content=content,
                is_read=False,
                summary=f"Escalated: {escalation_reason}",
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
