"""Usage tracking and cost estimation utilities."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ModelPricing, ModelUsageEvent


def infer_provider(model: str) -> str:
    lowered = (model or "").lower()
    if "gemini" in lowered or lowered.startswith("google"):
        return "gemini"
    if "anthropic" in lowered or "claude" in lowered:
        return "claude"
    if "openai" in lowered or "gpt" in lowered or "o1" in lowered or "o3" in lowered:
        return "openai"
    if "kimi" in lowered or "moonshot" in lowered:
        return "kimi"
    if "minimax" in lowered:
        return "minimax"
    return "unknown"


async def lookup_pricing(
    db: AsyncSession,
    *,
    provider: str,
    model: str,
    route_type: str,
    at: datetime,
) -> ModelPricing | None:
    result = await db.execute(
        select(ModelPricing)
        .where(
            ModelPricing.provider == provider,
            ModelPricing.model == model,
            ModelPricing.route_type == route_type,
            ModelPricing.active.is_(True),
            ModelPricing.effective_date <= at,
        )
        .order_by(ModelPricing.effective_date.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def estimate_cost_usd(
    db: AsyncSession,
    *,
    provider: str,
    model: str,
    route_type: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    at: datetime,
) -> float:
    if route_type == "subscription":
        return 0.0

    pricing = await lookup_pricing(
        db,
        provider=provider,
        model=model,
        route_type=route_type,
        at=at,
    )
    if not pricing:
        return 0.0

    cost = (
        (max(input_tokens, 0) / 1_000_000.0) * pricing.input_per_1m_usd
        + (max(output_tokens, 0) / 1_000_000.0) * pricing.output_per_1m_usd
        + (max(cached_tokens, 0) / 1_000_000.0) * pricing.cached_input_per_1m_usd
    )
    return round(float(cost), 8)


async def log_usage_event(
    db: AsyncSession,
    *,
    source: str,
    model: str,
    provider: str | None = None,
    route_type: str = "api",
    task_type: str = "other",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    requests: int = 1,
    latency_ms: int | None = None,
    status: str = "success",
    estimated_cost_usd: float | None = None,
    error_code: str | None = None,
    metadata: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> ModelUsageEvent:
    event_ts = timestamp or datetime.now(timezone.utc)
    normalized_provider = provider or infer_provider(model)

    cost = estimated_cost_usd
    if cost is None:
        cost = await estimate_cost_usd(
            db,
            provider=normalized_provider,
            model=model,
            route_type=route_type,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            at=event_ts,
        )

    event = ModelUsageEvent(
        id=str(uuid4()),
        timestamp=event_ts,
        source=source,
        provider=normalized_provider,
        model=model,
        route_type=route_type,
        task_type=task_type,
        input_tokens=max(input_tokens, 0),
        output_tokens=max(output_tokens, 0),
        cached_tokens=max(cached_tokens, 0),
        requests=max(requests, 1),
        latency_ms=latency_ms,
        status=status,
        estimated_cost_usd=max(float(cost or 0.0), 0.0),
        error_code=error_code,
        event_metadata=metadata,
    )
    db.add(event)
    await db.flush()
    return event
