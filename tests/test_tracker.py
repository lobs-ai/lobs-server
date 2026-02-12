"""Tests for tracker API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_tracker_items_empty(client: AsyncClient, sample_project):
    """Test listing tracker items when empty."""
    response = await client.get(f"/api/tracker/{sample_project['id']}/items")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_tracker_item(client: AsyncClient, sample_project):
    """Test creating a tracker item."""
    item_data = {
        "id": "item-1",
        "project_id": sample_project["id"],
        "title": "Tracker Item",
        "status": "open",
        "difficulty": "medium",
        "tags": ["bug", "urgent"],
        "notes": "Item notes",
        "links": [{"url": "https://example.com", "title": "Link"}]
    }
    response = await client.post(
        f"/api/tracker/{sample_project['id']}/items",
        json=item_data
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "item-1"
    assert data["title"] == "Tracker Item"
    assert data["status"] == "open"
    assert data["difficulty"] == "medium"
    assert data["tags"] == ["bug", "urgent"]
    assert "created_at" in data


@pytest.mark.asyncio
async def test_list_tracker_items(client: AsyncClient, sample_project):
    """Test listing tracker items."""
    # Create item
    await client.post(
        f"/api/tracker/{sample_project['id']}/items",
        json={
            "id": "item-1",
            "project_id": sample_project["id"],
            "title": "Item 1"
        }
    )
    
    response = await client.get(f"/api/tracker/{sample_project['id']}/items")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "item-1"


@pytest.mark.asyncio
async def test_get_tracker_item(client: AsyncClient, sample_project):
    """Test getting a specific tracker item."""
    # Create item
    await client.post(
        f"/api/tracker/{sample_project['id']}/items",
        json={
            "id": "item-1",
            "project_id": sample_project["id"],
            "title": "Test Item"
        }
    )
    
    response = await client.get(f"/api/tracker/{sample_project['id']}/items/item-1")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "item-1"
    assert data["title"] == "Test Item"


@pytest.mark.asyncio
async def test_get_tracker_item_not_found(client: AsyncClient, sample_project):
    """Test getting non-existent tracker item returns 404."""
    response = await client.get(f"/api/tracker/{sample_project['id']}/items/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_tracker_item(client: AsyncClient, sample_project):
    """Test updating a tracker item."""
    # Create item
    await client.post(
        f"/api/tracker/{sample_project['id']}/items",
        json={
            "id": "item-1",
            "project_id": sample_project["id"],
            "title": "Original Title",
            "status": "open"
        }
    )
    
    # Update it
    response = await client.put(
        f"/api/tracker/{sample_project['id']}/items/item-1",
        json={
            "title": "Updated Title",
            "status": "closed"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Title"
    assert data["status"] == "closed"


@pytest.mark.asyncio
async def test_update_tracker_item_not_found(client: AsyncClient, sample_project):
    """Test updating non-existent tracker item returns 404."""
    response = await client.put(
        f"/api/tracker/{sample_project['id']}/items/nonexistent",
        json={"title": "Updated"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_tracker_item(client: AsyncClient, sample_project):
    """Test deleting a tracker item."""
    # Create item
    await client.post(
        f"/api/tracker/{sample_project['id']}/items",
        json={
            "id": "item-1",
            "project_id": sample_project["id"],
            "title": "Item to Delete"
        }
    )
    
    # Delete it
    response = await client.delete(f"/api/tracker/{sample_project['id']}/items/item-1")
    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    
    # Verify it's deleted
    get_response = await client.get(f"/api/tracker/{sample_project['id']}/items/item-1")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_delete_tracker_item_not_found(client: AsyncClient, sample_project):
    """Test deleting non-existent tracker item returns 404."""
    response = await client.delete(f"/api/tracker/{sample_project['id']}/items/nonexistent")
    assert response.status_code == 404
