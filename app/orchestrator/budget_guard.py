"""Budget-aware model routing guard with per-lane spend caps.

Enforces per-day spend limits by task criticality lane:

  critical   — high-criticality tasks (urgent, prod, auth, payment).
               Default: no daily cap — SLA must be preserved.
  standard   — default programmer/researcher/architect tasks.
               Default: $8/day; downgrades from standard → medium if exceeded.
  background — light writer/reviewer/inbox tasks.
               Default: $3/day; downgrades from medium/small → small/micro if exceeded.

When a lane's daily cap is reached, model tier candidates are downgraded to
cheaper alternatives. Critical tasks are never downgraded by default.

Override log entries are persisted to OrchestratorSetting (key:
``budget_guard.override_log``) as a JSON list of the last 500 entries.

Usage
-----
Call ``BudgetGuard.apply()`` just before spawning a worker to get the
effective model candidates (possibly downgraded) and a decision record.

DB key for lane policy: ``budget_guard.lane_policy``
DB key for override log: ``budget_guard.override_log``
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ModelUsageEvent, OrchestratorSetting
from app.orchestrator.model_router import TIER_ORDER, DEFAULT_TIER_MODELS

logger = logging.getLogger(__name__)

# --- DB keys ---
BUDGET_GUARD_LANE_POLICY_KEY = "budget_guard.lane_policy"
BUDGET_GUARD_OVERRIDE_LOG_KEY = "budget_guard.override_log"

# --- Lane names ---
LANE_CRITICAL = "critical"
LANE_STANDARD = "standard"
LANE_BACKGROUND = "background"
ALL_LANES = (LANE_CRITICAL, LANE_STANDARD, LANE_BACKGROUND)

# --- Default lane policy ---
# Each lane entry:
#   daily_cap_usd   — max API spend (USD) for this lane today; None means uncapped.
#   downgrade_tier  — when over cap, restrict candidates to this tier and below;
#                     None means no downgrade (critical SLA preserved).
DEFAULT_LANE_POLICY: dict[str, dict[str, Any]] = {
    LANE_CRITICAL: {
        "daily_cap_usd": None,
        "downgrade_tier": None,
    },
    LANE_STANDARD: {
        "daily_cap_usd": 25.0,
        "downgrade_tier": "medium",
    },
    LANE_BACKGROUND: {
        "daily_cap_usd": 15.0,
        "downgrade_tier": "small",
    },
}

# Models whose tier can be inferred from name (approximate, used for spend tracking).
# Prefer explicit tier_map from DB config for accuracy.
_CRITICAL_KEYWORDS = ("opus", "gpt-5", "o3", "ultra", "strong")
_BACKGROUND_KEYWORDS = ("haiku", "micro", "mini", "ollama", "gemini-nano", "phi", "qwen")
_MAX_OVERRIDE_LOG_ENTRIES = 500


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetGuardDecision:
    """Result of a budget guard check for one task."""

    lane: str
    cap_usd: float | None
    spent_usd: float
    over_budget: bool
    original_candidates: list[str]
    effective_candidates: list[str]
    downgrade_tier: str | None
    downgraded: bool
    reason: str
    task_id: str | None
    ts: str  # ISO timestamp


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def classify_task_lane(
    task: dict[str, Any],
    *,
    agent_type: str,
    criticality: str,
) -> str:
    """Classify a task into a budget lane.

    Hierarchy:
    1. High criticality → critical
    2. Explicit model_tier=strong → critical
    3. Background agents (writer/reviewer) with light/inbox tasks → background
    4. Everything else → standard
    """
    explicit_tier = (task.get("model_tier") or "").strip().lower()
    if criticality == "high" or explicit_tier == "strong":
        return LANE_CRITICAL

    task_status = (task.get("status") or "").strip().lower()
    if agent_type in {"writer", "reviewer"} or (
        task_status == "inbox" and agent_type not in {"programmer", "architect"}
    ):
        return LANE_BACKGROUND

    return LANE_STANDARD


def classify_model_lane(model_name: str) -> str:
    """Classify a model name into a spend lane (best-effort heuristic).

    Used for retroactively attributing historical spend by lane when no
    explicit lane metadata is stored on ``ModelUsageEvent``.
    """
    lower = (model_name or "").lower()
    if any(k in lower for k in _CRITICAL_KEYWORDS):
        return LANE_CRITICAL
    if any(k in lower for k in _BACKGROUND_KEYWORDS):
        return LANE_BACKGROUND
    return LANE_STANDARD


def _effective_lane_policy(raw: Any) -> dict[str, dict[str, Any]]:
    """Merge stored policy over defaults. Unknown keys ignored."""
    policy = {lane: dict(cfg) for lane, cfg in DEFAULT_LANE_POLICY.items()}
    if isinstance(raw, dict):
        for lane in ALL_LANES:
            if lane in raw and isinstance(raw[lane], dict):
                entry = raw[lane]
                if "daily_cap_usd" in entry:
                    policy[lane]["daily_cap_usd"] = entry["daily_cap_usd"]
                if "downgrade_tier" in entry:
                    policy[lane]["downgrade_tier"] = entry["downgrade_tier"]
    return policy


def _filter_to_tier_and_below(
    candidates: list[str],
    max_tier: str,
    tier_map: dict[str, list[str]],
) -> list[str]:
    """Keep only candidates whose tier is at or below *max_tier* in TIER_ORDER.

    Falls back to the full candidate list if filtering leaves nothing,
    so the caller always has models to work with.
    """
    max_idx = TIER_ORDER.index(max_tier) if max_tier in TIER_ORDER else len(TIER_ORDER) - 1  # type: ignore[arg-type]
    allowed_tiers = set(TIER_ORDER[: max_idx + 1])

    # Build reverse map: model → tier
    model_to_tier: dict[str, str] = {}
    for tier, models in tier_map.items():
        for m in models or []:
            model_to_tier[m] = tier

    filtered = [m for m in candidates if model_to_tier.get(m, LANE_STANDARD) in allowed_tiers]
    return filtered or candidates  # safety: never return empty list


# ---------------------------------------------------------------------------
# BudgetGuard class
# ---------------------------------------------------------------------------


class BudgetGuard:
    """Per-day spend cap enforcement with tier downgrade.

    Instantiate per-request (lightweight; all state is in DB).
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def today_lane_spend(self, lane: str) -> float:
        """Sum today's estimated API cost attributed to *lane*.

        Primary: uses the ``budget_lane`` column (set at worker spawn time).
        Fallback for legacy events without ``budget_lane``: model-name keyword heuristics.
        Subscription route costs are excluded (always $0).
        """
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        # --- Primary: events with explicit budget_lane set ---
        r_explicit = await self.db.execute(
            select(func.coalesce(func.sum(ModelUsageEvent.estimated_cost_usd), 0.0)).where(
                ModelUsageEvent.timestamp >= today_start,
                ModelUsageEvent.route_type != "subscription",
                ModelUsageEvent.budget_lane == lane,
            )
        )
        explicit_spend = float(r_explicit.scalar() or 0.0)

        # --- Fallback: legacy events where budget_lane is NULL, use model heuristics ---
        if lane == LANE_CRITICAL:
            fallback_total = 0.0
            for kw in _CRITICAL_KEYWORDS:
                r = await self.db.execute(
                    select(func.coalesce(func.sum(ModelUsageEvent.estimated_cost_usd), 0.0)).where(
                        ModelUsageEvent.timestamp >= today_start,
                        ModelUsageEvent.route_type != "subscription",
                        ModelUsageEvent.budget_lane.is_(None),
                        ModelUsageEvent.model.ilike(f"%{kw}%"),
                    )
                )
                # Sum across distinct keyword matches (additive attribution is
                # an overestimate but prevents undercount for legacy events).
                fallback_total += float(r.scalar() or 0.0)
            return explicit_spend + fallback_total

        if lane == LANE_BACKGROUND:
            fallback_total = 0.0
            for kw in _BACKGROUND_KEYWORDS:
                r = await self.db.execute(
                    select(func.coalesce(func.sum(ModelUsageEvent.estimated_cost_usd), 0.0)).where(
                        ModelUsageEvent.timestamp >= today_start,
                        ModelUsageEvent.route_type != "subscription",
                        ModelUsageEvent.budget_lane.is_(None),
                        ModelUsageEvent.model.ilike(f"%{kw}%"),
                    )
                )
                fallback_total += float(r.scalar() or 0.0)
            return explicit_spend + fallback_total

        # LANE_STANDARD: legacy events = total - critical - background (approximate)
        r_total = await self.db.execute(
            select(func.coalesce(func.sum(ModelUsageEvent.estimated_cost_usd), 0.0)).where(
                ModelUsageEvent.timestamp >= today_start,
                ModelUsageEvent.route_type != "subscription",
                ModelUsageEvent.budget_lane.is_(None),
            )
        )
        legacy_total = float(r_total.scalar() or 0.0)

        r_crit_legacy = await self.db.execute(
            select(func.coalesce(func.sum(ModelUsageEvent.estimated_cost_usd), 0.0)).where(
                ModelUsageEvent.timestamp >= today_start,
                ModelUsageEvent.route_type != "subscription",
                ModelUsageEvent.budget_lane.is_(None),
                # Critical keywords heuristic
                func.lower(ModelUsageEvent.model).contains("opus"),
            )
        )
        r_bg_legacy = await self.db.execute(
            select(func.coalesce(func.sum(ModelUsageEvent.estimated_cost_usd), 0.0)).where(
                ModelUsageEvent.timestamp >= today_start,
                ModelUsageEvent.route_type != "subscription",
                ModelUsageEvent.budget_lane.is_(None),
                # Background keywords heuristic
                func.lower(ModelUsageEvent.model).contains("haiku"),
            )
        )
        legacy_crit = float(r_crit_legacy.scalar() or 0.0)
        legacy_bg = float(r_bg_legacy.scalar() or 0.0)
        legacy_standard = max(0.0, legacy_total - legacy_crit - legacy_bg)

        return explicit_spend + legacy_standard

    async def _load_lane_policy(self) -> dict[str, dict[str, Any]]:
        row = await self.db.get(OrchestratorSetting, BUDGET_GUARD_LANE_POLICY_KEY)
        return _effective_lane_policy(row.value if row and isinstance(row.value, dict) else None)

    async def apply(
        self,
        *,
        task: dict[str, Any],
        agent_type: str,
        criticality: str,
        candidates: list[str],
        tier_map: dict[str, list[str]],
    ) -> BudgetGuardDecision:
        """Apply lane-based budget cap to *candidates*.

        Returns a decision with the effective candidate list (possibly
        downgraded) and an audit trail.
        """
        lane = classify_task_lane(task, agent_type=agent_type, criticality=criticality)
        policy = await self._load_lane_policy()
        lane_cfg = policy.get(lane, DEFAULT_LANE_POLICY[LANE_STANDARD])

        cap_usd: float | None = lane_cfg.get("daily_cap_usd")
        downgrade_tier: str | None = lane_cfg.get("downgrade_tier")

        ts = datetime.now(timezone.utc).isoformat()
        task_id = task.get("id")

        # No cap configured for this lane → pass through
        if cap_usd is None:
            return BudgetGuardDecision(
                lane=lane,
                cap_usd=None,
                spent_usd=0.0,
                over_budget=False,
                original_candidates=list(candidates),
                effective_candidates=list(candidates),
                downgrade_tier=None,
                downgraded=False,
                reason="no_cap",
                task_id=task_id,
                ts=ts,
            )

        spent_usd = await self.today_lane_spend(lane)
        over_budget = float(spent_usd) >= float(cap_usd)

        if not over_budget or downgrade_tier is None:
            return BudgetGuardDecision(
                lane=lane,
                cap_usd=cap_usd,
                spent_usd=spent_usd,
                over_budget=over_budget,
                original_candidates=list(candidates),
                effective_candidates=list(candidates),
                downgrade_tier=None,
                downgraded=False,
                reason="within_budget" if not over_budget else "over_budget_no_downgrade_policy",
                task_id=task_id,
                ts=ts,
            )

        # Over budget → apply downgrade
        effective = _filter_to_tier_and_below(candidates, downgrade_tier, tier_map)
        downgraded = effective != candidates

        if downgraded:
            reason = (
                f"over_budget: lane={lane} spent=${spent_usd:.4f} cap=${cap_usd:.2f} "
                f"downgraded_to_tier_max={downgrade_tier}"
            )
            logger.warning(
                "[BUDGET_GUARD] %s task=%s agent=%s %s",
                lane.upper(),
                task_id,
                agent_type,
                reason,
            )
            await self._append_override_log({
                "ts": ts,
                "task_id": task_id,
                "lane": lane,
                "agent_type": agent_type,
                "cap_usd": cap_usd,
                "spent_usd": round(spent_usd, 4),
                "downgrade_tier": downgrade_tier,
                "original_candidates": list(candidates),
                "effective_candidates": list(effective),
            })
        else:
            reason = f"over_budget_but_candidates_already_within_tier={downgrade_tier}"

        return BudgetGuardDecision(
            lane=lane,
            cap_usd=cap_usd,
            spent_usd=spent_usd,
            over_budget=over_budget,
            original_candidates=list(candidates),
            effective_candidates=list(effective),
            downgrade_tier=downgrade_tier,
            downgraded=downgraded,
            reason=reason,
            task_id=task_id,
            ts=ts,
        )

    async def _append_override_log(self, entry: dict[str, Any]) -> None:
        """Append an override log entry to the persistent log (capped at 500)."""
        row = await self.db.get(OrchestratorSetting, BUDGET_GUARD_OVERRIDE_LOG_KEY)
        current: list[dict[str, Any]] = []
        if row and isinstance(row.value, list):
            current = row.value

        current.append(entry)
        # Keep only latest N entries
        if len(current) > _MAX_OVERRIDE_LOG_ENTRIES:
            current = current[-_MAX_OVERRIDE_LOG_ENTRIES:]

        if row is None:
            row = OrchestratorSetting(key=BUDGET_GUARD_OVERRIDE_LOG_KEY, value=current)
            self.db.add(row)
        else:
            row.value = current
        # Flush (caller commits as part of broader transaction or main flow)
        await self.db.flush()

    async def today_spend_all_lanes(self) -> dict[str, float]:
        """Return today's spend per lane for the daily report."""
        return {
            LANE_CRITICAL: await self.today_lane_spend(LANE_CRITICAL),
            LANE_STANDARD: await self.today_lane_spend(LANE_STANDARD),
            LANE_BACKGROUND: await self.today_lane_spend(LANE_BACKGROUND),
        }

    async def today_override_log(self) -> list[dict[str, Any]]:
        """Return today's override log entries."""
        today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = await self.db.get(OrchestratorSetting, BUDGET_GUARD_OVERRIDE_LOG_KEY)
        if not row or not isinstance(row.value, list):
            return []
        return [e for e in row.value if isinstance(e, dict) and str(e.get("ts", "")).startswith(today_prefix)]
