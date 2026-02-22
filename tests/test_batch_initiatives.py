"""Tests for batch initiative processing with Lobs new task creation."""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentInitiative, Project


@pytest_asyncio.fixture
async def initiative_project(db_session: AsyncSession):
    project = Project(id="test-proj", title="Test Project", type="kanban", archived=False)
    db_session.add(project)
    await db_session.commit()
    return project


@pytest_asyncio.fixture
async def pending_initiatives(db_session: AsyncSession, initiative_project):
    for i in range(4):
        db_session.add(AgentInitiative(
            id=f"init-{i}",
            proposed_by_agent="researcher",
            title=f"Initiative {i}",
            description=f"Description for initiative {i}",
            category="light_research",
            status="lobs_review",
            risk_tier="low",
            policy_lane="auto",
        ))
    await db_session.commit()


@pytest.mark.asyncio
async def test_batch_decide_approve_and_reject(client: AsyncClient, pending_initiatives):
    payload = {
        "decisions": [
            {"initiative_id": "init-0", "decision": "approve", "decision_summary": "Good idea"},
            {"initiative_id": "init-1", "decision": "reject", "decision_summary": "Not needed"},
            {"initiative_id": "init-2", "decision": "defer", "decision_summary": "Later"},
            {"initiative_id": "init-3", "decision": "approve", "decision_summary": "Do it"},
        ]
    }
    response = await client.post("/api/orchestrator/intelligence/initiatives/batch-decide", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["approved"] == 2
    assert data["rejected"] == 1
    assert data["deferred"] == 1


@pytest.mark.asyncio
async def test_batch_decide_with_new_tasks(client: AsyncClient, pending_initiatives, initiative_project):
    """Lobs can create new tasks alongside initiative decisions."""
    payload = {
        "decisions": [
            {"initiative_id": "init-0", "decision": "approve"},
        ],
        "new_tasks": [
            {
                "title": "Lobs idea: consolidate init-1 and init-2",
                "notes": "These two initiatives overlap, combining into one task",
                "project_id": "test-proj",
                "agent": "programmer",
                "rationale": "init-1 and init-2 are basically the same thing"
            },
            {
                "title": "Missing: we need API rate limiting",
                "project_id": "test-proj",
                "agent": "architect",
                "rationale": "None of the initiatives mentioned this but it's critical"
            },
        ]
    }
    response = await client.post("/api/orchestrator/intelligence/initiatives/batch-decide", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["approved"] == 1
    assert data["new_tasks"]["created"] == 2
    assert len(data["new_tasks"]["tasks"]) == 2
    # Verify task details
    task_titles = [t["title"] for t in data["new_tasks"]["tasks"]]
    assert "Lobs idea: consolidate init-1 and init-2" in task_titles
    assert "Missing: we need API rate limiting" in task_titles
    # Each created task should have an id
    assert all(t["task_id"] for t in data["new_tasks"]["tasks"])


@pytest.mark.asyncio
async def test_batch_only_new_tasks_no_decisions(client: AsyncClient, initiative_project):
    """Lobs can submit only new tasks with no initiative decisions."""
    payload = {
        "new_tasks": [
            {
                "title": "Brand new idea from Lobs",
                "project_id": "test-proj",
                "agent": "writer",
                "rationale": "Saw a pattern in recent work"
            }
        ]
    }
    response = await client.post("/api/orchestrator/intelligence/initiatives/batch-decide", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["new_tasks"]["created"] == 1
    assert data["processed"] == 0


@pytest.mark.asyncio
async def test_batch_new_task_bad_project(client: AsyncClient):
    """New task with nonexistent project is reported as error."""
    payload = {
        "new_tasks": [
            {"title": "Task", "project_id": "nonexistent"}
        ]
    }
    response = await client.post("/api/orchestrator/intelligence/initiatives/batch-decide", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["new_tasks"]["created"] == 0
    assert data["failed"] == 1


@pytest.mark.asyncio
async def test_batch_empty_rejected(client: AsyncClient):
    response = await client.post(
        "/api/orchestrator/intelligence/initiatives/batch-decide",
        json={"decisions": [], "new_tasks": []},
    )
    assert response.status_code == 400
