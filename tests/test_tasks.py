"""Tests for task API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_task(client: AsyncClient, sample_project):
    """Test creating a task."""
    task_data = {
        "id": "task-1",
        "title": "My Task",
        "status": "inbox",
        "project_id": sample_project["id"],
        "notes": "Task notes",
        "sort_order": 0,
        "pinned": False
    }
    response = await client.post("/api/tasks", json=task_data)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "task-1"
    assert data["title"] == "My Task"
    assert data["status"] == "inbox"
    assert data["project_id"] == sample_project["id"]
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_create_task_with_all_fields(client: AsyncClient, sample_project):
    """Test creating a task with all optional fields."""
    task_data = {
        "id": "task-full",
        "title": "Full Task",
        "status": "active",
        "owner": "rafe",
        "work_state": "in_progress",
        "review_state": "pending",
        "project_id": sample_project["id"],
        "notes": "Full task notes",
        "artifact_path": "/path/to/artifact",
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-02T00:00:00Z",
        "sort_order": 5,
        "blocked_by": ["task-1", "task-2"],
        "pinned": True,
        "shape": "large",
        "github_issue_number": 123,
        "agent": "programmer"
    }
    response = await client.post("/api/tasks", json=task_data)
    assert response.status_code == 200
    data = response.json()
    assert data["owner"] == "rafe"
    assert data["work_state"] == "in_progress"
    assert data["review_state"] == "pending"
    assert data["artifact_path"] == "/path/to/artifact"
    assert data["blocked_by"] == ["task-1", "task-2"]
    assert data["pinned"] is True
    assert data["shape"] == "large"
    assert data["github_issue_number"] == 123
    assert data["agent"] == "programmer"


@pytest.mark.asyncio
async def test_list_tasks(client: AsyncClient, sample_task):
    """Test listing tasks."""
    response = await client.get("/api/tasks")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == sample_task["id"]


@pytest.mark.asyncio
async def test_list_tasks_pagination(client: AsyncClient, sample_project):
    """Test task pagination."""
    # Create multiple tasks
    for i in range(5):
        await client.post("/api/tasks", json={
            "id": f"task-{i}",
            "title": f"Task {i}",
            "status": "inbox",
            "project_id": sample_project["id"],
            "sort_order": i,
            "pinned": False
        })
    
    # Test limit
    response = await client.get("/api/tasks?limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2
    
    # Test offset
    response = await client.get("/api/tasks?offset=2&limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_list_tasks_filter_by_project(client: AsyncClient, sample_project):
    """Test filtering tasks by project_id."""
    # Create another project
    other_proj = await client.post("/api/projects", json={
        "id": "other-proj",
        "title": "Other Project",
        "archived": False,
        "type": "kanban",
        "sort_order": 0
    })
    other_proj_data = other_proj.json()
    
    # Create tasks in different projects
    await client.post("/api/tasks", json={
        "id": "task-1",
        "title": "Task 1",
        "status": "inbox",
        "project_id": sample_project["id"],
        "sort_order": 0,
        "pinned": False
    })
    await client.post("/api/tasks", json={
        "id": "task-2",
        "title": "Task 2",
        "status": "inbox",
        "project_id": other_proj_data["id"],
        "sort_order": 0,
        "pinned": False
    })
    
    # Filter by first project
    response = await client.get(f"/api/tasks?project_id={sample_project['id']}")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["project_id"] == sample_project["id"]


@pytest.mark.asyncio
async def test_list_tasks_filter_by_status(client: AsyncClient, sample_project):
    """Test filtering tasks by status."""
    # Create tasks with different statuses
    await client.post("/api/tasks", json={
        "id": "inbox-task",
        "title": "Inbox Task",
        "status": "inbox",
        "project_id": sample_project["id"],
        "sort_order": 0,
        "pinned": False
    })
    await client.post("/api/tasks", json={
        "id": "active-task",
        "title": "Active Task",
        "status": "active",
        "project_id": sample_project["id"],
        "sort_order": 0,
        "pinned": False
    })
    
    # Filter by status
    response = await client.get("/api/tasks?status=active")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["status"] == "active"


@pytest.mark.asyncio
async def test_list_tasks_filter_by_owner(client: AsyncClient, sample_project):
    """Test filtering tasks by owner."""
    # Create tasks with different owners
    await client.post("/api/tasks", json={
        "id": "rafe-task",
        "title": "Rafe's Task",
        "status": "inbox",
        "owner": "rafe",
        "project_id": sample_project["id"],
        "sort_order": 0,
        "pinned": False
    })
    await client.post("/api/tasks", json={
        "id": "lobs-task",
        "title": "Lobs's Task",
        "status": "inbox",
        "owner": "lobs",
        "project_id": sample_project["id"],
        "sort_order": 0,
        "pinned": False
    })
    
    # Filter by owner
    response = await client.get("/api/tasks?owner=rafe")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["owner"] == "rafe"


@pytest.mark.asyncio
async def test_get_task(client: AsyncClient, sample_task):
    """Test getting a single task."""
    response = await client.get(f"/api/tasks/{sample_task['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == sample_task["id"]
    assert data["title"] == sample_task["title"]


@pytest.mark.asyncio
async def test_get_task_not_found(client: AsyncClient):
    """Test getting a non-existent task returns 404."""
    response = await client.get("/api/tasks/nonexistent")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_task(client: AsyncClient, sample_task):
    """Test updating a task."""
    update_data = {
        "title": "Updated Task",
        "notes": "Updated notes",
        "status": "active"
    }
    response = await client.put(
        f"/api/tasks/{sample_task['id']}",
        json=update_data
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Task"
    assert data["notes"] == "Updated notes"
    assert data["status"] == "active"


@pytest.mark.asyncio
async def test_update_task_not_found(client: AsyncClient):
    """Test updating a non-existent task returns 404."""
    response = await client.put(
        "/api/tasks/nonexistent",
        json={"title": "Updated"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_task(client: AsyncClient, sample_task):
    """Test deleting a task."""
    response = await client.delete(f"/api/tasks/{sample_task['id']}")
    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    
    # Verify it's deleted
    get_response = await client.get(f"/api/tasks/{sample_task['id']}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_delete_task_not_found(client: AsyncClient):
    """Test deleting a non-existent task returns 404."""
    response = await client.delete("/api/tasks/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_archive_task(client: AsyncClient, sample_task):
    """Test archiving a task."""
    response = await client.post(f"/api/tasks/{sample_task['id']}/archive")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["id"] == sample_task["id"]


@pytest.mark.asyncio
async def test_archive_task_not_found(client: AsyncClient):
    """Test archiving a non-existent task returns 404."""
    response = await client.post("/api/tasks/nonexistent/archive")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_task_status(client: AsyncClient, sample_task):
    """Test updating task status."""
    response = await client.patch(
        f"/api/tasks/{sample_task['id']}/status",
        json={"status": "active"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "active"


@pytest.mark.asyncio
async def test_update_task_status_not_found(client: AsyncClient):
    """Test updating status of non-existent task returns 404."""
    response = await client.patch(
        "/api/tasks/nonexistent/status",
        json={"status": "active"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_task_work_state(client: AsyncClient, sample_task):
    """Test updating task work state."""
    response = await client.patch(
        f"/api/tasks/{sample_task['id']}/work-state",
        json={"work_state": "in_progress"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["work_state"] == "in_progress"


@pytest.mark.asyncio
async def test_update_task_work_state_not_found(client: AsyncClient):
    """Test updating work state of non-existent task returns 404."""
    response = await client.patch(
        "/api/tasks/nonexistent/work-state",
        json={"work_state": "in_progress"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_task_review_state(client: AsyncClient, sample_task):
    """Test updating task review state."""
    response = await client.patch(
        f"/api/tasks/{sample_task['id']}/review-state",
        json={"review_state": "approved"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["review_state"] == "approved"


@pytest.mark.asyncio
async def test_update_task_review_state_not_found(client: AsyncClient):
    """Test updating review state of non-existent task returns 404."""
    response = await client.patch(
        "/api/tasks/nonexistent/review-state",
        json={"review_state": "approved"}
    )
    assert response.status_code == 404
