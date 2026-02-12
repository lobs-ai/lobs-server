"""Tests for inbox API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_inbox_item(client: AsyncClient):
    """Test creating an inbox item."""
    item_data = {
        "id": "inbox-1",
        "title": "Inbox Item",
        "filename": "test.txt",
        "content": "Test content",
        "is_read": False
    }
    response = await client.post("/api/inbox", json=item_data)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "inbox-1"
    assert data["title"] == "Inbox Item"
    assert data["filename"] == "test.txt"
    assert data["content"] == "Test content"
    assert data["is_read"] is False


@pytest.mark.asyncio
async def test_list_inbox_items(client: AsyncClient, sample_inbox_item):
    """Test listing inbox items."""
    response = await client.get("/api/inbox")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == sample_inbox_item["id"]


@pytest.mark.asyncio
async def test_list_inbox_items_pagination(client: AsyncClient):
    """Test inbox item pagination."""
    # Create multiple items
    for i in range(5):
        await client.post("/api/inbox", json={
            "id": f"inbox-{i}",
            "title": f"Item {i}",
            "is_read": False
        })
    
    # Test limit
    response = await client.get("/api/inbox?limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2
    
    # Test offset
    response = await client.get("/api/inbox?offset=2&limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_get_inbox_item(client: AsyncClient, sample_inbox_item):
    """Test getting a single inbox item."""
    response = await client.get(f"/api/inbox/{sample_inbox_item['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == sample_inbox_item["id"]
    assert data["title"] == sample_inbox_item["title"]


@pytest.mark.asyncio
async def test_get_inbox_item_not_found(client: AsyncClient):
    """Test getting a non-existent inbox item returns 404."""
    response = await client.get("/api/inbox/nonexistent")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_inbox_item(client: AsyncClient, sample_inbox_item):
    """Test updating an inbox item."""
    update_data = {
        "title": "Updated Title",
        "is_read": True,
        "summary": "This is a summary"
    }
    response = await client.put(
        f"/api/inbox/{sample_inbox_item['id']}",
        json=update_data
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Title"
    assert data["is_read"] is True
    assert data["summary"] == "This is a summary"


@pytest.mark.asyncio
async def test_update_inbox_item_not_found(client: AsyncClient):
    """Test updating a non-existent inbox item returns 404."""
    response = await client.put(
        "/api/inbox/nonexistent",
        json={"title": "Updated"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_inbox_item(client: AsyncClient, sample_inbox_item):
    """Test deleting an inbox item."""
    response = await client.delete(f"/api/inbox/{sample_inbox_item['id']}")
    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    
    # Verify it's deleted
    get_response = await client.get(f"/api/inbox/{sample_inbox_item['id']}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_delete_inbox_item_not_found(client: AsyncClient):
    """Test deleting a non-existent inbox item returns 404."""
    response = await client.delete("/api/inbox/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_inbox_thread_empty(client: AsyncClient, sample_inbox_item):
    """Test getting inbox thread when no thread exists."""
    response = await client.get(f"/api/inbox/{sample_inbox_item['id']}/thread")
    assert response.status_code == 200
    data = response.json()
    assert data["thread"] is None
    assert data["messages"] == []


@pytest.mark.asyncio
async def test_create_inbox_message(client: AsyncClient, sample_inbox_item):
    """Test creating an inbox message (auto-creates thread)."""
    message_data = {
        "id": "msg-1",
        "thread_id": "thread-1",
        "author": "rafe",
        "text": "This is a message"
    }
    response = await client.post(
        f"/api/inbox/{sample_inbox_item['id']}/thread/messages",
        json=message_data
    )
    assert response.status_code == 200
    data = response.json()
    assert data["author"] == "rafe"
    assert data["text"] == "This is a message"
    assert "created_at" in data


@pytest.mark.asyncio
async def test_get_inbox_thread_with_messages(client: AsyncClient, sample_inbox_item):
    """Test getting inbox thread with messages."""
    # Create first message (auto-creates thread)
    await client.post(
        f"/api/inbox/{sample_inbox_item['id']}/thread/messages",
        json={
            "id": "msg-1",
            "thread_id": "thread-1",
            "author": "rafe",
            "text": "First message"
        }
    )
    
    # Create second message
    await client.post(
        f"/api/inbox/{sample_inbox_item['id']}/thread/messages",
        json={
            "id": "msg-2",
            "thread_id": "thread-1",
            "author": "lobs",
            "text": "Second message"
        }
    )
    
    # Get thread
    response = await client.get(f"/api/inbox/{sample_inbox_item['id']}/thread")
    assert response.status_code == 200
    data = response.json()
    assert data["thread"] is not None
    assert data["thread"]["doc_id"] == sample_inbox_item["id"]
    assert data["thread"]["triage_status"] == "needs_response"
    assert len(data["messages"]) == 2


@pytest.mark.asyncio
async def test_update_inbox_triage(client: AsyncClient, sample_inbox_item):
    """Test updating inbox triage status."""
    # First create a thread
    await client.post(
        f"/api/inbox/{sample_inbox_item['id']}/thread/messages",
        json={
            "id": "msg-1",
            "thread_id": "thread-1",
            "author": "rafe",
            "text": "Message"
        }
    )
    
    # Update triage status
    response = await client.patch(
        f"/api/inbox/{sample_inbox_item['id']}/triage",
        json={"triage_status": "resolved"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["triage_status"] == "resolved"


@pytest.mark.asyncio
async def test_update_inbox_triage_not_found(client: AsyncClient):
    """Test updating triage status when no thread exists returns 404."""
    response = await client.patch(
        "/api/inbox/nonexistent/triage",
        json={"triage_status": "resolved"}
    )
    assert response.status_code == 404
