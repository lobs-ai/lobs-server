"""Orchestrator API endpoints."""

from fastapi import APIRouter, HTTPException, Request
from typing import Any

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


@router.get("/status")
async def get_orchestrator_status(request: Request) -> dict[str, Any]:
    """Get orchestrator status."""
    orchestrator = request.app.state.orchestrator
    
    if not orchestrator:
        return {
            "enabled": False,
            "running": False,
            "message": "Orchestrator is disabled"
        }
    
    status = orchestrator.get_status()
    return {
        "enabled": True,
        **status
    }


@router.get("/workers")
async def get_worker_status(request: Request) -> dict[str, Any]:
    """Get current worker status."""
    orchestrator = request.app.state.orchestrator
    
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator is disabled")
    
    return await orchestrator.get_worker_status()


@router.post("/pause")
async def pause_orchestrator(request: Request) -> dict[str, Any]:
    """Pause the orchestrator (stop accepting new work)."""
    orchestrator = request.app.state.orchestrator
    
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator is disabled")
    
    await orchestrator.pause()
    
    return {
        "status": "paused",
        "message": "Orchestrator paused successfully"
    }


@router.post("/resume")
async def resume_orchestrator(request: Request) -> dict[str, Any]:
    """Resume the orchestrator."""
    orchestrator = request.app.state.orchestrator
    
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator is disabled")
    
    await orchestrator.resume()
    
    return {
        "status": "running",
        "message": "Orchestrator resumed successfully"
    }
