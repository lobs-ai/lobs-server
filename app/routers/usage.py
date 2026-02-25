"""Usage tracking, budgets, and routing policy endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ModelPricing as ModelPricingModel, ModelUsageEvent as ModelUsageEventModel, OrchestratorSetting
from app.orchestrator.budget_guard import (
    BudgetGuard,
    BUDGET_GUARD_LANE_POLICY_KEY,
    DEFAULT_LANE_POLICY,
    ALL_LANES,
    _effective_lane_policy,
)
from app.orchestrator.budget_guardrails import build_daily_report
from app.schemas import (
    BudgetLimits,
    ModelPricing,
    ModelPricingCreate,
    ModelUsageEvent,
    ModelUsageEventCreate,
    RoutingPolicy,
    UsageModelSummary,
    UsageProjectionResponse,
    UsageProviderSummary,
    UsageSummaryResponse,
)
from app.services.openclaw_models import fetch_openclaw_model_catalog
from app.services.usage import log_usage_event

router = APIRouter(prefix="/usage", tags=["usage"])
routing_router = APIRouter(prefix="/routing", tags=["usage"])

BUDGETS_KEY = "usage.budgets"
ROUTING_POLICY_KEY = "usage.routing_policy"
OPENCLAW_MODEL_CATALOG_KEY = "usage.openclaw_model_catalog"

DEFAULT_BUDGETS = BudgetLimits(
    monthly_total_usd=150.0,
    daily_alert_usd=10.0,
    per_provider_monthly_usd={
        "openai": 75.0,
        "claude": 75.0,
        "kimi": 50.0,
        "minimax": 50.0,
    },
    per_task_hard_cap_usd=2.0,
)

DEFAULT_ROUTING_POLICY = RoutingPolicy(
    subscription_first_task_types=["inbox", "quick_summary", "triage", "inbox_item"],
    subscription_providers=[],
    subscription_models=[],
    fallback_chains={
        "inbox": ["subscription", "kimi", "minimax", "openai", "claude"],
        "quick_summary": ["subscription", "kimi", "minimax", "openai", "claude"],
        "triage": ["subscription", "kimi", "minimax", "openai", "claude"],
        "default": ["openai", "claude", "kimi", "minimax", "subscription"],
    },
    quality_preference=["claude", "openai", "kimi", "minimax"],
)


def _window_start(window: str, now: datetime) -> datetime:
    if window == "day":
        return now - timedelta(days=1)
    if window == "week":
        return now - timedelta(days=7)
    if window == "month":
        return now - timedelta(days=30)
    raise ValueError("window must be one of: day, week, month")


def _error_rate(total_requests: int, error_requests: int) -> float:
    if total_requests <= 0:
        return 0.0
    return round(error_requests / total_requests, 4)


async def _get_setting_json(db: AsyncSession, key: str) -> Any | None:
    row = await db.get(OrchestratorSetting, key)
    return row.value if row else None


def _normalize_routing_policy_dict(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw)
    if not out.get("subscription_first_task_types") and isinstance(out.get("gemini_first_task_types"), list):
        out["subscription_first_task_types"] = out["gemini_first_task_types"]
    out.setdefault("subscription_providers", [])
    out.setdefault("subscription_models", [])
    out.setdefault("subscription_first_task_types", ["inbox", "quick_summary", "triage"])
    return out


async def _put_setting_json(db: AsyncSession, key: str, value: Any) -> None:
    if key == ROUTING_POLICY_KEY and isinstance(value, dict):
        value = _normalize_routing_policy_dict(value)

    row = await db.get(OrchestratorSetting, key)
    if row is None:
        row = OrchestratorSetting(key=key, value=value)
        db.add(row)
    else:
        row.value = value
    await db.commit()


@router.post("/events", response_model=ModelUsageEvent)
async def create_usage_event(payload: ModelUsageEventCreate, db: AsyncSession = Depends(get_db)) -> ModelUsageEvent:
    event = await log_usage_event(
        db,
        source=payload.source,
        model=payload.model,
        provider=payload.provider,
        route_type=payload.route_type,
        task_type=payload.task_type,
        budget_lane=payload.budget_lane,
        input_tokens=payload.input_tokens,
        output_tokens=payload.output_tokens,
        cached_tokens=payload.cached_tokens,
        requests=payload.requests,
        latency_ms=payload.latency_ms,
        status=payload.status,
        estimated_cost_usd=payload.estimated_cost_usd,
        error_code=payload.error_code,
        metadata=payload.event_metadata,
        timestamp=payload.timestamp,
    )
    await db.commit()
    await db.refresh(event)
    return event


@router.get("/summary", response_model=UsageSummaryResponse)
async def get_usage_summary(window: str = "month", db: AsyncSession = Depends(get_db)) -> UsageSummaryResponse:
    now = datetime.now(timezone.utc)
    start = _window_start(window, now)

    totals_result = await db.execute(
        select(
            func.coalesce(func.sum(ModelUsageEventModel.requests), 0),
            func.coalesce(func.sum(ModelUsageEventModel.input_tokens), 0),
            func.coalesce(func.sum(ModelUsageEventModel.output_tokens), 0),
            func.coalesce(func.sum(ModelUsageEventModel.cached_tokens), 0),
            func.coalesce(func.sum(ModelUsageEventModel.estimated_cost_usd), 0.0),
        ).where(ModelUsageEventModel.timestamp >= start)
    )
    total_requests, total_input, total_output, total_cached, total_cost = totals_result.one()

    provider_result = await db.execute(
        select(
            ModelUsageEventModel.provider,
            func.coalesce(func.sum(ModelUsageEventModel.requests), 0).label("requests"),
            func.coalesce(func.sum(ModelUsageEventModel.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(ModelUsageEventModel.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(ModelUsageEventModel.cached_tokens), 0).label("cached_tokens"),
            func.coalesce(func.sum(ModelUsageEventModel.estimated_cost_usd), 0.0).label("cost"),
            func.avg(ModelUsageEventModel.latency_ms).label("avg_latency"),
            func.coalesce(
                func.sum(case((ModelUsageEventModel.status != "success", ModelUsageEventModel.requests), else_=0)),
                0,
            ).label("error_requests"),
        )
        .where(ModelUsageEventModel.timestamp >= start)
        .group_by(ModelUsageEventModel.provider)
        .order_by(func.sum(ModelUsageEventModel.estimated_cost_usd).desc())
    )

    by_provider: list[UsageProviderSummary] = []
    for row in provider_result.all():
        reqs = int(row.requests or 0)
        errs = int(row.error_requests or 0)
        by_provider.append(
            UsageProviderSummary(
                provider=row.provider,
                requests=reqs,
                input_tokens=int(row.input_tokens or 0),
                output_tokens=int(row.output_tokens or 0),
                cached_tokens=int(row.cached_tokens or 0),
                estimated_cost_usd=float(row.cost or 0.0),
                avg_latency_ms=float(row.avg_latency) if row.avg_latency is not None else None,
                error_rate=_error_rate(reqs, errs),
            )
        )

    model_result = await db.execute(
        select(
            ModelUsageEventModel.provider,
            ModelUsageEventModel.model,
            ModelUsageEventModel.route_type,
            func.coalesce(func.sum(ModelUsageEventModel.requests), 0).label("requests"),
            func.coalesce(func.sum(ModelUsageEventModel.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(ModelUsageEventModel.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(ModelUsageEventModel.cached_tokens), 0).label("cached_tokens"),
            func.coalesce(func.sum(ModelUsageEventModel.estimated_cost_usd), 0.0).label("cost"),
            func.avg(ModelUsageEventModel.latency_ms).label("avg_latency"),
            func.coalesce(
                func.sum(case((ModelUsageEventModel.status != "success", ModelUsageEventModel.requests), else_=0)),
                0,
            ).label("error_requests"),
        )
        .where(ModelUsageEventModel.timestamp >= start)
        .group_by(ModelUsageEventModel.provider, ModelUsageEventModel.model, ModelUsageEventModel.route_type)
        .order_by(func.sum(ModelUsageEventModel.estimated_cost_usd).desc())
    )

    by_model: list[UsageModelSummary] = []
    for row in model_result.all():
        reqs = int(row.requests or 0)
        errs = int(row.error_requests or 0)
        by_model.append(
            UsageModelSummary(
                provider=row.provider,
                model=row.model,
                route_type=row.route_type,
                requests=reqs,
                input_tokens=int(row.input_tokens or 0),
                output_tokens=int(row.output_tokens or 0),
                cached_tokens=int(row.cached_tokens or 0),
                estimated_cost_usd=float(row.cost or 0.0),
                avg_latency_ms=float(row.avg_latency) if row.avg_latency is not None else None,
                error_rate=_error_rate(reqs, errs),
            )
        )

    return UsageSummaryResponse(
        window=window,
        period_start=start,
        period_end=now,
        total_requests=int(total_requests or 0),
        total_input_tokens=int(total_input or 0),
        total_output_tokens=int(total_output or 0),
        total_cached_tokens=int(total_cached or 0),
        total_estimated_cost_usd=float(total_cost or 0.0),
        by_provider=by_provider,
        by_model=by_model,
    )


@router.get("/providers")
async def get_usage_by_provider(window: str = "month", db: AsyncSession = Depends(get_db)) -> list[UsageProviderSummary]:
    summary = await get_usage_summary(window=window, db=db)
    return summary.by_provider


@router.get("/models")
async def get_usage_by_model(window: str = "month", db: AsyncSession = Depends(get_db)) -> list[UsageModelSummary]:
    summary = await get_usage_summary(window=window, db=db)
    return summary.by_model


@router.get("/projection", response_model=UsageProjectionResponse)
async def get_usage_projection(db: AsyncSession = Depends(get_db)) -> UsageProjectionResponse:
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    result = await db.execute(
        select(func.coalesce(func.sum(ModelUsageEventModel.estimated_cost_usd), 0.0)).where(ModelUsageEventModel.timestamp >= month_start)
    )
    month_to_date = float(result.scalar() or 0.0)

    elapsed_days = max((now - month_start).total_seconds() / 86400.0, 1 / 24.0)
    daily_burn = month_to_date / elapsed_days

    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    days_in_month = (next_month - month_start).days
    projected = daily_burn * days_in_month

    return UsageProjectionResponse(
        month_start=month_start,
        now=now,
        month_to_date_cost_usd=round(month_to_date, 4),
        current_daily_burn_usd=round(daily_burn, 4),
        projected_month_end_cost_usd=round(projected, 4),
    )


@router.get("/budgets", response_model=BudgetLimits)
async def get_budgets(db: AsyncSession = Depends(get_db)) -> BudgetLimits:
    raw = await _get_setting_json(db, BUDGETS_KEY)
    if not isinstance(raw, dict):
        return DEFAULT_BUDGETS
    return BudgetLimits(**raw)


@router.patch("/budgets", response_model=BudgetLimits)
async def patch_budgets(payload: BudgetLimits, db: AsyncSession = Depends(get_db)) -> BudgetLimits:
    await _put_setting_json(db, BUDGETS_KEY, payload.model_dump())
    return payload


@router.get("/pricing", response_model=list[ModelPricing])
async def list_pricing(db: AsyncSession = Depends(get_db)) -> list[ModelPricing]:
    result = await db.execute(select(ModelPricingModel).order_by(ModelPricingModel.provider, ModelPricingModel.model, ModelPricingModel.effective_date.desc()))
    return list(result.scalars().all())


@router.post("/pricing", response_model=ModelPricing)
async def create_pricing(payload: ModelPricingCreate, db: AsyncSession = Depends(get_db)) -> ModelPricing:
    pricing = ModelPricingModel(
        id=payload.id or datetime.now(timezone.utc).strftime("pricing-%Y%m%d%H%M%S%f"),
        provider=payload.provider,
        model=payload.model,
        route_type=payload.route_type,
        input_per_1m_usd=payload.input_per_1m_usd,
        output_per_1m_usd=payload.output_per_1m_usd,
        cached_input_per_1m_usd=payload.cached_input_per_1m_usd,
        effective_date=payload.effective_date or datetime.now(timezone.utc),
        active=payload.active,
        notes=payload.notes,
    )
    db.add(pricing)
    await db.commit()
    await db.refresh(pricing)
    return pricing


@routing_router.get("/policy", response_model=RoutingPolicy)
async def get_routing_policy(db: AsyncSession = Depends(get_db)) -> RoutingPolicy:
    raw = await _get_setting_json(db, ROUTING_POLICY_KEY)
    if not isinstance(raw, dict):
        return DEFAULT_ROUTING_POLICY
    return RoutingPolicy(**_normalize_routing_policy_dict(raw))


@routing_router.patch("/policy", response_model=RoutingPolicy)
async def patch_routing_policy(payload: RoutingPolicy, db: AsyncSession = Depends(get_db)) -> RoutingPolicy:
    await _put_setting_json(db, ROUTING_POLICY_KEY, payload.model_dump())
    return payload


@router.get("/openclaw/models")
async def get_openclaw_model_catalog(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    raw = await _get_setting_json(db, OPENCLAW_MODEL_CATALOG_KEY)
    if isinstance(raw, dict):
        return raw
    return {"synced_at": None, "count": 0, "models": []}


@router.post("/openclaw/sync")
async def sync_openclaw_model_catalog(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    catalog = await fetch_openclaw_model_catalog()
    await _put_setting_json(db, OPENCLAW_MODEL_CATALOG_KEY, catalog)

    # Smart default: feed subscription-capable providers/models into routing policy.
    models = catalog.get("models") if isinstance(catalog, dict) else []
    subscription_models = []
    subscription_providers = []
    if isinstance(models, list):
        for item in models:
            if not isinstance(item, dict):
                continue
            if str(item.get("billing_type", "")).lower() != "subscription":
                continue
            model = item.get("model")
            provider = item.get("provider")
            if isinstance(model, str) and model not in subscription_models:
                subscription_models.append(model)
            if isinstance(provider, str) and provider not in subscription_providers:
                subscription_providers.append(provider)

    current_policy_raw = await _get_setting_json(db, ROUTING_POLICY_KEY)
    current_policy = _normalize_routing_policy_dict(current_policy_raw if isinstance(current_policy_raw, dict) else DEFAULT_ROUTING_POLICY.model_dump())
    current_policy["subscription_models"] = subscription_models
    current_policy["subscription_providers"] = subscription_providers
    if subscription_models and "subscription" not in current_policy.get("fallback_chains", {}).get("default", []):
        chains = current_policy.get("fallback_chains") or {}
        default_chain = chains.get("default") or ["openai", "claude", "kimi", "minimax"]
        chains["default"] = ["subscription", *[x for x in default_chain if x != "subscription"]]
        current_policy["fallback_chains"] = chains

    await _put_setting_json(db, ROUTING_POLICY_KEY, current_policy)

    return {
        "catalog": catalog,
        "routing_policy": current_policy,
        "synced_subscription_models": subscription_models,
        "synced_subscription_providers": subscription_providers,
    }


@router.get("/dashboard")
async def get_usage_dashboard(
    window: str = Query("month", description="day, week, or month"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Dashboard-optimized usage view: tasks per model/provider, tokens, costs.
    
    Returns everything the Mission Control dashboard needs in a single call:
    - Summary totals (tasks, tokens, cost)
    - Per-provider breakdown (tasks, tokens, cost, avg latency)
    - Per-model breakdown (tasks, tokens, cost)
    - Daily time series for charts
    - Top models by task count and by cost
    """
    now = datetime.now(timezone.utc)
    start = _window_start(window, now)

    # === Totals ===
    totals_q = await db.execute(
        select(
            func.coalesce(func.sum(ModelUsageEventModel.requests), 0),
            func.coalesce(func.sum(ModelUsageEventModel.input_tokens), 0),
            func.coalesce(func.sum(ModelUsageEventModel.output_tokens), 0),
            func.coalesce(func.sum(ModelUsageEventModel.cached_tokens), 0),
            func.coalesce(func.sum(ModelUsageEventModel.estimated_cost_usd), 0.0),
            func.count(ModelUsageEventModel.id),
        ).where(
            ModelUsageEventModel.timestamp >= start,
            ModelUsageEventModel.source == "orchestrator-worker",
        )
    )
    total_requests, total_input, total_output, total_cached, total_cost, total_events = totals_q.one()

    # === Per-provider ===
    provider_q = await db.execute(
        select(
            ModelUsageEventModel.provider,
            func.count(ModelUsageEventModel.id).label("task_count"),
            func.coalesce(func.sum(ModelUsageEventModel.requests), 0).label("requests"),
            func.coalesce(func.sum(ModelUsageEventModel.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(ModelUsageEventModel.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(ModelUsageEventModel.cached_tokens), 0).label("cached_tokens"),
            func.coalesce(func.sum(ModelUsageEventModel.estimated_cost_usd), 0.0).label("cost"),
            func.avg(ModelUsageEventModel.latency_ms).label("avg_latency"),
            func.coalesce(
                func.sum(case((ModelUsageEventModel.status != "success", 1), else_=0)),
                0,
            ).label("error_count"),
        )
        .where(
            ModelUsageEventModel.timestamp >= start,
            ModelUsageEventModel.source == "orchestrator-worker",
        )
        .group_by(ModelUsageEventModel.provider)
        .order_by(func.count(ModelUsageEventModel.id).desc())
    )

    by_provider = []
    for row in provider_q.all():
        by_provider.append({
            "provider": row.provider,
            "task_count": int(row.task_count),
            "requests": int(row.requests),
            "input_tokens": int(row.input_tokens),
            "output_tokens": int(row.output_tokens),
            "cached_tokens": int(row.cached_tokens),
            "total_tokens": int(row.input_tokens) + int(row.output_tokens),
            "estimated_cost_usd": round(float(row.cost), 4),
            "avg_latency_ms": round(float(row.avg_latency), 1) if row.avg_latency else None,
            "error_count": int(row.error_count),
        })

    # === Per-model ===
    model_q = await db.execute(
        select(
            ModelUsageEventModel.provider,
            ModelUsageEventModel.model,
            ModelUsageEventModel.route_type,
            func.count(ModelUsageEventModel.id).label("task_count"),
            func.coalesce(func.sum(ModelUsageEventModel.requests), 0).label("requests"),
            func.coalesce(func.sum(ModelUsageEventModel.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(ModelUsageEventModel.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(ModelUsageEventModel.cached_tokens), 0).label("cached_tokens"),
            func.coalesce(func.sum(ModelUsageEventModel.estimated_cost_usd), 0.0).label("cost"),
            func.avg(ModelUsageEventModel.latency_ms).label("avg_latency"),
        )
        .where(
            ModelUsageEventModel.timestamp >= start,
            ModelUsageEventModel.source == "orchestrator-worker",
        )
        .group_by(ModelUsageEventModel.provider, ModelUsageEventModel.model, ModelUsageEventModel.route_type)
        .order_by(func.count(ModelUsageEventModel.id).desc())
    )

    by_model = []
    for row in model_q.all():
        by_model.append({
            "provider": row.provider,
            "model": row.model,
            "route_type": row.route_type,
            "task_count": int(row.task_count),
            "requests": int(row.requests),
            "input_tokens": int(row.input_tokens),
            "output_tokens": int(row.output_tokens),
            "cached_tokens": int(row.cached_tokens),
            "total_tokens": int(row.input_tokens) + int(row.output_tokens),
            "estimated_cost_usd": round(float(row.cost), 4),
            "avg_latency_ms": round(float(row.avg_latency), 1) if row.avg_latency else None,
        })

    # === Daily time series ===
    daily_q = await db.execute(
        select(
            func.date(ModelUsageEventModel.timestamp).label("day"),
            ModelUsageEventModel.provider,
            func.count(ModelUsageEventModel.id).label("task_count"),
            func.coalesce(func.sum(ModelUsageEventModel.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(ModelUsageEventModel.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(ModelUsageEventModel.estimated_cost_usd), 0.0).label("cost"),
        )
        .where(
            ModelUsageEventModel.timestamp >= start,
            ModelUsageEventModel.source == "orchestrator-worker",
        )
        .group_by(func.date(ModelUsageEventModel.timestamp), ModelUsageEventModel.provider)
        .order_by(func.date(ModelUsageEventModel.timestamp).asc())
    )

    daily_series = []
    for row in daily_q.all():
        daily_series.append({
            "date": str(row.day),
            "provider": row.provider,
            "task_count": int(row.task_count),
            "input_tokens": int(row.input_tokens),
            "output_tokens": int(row.output_tokens),
            "total_tokens": int(row.input_tokens) + int(row.output_tokens),
            "estimated_cost_usd": round(float(row.cost), 4),
        })

    return {
        "window": window,
        "period_start": start.isoformat(),
        "period_end": now.isoformat(),
        "totals": {
            "task_count": int(total_events),
            "requests": int(total_requests),
            "input_tokens": int(total_input),
            "output_tokens": int(total_output),
            "cached_tokens": int(total_cached),
            "total_tokens": int(total_input) + int(total_output),
            "estimated_cost_usd": round(float(total_cost), 4),
        },
        "by_provider": by_provider,
        "by_model": by_model,
        "daily_series": daily_series,
    }


# ---------------------------------------------------------------------------
# Budget lane policy endpoints
# ---------------------------------------------------------------------------


@router.get("/budget-lanes")
async def get_budget_lane_policy(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Return the current per-lane daily spend cap policy.

    Lanes: critical (no cap by default), standard ($8/day), background ($3/day).
    """
    row = await db.get(OrchestratorSetting, BUDGET_GUARD_LANE_POLICY_KEY)
    raw = row.value if (row and isinstance(row.value, dict)) else None
    policy = _effective_lane_policy(raw)

    # Enrich with today's spend per lane for context
    guard = BudgetGuard(db)
    lane_spend = await guard.today_spend_all_lanes()

    return {
        "policy": policy,
        "today_spend": {
            lane: {
                "spent_usd": round(lane_spend.get(lane, 0.0), 4),
                "cap_usd": policy[lane].get("daily_cap_usd"),
                "over_budget": (
                    policy[lane].get("daily_cap_usd") is not None
                    and lane_spend.get(lane, 0.0) >= (policy[lane].get("daily_cap_usd") or 0.0)
                ),
            }
            for lane in ALL_LANES
        },
        "override_log_today": await guard.today_override_log(),
    }


@router.patch("/budget-lanes")
async def patch_budget_lane_policy(
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update per-lane daily spend caps.

    Accepts a partial policy dict. Each lane entry may include:
    - ``daily_cap_usd``: float or null (null = uncapped)
    - ``downgrade_tier``: tier name or null (null = no downgrade)

    Example::

        {
          "standard": {"daily_cap_usd": 10.0, "downgrade_tier": "medium"},
          "background": {"daily_cap_usd": 5.0, "downgrade_tier": "small"}
        }
    """
    # Validate lanes
    for lane in payload:
        if lane not in ALL_LANES:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail=f"Unknown lane: {lane!r}. Valid lanes: {ALL_LANES}")

    row = await db.get(OrchestratorSetting, BUDGET_GUARD_LANE_POLICY_KEY)
    current_raw = row.value if (row and isinstance(row.value, dict)) else {}

    # Merge incoming payload into current stored policy
    merged: dict[str, Any] = dict(current_raw)
    for lane, lane_cfg in payload.items():
        if not isinstance(lane_cfg, dict):
            continue
        existing = dict(merged.get(lane, {}))
        if "daily_cap_usd" in lane_cfg:
            existing["daily_cap_usd"] = lane_cfg["daily_cap_usd"]
        if "downgrade_tier" in lane_cfg:
            existing["downgrade_tier"] = lane_cfg["downgrade_tier"]
        merged[lane] = existing

    if row is None:
        row = OrchestratorSetting(key=BUDGET_GUARD_LANE_POLICY_KEY, value=merged)
        db.add(row)
    else:
        row.value = merged
    await db.commit()

    effective = _effective_lane_policy(merged)
    return {"policy": effective, "stored": merged}


@router.get("/daily-report")
async def get_daily_report(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Daily cost-vs-quality report.

    Returns:
    - Today's total spend vs daily hard cap
    - Per-provider spend breakdown
    - Per budget lane: spend, cap, utilisation, at-cap flag, downgrade tier
    - Recent override (auto-downgrade) events from BudgetGuard
    - Budget alerts for high utilisation or cap breaches

    Lane spend data uses the ``budget_lane`` column set at worker spawn time for
    accuracy.  Legacy events without an explicit lane fall back to model-name
    keyword heuristics inside BudgetGuard.today_lane_spend().
    """
    raw_budgets = await _get_setting_json(db, BUDGETS_KEY)
    budget_limits: dict[str, Any] = raw_budgets if isinstance(raw_budgets, dict) else DEFAULT_BUDGETS.model_dump()

    report = await build_daily_report(db, budget_limits=budget_limits)

    # --- Use BudgetGuard for accurate per-lane spend and real lane policy ---
    guard = BudgetGuard(db)

    # Actual lane policy (stored under budget_guard.lane_policy).
    lane_policy_raw = await _get_setting_json(db, BUDGET_GUARD_LANE_POLICY_KEY)
    lane_policy = _effective_lane_policy(lane_policy_raw)

    # Accurate lane spend from budget_lane column (with heuristic fallback for
    # legacy events that predate the budget_lane column).
    lane_spend = await guard.today_spend_all_lanes()

    # Override log from BudgetGuard (downgrade audit trail).
    override_today = await guard.today_override_log()

    # Build authoritative lane_status, replacing the heuristic-based version
    # returned by build_daily_report().
    lane_status: dict[str, Any] = {}
    alerts: list[str] = []

    # Daily hard cap alert (computed before per-lane loop so it appears first).
    daily_hard_cap = float(budget_limits.get("daily_hard_cap_usd") or 0.0)
    total_spend = float(report.get("total_spend_usd") or 0.0)
    if daily_hard_cap > 0:
        hard_util = round(total_spend / daily_hard_cap * 100, 1)
        if hard_util >= 90:
            alerts.append(
                f"Daily hard cap at {hard_util:.0f}% utilization "
                f"(${total_spend:.2f}/${daily_hard_cap:.2f})"
            )

    for lane in ALL_LANES:
        spend = lane_spend.get(lane, 0.0)
        cap = lane_policy[lane].get("daily_cap_usd")
        downgrade_tier = lane_policy[lane].get("downgrade_tier")
        at_cap = cap is not None and spend >= cap
        util = round(spend / cap * 100, 1) if cap and cap > 0 else None

        lane_status[lane] = {
            "spend_usd": round(spend, 4),
            "cap_usd": cap,
            "utilization_pct": util,
            "at_cap": at_cap,
            "downgrade_tier": downgrade_tier,
        }

        if at_cap:
            alerts.append(
                f"{lane} lane cap exceeded — auto-downgrade active"
                + (f" (downgrade_tier={downgrade_tier})" if downgrade_tier else "")
            )
        elif util is not None and util >= 80:
            alerts.append(
                f"{lane} lane at {util:.0f}% of daily cap "
                f"(${spend:.4f}/${cap:.2f})"
            )

    report["lane_status"] = lane_status
    report["alerts"] = alerts
    report["budget_guard_overrides_today"] = override_today
    report["budget_guard_override_count"] = len(override_today)

    return report
