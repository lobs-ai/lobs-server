"""Analytics endpoints — outcome stats for worker runs."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import WorkerRun

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/outcomes")
async def get_outcomes(
    days: int = Query(7, ge=1, le=90, description="Number of days to look back"),
    db: AsyncSession = Depends(get_db),
):
    """
    Aggregated outcome stats for worker runs over the last N days.

    Returns:
    - success_rate_by_agent: {agent_type: {total, succeeded, success_rate}}
    - avg_duration_by_model: {model: avg_seconds}
    - cost_breakdown: {model: total_cost_usd}
    - overall: summary counts
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(WorkerRun).where(WorkerRun.started_at >= since)
    )
    runs = result.scalars().all()

    # --- by agent type ---
    agent_stats: dict = {}
    for run in runs:
        agent = run.agent_type or "unknown"
        if agent not in agent_stats:
            agent_stats[agent] = {"total": 0, "succeeded": 0}
        agent_stats[agent]["total"] += 1
        if run.succeeded:
            agent_stats[agent]["succeeded"] += 1

    for agent, stats in agent_stats.items():
        total = stats["total"]
        stats["success_rate"] = round(stats["succeeded"] / total, 4) if total else 0.0

    # --- by model ---
    model_duration: dict = {}
    model_cost: dict = {}
    for run in runs:
        model = run.model or "unknown"
        # Duration: use stored column if available, else compute from timestamps
        duration = run.duration_seconds
        if duration is None and run.started_at and run.ended_at:
            duration = (run.ended_at - run.started_at).total_seconds()
        if duration is not None:
            model_duration.setdefault(model, []).append(duration)
        cost = run.total_cost_usd or 0.0
        model_cost[model] = model_cost.get(model, 0.0) + cost

    avg_duration_by_model = {
        model: round(sum(durs) / len(durs), 2)
        for model, durs in model_duration.items()
    }

    cost_breakdown = {
        model: round(cost, 6) for model, cost in model_cost.items()
    }

    # --- overall ---
    total = len(runs)
    succeeded = sum(1 for r in runs if r.succeeded)
    failed = sum(1 for r in runs if r.succeeded is False)
    total_cost = sum(r.total_cost_usd or 0.0 for r in runs)

    return {
        "period_days": days,
        "since": since.isoformat(),
        "overall": {
            "total_runs": total,
            "succeeded": succeeded,
            "failed": failed,
            "unknown": total - succeeded - failed,
            "success_rate": round(succeeded / total, 4) if total else 0.0,
            "total_cost_usd": round(total_cost, 6),
        },
        "success_rate_by_agent": agent_stats,
        "avg_duration_by_model": avg_duration_by_model,
        "cost_breakdown_by_model": cost_breakdown,
    }
