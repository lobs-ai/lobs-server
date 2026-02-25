"""Initiative pruning service.

Weekly review that:
1. Finds approved research initiatives with no build/no-build memo after 7+ days
2. Flags them as stale
3. Creates inbox items to surface the gap

Also flags initiatives that have been `deferred` for 30+ days as stale.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentInitiative as AgentInitiativeModel, InboxItem, ResearchMemo as ResearchMemoModel

logger = logging.getLogger(__name__)

# Thresholds
MEMO_MISSING_DAYS = 7   # approved research initiative must have a memo within this many days
DEFERRED_STALE_DAYS = 30  # deferred initiatives expire after this many days

RESEARCH_CATEGORIES = {
    "research",
    "light_research",
    "investigation",
    "discovery",
    "analysis",
    "feasibility",
}


async def get_stale_initiatives(db: AsyncSession) -> list[AgentInitiativeModel]:
    """Return approved research initiatives that are >7 days old and have no memo."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=MEMO_MISSING_DAYS)

    result = await db.execute(
        select(AgentInitiativeModel).where(
            AgentInitiativeModel.status == "approved",
            AgentInitiativeModel.created_at < cutoff,
        )
    )
    candidates = result.scalars().all()

    stale: list[AgentInitiativeModel] = []
    for initiative in candidates:
        # Only flag research-category initiatives
        cat = (initiative.category or "").lower()
        if not any(rc in cat for rc in RESEARCH_CATEGORIES):
            continue

        # Check if a memo exists
        memo_result = await db.execute(
            select(ResearchMemoModel).where(ResearchMemoModel.initiative_id == initiative.id)
        )
        if memo_result.scalar_one_or_none() is None:
            stale.append(initiative)

    return stale


async def prune_stale_initiatives(db: AsyncSession) -> dict:
    """Weekly pruning job — flags stale initiatives and creates inbox items.

    Returns a dict summarising what was done.
    """
    now = datetime.now(timezone.utc)
    flagged_ids: list[str] = []
    inbox_ids: list[str] = []

    # 1. Approved research initiatives with no memo after MEMO_MISSING_DAYS
    stale = await get_stale_initiatives(db)
    for initiative in stale:
        title = initiative.title or "Untitled"
        age_days = (now - initiative.created_at.replace(tzinfo=timezone.utc)).days

        logger.info(
            "[PRUNING] Stale research initiative (no memo): %s — %s (%d days old)",
            initiative.id[:8],
            title,
            age_days,
        )
        flagged_ids.append(initiative.id)

        # Create inbox item
        item_id = str(uuid.uuid4())
        filename = f"stale_initiative_{initiative.id[:8]}_{int(now.timestamp())}.md"
        content = (
            f"## 📋 Stale Research Initiative — Build/No-Build Memo Missing\n\n"
            f"**Initiative:** {title}\n"
            f"**ID:** `{initiative.id}`\n"
            f"**Age:** {age_days} days since approval\n"
            f"**Proposed by:** {initiative.proposed_by_agent}\n"
            f"**Category:** {initiative.category}\n\n"
            f"This research initiative was approved but has no build/no-build decision memo.\n"
            f"Every research initiative must close with a memo before the associated task "
            f"can be marked complete.\n\n"
            f"**Action needed:** Submit a memo via `POST /api/initiatives/{initiative.id}/memo`\n\n"
            f"Memo fields required: problem, user_segment, spec_touchpoints, mvp_scope, owner, "
            f"decision (build/no_build), rationale.\n"
        )
        db.add(
            InboxItem(
                id=item_id,
                title=f"📋 [MEMO MISSING] Research initiative stale: {title[:60]}",
                filename=filename,
                relative_path=f"inbox/{filename}",
                content=content,
                is_read=False,
                summary=f"stale_research_initiative:{initiative.id}",
                modified_at=now,
            )
        )
        inbox_ids.append(item_id)

    # 2. Deferred initiatives that have been deferred >DEFERRED_STALE_DAYS
    deferred_cutoff = now - timedelta(days=DEFERRED_STALE_DAYS)
    deferred_result = await db.execute(
        select(AgentInitiativeModel).where(
            AgentInitiativeModel.status == "deferred",
            AgentInitiativeModel.updated_at < deferred_cutoff,
        )
    )
    deferred_stale = deferred_result.scalars().all()
    for initiative in deferred_stale:
        title = initiative.title or "Untitled"
        days_deferred = (now - initiative.updated_at.replace(tzinfo=timezone.utc)).days
        logger.info(
            "[PRUNING] Long-deferred initiative: %s — %s (%d days)",
            initiative.id[:8],
            title,
            days_deferred,
        )

        item_id = str(uuid.uuid4())
        filename = f"deferred_initiative_{initiative.id[:8]}_{int(now.timestamp())}.md"
        content = (
            f"## 🗃️ Long-Deferred Initiative — Weekly Review\n\n"
            f"**Initiative:** {title}\n"
            f"**ID:** `{initiative.id}`\n"
            f"**Deferred for:** {days_deferred} days\n"
            f"**Proposed by:** {initiative.proposed_by_agent}\n"
            f"**Category:** {initiative.category}\n\n"
            f"This initiative has been in 'deferred' status for over {DEFERRED_STALE_DAYS} days.\n"
            f"Consider rejecting it to keep the initiative backlog clean, or re-approve if "
            f"it's still relevant.\n\n"
            f"**Action:** `reject {initiative.id[:8]}` or `approve {initiative.id[:8]}`\n"
        )
        db.add(
            InboxItem(
                id=item_id,
                title=f"🗃️ [REVIEW] Long-deferred initiative: {title[:60]}",
                filename=filename,
                relative_path=f"inbox/{filename}",
                content=content,
                is_read=False,
                summary=f"long_deferred_initiative:{initiative.id}",
                modified_at=now,
            )
        )
        inbox_ids.append(item_id)

    await db.flush()

    summary = {
        "stale_memo_missing": len(stale),
        "long_deferred": len(deferred_stale),
        "inbox_items_created": len(inbox_ids),
        "flagged_initiative_ids": flagged_ids,
    }
    logger.info("[PRUNING] Done: %s", summary)
    return summary
