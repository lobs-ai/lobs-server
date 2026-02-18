"""Orchestrator API endpoints."""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

from app.database import get_db
from app.models import OrchestratorSetting
from app.orchestrator import OrchestratorEngine
from app.orchestrator.model_router import (
    MODEL_ROUTER_TIER_CHEAP_KEY,
    MODEL_ROUTER_TIER_STANDARD_KEY,
    MODEL_ROUTER_TIER_STRONG_KEY,
    MODEL_ROUTER_AVAILABLE_MODELS_KEY,
)

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


class ModelTierConfig(BaseModel):
    cheap: list[str] | None = Field(default=None)
    standard: list[str] | None = Field(default=None)
    strong: list[str] | None = Field(default=None)


class ModelRouterConfigUpdate(BaseModel):
    tiers: ModelTierConfig | None = Field(default=None)
    available_models: list[str] | None = Field(default=None)


def get_orchestrator(request: Request) -> OrchestratorEngine:
    """Get the orchestrator instance from app state."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(
            status_code=503,
            detail="Orchestrator not initialized or disabled"
        )
    return orchestrator


@router.get("/status")
async def get_status(
    request: Request
) -> dict[str, Any]:
    """
    Get orchestrator status.
    
    Returns current state, worker status, and agent statuses.
    """
    orchestrator = get_orchestrator(request)
    return await orchestrator.get_status()


@router.post("/pause")
async def pause(
    request: Request
) -> dict[str, str]:
    """
    Pause the orchestrator.
    
    Stops spawning new workers but allows active workers to complete.
    """
    orchestrator = get_orchestrator(request)
    orchestrator.pause()
    return {"message": "Orchestrator paused"}


@router.post("/resume")
async def resume(
    request: Request
) -> dict[str, str]:
    """
    Resume the orchestrator.
    
    Resumes normal operation after pause.
    """
    orchestrator = get_orchestrator(request)
    orchestrator.resume()
    return {"message": "Orchestrator resumed"}


@router.get("/workers")
async def get_workers(
    request: Request
) -> dict[str, Any]:
    """
    Get details of all active workers.
    
    Returns list of currently running workers with their status.
    """
    orchestrator = get_orchestrator(request)
    workers = await orchestrator.get_worker_details()
    
    return {
        "count": len(workers),
        "workers": workers
    }


@router.get("/health")
async def get_health(
    db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """
    Get orchestrator health summary.
    
    Includes worker health and stuck task detection.
    """
    from app.orchestrator import Monitor
    
    monitor = Monitor(db)
    return await monitor.get_health_summary()


@router.get("/model-router")
async def get_model_router_config(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get runtime model-router config stored in DB."""

    keys = (
        MODEL_ROUTER_TIER_CHEAP_KEY,
        MODEL_ROUTER_TIER_STANDARD_KEY,
        MODEL_ROUTER_TIER_STRONG_KEY,
        MODEL_ROUTER_AVAILABLE_MODELS_KEY,
    )
    result = await db.execute(
        select(OrchestratorSetting).where(OrchestratorSetting.key.in_(keys))
    )
    rows = {row.key: row.value for row in result.scalars().all()}

    return {
        "tiers": {
            "cheap": rows.get(MODEL_ROUTER_TIER_CHEAP_KEY),
            "standard": rows.get(MODEL_ROUTER_TIER_STANDARD_KEY),
            "strong": rows.get(MODEL_ROUTER_TIER_STRONG_KEY),
        },
        "available_models": rows.get(MODEL_ROUTER_AVAILABLE_MODELS_KEY),
    }


@router.put("/model-router")
async def update_model_router_config(
    payload: ModelRouterConfigUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update runtime model-router config in DB.

    Updates apply to newly spawned workers without restarting the server.
    Set a field to null to clear the DB override for that field.
    """

    updates: dict[str, list[str] | None] = {}

    if payload.tiers is not None:
        updates[MODEL_ROUTER_TIER_CHEAP_KEY] = payload.tiers.cheap
        updates[MODEL_ROUTER_TIER_STANDARD_KEY] = payload.tiers.standard
        updates[MODEL_ROUTER_TIER_STRONG_KEY] = payload.tiers.strong

    if payload.available_models is not None:
        updates[MODEL_ROUTER_AVAILABLE_MODELS_KEY] = payload.available_models

    for key, value in updates.items():
        result = await db.execute(select(OrchestratorSetting).where(OrchestratorSetting.key == key))
        row = result.scalar_one_or_none()

        if value is None:
            if row is not None:
                await db.delete(row)
            continue

        cleaned = [str(v).strip() for v in value if str(v).strip()]
        if row is None:
            row = OrchestratorSetting(key=key, value=cleaned)
            db.add(row)
        else:
            row.value = cleaned

    await db.commit()

    return await get_model_router_config(db)
