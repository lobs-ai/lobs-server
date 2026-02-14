"""Tests for tracker API endpoints."""

import pytest
from datetime import timezone
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


# Personal Work Tracker tests
@pytest.mark.asyncio
async def test_create_tracker_entry(client: AsyncClient):
    """Test creating a tracker entry."""
    entry_data = {
        "id": "entry-1",
        "type": "work_session",
        "raw_text": "Worked on feature X for 2 hours",
        "duration": 120,
        "category": "development"
    }
    response = await client.post("/api/tracker/entries", json=entry_data)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "entry-1"
    assert data["type"] == "work_session"
    assert data["raw_text"] == "Worked on feature X for 2 hours"
    assert data["duration"] == 120
    assert data["category"] == "development"
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_create_deadline_entry(client: AsyncClient):
    """Test creating a deadline tracker entry."""
    from datetime import datetime, timedelta
    
    due_date = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    entry_data = {
        "id": "deadline-1",
        "type": "deadline",
        "raw_text": "Project proposal due next week",
        "due_date": due_date,
        "estimated_minutes": 180,
        "category": "proposal"
    }
    response = await client.post("/api/tracker/entries", json=entry_data)
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "deadline"
    assert data["due_date"] is not None
    assert data["estimated_minutes"] == 180


@pytest.mark.asyncio
async def test_create_note_entry(client: AsyncClient):
    """Test creating a note tracker entry."""
    entry_data = {
        "id": "note-1",
        "type": "note",
        "raw_text": "Remember to review PRs tomorrow",
        "category": "reminder"
    }
    response = await client.post("/api/tracker/entries", json=entry_data)
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "note"
    assert data["raw_text"] == "Remember to review PRs tomorrow"


@pytest.mark.asyncio
async def test_list_tracker_entries(client: AsyncClient):
    """Test listing tracker entries."""
    # Create some entries
    await client.post("/api/tracker/entries", json={
        "id": "entry-1",
        "type": "work_session",
        "raw_text": "Work 1",
        "duration": 60
    })
    await client.post("/api/tracker/entries", json={
        "id": "entry-2",
        "type": "note",
        "raw_text": "Note 1"
    })
    
    response = await client.get("/api/tracker/entries")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 2


@pytest.mark.asyncio
async def test_list_tracker_entries_filtered_by_type(client: AsyncClient):
    """Test listing tracker entries filtered by type."""
    # Create entries of different types
    await client.post("/api/tracker/entries", json={
        "id": "work-1",
        "type": "work_session",
        "raw_text": "Work session",
        "duration": 60
    })
    await client.post("/api/tracker/entries", json={
        "id": "note-1",
        "type": "note",
        "raw_text": "Note"
    })
    
    # Filter by work_session
    response = await client.get("/api/tracker/entries?type=work_session")
    assert response.status_code == 200
    data = response.json()
    assert all(e["type"] == "work_session" for e in data)


@pytest.mark.asyncio
async def test_get_tracker_entry(client: AsyncClient):
    """Test getting a specific tracker entry."""
    # Create entry
    await client.post("/api/tracker/entries", json={
        "id": "entry-get",
        "type": "work_session",
        "raw_text": "Test work session",
        "duration": 90
    })
    
    response = await client.get("/api/tracker/entries/entry-get")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "entry-get"
    assert data["duration"] == 90


