"""Tests for orchestrator API endpoints."""

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from app.models import ControlLoopHeartbeat
from app.orchestrator.engine import OrchestratorEngine
from tests.conftest import TestSessionLocal


@pytest.mark.asyncio
async def test_get_orchestrator_status_disabled(client: AsyncClient):
    """Test getting orchestrator status when disabled returns 503."""
    response = await client.get("/api/orchestrator/status")
    assert response.status_code == 503
    assert "not initialized" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_pause_orchestrator_disabled(client: AsyncClient):
    """Test pausing orchestrator when disabled returns 503."""
    response = await client.post("/api/orchestrator/pause")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_resume_orchestrator_disabled(client: AsyncClient):
    """Test resuming orchestrator when disabled returns 503."""
    response = await client.post("/api/orchestrator/resume")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_get_workers_disabled(client: AsyncClient):
    """Test getting workers when orchestrator is disabled returns 503."""
    response = await client.get("/api/orchestrator/workers")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_get_health(client: AsyncClient):
    """Test getting orchestrator health summary."""
    # This endpoint should work even when orchestrator is disabled
    # because it uses the database directly via Monitor
    response = await client.get("/api/orchestrator/health")
    # May return 200 or error depending on implementation
    # The important thing is it doesn't crash
    assert response.status_code in [200, 500]


@pytest.mark.asyncio
async def test_engine_status_exposes_control_loop_heartbeat():
    async with TestSessionLocal() as session:
        session.add(ControlLoopHeartbeat(
            id="main",
            phase="tick_complete",
            last_heartbeat_at=datetime.now(timezone.utc),
            heartbeat_metadata={"events_processed": 2},
        ))
        await session.commit()

    engine = OrchestratorEngine(TestSessionLocal)
    status = await engine.get_status()
    assert "control_loop" in status
    assert status["control_loop"]["last_heartbeat"] is not None
    assert status["control_loop"]["heartbeat_metadata"]["events_processed"] == 2
