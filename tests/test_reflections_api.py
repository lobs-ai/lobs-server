"""API integration tests for reflections list endpoint."""

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models import AgentReflection, AgentInitiative


@pytest.mark.asyncio
async def test_list_reflections_empty(client: AsyncClient, db_session):
    """Test listing reflections when there are none."""
    response = await client.get("/api/orchestrator/intelligence/reflections")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["reflections"] == []
    assert data["total"] == 0
    assert data["limit"] == 50
    assert data["offset"] == 0


@pytest.mark.asyncio
async def test_list_reflections_with_data(client: AsyncClient, db_session):
    """Test listing reflections with sample data."""
    # Create test reflections
    reflection1 = AgentReflection(
        id=str(uuid.uuid4()),
        agent_type="programmer",
        reflection_type="strategic",
        status="completed",
        window_start=datetime(2026, 2, 20, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 2, 21, 0, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 2, 21, 10, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 2, 21, 10, 30, tzinfo=timezone.utc),
        inefficiencies=["Inefficiency 1", "Inefficiency 2"],
        missed_opportunities=["Opportunity 1"],
        system_risks=["Risk 1"],
        identity_adjustments=["Adjustment 1"],
        result={"proposed_initiatives": ["init-1", "init-2"]},
    )
    
    reflection2 = AgentReflection(
        id=str(uuid.uuid4()),
        agent_type="researcher",
        reflection_type="diagnostic",
        status="pending",
        window_start=datetime(2026, 2, 21, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 2, 22, 0, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 2, 22, 9, 0, tzinfo=timezone.utc),
        inefficiencies=[],
        missed_opportunities=[],
        system_risks=[],
        identity_adjustments=[],
        result={},
    )
    
    db_session.add(reflection1)
    db_session.add(reflection2)
    await db_session.commit()
    
    # Test listing all reflections
    response = await client.get("/api/orchestrator/intelligence/reflections")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["total"] == 2
    assert len(data["reflections"]) == 2
    
    # Verify order (most recent first)
    assert data["reflections"][0]["agent_type"] == "researcher"
    assert data["reflections"][1]["agent_type"] == "programmer"
    
    # Verify structure of first reflection
    r = data["reflections"][1]  # programmer reflection
    assert r["id"] == reflection1.id
    assert r["agent_type"] == "programmer"
    assert r["reflection_type"] == "strategic"
    assert r["status"] == "completed"
    assert r["inefficiencies"] == ["Inefficiency 1", "Inefficiency 2"]
    assert r["missed_opportunities"] == ["Opportunity 1"]
    assert r["system_risks"] == ["Risk 1"]
    assert r["identity_adjustments"] == ["Adjustment 1"]
    assert r["proposed_initiatives"] == ["init-1", "init-2"]
    assert r["linked_initiatives"] == []


@pytest.mark.asyncio
async def test_list_reflections_with_initiatives(client: AsyncClient, db_session):
    """Test that linked initiatives are included in the response."""
    # Create reflection
    reflection = AgentReflection(
        id=str(uuid.uuid4()),
        agent_type="programmer",
        reflection_type="strategic",
        status="completed",
        created_at=datetime(2026, 2, 21, 10, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 2, 21, 10, 30, tzinfo=timezone.utc),
        inefficiencies=[],
        missed_opportunities=[],
        system_risks=[],
        identity_adjustments=[],
        result={},
    )
    db_session.add(reflection)
    
    # Create linked initiatives
    initiative1 = AgentInitiative(
        id=str(uuid.uuid4()),
        source_reflection_id=reflection.id,
        proposed_by_agent="programmer",
        title="Initiative 1",
        description="Description 1",
        category="test_hygiene",
        status="approved",
        decision_summary="Approved for testing",
        learning_feedback="Good idea",
        created_at=datetime(2026, 2, 21, 11, 0, tzinfo=timezone.utc),
    )
    
    initiative2 = AgentInitiative(
        id=str(uuid.uuid4()),
        source_reflection_id=reflection.id,
        proposed_by_agent="programmer",
        title="Initiative 2",
        description="Description 2",
        category="refactor",
        status="rejected",
        decision_summary="Not aligned with current goals",
        created_at=datetime(2026, 2, 21, 11, 5, tzinfo=timezone.utc),
    )
    
    db_session.add(initiative1)
    db_session.add(initiative2)
    await db_session.commit()
    
    # Fetch reflections
    response = await client.get("/api/orchestrator/intelligence/reflections")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["total"] == 1
    assert len(data["reflections"]) == 1
    
    r = data["reflections"][0]
    assert len(r["linked_initiatives"]) == 2
    
    # Verify initiative details
    inits = r["linked_initiatives"]
    init_titles = {i["title"] for i in inits}
    assert init_titles == {"Initiative 1", "Initiative 2"}
    
    # Check specific fields
    approved = [i for i in inits if i["status"] == "approved"][0]
    assert approved["decision_summary"] == "Approved for testing"
    assert approved["learning_feedback"] == "Good idea"
    
    rejected = [i for i in inits if i["status"] == "rejected"][0]
    assert rejected["decision_summary"] == "Not aligned with current goals"


