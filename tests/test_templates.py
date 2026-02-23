"""Tests for templates API endpoints."""

import pytest
from httpx import AsyncClient
from tests.helpers import (
    create_template_data,
    assert_created,
    assert_list_response,
    assert_response_success,
    assert_not_found,
    assert_updated,
    assert_deleted,
)


@pytest.mark.asyncio
async def test_create_template(client: AsyncClient):
    """Test creating a template."""
    template_data = create_template_data(
        id="template-1",
        name="Test Template",
        description="A test template",
        items=[
            {"title": "Task 1", "status": "inbox"},
            {"title": "Task 2", "status": "inbox"}
        ]
    )
    response = await client.post("/api/templates", json=template_data)
    data = assert_created(response, expected_fields=["name", "items"])
    assert data["id"] == "template-1"
    assert data["name"] == "Test Template"
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_list_templates(client: AsyncClient, sample_template):
    """Test listing templates."""
    response = await client.get("/api/templates")
    templates = assert_list_response(
        response,
        min_length=1,
        item_schema=["id", "name"]
    )
    assert templates[0]["id"] == sample_template["id"]


@pytest.mark.asyncio
async def test_list_templates_empty(client: AsyncClient):
    """Test listing templates when empty."""
    response = await client.get("/api/templates")
    templates = assert_list_response(response, min_length=0)
    assert templates == []


@pytest.mark.asyncio
async def test_get_template(client: AsyncClient, sample_template):
    """Test getting a single template."""
    response = await client.get(f"/api/templates/{sample_template['id']}")
    assert_response_success(response)
    data = response.json()
    assert data["id"] == sample_template["id"]
    assert data["name"] == sample_template["name"]


@pytest.mark.asyncio
async def test_get_template_not_found(client: AsyncClient):
    """Test getting a non-existent template returns 404."""
    response = await client.get("/api/templates/nonexistent")
    assert_not_found(response, resource_type="template")


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
    data = assert_updated(response, expected_changes={
        "name": "Updated Template",
        "description": "Updated description"
    })
    assert len(data["items"]) == 1


@pytest.mark.asyncio
async def test_update_template_not_found(client: AsyncClient):
    """Test updating a non-existent template returns 404."""
    response = await client.put(
        "/api/templates/nonexistent",
        json={"name": "Updated"}
    )
    assert_not_found(response)


@pytest.mark.asyncio
async def test_delete_template(client: AsyncClient, sample_template):
    """Test deleting a template."""
    response = await client.delete(f"/api/templates/{sample_template['id']}")
    assert_deleted(response)
    
    # Verify it's deleted
    get_response = await client.get(f"/api/templates/{sample_template['id']}")
    assert_not_found(get_response)


@pytest.mark.asyncio
async def test_delete_template_not_found(client: AsyncClient):
    """Test deleting a non-existent template returns 404."""
    response = await client.delete("/api/templates/nonexistent")
    assert_not_found(response)
