"""Tests for calendar/scheduling endpoints."""

import pytest
import uuid
from datetime import datetime, timedelta, timezone
from httpx import AsyncClient
from sqlalchemy import select

from app.models import ScheduledEvent, Task
from app.orchestrator.scheduler import compute_next_fire_time


class TestCalendarCRUD:
    """Test basic CRUD operations for calendar events."""
    
    @pytest.mark.asyncio
    async def test_create_reminder_event(self, client: AsyncClient, auth_headers: dict, db_session):
        """Test creating a simple reminder event."""
        event_id = str(uuid.uuid4())
        event_data = {
            "id": event_id,
            "title": "Test Reminder",
            "description": "This is a test reminder",
            "event_type": "reminder",
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "target_type": "self"
        }
        
        response = await client.post("/api/calendar/events", json=event_data, headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        assert data["id"] == event_id
        assert data["title"] == "Test Reminder"
        assert data["event_type"] == "reminder"
        assert data["target_type"] == "self"
        assert data["status"] == "pending"
        assert data["fire_count"] == 0
    
    @pytest.mark.asyncio
    async def test_create_task_event(self, client: AsyncClient, auth_headers: dict, project_id: str):
        """Test creating a task event."""
        event_id = str(uuid.uuid4())
        event_data = {
            "id": event_id,
            "title": "Scheduled Task",
            "event_type": "task",
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "target_type": "agent",
            "target_agent": "programmer",
            "task_project_id": project_id,
            "task_notes": "Task notes here",
            "task_priority": "high"
        }
        
        response = await client.post("/api/calendar/events", json=event_data, headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        assert data["event_type"] == "task"
        assert data["target_agent"] == "programmer"
        assert data["task_project_id"] == project_id
    
    @pytest.mark.asyncio
    async def test_create_recurring_event(self, client: AsyncClient, auth_headers: dict):
        """Test creating a recurring event."""
        event_id = str(uuid.uuid4())
        event_data = {
            "id": event_id,
            "title": "Daily Standup",
            "event_type": "meeting",
            "scheduled_at": datetime.now(timezone.utc).replace(hour=9, minute=0, second=0).isoformat(),
            "recurrence_rule": "0 9 * * *",  # Every day at 9am
            "target_type": "self"
        }
        
        response = await client.post("/api/calendar/events", json=event_data, headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        assert data["status"] == "recurring"
        assert data["next_fire_at"] is not None
        assert data["recurrence_rule"] == "0 9 * * *"
    
    @pytest.mark.asyncio
    async def test_list_events(self, client: AsyncClient, auth_headers: dict):
        """Test listing events."""
        # Create a couple of events
        for i in range(2):
            event_data = {
                "id": str(uuid.uuid4()),
                "title": f"Event {i}",
                "event_type": "reminder",
                "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=i+1)).isoformat(),
                "target_type": "self"
            }
            await client.post("/api/calendar/events", json=event_data, headers=auth_headers)
        
        response = await client.get("/api/calendar/events", headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        assert "events" in data
        assert "total" in data
        assert len(data["events"]) >= 2
    
    @pytest.mark.asyncio
    async def test_get_event(self, client: AsyncClient, auth_headers: dict):
        """Test getting a single event."""
        event_id = str(uuid.uuid4())
        event_data = {
            "id": event_id,
            "title": "Specific Event",
            "event_type": "reminder",
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
            "target_type": "self"
        }
        
        await client.post("/api/calendar/events", json=event_data, headers=auth_headers)
        
        response = await client.get(f"/api/calendar/events/{event_id}", headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        assert data["id"] == event_id
        assert data["title"] == "Specific Event"
    
    @pytest.mark.asyncio
    async def test_update_event(self, client: AsyncClient, auth_headers: dict):
        """Test updating an event."""
        event_id = str(uuid.uuid4())
        event_data = {
            "id": event_id,
            "title": "Original Title",
            "event_type": "reminder",
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
            "target_type": "self"
        }
        
        await client.post("/api/calendar/events", json=event_data, headers=auth_headers)
        
        update_data = {
            "title": "Updated Title",
            "description": "New description"
        }
        
        response = await client.put(f"/api/calendar/events/{event_id}", json=update_data, headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        assert data["title"] == "Updated Title"
        assert data["description"] == "New description"
    
    @pytest.mark.asyncio
    async def test_cancel_event(self, client: AsyncClient, auth_headers: dict):
        """Test cancelling an event."""
        event_id = str(uuid.uuid4())
        event_data = {
            "id": event_id,
            "title": "To Be Cancelled",
            "event_type": "reminder",
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
            "target_type": "self"
        }
        
        await client.post("/api/calendar/events", json=event_data, headers=auth_headers)
        
        response = await client.post(f"/api/calendar/events/{event_id}/cancel", headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        assert data["status"] == "cancelled"
    
    @pytest.mark.asyncio
    async def test_delete_event(self, client: AsyncClient, auth_headers: dict):
        """Test deleting an event."""
        event_id = str(uuid.uuid4())
        event_data = {
            "id": event_id,
            "title": "To Be Deleted",
            "event_type": "reminder",
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(),
            "target_type": "self"
        }
        
        await client.post("/api/calendar/events", json=event_data, headers=auth_headers)
        
        response = await client.delete(f"/api/calendar/events/{event_id}", headers=auth_headers)
        assert response.status_code == 200
        
        # Verify it's gone
        response = await client.get(f"/api/calendar/events/{event_id}", headers=auth_headers)
        assert response.status_code == 404


class TestCalendarQueries:
    """Test calendar query endpoints."""
    
    @pytest.mark.asyncio
    async def test_upcoming_events(self, client: AsyncClient, auth_headers: dict):
        """Test getting upcoming events."""
        # Create events at different times
        now = datetime.now(timezone.utc)
        for i in range(3):
            event_data = {
                "id": str(uuid.uuid4()),
                "title": f"Upcoming Event {i}",
                "event_type": "reminder",
                "scheduled_at": (now + timedelta(hours=i+1)).isoformat(),
                "target_type": "self"
            }
            await client.post("/api/calendar/events", json=event_data, headers=auth_headers)
        
        response = await client.get("/api/calendar/upcoming?limit=5", headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        assert len(data) >= 3
        
        # Verify they're sorted by time
        times = [datetime.fromisoformat(e["scheduled_at"].replace("Z", "+00:00")) for e in data]
        assert times == sorted(times)
    
    @pytest.mark.asyncio
    async def test_today_events(self, client: AsyncClient, auth_headers: dict):
        """Test getting today's events."""
        now = datetime.now(timezone.utc)
        
        # Create an event for today
        today_event = {
            "id": str(uuid.uuid4()),
            "title": "Today Event",
            "event_type": "reminder",
            "scheduled_at": now.replace(hour=14, minute=0).isoformat(),
            "target_type": "self"
        }
        await client.post("/api/calendar/events", json=today_event, headers=auth_headers)
        
        # Create an event for tomorrow
        tomorrow_event = {
            "id": str(uuid.uuid4()),
            "title": "Tomorrow Event",
            "event_type": "reminder",
            "scheduled_at": (now + timedelta(days=1)).isoformat(),
            "target_type": "self"
        }
        await client.post("/api/calendar/events", json=tomorrow_event, headers=auth_headers)
        
        response = await client.get("/api/calendar/today", headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        today_titles = [e["title"] for e in data]
        assert "Today Event" in today_titles
        assert "Tomorrow Event" not in today_titles
    
    @pytest.mark.asyncio
    async def test_calendar_range(self, client: AsyncClient, auth_headers: dict):
        """Test getting events in a date range."""
        now = datetime.now(timezone.utc)
        start_date = now.date()
        end_date = (now + timedelta(days=7)).date()
        
        # Create events across the range
        for i in range(5):
            event_data = {
                "id": str(uuid.uuid4()),
                "title": f"Range Event {i}",
                "event_type": "meeting",
                "scheduled_at": (now + timedelta(days=i)).isoformat(),
                "target_type": "self"
            }
            await client.post("/api/calendar/events", json=event_data, headers=auth_headers)
        
        response = await client.get(
            f"/api/calendar/range?start_date={start_date}&end_date={end_date}",
            headers=auth_headers
        )
        assert response.status_code == 200
        
        data = response.json()
        assert "days" in data
        assert "start_date" in data
        assert "end_date" in data
        assert len(data["days"]) >= 5


class TestEventFiring:
    """Test event firing logic."""
    
    @pytest.mark.asyncio
    async def test_fire_reminder_event(self, client: AsyncClient, auth_headers: dict, db_session):
        """Test firing a reminder event."""
        event_id = str(uuid.uuid4())
        event_data = {
            "id": event_id,
            "title": "Fire Test Reminder",
            "event_type": "reminder",
            "scheduled_at": datetime.now(timezone.utc).isoformat(),
            "target_type": "self"
        }
        
        await client.post("/api/calendar/events", json=event_data, headers=auth_headers)
        
        response = await client.post(f"/api/calendar/events/{event_id}/fire", headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        assert data["status"] == "fired"
        assert data["last_fired_at"] is not None
        assert data["fire_count"] == 1
    
    @pytest.mark.asyncio
    async def test_fire_task_event(self, client: AsyncClient, auth_headers: dict, project_id: str, db_session):
        """Test firing a task event creates a task."""
        event_id = str(uuid.uuid4())
        event_data = {
            "id": event_id,
            "title": "Create Task Test",
            "event_type": "task",
            "scheduled_at": datetime.now(timezone.utc).isoformat(),
            "target_type": "agent",
            "target_agent": "programmer",
            "task_project_id": project_id,
            "task_notes": "Task from event"
        }
        
        await client.post("/api/calendar/events", json=event_data, headers=auth_headers)
        
        # Count tasks before
        result = await db_session.execute(
            select(Task).where(Task.project_id == project_id)
        )
        tasks_before = len(result.scalars().all())
        
        # Fire event
        await client.post(f"/api/calendar/events/{event_id}/fire", headers=auth_headers)
        
        # Check that a task was created
        result = await db_session.execute(
            select(Task).where(Task.project_id == project_id)
        )
        tasks_after = result.scalars().all()
        
        assert len(tasks_after) == tasks_before + 1
        
        # Find the new task
        new_task = [t for t in tasks_after if t.title == "Create Task Test"][0]
        assert new_task.notes == "Task from event"
        assert new_task.agent == "programmer"
    
    @pytest.mark.asyncio
    async def test_recurring_event_computation(self, client: AsyncClient, auth_headers: dict):
        """Test that recurring events compute next_fire_at correctly."""
        event_id = str(uuid.uuid4())
        base_time = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
        
        event_data = {
            "id": event_id,
            "title": "Recurring Test",
            "event_type": "reminder",
            "scheduled_at": base_time.isoformat(),
            "recurrence_rule": "0 9 * * *",  # Daily at 9am
            "target_type": "self"
        }
        
        response = await client.post("/api/calendar/events", json=event_data, headers=auth_headers)
        data = response.json()
        
        assert data["status"] == "recurring"
        assert data["next_fire_at"] is not None
        
        # Fire it
        await client.post(f"/api/calendar/events/{event_id}/fire", headers=auth_headers)
        
        # Check that next_fire_at was updated
        response = await client.get(f"/api/calendar/events/{event_id}", headers=auth_headers)
        data = response.json()
        
        assert data["fire_count"] == 1
        assert data["status"] == "recurring"
        assert data["next_fire_at"] is not None


class TestSchedulerHelpers:
    """Test scheduler helper functions."""
    
    def test_compute_next_fire_time(self):
        """Test cron parsing for next fire time."""
        # Daily at 9am
        base_time = datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
        next_time = compute_next_fire_time("0 9 * * *", base_time)
        
        assert next_time.hour == 9
        assert next_time.minute == 0
        assert next_time.date() == base_time.date()
        
        # If already past 9am, should be tomorrow
        base_time = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        next_time = compute_next_fire_time("0 9 * * *", base_time)
        
        assert next_time.hour == 9
        assert next_time.date() == (base_time + timedelta(days=1)).date()
