"""Budget guardrails and auto-downgrade policy for model selection.

Implements per-day spend caps organized by task criticality lane:
  - critical   — high-criticality tasks; cap enforced by capping at "standard" tier max
  - standard   — normal tasks; cap enforced by capping at "medium" tier max
  - background — light/reflective tasks; cap enforced by capping at "small" tier max

Design:
  1. At model-selection time, classify task into a lane.
  2. Query today's spend for that lane (estimated from usage events).
  3. If lane cap is hit, strip higher-tier models from the candidate list.
  4. Audit all downgrade decisions for the daily report.

Daily report: GET /api/usage/daily-report provides:
  - Today's total spend vs daily hard cap
  - Per-provider spend
  - Budget lane caps and utilization
  - Recent downgrade events (stored in OrchestratorSetting as rolling log)
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ModelUsageEvent, OrchestratorSetting
from app.orchestrator.model_router import TIER_ORDER, ModelTier

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

TaskLane = Literal["critical", "standard", "background"]

LANE_ORDER: list[TaskLane] = ["critical", "standard", "background"]

# When a lane cap is hit, downgrade to this maximum tier.
# "critical" → still allow "standard" (preserve SLA, just no "strong")
# "standard" → allow up to "medium"
# "background" → allow up to "small"
LANE_DOWNGRADE_MAX_TIER: dict[TaskLane, ModelTier] = {
    "critical": "standard",
    "standard": "medium",
    "background": "small",
}

BUDGET_POLICY_KEY = "usage.budget_policy"
BUDGET_OVERRIDE_LOG_KEY = "usage.budget_override_log"


# ---------------------------------------------------------------------------
# Lane classification
# ---------------------------------------------------------------------------


def classify_task_lane(
    task: dict[str, Any],
    *,
    agent_type: str,
    complexity: str,
    purpose: str = "execution",
) -> TaskLane:
    """Classify a task into a budget lane.

    Lane determines which per-day cap applies and how aggressively to downgrade.

    critical   — High-criticality tasks or programmer execution (real code changes)
    standard   — Normal agent work (researcher, reviewer, writer on complex tasks)
    background — Light tasks, reflections, diagnostics, or writer on simple tasks
    """
    criticality_text = (
        (task.get("title") or "") + " " + (task.get("notes") or "")
    ).lower()

    _high_criticality_keywords = (
        "incident", "outage", "downtime", "urgent", "security",
        "vulnerability", "data loss", "prod", "production", "auth", "payment",
    )
    is_high_criticality = any(k in criticality_text for k in _high_criticality_keywords)

    if is_high_criticality or (agent_type == "programmer" and purpose == "execution"):
        return "critical"

    if purpose in {"reflection", "diagnostic"} or complexity == "light":
        return "background"

    return "standard"


# ---------------------------------------------------------------------------
# Daily spend queries
# ---------------------------------------------------------------------------


def _today_start_utc() -> datetime:
    today = date.today()
    return datetime(today.year, today.month, today.day, tzinfo=timezone.utc)


async def get_today_total_spend(db: AsyncSession) -> float:
    """Return today's total estimated spend in USD."""
    since = _today_start_utc()
    result = await db.execute(
        select(func.coalesce(func.sum(ModelUsageEvent.estimated_cost_usd), 0.0)).where(
            ModelUsageEvent.timestamp >= since,
        )
    )
    return float(result.scalar() or 0.0)


async def get_today_spend_by_provider(db: AsyncSession) -> dict[str, float]:
    """Return today's spend broken out by provider."""
    since = _today_start_utc()
    result = await db.execute(
        select(
            ModelUsageEvent.provider,
            func.coalesce(func.sum(ModelUsageEvent.estimated_cost_usd), 0.0).label("spend"),
        )
        .where(ModelUsageEvent.timestamp >= since)
        .group_by(ModelUsageEvent.provider)
    )
    return {row.provider: float(row.spend) for row in result.all()}


async def get_today_spend_by_task_type(db: AsyncSession) -> dict[str, float]:
    """Return today's spend broken out by task_type (proxy for lane).

    Lane-to-task_type rough mapping:
      critical   ← task_type contains 'programmer', 'critical', 'urgent'
      background ← task_type in ('reflection', 'diagnostic', 'inbox', 'quick_summary')
      standard   ← everything else
    """
    since = _today_start_utc()
    result = await db.execute(
        select(
            ModelUsageEvent.task_type,
            func.coalesce(func.sum(ModelUsageEvent.estimated_cost_usd), 0.0).label("spend"),
        )
        .where(ModelUsageEvent.timestamp >= since)
        .group_by(ModelUsageEvent.task_type)
    )
    return {row.task_type: float(row.spend) for row in result.all()}


