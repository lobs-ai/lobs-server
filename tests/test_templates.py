"""Tests for templates API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_template(client: AsyncClient):
    """Test creating a template."""
    template_data = {
        "id": "template-1",
        "name": "Test Template",
        "description": "A test template",
        "items": [
            {"title": "Task 1", "status": "inbox"},
            {"title": "Task 2", "status": "inbox"}
        ]
    }
    response = await client.post("/api/templates", json=template_data)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "template-1"
    assert data["name"] == "Test Template"
    assert data["description"] == "A test template"
    assert len(data["items"]) == 2
    assert "created_at" in data


@pytest.mark.asyncio
async def test_list_templates(client: AsyncClient, sample_template):
    """Test listing templates."""
    response = await client.get("/api/templates")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == sample_template["id"]


@pytest.mark.asyncio
async def test_list_templates_empty(client: AsyncClient):
    """Test listing templates when empty."""
    response = await client.get("/api/templates")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_get_template(client: AsyncClient, sample_template):
    """Test getting a single template."""
    response = await client.get(f"/api/templates/{sample_template['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == sample_template["id"]
    assert data["name"] == sample_template["name"]


@pytest.mark.asyncio
async def test_get_template_not_found(client: AsyncClient):
    """Test getting a non-existent template returns 404."""
    response = await client.get("/api/templates/nonexistent")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_template(client: AsyncClient, sample_template):
    """Test updating a template."""
    update_data = {
        "name": "Updated Template",
        "description": "Updated description",
        "items": [{"title": "New Task"}]
    }
    response = await client.put(
        f"/api/templates/{sample_template['id']}",
        json=update_data
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Template"
    assert data["description"] == "Updated description"
    assert len(data["items"]) == 1


@pytest.mark.asyncio
async def test_update_template_not_found(client: AsyncClient):
    """Test updating a non-existent template returns 404."""
    response = await client.put(
        "/api/templates/nonexistent",
        json={"name": "Updated"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_template(client: AsyncClient, sample_template):
    """Test deleting a template."""
    response = await client.delete(f"/api/templates/{sample_template['id']}")
    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    
    # Verify it's deleted
    get_response = await client.get(f"/api/templates/{sample_template['id']}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_delete_template_not_found(client: AsyncClient):
    """Test deleting a non-existent template returns 404."""
    response = await client.delete("/api/templates/nonexistent")
    assert response.status_code == 404
