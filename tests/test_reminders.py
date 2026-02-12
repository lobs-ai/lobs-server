"""Tests for reminders API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_reminder(client: AsyncClient):
    """Test creating a reminder."""
    reminder_data = {
        "id": "reminder-1",
        "title": "Test Reminder",
        "due_at": "2026-12-31T23:59:59Z"
    }
    response = await client.post("/api/reminders", json=reminder_data)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "reminder-1"
    assert data["title"] == "Test Reminder"
    assert "2026-12-31" in data["due_at"]


@pytest.mark.asyncio
async def test_list_reminders(client: AsyncClient, sample_reminder):
    """Test listing reminders."""
    response = await client.get("/api/reminders")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == sample_reminder["id"]


@pytest.mark.asyncio
async def test_list_reminders_empty(client: AsyncClient):
    """Test listing reminders when empty."""
    response = await client.get("/api/reminders")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_delete_reminder(client: AsyncClient, sample_reminder):
    """Test deleting a reminder."""
    response = await client.delete(f"/api/reminders/{sample_reminder['id']}")
    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    
    # Verify it's deleted
    list_response = await client.get("/api/reminders")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 0


@pytest.mark.asyncio
async def test_delete_reminder_not_found(client: AsyncClient):
    """Test deleting a non-existent reminder returns 404."""
    response = await client.delete("/api/reminders/nonexistent")
    assert response.status_code == 404
