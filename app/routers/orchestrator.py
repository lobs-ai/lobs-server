"""Orchestrator API endpoints."""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

from app.database import get_db
from app.models import OrchestratorSetting, AgentInitiative, AgentReflection, SystemSweep
from app.orchestrator.sweep_arbitrator import DEFAULT_DAILY_BUDGET
from app.orchestrator.initiative_decisions import InitiativeDecisionEngine
from app.orchestrator import OrchestratorEngine
from app.orchestrator.model_router import (
    MODEL_ROUTER_TIER_CHEAP_KEY,
    MODEL_ROUTER_TIER_STANDARD_KEY,
    MODEL_ROUTER_TIER_STRONG_KEY,
    MODEL_ROUTER_AVAILABLE_MODELS_KEY,
)
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

PROVIDER_CONFIG_KEY = "provider_config"


class ModelTierConfig(BaseModel):
    cheap: list[str] | None = Field(default=None)
    standard: list[str] | None = Field(default=None)
    strong: list[str] | None = Field(default=None)


class ModelRouterConfigUpdate(BaseModel):
    tiers: ModelTierConfig | None = Field(default=None)
    available_models: list[str] | None = Field(default=None)


class AutonomyBudgetUpdate(BaseModel):
    daily: dict[str, int]


class InitiativeDecisionRequest(BaseModel):
    decision: str  # approve|defer|reject
    revised_title: str | None = None
    revised_description: str | None = None
    selected_agent: str | None = None
    selected_project_id: str | None = None
    decision_summary: str | None = None
    learning_feedback: str | None = None


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


class ProviderConfigItem(BaseModel):
    billing: str = Field(..., description="subscription | api | free")
    subscription_tier: str | None = None
    models: list[str] = Field(default_factory=list)
    enabled: bool = True
    priority: int = Field(default=10, description="Lower = preferred")
    use_for: list[str] = Field(default_factory=list, description="Categories: chat, programming, inbox, etc")


class ProviderConfigUpdate(BaseModel):
    providers: dict[str, ProviderConfigItem] | None = None
    fallback_chains: dict[str, list[str]] | None = None


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


def _upsert_setting_payload(current: OrchestratorSetting | None, key: str, value: Any) -> OrchestratorSetting:
    if current is None:
        return OrchestratorSetting(key=key, value=value)
    current.value = value
    return current


