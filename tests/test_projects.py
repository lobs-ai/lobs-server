"""Tests for project API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_project(client: AsyncClient):
    """Test creating a project."""
    project_data = {
        "id": "proj-1",
        "title": "My Project",
        "notes": "Project notes",
        "archived": False,
        "type": "kanban",
        "sort_order": 0
    }
    response = await client.post("/api/projects", json=project_data)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "proj-1"
    assert data["title"] == "My Project"
    assert data["notes"] == "Project notes"
    assert data["archived"] is False
    assert data["type"] == "kanban"
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_list_projects(client: AsyncClient, sample_project):
    """Test listing projects."""
    response = await client.get("/api/projects")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == sample_project["id"]


@pytest.mark.asyncio
async def test_list_projects_pagination(client: AsyncClient):
    """Test project pagination."""
    # Create multiple projects
    for i in range(5):
        await client.post("/api/projects", json={
            "id": f"proj-{i}",
            "title": f"Project {i}",
            "archived": False,
            "type": "kanban",
            "sort_order": i
        })
    
    # Test limit
    response = await client.get("/api/projects?limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2
    
    # Test offset
    response = await client.get("/api/projects?offset=2&limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_list_projects_filter_archived(client: AsyncClient):
    """Test filtering projects by archived status."""
    # Create archived and non-archived projects
    await client.post("/api/projects", json={
        "id": "active-proj",
        "title": "Active Project",
        "archived": False,
        "type": "kanban",
        "sort_order": 0
    })
    await client.post("/api/projects", json={
        "id": "archived-proj",
        "title": "Archived Project",
        "archived": True,
        "type": "kanban",
        "sort_order": 0
    })
    
    # Get non-archived
    response = await client.get("/api/projects?archived=false")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "active-proj"
    
    # Get archived
    response = await client.get("/api/projects?archived=true")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "archived-proj"


@pytest.mark.asyncio
async def test_get_project(client: AsyncClient, sample_project):
    """Test getting a single project."""
    response = await client.get(f"/api/projects/{sample_project['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == sample_project["id"]
    assert data["title"] == sample_project["title"]


@pytest.mark.asyncio
async def test_get_project_not_found(client: AsyncClient):
    """Test getting a non-existent project returns 404."""
    response = await client.get("/api/projects/nonexistent")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_project(client: AsyncClient, sample_project):
    """Test updating a project."""
    update_data = {
        "title": "Updated Title",
        "notes": "Updated notes"
    }
    response = await client.put(
        f"/api/projects/{sample_project['id']}",
        json=update_data
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Title"
    assert data["notes"] == "Updated notes"
    assert data["id"] == sample_project["id"]


@pytest.mark.asyncio
async def test_update_project_not_found(client: AsyncClient):
    """Test updating a non-existent project returns 404."""
    response = await client.put(
        "/api/projects/nonexistent",
        json={"title": "Updated"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_project(client: AsyncClient, sample_project):
    """Test deleting a project."""
    response = await client.delete(f"/api/projects/{sample_project['id']}")
    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    
    # Verify it's deleted
    get_response = await client.get(f"/api/projects/{sample_project['id']}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_delete_project_not_found(client: AsyncClient):
    """Test deleting a non-existent project returns 404."""
    response = await client.delete("/api/projects/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_archive_project(client: AsyncClient, sample_project):
    """Test archiving a project."""
    response = await client.post(f"/api/projects/{sample_project['id']}/archive")
    assert response.status_code == 200
    data = response.json()
    assert data["archived"] is True
    assert data["id"] == sample_project["id"]


@pytest.mark.asyncio
async def test_archive_project_not_found(client: AsyncClient):
    """Test archiving a non-existent project returns 404."""
    response = await client.post("/api/projects/nonexistent/archive")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_project_with_github_tracking(client: AsyncClient):
    """Test creating a project with GitHub tracking."""
    project_data = {
        "id": "github-proj",
        "title": "GitHub Project",
        "archived": False,
        "type": "kanban",
        "sort_order": 0,
        "tracking": "github",
        "github_repo": "owner/repo",
        "github_label_filter": ["bug", "feature"]
    }
    response = await client.post("/api/projects", json=project_data)
    assert response.status_code == 200
    data = response.json()
    assert data["tracking"] == "github"
    assert data["github_repo"] == "owner/repo"
    assert data["github_label_filter"] == ["bug", "feature"]
