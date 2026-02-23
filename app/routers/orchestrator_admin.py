"""Orchestrator admin endpoints - status, control, and configuration."""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

from app.database import get_db
from app.models import OrchestratorSetting
from app.orchestrator import OrchestratorEngine
from app.orchestrator.model_router import (
    MODEL_ROUTER_TIER_MICRO_KEY,
    MODEL_ROUTER_TIER_SMALL_KEY,
    MODEL_ROUTER_TIER_MEDIUM_KEY,
    MODEL_ROUTER_TIER_STANDARD_KEY,
    MODEL_ROUTER_TIER_STRONG_KEY,
    MODEL_ROUTER_AVAILABLE_MODELS_KEY,
    TIER_ORDER,
)
from app.orchestrator.model_chooser import discover_ollama_models
from app.orchestrator.runtime_settings import (
    DEFAULT_RUNTIME_SETTINGS,
    SETTINGS_KEY_REFLECTION_INTERVAL_SECONDS,
    SETTINGS_KEY_SWEEP_INTERVAL_SECONDS,
    SETTINGS_KEY_DIAGNOSTIC_INTERVAL_SECONDS,
    SETTINGS_KEY_GITHUB_SYNC_INTERVAL_SECONDS,
    SETTINGS_KEY_OPENCLAW_MODEL_SYNC_INTERVAL_SECONDS,
    SETTINGS_KEY_DAILY_COMPRESSION_HOUR_UTC,
    SETTINGS_KEY_MODEL_ROUTER_STRICT_CODING_TIER,
    SETTINGS_KEY_MODEL_ROUTER_DEGRADE_ON_QUOTA,
)

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


class ModelTierConfig(BaseModel):
    micro: list[str] | None = Field(default=None)
    small: list[str] | None = Field(default=None)
    medium: list[str] | None = Field(default=None)
    standard: list[str] | None = Field(default=None)
    strong: list[str] | None = Field(default=None)


class ModelRouterConfigUpdate(BaseModel):
    tiers: ModelTierConfig | None = Field(default=None)
    available_models: list[str] | None = Field(default=None)


class RuntimeIntervalsUpdate(BaseModel):
    reflection_seconds: int | None = None
    sweep_seconds: int | None = None
    diagnostic_seconds: int | None = None
    github_sync_seconds: int | None = None
    openclaw_model_sync_seconds: int | None = None
    daily_compression_hour_utc: int | None = None


class ModelPolicyUpdate(BaseModel):
    strict_coding_tier: bool | None = None
    degrade_on_quota: bool | None = None


def get_orchestrator(request: Request) -> OrchestratorEngine:
    """Get the orchestrator instance from app state."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(
            status_code=503,
            detail="Orchestrator not initialized or disabled"
        )
    return orchestrator


def _upsert_setting_payload(current: OrchestratorSetting | None, key: str, value: Any) -> OrchestratorSetting:
    """Helper to create or update an OrchestratorSetting."""
    if current is None:
        return OrchestratorSetting(key=key, value=value)
    current.value = value
    return current


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

    tier_keys = {
        "micro": MODEL_ROUTER_TIER_MICRO_KEY,
        "small": MODEL_ROUTER_TIER_SMALL_KEY,
        "medium": MODEL_ROUTER_TIER_MEDIUM_KEY,
        "standard": MODEL_ROUTER_TIER_STANDARD_KEY,
        "strong": MODEL_ROUTER_TIER_STRONG_KEY,
    }
    all_keys = list(tier_keys.values()) + [MODEL_ROUTER_AVAILABLE_MODELS_KEY]
    result = await db.execute(
        select(OrchestratorSetting).where(OrchestratorSetting.key.in_(all_keys))
    )
    rows = {row.key: row.value for row in result.scalars().all()}

    # Include auto-discovered Ollama models
    ollama_tiers = await discover_ollama_models()

    return {
        "tiers": {name: rows.get(key) for name, key in tier_keys.items()},
        "available_models": rows.get(MODEL_ROUTER_AVAILABLE_MODELS_KEY),
        "ollama_models": ollama_tiers if ollama_tiers else None,
        "tier_order": TIER_ORDER,
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
        updates[MODEL_ROUTER_TIER_MICRO_KEY] = payload.tiers.micro
        updates[MODEL_ROUTER_TIER_SMALL_KEY] = payload.tiers.small
        updates[MODEL_ROUTER_TIER_MEDIUM_KEY] = payload.tiers.medium
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


@router.get("/runtime")
async def get_runtime_settings(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get runtime settings with defaults merged from database overrides."""
    keys = tuple(DEFAULT_RUNTIME_SETTINGS.keys())
    result = await db.execute(select(OrchestratorSetting).where(OrchestratorSetting.key.in_(keys)))
    rows = {row.key: row.value for row in result.scalars().all()}

    merged = dict(DEFAULT_RUNTIME_SETTINGS)
    merged.update(rows)
    return merged


@router.put("/runtime/intervals")
async def update_runtime_intervals(
    payload: RuntimeIntervalsUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update runtime interval settings."""
    updates: dict[str, int] = {}
    if payload.reflection_seconds is not None:
        updates[SETTINGS_KEY_REFLECTION_INTERVAL_SECONDS] = max(60, int(payload.reflection_seconds))
    if payload.sweep_seconds is not None:
        updates[SETTINGS_KEY_SWEEP_INTERVAL_SECONDS] = max(60, int(payload.sweep_seconds))
    if payload.diagnostic_seconds is not None:
        updates[SETTINGS_KEY_DIAGNOSTIC_INTERVAL_SECONDS] = max(60, int(payload.diagnostic_seconds))
    if payload.github_sync_seconds is not None:
        updates[SETTINGS_KEY_GITHUB_SYNC_INTERVAL_SECONDS] = max(30, int(payload.github_sync_seconds))
    if payload.openclaw_model_sync_seconds is not None:
        updates[SETTINGS_KEY_OPENCLAW_MODEL_SYNC_INTERVAL_SECONDS] = max(60, int(payload.openclaw_model_sync_seconds))
    if payload.daily_compression_hour_utc is not None:
        updates[SETTINGS_KEY_DAILY_COMPRESSION_HOUR_UTC] = max(0, min(23, int(payload.daily_compression_hour_utc)))

    for key, value in updates.items():
        row = await db.get(OrchestratorSetting, key)
        row = _upsert_setting_payload(row, key, value)
        db.add(row)

    await db.commit()
    return await get_runtime_settings(db)


@router.put("/runtime/model-policy")
async def update_model_policy(
    payload: ModelPolicyUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update model policy settings."""
    if payload.strict_coding_tier is not None:
        row = await db.get(OrchestratorSetting, SETTINGS_KEY_MODEL_ROUTER_STRICT_CODING_TIER)
        row = _upsert_setting_payload(row, SETTINGS_KEY_MODEL_ROUTER_STRICT_CODING_TIER, bool(payload.strict_coding_tier))
        db.add(row)

    if payload.degrade_on_quota is not None:
        row = await db.get(OrchestratorSetting, SETTINGS_KEY_MODEL_ROUTER_DEGRADE_ON_QUOTA)
        row = _upsert_setting_payload(row, SETTINGS_KEY_MODEL_ROUTER_DEGRADE_ON_QUOTA, bool(payload.degrade_on_quota))
        db.add(row)

    await db.commit()
    return await get_runtime_settings(db)