@router.get("/runtime")
async def get_runtime_settings(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
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


@router.get("/intelligence/summary")
async def get_intelligence_summary(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Summarize reflection/initiative/sweep pipeline state."""

    reflections = await db.execute(select(AgentReflection))
    initiatives = await db.execute(select(AgentInitiative))
    sweeps = await db.execute(select(SystemSweep).order_by(SystemSweep.created_at.desc()).limit(10))

    reflection_rows = reflections.scalars().all()
    initiative_rows = initiatives.scalars().all()
    sweep_rows = sweeps.scalars().all()

    return {
        "reflections": {
            "total": len(reflection_rows),
            "pending": sum(1 for r in reflection_rows if r.status == "pending"),
            "completed": sum(1 for r in reflection_rows if r.status == "completed"),
            "failed": sum(1 for r in reflection_rows if r.status == "failed"),
        },
        "initiatives": {
            "total": len(initiative_rows),
            "approved": sum(1 for i in initiative_rows if i.status == "approved"),
            "proposed": sum(1 for i in initiative_rows if i.status == "proposed"),
            "lobs_review": sum(1 for i in initiative_rows if i.status == "lobs_review"),
            "deferred": sum(1 for i in initiative_rows if i.status == "deferred"),
            "rejected": sum(1 for i in initiative_rows if i.status == "rejected"),
            "active": sum(1 for i in initiative_rows if i.status == "active"),
        },
        "recent_sweeps": [
            {
                "id": s.id,
                "type": s.sweep_type,
                "status": s.status,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "summary": s.summary,
            }
            for s in sweep_rows
        ],
    }


@router.get("/intelligence/initiatives")
async def list_initiatives(
    status: str | None = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List initiatives proposed from reflection cycles."""

    query = select(AgentInitiative).order_by(AgentInitiative.created_at.desc())
    if status:
        query = query.where(AgentInitiative.status == status)

    result = await db.execute(query.limit(max(1, min(1000, int(limit)))))
    rows = result.scalars().all()

    return {
        "count": len(rows),
        "items": [
            {
                "id": row.id,
                "proposed_by_agent": row.proposed_by_agent,
                "owner_agent": row.owner_agent,
                "selected_agent": row.selected_agent,
                "selected_project_id": row.selected_project_id,
                "task_id": row.task_id,
                "title": row.title,
                "description": row.description,
                "category": row.category,
                "risk_tier": row.risk_tier,
                "policy_lane": row.policy_lane,
                "policy_reason": row.policy_reason,
                "status": row.status,
                "rationale": row.rationale,
                "decision_summary": row.decision_summary,
                "learning_feedback": row.learning_feedback,
                "approved_by": row.approved_by,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ],
    }


@router.post("/intelligence/initiatives/{initiative_id}/decide")
async def decide_initiative(
    initiative_id: str,
    payload: InitiativeDecisionRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Apply a Lobs decision to an initiative and convert approved ideas into tasks."""

    initiative = await db.get(AgentInitiative, initiative_id)
    if initiative is None:
        raise HTTPException(status_code=404, detail="Initiative not found")

    engine = InitiativeDecisionEngine(db)
    try:
        result = await engine.decide(
            initiative,
            decision=payload.decision,
            revised_title=payload.revised_title,
            revised_description=payload.revised_description,
            selected_agent=payload.selected_agent,
            selected_project_id=payload.selected_project_id,
            decision_summary=payload.decision_summary,
            learning_feedback=payload.learning_feedback,
            decided_by="lobs",
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/intelligence/budgets")
async def get_autonomy_budgets(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get per-agent daily autonomy budgets for auto-approved initiatives."""

    row = await db.get(OrchestratorSetting, "autonomy_budget.daily")
    data = row.value if row and isinstance(row.value, dict) else {}

    budgets = dict(DEFAULT_DAILY_BUDGET)
    for key, value in data.items():
        try:
            budgets[str(key).lower()] = int(value)
        except (TypeError, ValueError):
            continue

    return {"daily": budgets}


@router.put("/intelligence/budgets")
async def update_autonomy_budgets(
    payload: AutonomyBudgetUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update per-agent daily autonomy budgets."""

    normalized: dict[str, int] = {}
    for key, value in payload.daily.items():
        try:
            normalized[str(key).lower()] = max(0, int(value))
        except (TypeError, ValueError):
            continue

    row = await db.get(OrchestratorSetting, "autonomy_budget.daily")
    if row is None:
        row = OrchestratorSetting(key="autonomy_budget.daily", value=normalized)
        db.add(row)
    else:
        row.value = normalized

    await db.commit()
    return {"daily": normalized}


@router.get("/providers")
async def get_provider_config(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Get current provider configuration and health status.
    
    Returns provider config (enabled/disabled, models, priorities, fallback chains)
    combined with real-time health data from the orchestrator.
    """
    # Load provider config from DB
    result = await db.execute(
        select(OrchestratorSetting).where(
            OrchestratorSetting.key == PROVIDER_CONFIG_KEY
        )
    )
    config_row = result.scalar_one_or_none()
    config = config_row.value if config_row and isinstance(config_row.value, dict) else {}
    
    # Get health report from orchestrator (if available)
    health_report = {}
    try:
        orchestrator = get_orchestrator(request)
        if hasattr(orchestrator, 'provider_health') and orchestrator.provider_health:
            health_report = orchestrator.provider_health.get_health_report()
    except Exception:
        # Orchestrator may not be running or provider_health not initialized
        pass
    
    return {
        "config": config,
        "health": health_report,
    }


@router.put("/providers")
async def update_provider_config(
    payload: ProviderConfigUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Update provider configuration.
    
    Updates provider settings (enabled/disabled, models, priorities, fallback chains).
    Changes apply to newly spawned workers.
    """
    # Load existing config
    result = await db.execute(
        select(OrchestratorSetting).where(
            OrchestratorSetting.key == PROVIDER_CONFIG_KEY
        )
    )
    config_row = result.scalar_one_or_none()
    config = config_row.value if config_row and isinstance(config_row.value, dict) else {}
    
    # Update providers
    if payload.providers is not None:
        if "providers" not in config:
            config["providers"] = {}
        
        for provider_name, provider_config in payload.providers.items():
            config["providers"][provider_name] = {
                "billing": provider_config.billing,
                "subscription_tier": provider_config.subscription_tier,
                "models": provider_config.models,
                "enabled": provider_config.enabled,
                "priority": provider_config.priority,
                "use_for": provider_config.use_for,
            }
    
    # Update fallback chains
    if payload.fallback_chains is not None:
        config["fallback_chains"] = payload.fallback_chains
    
    # Save to DB
    if config_row is None:
        config_row = OrchestratorSetting(key=PROVIDER_CONFIG_KEY, value=config)
        db.add(config_row)
    else:
        config_row.value = config
    
    await db.commit()
    
    return {"config": config}


@router.get("/providers/health")
async def get_provider_health(
    request: Request
) -> dict[str, Any]:
    """
    Get detailed provider health report.
    
    Returns per-provider and per-model health scores, success rates,
    active cooldowns, and disabled status.
    """
    orchestrator = get_orchestrator(request)
    
    if not hasattr(orchestrator, 'provider_health') or not orchestrator.provider_health:
        raise HTTPException(
            status_code=503,
            detail="Provider health tracking not initialized"
        )
    
    return orchestrator.provider_health.get_health_report()


@router.post("/providers/{provider}/reset")
async def reset_provider_health(
    provider: str,
    request: Request
) -> dict[str, str]:
    """
    Reset health state for a provider.
    
    Clears cooldowns, error counts, and re-enables if auto-disabled.
    Manual intervention for recovering from provider issues.
    """
    orchestrator = get_orchestrator(request)
    
    if not hasattr(orchestrator, 'provider_health') or not orchestrator.provider_health:
        raise HTTPException(
            status_code=503,
            detail="Provider health tracking not initialized"
        )
    
    success = orchestrator.provider_health.reset_provider(provider)
    
    if success:
        return {"message": f"Reset health for provider: {provider}"}
    else:
        raise HTTPException(
            status_code=404,
            detail=f"Provider not found: {provider}"
        )


@router.post("/providers/{provider}/toggle")
async def toggle_provider(
    provider: str,
    enabled: bool,
    request: Request
) -> dict[str, str]:
    """
    Manually enable or disable a provider.
    
    Disabled providers will not be used for new tasks until re-enabled.
    """
    orchestrator = get_orchestrator(request)
    
    if not hasattr(orchestrator, 'provider_health') or not orchestrator.provider_health:
        raise HTTPException(
            status_code=503,
            detail="Provider health tracking not initialized"
        )
    
    success = orchestrator.provider_health.toggle_provider(provider, enabled)
    
    if success:
        status = "enabled" if enabled else "disabled"
        return {"message": f"Provider {provider} {status}"}
    else:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to toggle provider: {provider}"
        )
