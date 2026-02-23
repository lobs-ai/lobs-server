"""Orchestrator worker and provider management endpoints."""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

from app.database import get_db
from app.models import OrchestratorSetting
from app.orchestrator import OrchestratorEngine

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])

PROVIDER_CONFIG_KEY = "provider_config"


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