def _estimate_lane_spend(spend_by_task_type: dict[str, float]) -> dict[TaskLane, float]:
    """Estimate per-lane spend from task_type spend data.

    This is a best-effort heuristic.  The worker logs usage with task_type
    set to the agent type (programmer, researcher, etc.).  Over time this can
    be refined to store the explicit lane.
    """
    critical_types = {"programmer", "critical", "urgent"}
    background_types = {"reflection", "diagnostic", "inbox", "quick_summary", "triage", "inbox_item"}

    lane_spend: dict[TaskLane, float] = {"critical": 0.0, "standard": 0.0, "background": 0.0}
    for task_type, spend in spend_by_task_type.items():
        if task_type in critical_types:
            lane_spend["critical"] += spend
        elif task_type in background_types:
            lane_spend["background"] += spend
        else:
            lane_spend["standard"] += spend

    return lane_spend


# ---------------------------------------------------------------------------
# Downgrade logic
# ---------------------------------------------------------------------------


def apply_lane_downgrade(
    candidates: list[str],
    *,
    tier_map: dict[str, list[str]],
    lane: TaskLane,
    lane_spend: float,
    lane_cap: float,
) -> tuple[list[str], str | None]:
    """Downgrade candidates when lane cap is exceeded.

    Returns (filtered_candidates, downgrade_reason | None).
    If no downgrade is needed, returns original candidates unchanged.
    If downgrade removes everything, falls back to original list (safety net).
    """
    if lane_cap <= 0.0 or lane_spend < lane_cap:
        return candidates, None

    max_tier: ModelTier = LANE_DOWNGRADE_MAX_TIER[lane]
    max_tier_idx = TIER_ORDER.index(max_tier)  # type: ignore[arg-type]
    allowed_tiers: list[str] = TIER_ORDER[: max_tier_idx + 1]

    allowed_models: set[str] = set()
    for tier in allowed_tiers:
        for m in tier_map.get(tier) or []:
            allowed_models.add(m)

    filtered = [c for c in candidates if c in allowed_models]

    if not filtered:
        # Safety fallback: cap removed everything, keep original list
        logger.warning(
            "[BUDGET_GUARDRAILS] Lane cap downgrade (%s) removed all candidates — "
            "falling back to original list. spend=%.4f cap=%.4f",
            lane,
            lane_spend,
            lane_cap,
        )
        return candidates, f"lane_cap_hit_but_no_fallback({lane})"

    downgrade_reason = (
        f"lane_cap_exceeded(lane={lane}, spend={lane_spend:.4f}, cap={lane_cap:.4f}, "
        f"max_tier={max_tier})"
    )
    logger.info(
        "[BUDGET_GUARDRAILS] %s — dropped %d candidates, kept %d",
        downgrade_reason,
        len(candidates) - len(filtered),
        len(filtered),
    )
    return filtered, downgrade_reason


def apply_daily_hard_cap(
    candidates: list[str],
    *,
    daily_spend: float,
    daily_hard_cap: float,
    tier_map: dict[str, list[str]],
) -> tuple[list[str], str | None]:
    """Enforce global daily hard cap across all lanes.

    When the daily hard cap is hit, downgrade to micro/small tier only.
    This is a last-resort guardrail applied before lane-level downgrade.
    """
    if daily_hard_cap <= 0.0 or daily_spend < daily_hard_cap:
        return candidates, None

    # Hard cap hit: allow only micro/small models (cheapest possible)
    allowed_models: set[str] = set()
    for tier in ("micro", "small"):
        for m in tier_map.get(tier) or []:
            allowed_models.add(m)

    filtered = [c for c in candidates if c in allowed_models]

    if not filtered:
        logger.warning(
            "[BUDGET_GUARDRAILS] Daily hard cap hit (%.4f/%.4f) but no micro/small "
            "candidates available — keeping original list",
            daily_spend,
            daily_hard_cap,
        )
        return candidates, f"daily_hard_cap_hit_no_cheap_fallback(spend={daily_spend:.4f})"

    reason = f"daily_hard_cap_exceeded(spend={daily_spend:.4f}, cap={daily_hard_cap:.4f})"
    logger.warning("[BUDGET_GUARDRAILS] %s", reason)
    return filtered, reason


# ---------------------------------------------------------------------------
# Override log (rolling ring buffer in OrchestratorSetting)
# ---------------------------------------------------------------------------

_MAX_OVERRIDE_LOG_SIZE = 200