@pytest.mark.asyncio
async def test_list_reflections_pagination(client: AsyncClient, db_session):
    """Test pagination parameters."""
    # Create 5 reflections
    for i in range(5):
        reflection = AgentReflection(
            id=str(uuid.uuid4()),
            agent_type="programmer",
            reflection_type="strategic",
            status="completed",
            created_at=datetime(2026, 2, 20 + i, 10, 0, tzinfo=timezone.utc),
            inefficiencies=[],
            missed_opportunities=[],
            system_risks=[],
            identity_adjustments=[],
            result={},
        )
        db_session.add(reflection)
    
    await db_session.commit()
    
    # Test limit
    response = await client.get("/api/orchestrator/intelligence/reflections?limit=2")
    data = response.json()
    assert len(data["reflections"]) == 2
    assert data["total"] == 5
    assert data["limit"] == 2
    
    # Test offset
    response = await client.get("/api/orchestrator/intelligence/reflections?limit=2&offset=2")
    data = response.json()
    assert len(data["reflections"]) == 2
    assert data["offset"] == 2


@pytest.mark.asyncio
async def test_list_reflections_filter_by_agent_type(client: AsyncClient, db_session):
    """Test filtering by agent_type."""
    # Create reflections for different agents
    programmer_reflection = AgentReflection(
        id=str(uuid.uuid4()),
        agent_type="programmer",
        reflection_type="strategic",
        status="completed",
        created_at=datetime(2026, 2, 21, 10, 0, tzinfo=timezone.utc),
        inefficiencies=[],
        missed_opportunities=[],
        system_risks=[],
        identity_adjustments=[],
        result={},
    )
    
    researcher_reflection = AgentReflection(
        id=str(uuid.uuid4()),
        agent_type="researcher",
        reflection_type="strategic",
        status="completed",
        created_at=datetime(2026, 2, 21, 11, 0, tzinfo=timezone.utc),
        inefficiencies=[],
        missed_opportunities=[],
        system_risks=[],
        identity_adjustments=[],
        result={},
    )
    
    db_session.add(programmer_reflection)
    db_session.add(researcher_reflection)
    await db_session.commit()
    
    # Filter by programmer
    response = await client.get("/api/orchestrator/intelligence/reflections?agent_type=programmer")
    data = response.json()
    
    assert data["total"] == 1
    assert len(data["reflections"]) == 1
    assert data["reflections"][0]["agent_type"] == "programmer"


@pytest.mark.asyncio
async def test_list_reflections_filter_by_status(client: AsyncClient, db_session):
    """Test filtering by status."""
    # Create reflections with different statuses
    completed_reflection = AgentReflection(
        id=str(uuid.uuid4()),
        agent_type="programmer",
        reflection_type="strategic",
        status="completed",
        created_at=datetime(2026, 2, 21, 10, 0, tzinfo=timezone.utc),
        inefficiencies=[],
        missed_opportunities=[],
        system_risks=[],
        identity_adjustments=[],
        result={},
    )
    
    pending_reflection = AgentReflection(
        id=str(uuid.uuid4()),
        agent_type="programmer",
        reflection_type="strategic",
        status="pending",
        created_at=datetime(2026, 2, 21, 11, 0, tzinfo=timezone.utc),
        inefficiencies=[],
        missed_opportunities=[],
        system_risks=[],
        identity_adjustments=[],
        result={},
    )
    
    db_session.add(completed_reflection)
    db_session.add(pending_reflection)
    await db_session.commit()
    
    # Filter by completed
    response = await client.get("/api/orchestrator/intelligence/reflections?status=completed")
    data = response.json()
    
    assert data["total"] == 1
    assert len(data["reflections"]) == 1
    assert data["reflections"][0]["status"] == "completed"
    
    # Filter by pending
    response = await client.get("/api/orchestrator/intelligence/reflections?status=pending")
    data = response.json()
    
    assert data["total"] == 1
    assert len(data["reflections"]) == 1
    assert data["reflections"][0]["status"] == "pending"
