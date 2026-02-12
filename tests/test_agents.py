"""Tests for agents API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_agents_empty(client: AsyncClient):
    """Test listing agent statuses when empty."""
    response = await client.get("/api/agents")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_agent_status(client: AsyncClient):
    """Test creating/updating agent status."""
    status_data = {
        "status": "active",
        "activity": "working on task",
        "thinking": "analyzing requirements"
    }
    response = await client.put("/api/agents/programmer", json=status_data)
    assert response.status_code == 200
    data = response.json()
    assert data["agent_type"] == "programmer"
    assert data["status"] == "active"
    assert data["activity"] == "working on task"
    assert data["thinking"] == "analyzing requirements"


@pytest.mark.asyncio
async def test_update_agent_status(client: AsyncClient):
    """Test updating existing agent status."""
    # Create initial status
    await client.put("/api/agents/writer", json={
        "status": "idle"
    })
    
    # Update it
    response = await client.put("/api/agents/writer", json={
        "status": "active",
        "activity": "writing documentation"
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "active"
    assert data["activity"] == "writing documentation"


@pytest.mark.asyncio
async def test_get_agent_status(client: AsyncClient):
    """Test getting a specific agent status."""
    # Create status
    await client.put("/api/agents/researcher", json={
        "status": "active",
        "activity": "researching"
    })
    
    # Get it
    response = await client.get("/api/agents/researcher")
    assert response.status_code == 200
    data = response.json()
    assert data["agent_type"] == "researcher"
    assert data["status"] == "active"


@pytest.mark.asyncio
async def test_get_agent_status_not_found(client: AsyncClient):
    """Test getting non-existent agent status returns 404."""
    response = await client.get("/api/agents/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_agents(client: AsyncClient):
    """Test listing all agent statuses."""
    # Create multiple agent statuses
    await client.put("/api/agents/programmer", json={"status": "active"})
    await client.put("/api/agents/writer", json={"status": "idle"})
    await client.put("/api/agents/researcher", json={"status": "active"})
    
    response = await client.get("/api/agents")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    agent_types = [agent["agent_type"] for agent in data]
    assert "programmer" in agent_types
    assert "writer" in agent_types
    assert "researcher" in agent_types


@pytest.mark.asyncio
async def test_agent_status_with_task_references(client: AsyncClient, sample_task):
    """Test agent status with task references."""
    status_data = {
        "status": "active",
        "current_task_id": sample_task["id"],
        "last_completed_task_id": "prev-task"
    }
    response = await client.put("/api/agents/programmer", json=status_data)
    assert response.status_code == 200
    data = response.json()
    assert data["current_task_id"] == sample_task["id"]
    assert data["last_completed_task_id"] == "prev-task"


@pytest.mark.asyncio
async def test_agent_status_with_stats(client: AsyncClient):
    """Test agent status with statistics."""
    status_data = {
        "status": "active",
        "stats": {
            "tasks_completed": 10,
            "total_tokens": 50000,
            "avg_task_time_minutes": 45
        }
    }
    response = await client.put("/api/agents/programmer", json=status_data)
    assert response.status_code == 200
    data = response.json()
    assert data["stats"]["tasks_completed"] == 10
    assert data["stats"]["total_tokens"] == 50000
