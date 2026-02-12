"""Orchestrator API endpoints."""

from fastapi import APIRouter, HTTPException, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

from app.database import get_db
from app.orchestrator import OrchestratorEngine

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


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
