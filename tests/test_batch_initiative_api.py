"""API integration tests for batch initiative decision endpoint.

Tests the actual HTTP API for batch processing initiatives.
"""

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models import AgentInitiative, Task, Project


@pytest.mark.asyncio
async def test_batch_decide_api_processes_multiple_initiatives(client: AsyncClient, db_session):
    """Test the batch-decide API endpoint with valid initiatives."""
    # Setup: create a project
    db_session.add(Project(id="test-project", title="Test Project", type="kanban", archived=False))
    
    # Create 3 initiatives
    initiatives = []
    for i in range(3):
        initiative = AgentInitiative(
            id=str(uuid.uuid4()),
            proposed_by_agent="programmer",
            title=f"API Test Initiative {i+1}",
            description=f"Description {i+1}",
            category="test_hygiene",
            status="proposed",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(initiative)
        initiatives.append(initiative)
    
    await db_session.commit()
    
    # Call the batch API
    response = await client.post(
        "/api/orchestrator/intelligence/initiatives/batch-decide",
        json={
            "decisions": [
                {
                    "initiative_id": initiatives[0].id,
                    "decision": "approve",
                    "decision_summary": "Good idea",
                },
                {
                    "initiative_id": initiatives[1].id,
                    "decision": "defer",
                    "decision_summary": "Maybe later",
                },
                {
                    "initiative_id": initiatives[2].id,
                    "decision": "reject",
                    "decision_summary": "Not aligned",
                },
            ]
        },
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Verify stats
    assert data["total"] == 3
    assert data["processed"] == 3
    assert data["approved"] == 1
    assert data["deferred"] == 1
    assert data["rejected"] == 1
    assert data["failed"] == 0
    
    # Verify results array
    assert len(data["results"]) == 3
    assert data["results"][0]["status"] == "approved"
    assert data["results"][1]["status"] == "deferred"
    assert data["results"][2]["status"] == "rejected"
    
    # Verify task created for approved
    assert data["results"][0]["task_id"] is not None
    assert data["results"][1].get("task_id") is None
    assert data["results"][2].get("task_id") is None
    
    # Verify database state
    tasks = (await db_session.execute(select(Task))).scalars().all()
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_batch_decide_api_handles_missing_initiatives(client: AsyncClient, db_session):
    """Test that API handles missing initiative IDs gracefully."""
    # Setup: create a project
    db_session.add(Project(id="test-project", title="Test Project", type="kanban", archived=False))
    
    # Create 1 valid initiative
    valid_initiative = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="programmer",
        title="Valid initiative",
        description="This one exists",
        category="test_hygiene",
        status="proposed",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(valid_initiative)
    await db_session.commit()
    
    # Call API with 1 valid + 1 invalid ID
    invalid_id = str(uuid.uuid4())
    
    response = await client.post(
        "/api/orchestrator/intelligence/initiatives/batch-decide",
        json={
            "decisions": [
                {
                    "initiative_id": valid_initiative.id,
                    "decision": "approve",
                    "decision_summary": "Approved",
                },
                {
                    "initiative_id": invalid_id,
                    "decision": "approve",
                    "decision_summary": "This should fail",
                },
            ]
        },
    )
    
    assert response.status_code == 200
    data = response.json()
    
    # Verify stats
    assert data["total"] == 2
    assert data["processed"] == 1  # Only valid one
    assert data["approved"] == 1
    assert data["failed"] == 1  # Invalid one failed
    
    # Verify errors reported
    assert len(data["errors"]) == 1
    assert data["errors"][0]["initiative_id"] == invalid_id
    assert "not found" in data["errors"][0]["error"].lower()
    
    # Verify task created for valid one
    tasks = (await db_session.execute(select(Task))).scalars().all()
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_batch_decide_api_rejects_empty_batch(client: AsyncClient, db_session):
    """Test that API rejects empty decision arrays."""
    response = await client.post(
        "/api/orchestrator/intelligence/initiatives/batch-decide",
        json={"decisions": []},
    )
    
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_initiatives_api_filters_by_status(client: AsyncClient, db_session):
    """Test that the list endpoint filters initiatives by status."""
    # Create initiatives with different statuses
    proposed = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="programmer",
        title="Proposed initiative",
        description="Still pending",
        category="test_hygiene",
        status="proposed",
        created_at=datetime.now(timezone.utc),
    )
    
    approved = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="programmer",
        title="Approved initiative",
        description="Already decided",
        category="test_hygiene",
        status="approved",
        created_at=datetime.now(timezone.utc),
    )
    
    db_session.add_all([proposed, approved])
    await db_session.commit()
    
    # List only proposed
    response = await client.get("/api/orchestrator/intelligence/initiatives?status=proposed")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["count"] == 1
    assert data["items"][0]["status"] == "proposed"
    assert data["items"][0]["id"] == proposed.id


@pytest.mark.asyncio
async def test_batch_workflow_list_then_decide(client: AsyncClient, db_session):
    """
    Test the complete batch workflow:
    1. List all proposed initiatives
    2. Review as a batch
    3. Submit all decisions
    4. Verify results
    """
    # Setup: create a project
    db_session.add(Project(id="test-project", title="Test Project", type="kanban", archived=False))
    
    # Create several proposed initiatives
    initiatives = []
    for i in range(5):
        initiative = AgentInitiative(
            id=str(uuid.uuid4()),
            proposed_by_agent="programmer",
            title=f"Workflow Initiative {i+1}",
            description=f"Description {i+1}",
            category="test_hygiene",
            status="proposed",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(initiative)
        initiatives.append(initiative)
    
    await db_session.commit()
    
    # Step 1: List all proposed initiatives
    list_response = await client.get("/api/orchestrator/intelligence/initiatives?status=proposed")
    assert list_response.status_code == 200
    
    list_data = list_response.json()
    assert list_data["count"] == 5
    
    # Step 2: Review and prepare batch decisions
    # (In real usage, Lobs would review the full list and make decisions)
    decisions = []
    for item in list_data["items"]:
        # Simulate Lobs reviewing: approve first 3, defer rest
        decision = "approve" if item["title"].endswith(("1", "2", "3")) else "defer"
        decisions.append({
            "initiative_id": item["id"],
            "decision": decision,
            "decision_summary": f"{decision.capitalize()} based on batch review",
        })
    
    # Step 3: Submit batch decisions
    batch_response = await client.post(
        "/api/orchestrator/intelligence/initiatives/batch-decide",
        json={"decisions": decisions},
    )
    
    assert batch_response.status_code == 200
    batch_data = batch_response.json()
    
    # Verify batch results
    assert batch_data["total"] == 5
    assert batch_data["processed"] == 5
    assert batch_data["approved"] == 3
    assert batch_data["deferred"] == 2
    assert batch_data["failed"] == 0
    
    # Step 4: Verify only proposed initiatives are gone
    updated_list_response = await client.get("/api/orchestrator/intelligence/initiatives?status=proposed")
    assert updated_list_response.status_code == 200
    
    updated_data = updated_list_response.json()
    assert updated_data["count"] == 0  # All decided
    
    # Verify database state
    tasks = (await db_session.execute(select(Task))).scalars().all()
    assert len(tasks) == 3  # Only approved ones
