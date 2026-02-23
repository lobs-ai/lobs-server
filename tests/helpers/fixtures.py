"""Additional pytest fixtures to supplement conftest.py.

These fixtures are automatically available via conftest.py import.
Provides commonly used test objects and configurations.
"""

import pytest
import pytest_asyncio
from typing import AsyncGenerator, Dict, Any
from datetime import datetime, timezone
from httpx import AsyncClient

from tests.helpers.factories import (
    create_project_data,
    create_task_data,
    create_agent_data,
    create_topic_data,
    create_memory_data,
)


@pytest_asyncio.fixture
async def multiple_projects(client: AsyncClient) -> list[Dict[str, Any]]:
    """Create multiple test projects.
    
    Returns:
        List of 3 created project dicts
    """
    projects = []
    for i in range(3):
        project_data = create_project_data(
            title=f"Test Project {i+1}",
            sort_order=i
        )
        response = await client.post("/api/projects", json=project_data)
        assert response.status_code == 200
        projects.append(response.json())
    return projects


@pytest_asyncio.fixture
async def multiple_tasks(client: AsyncClient, sample_project) -> list[Dict[str, Any]]:
    """Create multiple test tasks in the same project.
    
    Returns:
        List of 5 created task dicts
    """
    tasks = []
    statuses = ["inbox", "queued", "in_progress", "blocked", "completed"]
    for i, status in enumerate(statuses):
        task_data = create_task_data(
            title=f"Test Task {i+1}",
            project_id=sample_project["id"],
            status=status,
            sort_order=i
        )
        response = await client.post("/api/tasks", json=task_data)
        assert response.status_code == 200
        tasks.append(response.json())
    return tasks


@pytest_asyncio.fixture
async def agent_status(client: AsyncClient) -> Dict[str, Any]:
    """Create a test agent status record.
    
    Returns:
        Created agent status dict
    """
    agent_data = create_agent_data(
        agent_id="programmer",
        status="active"
    )
    response = await client.post("/api/agents/status", json=agent_data)
    assert response.status_code == 200
    return response.json()


@pytest_asyncio.fixture
async def sample_topic(client: AsyncClient) -> Dict[str, Any]:
    """Create a test topic.
    
    Returns:
        Created topic dict
    """
    topic_data = create_topic_data(
        title="Test Topic",
        description="A test topic for unit tests"
    )
    response = await client.post("/api/topics", json=topic_data)
    assert response.status_code == 200
    return response.json()


@pytest_asyncio.fixture
async def sample_memory(client: AsyncClient) -> Dict[str, Any]:
    """Create a test memory entry.
    
    Returns:
        Created memory dict
    """
    memory_data = create_memory_data(
        title="Test Memory",
        content="This is a test memory for unit tests",
        type="note"
    )
    response = await client.post("/api/memories", json=memory_data)
    assert response.status_code == 200
    return response.json()


@pytest_asyncio.fixture
async def queued_task(client: AsyncClient, sample_project) -> Dict[str, Any]:
    """Create a task in 'queued' status ready for orchestrator.
    
    Returns:
        Created task dict
    """
    task_data = create_task_data(
        title="Queued Task",
        project_id=sample_project["id"],
        status="queued",
        agent="programmer"
    )
    response = await client.post("/api/tasks", json=task_data)
    assert response.status_code == 200
    return response.json()


@pytest_asyncio.fixture
async def blocked_task(client: AsyncClient, sample_project) -> Dict[str, Any]:
    """Create a task in 'blocked' status.
    
    Returns:
        Created task dict
    """
    task_data = create_task_data(
        title="Blocked Task",
        project_id=sample_project["id"],
        status="blocked",
        notes="Blocked on external dependency"
    )
    response = await client.post("/api/tasks", json=task_data)
    assert response.status_code == 200
    return response.json()


@pytest_asyncio.fixture
async def completed_task(client: AsyncClient, sample_project) -> Dict[str, Any]:
    """Create a completed task.
    
    Returns:
        Created task dict
    """
    task_data = create_task_data(
        title="Completed Task",
        project_id=sample_project["id"],
        status="completed"
    )
    response = await client.post("/api/tasks", json=task_data)
    assert response.status_code == 200
    return response.json()


@pytest.fixture
def mock_datetime_now(monkeypatch) -> datetime:
    """Mock datetime.now() to return a fixed time.
    
    Returns:
        The mocked datetime object
    """
    fixed_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    
    class MockDatetime:
        @classmethod
        def now(cls, tz=None):
            return fixed_time
        
        @classmethod
        def utcnow(cls):
            return fixed_time
    
    monkeypatch.setattr("datetime.datetime", MockDatetime)
    return fixed_time


@pytest.fixture
def disable_orchestrator(monkeypatch):
    """Disable orchestrator for tests that don't need it.
    
    This is already done in conftest.py client fixture, but available
    for tests that don't use the client fixture.
    """
    from app.config import settings
    settings.ORCHESTRATOR_ENABLED = False
    yield
    settings.ORCHESTRATOR_ENABLED = True


@pytest.fixture
def api_base_headers(test_token) -> Dict[str, str]:
    """Get base headers for API requests (auth + content-type).
    
    Returns:
        Dictionary of HTTP headers
    """
    return {
        "Authorization": f"Bearer {test_token}",
        "Content-Type": "application/json"
    }
