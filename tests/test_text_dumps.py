"""Tests for text dumps API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_text_dump(client: AsyncClient):
    """Test creating a text dump."""
    dump_data = {
        "id": "dump-1",
        "text": "This is a text dump with some content",
        "status": "pending"
    }
    response = await client.post("/api/text-dumps", json=dump_data)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "dump-1"
    assert data["text"] == "This is a text dump with some content"
    assert data["status"] == "pending"
    assert "created_at" in data


@pytest.mark.asyncio
async def test_create_text_dump_with_project(client: AsyncClient, sample_project):
    """Test creating a text dump with project association."""
    dump_data = {
        "id": "dump-2",
        "project_id": sample_project["id"],
        "text": "Project-related text dump",
        "task_ids": ["task-1", "task-2"]
    }
    response = await client.post("/api/text-dumps", json=dump_data)
    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == sample_project["id"]
    assert data["task_ids"] == ["task-1", "task-2"]


@pytest.mark.asyncio
async def test_list_text_dumps(client: AsyncClient):
    """Test listing text dumps."""
    # Create dumps
    await client.post("/api/text-dumps", json={
        "id": "dump-1",
        "text": "First dump"
    })
    await client.post("/api/text-dumps", json={
        "id": "dump-2",
        "text": "Second dump"
    })
    
    response = await client.get("/api/text-dumps")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2


@pytest.mark.asyncio
async def test_list_text_dumps_empty(client: AsyncClient):
    """Test listing text dumps when empty."""
    response = await client.get("/api/text-dumps")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_get_text_dump(client: AsyncClient):
    """Test getting a specific text dump."""
    # Create dump
    await client.post("/api/text-dumps", json={
        "id": "dump-1",
        "text": "Test dump"
    })
    
    response = await client.get("/api/text-dumps/dump-1")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "dump-1"
    assert data["text"] == "Test dump"


@pytest.mark.asyncio
async def test_get_text_dump_not_found(client: AsyncClient):
    """Test getting non-existent text dump returns 404."""
    response = await client.get("/api/text-dumps/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_text_dump(client: AsyncClient):
    """Test updating a text dump."""
    # Create dump
    await client.post("/api/text-dumps", json={
        "id": "dump-1",
        "text": "Original text",
        "status": "pending"
    })
    
    # Update it
    response = await client.put("/api/text-dumps/dump-1", json={
        "text": "Updated text",
        "status": "processed"
    })
    assert response.status_code == 200
    data = response.json()
    assert data["text"] == "Updated text"
    assert data["status"] == "processed"


@pytest.mark.asyncio
async def test_update_text_dump_not_found(client: AsyncClient):
    """Test updating non-existent text dump returns 404."""
    response = await client.put("/api/text-dumps/nonexistent", json={
        "text": "Updated"
    })
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_text_dump(client: AsyncClient):
    """Test deleting a text dump."""
    # Create dump
    await client.post("/api/text-dumps", json={
        "id": "dump-1",
        "text": "Dump to delete"
    })
    
    # Delete it
    response = await client.delete("/api/text-dumps/dump-1")
    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    
    # Verify it's deleted
    get_response = await client.get("/api/text-dumps/dump-1")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_delete_text_dump_not_found(client: AsyncClient):
    """Test deleting non-existent text dump returns 404."""
    response = await client.delete("/api/text-dumps/nonexistent")
    assert response.status_code == 404