async def append_override_log(
    db: AsyncSession,
    *,
    lane: TaskLane | None,
    reason: str,
    original_model: str | None,
    downgraded_model: str | None,
    task_id: Any | None,
    agent_type: str | None,
) -> None:
    """Append a downgrade event to the rolling override log in OrchestratorSetting."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "lane": lane,
        "reason": reason,
        "original_model": original_model,
        "downgraded_model": downgraded_model,
        "task_id": str(task_id) if task_id is not None else None,
        "agent_type": agent_type,
    }

    row = await db.get(OrchestratorSetting, BUDGET_OVERRIDE_LOG_KEY)
    if row is None:
        row = OrchestratorSetting(key=BUDGET_OVERRIDE_LOG_KEY, value=[entry])
        db.add(row)
    else:
        log: list[dict] = row.value if isinstance(row.value, list) else []
        log.append(entry)
        # Keep only the most recent N entries
        row.value = log[-_MAX_OVERRIDE_LOG_SIZE:]

    await db.flush()


async def get_override_log(
    db: AsyncSession,
    *,
    limit: int = 50,
    since: datetime | None = None,
) -> list[dict]:
    """Fetch recent override log entries."""
    row = await db.get(OrchestratorSetting, BUDGET_OVERRIDE_LOG_KEY)
    if row is None or not isinstance(row.value, list):
        return []

    entries: list[dict] = row.value
    if since is not None:
        since_str = since.isoformat()
        entries = [e for e in entries if e.get("ts", "") >= since_str]

    return entries[-limit:]


# ---------------------------------------------------------------------------
# Daily cost-vs-quality report
# ---------------------------------------------------------------------------


async def build_daily_report(
    db: AsyncSession,
    *,
    budget_limits: dict[str, Any],
) -> dict[str, Any]:
    """Build the daily cost-vs-quality report.

    budget_limits is the raw dict from BudgetLimits (loaded from DB or defaults).
    """
    today = date.today().isoformat()
    total_spend = await get_today_total_spend(db)
    by_provider = await get_today_spend_by_provider(db)
    by_task_type = await get_today_spend_by_task_type(db)
    lane_spend = _estimate_lane_spend(by_task_type)

    daily_hard_cap = float(budget_limits.get("daily_hard_cap_usd") or 0.0)
    lane_caps: dict[str, float] = {}
    raw_lane_caps = budget_limits.get("per_lane_daily_caps")
    if isinstance(raw_lane_caps, dict):
        lane_caps = {k: float(v) for k, v in raw_lane_caps.items() if v is not None}

    # Utilization percentages
    def _utilization(spend: float, cap: float) -> float | None:
        if cap <= 0:
            return None
        return round(spend / cap * 100, 1)

    lane_status = {}
    for lane in LANE_ORDER:
        cap = lane_caps.get(lane, 0.0)
        spend = lane_spend.get(lane, 0.0)
        util = _utilization(spend, cap)
        lane_status[lane] = {
            "spend_usd": round(spend, 4),
            "cap_usd": cap if cap > 0 else None,
            "utilization_pct": util,
            "at_cap": cap > 0 and spend >= cap,
        }

    # Recent overrides (today only)
    today_start = _today_start_utc()
    recent_overrides = await get_override_log(db, limit=100, since=today_start)

    # Budget alerts
    alerts: list[str] = []
    if daily_hard_cap > 0:
        util = _utilization(total_spend, daily_hard_cap)
        if util is not None and util >= 90:
            alerts.append(f"Daily hard cap at {util:.0f}% utilization (${total_spend:.2f}/${daily_hard_cap:.2f})")
    for lane, status in lane_status.items():
        if status["at_cap"]:
            alerts.append(f"{lane} lane cap exceeded — auto-downgrade active")
        elif status["utilization_pct"] is not None and status["utilization_pct"] >= 80:
            alerts.append(f"{lane} lane at {status['utilization_pct']:.0f}% of daily cap")

    return {
        "date": today,
        "total_spend_usd": round(total_spend, 4),
        "daily_hard_cap_usd": daily_hard_cap if daily_hard_cap > 0 else None,
        "hard_cap_utilization_pct": _utilization(total_spend, daily_hard_cap),
        "hard_cap_exceeded": daily_hard_cap > 0 and total_spend >= daily_hard_cap,
        "by_provider": [
            {"provider": p, "spend_usd": round(s, 4)}
            for p, s in sorted(by_provider.items(), key=lambda x: -x[1])
        ],
        "lane_status": lane_status,
        "alerts": alerts,
        "override_events": recent_overrides,
        "override_count_today": len(recent_overrides),
    }
