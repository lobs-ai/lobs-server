"""Tests for worker API endpoints and worker manager."""

import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestrator.worker import WorkerManager


@pytest.mark.asyncio
async def test_get_worker_status_empty(client: AsyncClient):
    """Test getting worker status returns default when empty."""
    response = await client.get("/api/worker/status")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 1
    assert data["active"] is False
    assert data["tasks_completed"] == 0
    assert data["input_tokens"] == 0
    assert data["output_tokens"] == 0


@pytest.mark.asyncio
async def test_update_worker_status(client: AsyncClient):
    """Test updating worker status."""
    status_data = {
        "active": True,
        "worker_id": "worker-123",
        "current_task": "task-1",
        "tasks_completed": 5,
        "input_tokens": 1000,
        "output_tokens": 500
    }
    response = await client.put("/api/worker/status", json=status_data)
    assert response.status_code == 200
    data = response.json()
    assert data["active"] is True
    assert data["worker_id"] == "worker-123"
    assert data["current_task"] == "task-1"
    assert data["tasks_completed"] == 5
    assert data["input_tokens"] == 1000
    assert data["output_tokens"] == 500


@pytest.mark.asyncio
async def test_update_worker_status_partial(client: AsyncClient):
    """Test partially updating worker status."""
    # Create initial status
    await client.put("/api/worker/status", json={
        "active": True,
        "worker_id": "worker-1",
        "tasks_completed": 3
    })
    
    # Partial update
    response = await client.put("/api/worker/status", json={
        "tasks_completed": 5
    })
    assert response.status_code == 200
    data = response.json()
    assert data["tasks_completed"] == 5
    assert data["active"] is True  # Unchanged


@pytest.mark.asyncio
async def test_list_worker_runs_empty(client: AsyncClient):
    """Test listing worker runs when empty."""
    response = await client.get("/api/worker/history")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_worker_run(client: AsyncClient):
    """Test creating a worker run."""
    run_data = {
        "worker_id": "worker-123",
        "started_at": "2026-01-01T00:00:00Z",
        "ended_at": "2026-01-01T01:00:00Z",
        "tasks_completed": 3,
        "model": "claude-sonnet-4-5",
        "input_tokens": 5000,
        "output_tokens": 2000,
        "total_tokens": 7000,
        "total_cost_usd": 0.05,
        "succeeded": True,
        "source": "orchestrator"
    }
    response = await client.post("/api/worker/history", json=run_data)
    assert response.status_code == 200
    data = response.json()
    assert data["worker_id"] == "worker-123"
    assert data["tasks_completed"] == 3
    assert data["model"] == "claude-sonnet-4-5"
    assert data["total_tokens"] == 7000
    assert data["succeeded"] is True
    assert "id" in data


@pytest.mark.asyncio
async def test_list_worker_runs(client: AsyncClient):
    """Test listing worker runs."""
    # Create runs
    for i in range(3):
        await client.post("/api/worker/history", json={
            "worker_id": f"worker-{i}",
            "tasks_completed": i,
            "input_tokens": 1000 * i,
            "output_tokens": 500 * i,
            "total_tokens": 1500 * i
        })
    
    response = await client.get("/api/worker/history")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    # Verify they're in descending order by ID
    assert data[0]["id"] > data[1]["id"]


@pytest.mark.asyncio
async def test_list_worker_runs_pagination(client: AsyncClient):
    """Test worker run history pagination."""
    # Create multiple runs
    for i in range(5):
        await client.post("/api/worker/history", json={
            "worker_id": f"worker-{i}",
            "tasks_completed": i,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0
        })
    
    # Test limit
    response = await client.get("/api/worker/history?limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2
    
    # Test offset
    response = await client.get("/api/worker/history?offset=2&limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2


class TestWorkerManager:
    """Tests for WorkerManager session termination."""
    
    @pytest.mark.asyncio
    async def test_terminate_session_success(self, db_session: AsyncSession):
        """Test successful session termination."""
        manager = WorkerManager(db_session)
        
        # Mock the Gateway API response
        mock_response = MagicMock()
        mock_response.json = AsyncMock(return_value={"ok": True})
        
        with patch("app.orchestrator.worker.aiohttp.ClientSession") as mock_session_class:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()
            mock_session.post = AsyncMock(return_value=mock_response)
            mock_session_class.return_value = mock_session
            
            result = await manager._terminate_session(
                "agent:programmer:subagent:test-123",
                "timeout"
            )
            
            assert result is True
            mock_session.post.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_terminate_session_not_found(self, db_session: AsyncSession):
        """Test session termination when session not found (treat as success)."""
        manager = WorkerManager(db_session)
        
        # Mock the Gateway API response - session not found
        mock_response = MagicMock()
        mock_response.json = AsyncMock(return_value={
            "ok": False,
            "error": {"message": "Session not found"}
        })
        
        with patch("app.orchestrator.worker.aiohttp.ClientSession") as mock_session_class:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()
            mock_session.post = AsyncMock(return_value=mock_response)
            mock_session_class.return_value = mock_session
            
            result = await manager._terminate_session(
                "agent:programmer:subagent:test-123",
                "cleanup"
            )
            
            assert result is True  # Not found treated as success
    
    @pytest.mark.asyncio
    async def test_terminate_session_api_error(self, db_session: AsyncSession):
        """Test session termination when API returns error."""
        manager = WorkerManager(db_session)
        
        # Mock the Gateway API response - other error
        mock_response = MagicMock()
        mock_response.json = AsyncMock(return_value={
            "ok": False,
            "error": {"message": "Internal server error"}
        })
        
        with patch("app.orchestrator.worker.aiohttp.ClientSession") as mock_session_class:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()
            mock_session.post = AsyncMock(return_value=mock_response)
            mock_session_class.return_value = mock_session
            
            result = await manager._terminate_session(
                "agent:programmer:subagent:test-123",
                "timeout"
            )
            
            assert result is False
    
    @pytest.mark.asyncio
    async def test_terminate_session_network_error(self, db_session: AsyncSession):
        """Test session termination when network error occurs."""
        manager = WorkerManager(db_session)
        
        with patch("app.orchestrator.worker.aiohttp.ClientSession") as mock_session_class:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.post = AsyncMock(side_effect=Exception("Network error"))
            mock_session_class.return_value = mock_session
            
            result = await manager._terminate_session(
                "agent:programmer:subagent:test-123",
                "timeout"
            )
            
            assert result is False
