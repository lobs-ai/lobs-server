"""Tests for governance API endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_list_agent_profiles_empty(client: AsyncClient):
    """Test listing agent profiles when empty."""
    response = await client.get("/api/governance/agent-profiles")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_agent_profile_success(client: AsyncClient):
    """Test creating an agent profile successfully."""
    profile_data = {
        "id": "profile-specialist-1",
        "agent_type": "specialist",
        "display_name": "Specialist Agent",
        "policy_tier": "standard",
        "active": True,
    }
    
    response = await client.post("/api/governance/agent-profiles", json=profile_data)
    assert response.status_code == 200
    data = response.json()
    assert data["agent_type"] == "specialist"
    assert data["display_name"] == "Specialist Agent"
    assert data["policy_tier"] == "standard"
    assert data["active"] is True


@pytest.mark.asyncio
async def test_create_duplicate_agent_profile(client: AsyncClient):
    """Test creating a duplicate agent profile fails."""
    profile_data = {
        "id": "profile-specialist-dup",
        "agent_type": "specialist",
        "display_name": "Specialist Agent",
        "policy_tier": "standard",
        "active": True,
    }
    
    # Create first profile
    response = await client.post("/api/governance/agent-profiles", json=profile_data)
    assert response.status_code == 200
    
    # Try to create duplicate (same agent_type)
    dup_data = {
        "id": "profile-specialist-dup-2",
        "agent_type": "specialist",  # Same agent_type
        "display_name": "Another Specialist",
        "policy_tier": "standard",
        "active": True,
    }
    response = await client.post("/api/governance/agent-profiles", json=dup_data)
    assert response.status_code == 409
    data = response.json()
    assert "already registered" in data["detail"]


@pytest.mark.asyncio
async def test_list_agent_profiles(client: AsyncClient):
    """Test listing multiple agent profiles."""
    profiles = [
        {"id": "profile-analyst", "agent_type": "analyst", "display_name": "Analyst", "policy_tier": "standard", "active": True},
        {"id": "profile-tester", "agent_type": "tester", "display_name": "Tester", "policy_tier": "standard", "active": True},
    ]
    
    for profile in profiles:
        await client.post("/api/governance/agent-profiles", json=profile)
    
    response = await client.get("/api/governance/agent-profiles")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    agent_types = [p["agent_type"] for p in data]
    assert "analyst" in agent_types
    assert "tester" in agent_types


@pytest.mark.asyncio
async def test_list_routines_empty(client: AsyncClient):
    """Test listing routines when empty."""
    response = await client.get("/api/governance/routines")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_routine_success(client: AsyncClient):
    """Test creating a routine successfully."""
    routine_data = {
        "id": "test-routine-1",
        "name": "Test Health Check",
        "description": "A test health check routine",
        "schedule": "0 * * * *",  # Every hour
        "enabled": True,
        "execution_policy": "auto",
        "hook": "noop",
    }
    
    response = await client.post("/api/governance/routines", json=routine_data)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "test-routine-1"
    assert data["name"] == "Test Health Check"
    assert data["schedule"] == "0 * * * *"
    assert data["enabled"] is True
    assert data["next_run_at"] is not None  # Should be computed from schedule


@pytest.mark.asyncio
async def test_list_routines(client: AsyncClient):
    """Test listing multiple routines."""
    routines = [
        {"id": "routine-1", "name": "Cleanup", "schedule": "0 0 * * *", "enabled": True, "execution_policy": "auto", "hook": "noop"},
        {"id": "routine-2", "name": "Backup", "schedule": "0 */6 * * *", "enabled": False, "execution_policy": "auto", "hook": "noop"},
    ]
    
    for routine in routines:
        await client.post("/api/governance/routines", json=routine)
    
    response = await client.get("/api/governance/routines")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2


@pytest.mark.asyncio
async def test_run_routine_now_success(client: AsyncClient):
    """Test running a routine manually."""
    # Create a routine first
    routine_data = {
        "id": "test-routine-manual",
        "name": "Manual Test Routine",
        "schedule": "0 0 * * *",
        "enabled": True,
        "execution_policy": "auto",
        "hook": "noop",
    }
    await client.post("/api/governance/routines", json=routine_data)
    
    # Run it manually
    response = await client.post("/api/governance/routines/test-routine-manual/run")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["routine_id"] == "test-routine-manual"
    assert "result" in data


@pytest.mark.asyncio
async def test_run_routine_not_found(client: AsyncClient):
    """Test running a non-existent routine fails."""
    response = await client.post("/api/governance/routines/nonexistent-routine/run")
    assert response.status_code == 404
    data = response.json()
    assert "not found" in data["detail"]


@pytest.mark.asyncio
async def test_list_routine_audit_events_empty(client: AsyncClient):
    """Test listing audit events for a routine when empty."""
    # Create a routine first
    routine_data = {
        "id": "test-routine-audit",
        "name": "Audit Test Routine",
        "schedule": "0 0 * * *",
        "enabled": True,
        "execution_policy": "auto",
        "hook": "noop",
    }
    await client.post("/api/governance/routines", json=routine_data)
    
    response = await client.get("/api/governance/routines/test-routine-audit/audit")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_knowledge_requests_empty(client: AsyncClient):
    """Test listing knowledge requests when empty."""
    response = await client.get("/api/governance/knowledge-requests")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_knowledge_request_success(client: AsyncClient):
    """Test creating a knowledge request successfully."""
    request_data = {
        "id": "kr-1",
        "project_id": "proj-1",
        "topic_id": "topic-1",
        "prompt": "Analyze competitor products",
        "status": "pending",
    }
    
    response = await client.post("/api/governance/knowledge-requests", json=request_data)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "kr-1"
    assert data["prompt"] == "Analyze competitor products"
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_list_knowledge_requests_by_project(client: AsyncClient):
    """Test filtering knowledge requests by project."""
    requests = [
        {"id": "kr-1", "project_id": "proj-1", "topic_id": "topic-1", "prompt": "Request 1", "status": "pending"},
        {"id": "kr-2", "project_id": "proj-2", "topic_id": "topic-2", "prompt": "Request 2", "status": "pending"},
        {"id": "kr-3", "project_id": "proj-1", "topic_id": "topic-3", "prompt": "Request 3", "status": "completed"},
    ]
    
    for req in requests:
        await client.post("/api/governance/knowledge-requests", json=req)
    
    # Filter by project
    response = await client.get("/api/governance/knowledge-requests?project_id=proj-1")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert all(kr["project_id"] == "proj-1" for kr in data)


@pytest.mark.asyncio
async def test_backfill_knowledge_from_research(client: AsyncClient):
    """Test backfilling knowledge requests from research requests."""
    response = await client.post("/api/governance/knowledge-requests/backfill-from-research")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "created" in data
    assert isinstance(data["created"], int)
