"""Tests for workspaces API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_workspaces_empty(client: AsyncClient):
    """Test listing workspaces when empty."""
    response = await client.get("/api/workspaces")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_workspace_success(client: AsyncClient):
    """Test creating a workspace successfully."""
    workspace_data = {
        "id": "workspace-1",
        "slug": "test-workspace",
        "name": "Test Workspace",
        "description": "A test workspace for testing",
        "is_default": False,
    }
    
    response = await client.post("/api/workspaces", json=workspace_data)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "workspace-1"
    assert data["slug"] == "test-workspace"
    assert data["name"] == "Test Workspace"
    assert data["description"] == "A test workspace for testing"


@pytest.mark.asyncio
async def test_create_duplicate_workspace(client: AsyncClient):
    """Test creating a duplicate workspace fails."""
    workspace_data = {
        "id": "workspace-dup",
        "slug": "dup-workspace",
        "name": "Duplicate Workspace",
        "is_default": False,
    }
    
    # Create first workspace
    response = await client.post("/api/workspaces", json=workspace_data)
    assert response.status_code == 200
    
    # Try to create duplicate
    response = await client.post("/api/workspaces", json=workspace_data)
    assert response.status_code == 409
    data = response.json()
    assert "already exists" in data["detail"]


@pytest.mark.asyncio
async def test_list_workspaces(client: AsyncClient):
    """Test listing multiple workspaces."""
    workspaces = [
        {"id": "ws-1", "slug": "workspace-1", "name": "Workspace 1", "is_default": False},
        {"id": "ws-2", "slug": "workspace-2", "name": "Workspace 2", "is_default": False},
        {"id": "ws-3", "slug": "workspace-3", "name": "Workspace 3", "is_default": False},
    ]
    
    for ws in workspaces:
        await client.post("/api/workspaces", json=ws)
    
    response = await client.get("/api/workspaces")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    workspace_ids = [w["id"] for w in data]
    assert "ws-1" in workspace_ids
    assert "ws-2" in workspace_ids
    assert "ws-3" in workspace_ids


@pytest.mark.asyncio
async def test_update_workspace_success(client: AsyncClient):
    """Test updating a workspace successfully."""
    # Create workspace
    workspace_data = {"id": "ws-update", "slug": "ws-update", "name": "Original Name", "is_default": False}
    await client.post("/api/workspaces", json=workspace_data)
    
    # Update it
    update_data = {
        "name": "Updated Name",
        "description": "New description",
    }
    response = await client.put("/api/workspaces/ws-update", json=update_data)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "ws-update"
    assert data["name"] == "Updated Name"
    assert data["description"] == "New description"
    assert data["slug"] == "ws-update"  # Unchanged


@pytest.mark.asyncio
async def test_update_workspace_not_found(client: AsyncClient):
    """Test updating a non-existent workspace fails."""
    update_data = {"name": "New Name"}
    response = await client.put("/api/workspaces/nonexistent", json=update_data)
    assert response.status_code == 404
    data = response.json()
    assert "not found" in data["detail"]


@pytest.mark.asyncio
async def test_list_files_empty(client: AsyncClient):
    """Test listing files in a workspace when empty."""
    # Create workspace
    workspace_data = {"id": "ws-files", "slug": "ws-files", "name": "Files Workspace", "is_default": False}
    await client.post("/api/workspaces", json=workspace_data)
    
    response = await client.get("/api/workspaces/ws-files/files")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_file_success(client: AsyncClient):
    """Test creating a file in a workspace."""
    # Create workspace
    workspace_data = {"id": "ws-file-create", "slug": "ws-file-create", "name": "File Create Workspace", "is_default": False}
    await client.post("/api/workspaces", json=workspace_data)
    
    # Create file
    file_data = {
        "id": "file-1",
        "workspace_id": "ws-file-create",
        "path": "/test/file1.txt",
        "content": "File content here",
    }
    response = await client.post("/api/workspaces/ws-file-create/files", json=file_data)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "file-1"
    assert data["path"] == "/test/file1.txt"
    assert data["content"] == "File content here"
    assert data["workspace_id"] == "ws-file-create"


@pytest.mark.asyncio
async def test_list_files(client: AsyncClient):
    """Test listing multiple files in a workspace."""
    # Create workspace
    workspace_data = {"id": "ws-multi-files", "slug": "ws-multi-files", "name": "Multi Files", "is_default": False}
    await client.post("/api/workspaces", json=workspace_data)
    
    # Create multiple files
    files = [
        {"id": "f1", "workspace_id": "ws-multi-files", "path": "/test/file1.txt", "content": "Content 1"},
        {"id": "f2", "workspace_id": "ws-multi-files", "path": "/test/file2.py", "content": "print('hello')"},
        {"id": "f3", "workspace_id": "ws-multi-files", "path": "/test/file3.md", "content": "# Markdown"},
    ]
    
    for file_data in files:
        await client.post("/api/workspaces/ws-multi-files/files", json=file_data)
    
    response = await client.get("/api/workspaces/ws-multi-files/files")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    file_ids = [f["id"] for f in data]
    assert "f1" in file_ids
    assert "f2" in file_ids
    assert "f3" in file_ids


@pytest.mark.asyncio
async def test_list_links_empty(client: AsyncClient):
    """Test listing links in a workspace when empty."""
    # Create workspace
    workspace_data = {"id": "ws-links", "slug": "ws-links", "name": "Links Workspace", "is_default": False}
    await client.post("/api/workspaces", json=workspace_data)
    
    response = await client.get("/api/workspaces/ws-links/links")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_link_success(client: AsyncClient):
    """Test creating a link in a workspace."""
    # Create workspace
    workspace_data = {"id": "ws-link-create", "name": "Link Create Workspace", "root_path": "/test"}
    await client.post("/api/workspaces", json=workspace_data)
    
    # Create link
    link_data = {
        "id": "link-1",
        "source_file_id": "file-1",
        "target_file_id": "file-2",
        "link_type": "import",
    }
    response = await client.post("/api/workspaces/ws-link-create/links", json=link_data)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "link-1"
    assert data["source_file_id"] == "file-1"
    assert data["target_file_id"] == "file-2"
    assert data["link_type"] == "import"
    assert data["workspace_id"] == "ws-link-create"


@pytest.mark.asyncio
async def test_list_links(client: AsyncClient):
    """Test listing multiple links in a workspace."""
    # Create workspace
    workspace_data = {"id": "ws-multi-links", "name": "Multi Links", "root_path": "/test"}
    await client.post("/api/workspaces", json=workspace_data)
    
    # Create multiple links
    links = [
        {"id": "l1", "source_file_id": "f1", "target_file_id": "f2", "link_type": "import"},
        {"id": "l2", "source_file_id": "f2", "target_file_id": "f3", "link_type": "reference"},
        {"id": "l3", "source_file_id": "f1", "target_file_id": "f3", "link_type": "dependency"},
    ]
    
    for link_data in links:
        await client.post("/api/workspaces/ws-multi-links/links", json=link_data)
    
    response = await client.get("/api/workspaces/ws-multi-links/links")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    link_ids = [l["id"] for l in data]
    assert "l1" in link_ids
    assert "l2" in link_ids
    assert "l3" in link_ids


@pytest.mark.asyncio
async def test_workspace_isolation(client: AsyncClient):
    """Test that files and links are isolated per workspace."""
    # Create two workspaces
    await client.post("/api/workspaces", json={"id": "ws-a", "name": "Workspace A", "root_path": "/a"})
    await client.post("/api/workspaces", json={"id": "ws-b", "name": "Workspace B", "root_path": "/b"})
    
    # Add files to each
    await client.post("/api/workspaces/ws-a/files", json={"id": "fa1", "path": "/a/file.txt", "content": "A", "file_type": "text"})
    await client.post("/api/workspaces/ws-b/files", json={"id": "fb1", "path": "/b/file.txt", "content": "B", "file_type": "text"})
    
    # Check isolation
    response_a = await client.get("/api/workspaces/ws-a/files")
    response_b = await client.get("/api/workspaces/ws-b/files")
    
    assert response_a.status_code == 200
    assert response_b.status_code == 200
    
    files_a = response_a.json()
    files_b = response_b.json()
    
    assert len(files_a) == 1
    assert len(files_b) == 1
    assert files_a[0]["id"] == "fa1"
    assert files_b[0]["id"] == "fb1"