@pytest.mark.asyncio
async def test_get_tracker_entry_not_found(client: AsyncClient):
    """Test getting non-existent tracker entry returns 404."""
    response = await client.get("/api/tracker/entries/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_tracker_entry(client: AsyncClient):
    """Test updating a tracker entry."""
    # Create entry
    await client.post("/api/tracker/entries", json={
        "id": "entry-update",
        "type": "work_session",
        "raw_text": "Original text",
        "duration": 60
    })
    
    # Update it
    response = await client.put("/api/tracker/entries/entry-update", json={
        "raw_text": "Updated text",
        "duration": 90,
        "category": "backend"
    })
    assert response.status_code == 200
    data = response.json()
    assert data["raw_text"] == "Updated text"
    assert data["duration"] == 90
    assert data["category"] == "backend"


@pytest.mark.asyncio
async def test_delete_tracker_entry(client: AsyncClient):
    """Test deleting a tracker entry."""
    # Create entry
    await client.post("/api/tracker/entries", json={
        "id": "entry-delete",
        "type": "note",
        "raw_text": "Entry to delete"
    })
    
    # Delete it
    response = await client.delete("/api/tracker/entries/entry-delete")
    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    
    # Verify it's deleted
    get_response = await client.get("/api/tracker/entries/entry-delete")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_get_tracker_summary(client: AsyncClient):
    """Test getting tracker summary statistics."""
    from datetime import datetime, timedelta
    
    # Create various entries
    await client.post("/api/tracker/entries", json={
        "id": "work-1",
        "type": "work_session",
        "raw_text": "Work 1",
        "duration": 120,
        "category": "development"
    })
    await client.post("/api/tracker/entries", json={
        "id": "work-2",
        "type": "work_session",
        "raw_text": "Work 2",
        "duration": 90,
        "category": "development"
    })
    due_date = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    await client.post("/api/tracker/entries", json={
        "id": "deadline-1",
        "type": "deadline",
        "raw_text": "Upcoming deadline",
        "due_date": due_date
    })
    await client.post("/api/tracker/entries", json={
        "id": "note-1",
        "type": "note",
        "raw_text": "Important note"
    })
    
    response = await client.get("/api/tracker/summary")
    assert response.status_code == 200
    data = response.json()
    
    assert data["total_entries"] >= 4
    assert data["work_sessions_count"] >= 2
    assert data["total_minutes_logged"] >= 210  # 120 + 90
    assert data["deadlines_count"] >= 1
    assert data["upcoming_deadlines"] >= 1
    assert data["notes_count"] >= 1
    assert "development" in data["categories"]
    assert data["categories"]["development"] >= 2
    assert data["last_7_days_minutes"] >= 210


@pytest.mark.asyncio
async def test_get_deadlines_upcoming(client: AsyncClient):
    """Test getting upcoming deadlines."""
    from datetime import datetime, timedelta
    
    # Create future deadline
    future_date = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    await client.post("/api/tracker/entries", json={
        "id": "future-deadline",
        "type": "deadline",
        "raw_text": "Future deadline",
        "due_date": future_date,
        "estimated_minutes": 120,
        "category": "project"
    })
    
    # Create past deadline
    past_date = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    await client.post("/api/tracker/entries", json={
        "id": "past-deadline",
        "type": "deadline",
        "raw_text": "Past deadline",
        "due_date": past_date
    })
    
    # Get upcoming deadlines only
    response = await client.get("/api/tracker/deadlines?upcoming=true")
    assert response.status_code == 200
    data = response.json()
    
    # Should include future deadline but not past
    ids = [d["id"] for d in data]
    assert "future-deadline" in ids
    assert "past-deadline" not in ids


@pytest.mark.asyncio
async def test_get_deadlines_all(client: AsyncClient):
    """Test getting all deadlines including past ones."""
    from datetime import datetime, timedelta
    
    # Create future deadline
    future_date = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    await client.post("/api/tracker/entries", json={
        "id": "future-dl",
        "type": "deadline",
        "raw_text": "Future",
        "due_date": future_date
    })
    
    # Create past deadline
    past_date = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    await client.post("/api/tracker/entries", json={
        "id": "past-dl",
        "type": "deadline",
        "raw_text": "Past",
        "due_date": past_date
    })
    
    # Get all deadlines
    response = await client.get("/api/tracker/deadlines?upcoming=false")
    assert response.status_code == 200
    data = response.json()
    
    # Should include both
    ids = [d["id"] for d in data]
    assert "future-dl" in ids
    assert "past-dl" in ids
