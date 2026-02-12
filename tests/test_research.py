"""Tests for research API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_research_doc_not_found(client: AsyncClient, sample_project):
    """Test getting research doc that doesn't exist returns 404."""
    response = await client.get(f"/api/research/{sample_project['id']}/doc")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_research_doc(client: AsyncClient, sample_project):
    """Test creating a research document."""
    doc_data = {
        "content": "Research findings..."
    }
    response = await client.put(
        f"/api/research/{sample_project['id']}/doc",
        json=doc_data
    )
    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == sample_project["id"]
    assert data["content"] == "Research findings..."
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_update_research_doc(client: AsyncClient, sample_project):
    """Test updating an existing research document."""
    # Create initial doc
    await client.put(
        f"/api/research/{sample_project['id']}/doc",
        json={"content": "Initial content"}
    )
    
    # Update it
    response = await client.put(
        f"/api/research/{sample_project['id']}/doc",
        json={"content": "Updated content"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["content"] == "Updated content"


@pytest.mark.asyncio
async def test_get_research_doc(client: AsyncClient, sample_project):
    """Test getting an existing research document."""
    # Create doc
    await client.put(
        f"/api/research/{sample_project['id']}/doc",
        json={"content": "Test content"}
    )
    
    # Get it
    response = await client.get(f"/api/research/{sample_project['id']}/doc")
    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == sample_project["id"]
    assert data["content"] == "Test content"


@pytest.mark.asyncio
async def test_list_research_sources_empty(client: AsyncClient, sample_project):
    """Test listing research sources when empty."""
    response = await client.get(f"/api/research/{sample_project['id']}/sources")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_research_source(client: AsyncClient, sample_project):
    """Test creating a research source."""
    source_data = {
        "id": "source-1",
        "project_id": sample_project["id"],
        "url": "https://example.com",
        "title": "Example Source",
        "tags": ["tag1", "tag2"]
    }
    response = await client.post(
        f"/api/research/{sample_project['id']}/sources",
        json=source_data
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "source-1"
    assert data["url"] == "https://example.com"
    assert data["title"] == "Example Source"
    assert data["tags"] == ["tag1", "tag2"]
    assert "added_at" in data


@pytest.mark.asyncio
async def test_list_research_sources(client: AsyncClient, sample_project):
    """Test listing research sources."""
    # Create sources
    await client.post(
        f"/api/research/{sample_project['id']}/sources",
        json={
            "id": "source-1",
            "project_id": sample_project["id"],
            "url": "https://example.com",
            "title": "Source 1"
        }
    )
    
    response = await client.get(f"/api/research/{sample_project['id']}/sources")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "source-1"


@pytest.mark.asyncio
async def test_list_research_requests_empty(client: AsyncClient, sample_project):
    """Test listing research requests when empty."""
    response = await client.get(f"/api/research/{sample_project['id']}/requests")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_research_request(client: AsyncClient, sample_project):
    """Test creating a research request."""
    request_data = {
        "id": "req-1",
        "project_id": sample_project["id"],
        "prompt": "Research this topic",
        "status": "pending",
        "priority": "high"
    }
    response = await client.post(
        f"/api/research/{sample_project['id']}/requests",
        json=request_data
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "req-1"
    assert data["prompt"] == "Research this topic"
    assert data["status"] == "pending"
    assert data["priority"] == "high"
    assert "created_at" in data


@pytest.mark.asyncio
async def test_list_research_requests(client: AsyncClient, sample_project):
    """Test listing research requests."""
    # Create request
    await client.post(
        f"/api/research/{sample_project['id']}/requests",
        json={
            "id": "req-1",
            "project_id": sample_project["id"],
            "prompt": "Test request"
        }
    )
    
    response = await client.get(f"/api/research/{sample_project['id']}/requests")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "req-1"


@pytest.mark.asyncio
async def test_get_research_request(client: AsyncClient, sample_project):
    """Test getting a specific research request."""
    # Create request
    await client.post(
        f"/api/research/{sample_project['id']}/requests",
        json={
            "id": "req-1",
            "project_id": sample_project["id"],
            "prompt": "Test request"
        }
    )
    
    response = await client.get(f"/api/research/{sample_project['id']}/requests/req-1")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "req-1"
    assert data["prompt"] == "Test request"


@pytest.mark.asyncio
async def test_get_research_request_not_found(client: AsyncClient, sample_project):
    """Test getting non-existent research request returns 404."""
    response = await client.get(f"/api/research/{sample_project['id']}/requests/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_research_request(client: AsyncClient, sample_project):
    """Test updating a research request."""
    # Create request
    await client.post(
        f"/api/research/{sample_project['id']}/requests",
        json={
            "id": "req-1",
            "project_id": sample_project["id"],
            "prompt": "Initial prompt",
            "status": "pending"
        }
    )
    
    # Update it
    response = await client.put(
        f"/api/research/{sample_project['id']}/requests/req-1",
        json={
            "status": "completed",
            "response": "Research completed"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["response"] == "Research completed"


@pytest.mark.asyncio
async def test_update_research_request_not_found(client: AsyncClient, sample_project):
    """Test updating non-existent research request returns 404."""
    response = await client.put(
        f"/api/research/{sample_project['id']}/requests/nonexistent",
        json={"status": "completed"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_research_request(client: AsyncClient, sample_project):
    """Test deleting a research request."""
    # Create request
    await client.post(
        f"/api/research/{sample_project['id']}/requests",
        json={
            "id": "req-1",
            "project_id": sample_project["id"],
            "prompt": "Test request"
        }
    )
    
    # Delete it
    response = await client.delete(f"/api/research/{sample_project['id']}/requests/req-1")
    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    
    # Verify it's deleted
    get_response = await client.get(f"/api/research/{sample_project['id']}/requests/req-1")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_delete_research_request_not_found(client: AsyncClient, sample_project):
    """Test deleting non-existent research request returns 404."""
    response = await client.delete(f"/api/research/{sample_project['id']}/requests/nonexistent")
    assert response.status_code == 404
