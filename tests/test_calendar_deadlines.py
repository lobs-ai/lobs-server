"""Tests for tracker deadline integration in calendar endpoints."""

import pytest
import uuid
from datetime import datetime, timedelta, timezone
from httpx import AsyncClient
from sqlalchemy import select

from app.models import TrackerEntry


class TestCalendarDeadlineIntegration:
    """Test that tracker deadlines appear in calendar views."""
    
    @pytest.mark.asyncio
    async def test_upcoming_includes_deadlines(self, client: AsyncClient, auth_headers: dict, db_session):
        """Test /calendar/upcoming includes tracker deadlines."""
        now = datetime.now(timezone.utc)
        
        # Create a tracker deadline
        deadline = TrackerEntry(
            id=str(uuid.uuid4()),
            type="deadline",
            raw_text="Submit final report",
            due_date=now + timedelta(days=2),
            category="work",
            created_at=now,
            updated_at=now
        )
        db_session.add(deadline)
        await db_session.flush()
        
        # Create a scheduled event for comparison
        from app.models import ScheduledEvent
        event = ScheduledEvent(
            id=str(uuid.uuid4()),
            title="Team Meeting",
            event_type="meeting",
            scheduled_at=now + timedelta(days=1),
            target_type="self",
            status="pending",
            fire_count=0,
            created_at=now,
            updated_at=now
        )
        db_session.add(event)
        await db_session.flush()
        
        # Get upcoming events
        response = await client.get("/api/calendar/upcoming?limit=20", headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        assert len(data) >= 2
        
        # Find the deadline in the response
        deadline_item = next((item for item in data if item["event_type"] == "deadline"), None)
        assert deadline_item is not None
        assert deadline_item["title"] == "Submit final report"
        assert deadline_item["all_day"] is True
        assert deadline_item["target_type"] == "self"
        assert "deadline-" in deadline_item["id"]
        
        # Find the meeting
        meeting_item = next((item for item in data if item["event_type"] == "meeting"), None)
        assert meeting_item is not None
        assert meeting_item["title"] == "Team Meeting"
    
    @pytest.mark.asyncio
    async def test_today_includes_deadlines(self, client: AsyncClient, auth_headers: dict, db_session):
        """Test /calendar/today includes tracker deadlines due today."""
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_deadline_time = today_start + timedelta(hours=12)
        tomorrow_deadline_time = today_start + timedelta(days=1, hours=12)
        
        # Create a deadline for today
        today_deadline = TrackerEntry(
            id=str(uuid.uuid4()),
            type="deadline",
            raw_text="Today's deadline",
            due_date=today_deadline_time,
            created_at=now,
            updated_at=now
        )
        db_session.add(today_deadline)
        
        # Create a deadline for tomorrow (should not appear)
        tomorrow_deadline = TrackerEntry(
            id=str(uuid.uuid4()),
            type="deadline",
            raw_text="Tomorrow's deadline",
            due_date=tomorrow_deadline_time,
            created_at=now,
            updated_at=now
        )
        db_session.add(tomorrow_deadline)
        await db_session.flush()
        
        # Get today's events
        response = await client.get("/api/calendar/today", headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        
        # Should include today's deadline
        today_items = [item for item in data if "Today's deadline" in item["title"]]
        assert len(today_items) == 1
        
        # Should not include tomorrow's deadline
        tomorrow_items = [item for item in data if "Tomorrow's deadline" in item["title"]]
        assert len(tomorrow_items) == 0
    
    @pytest.mark.asyncio
    async def test_range_includes_deadlines(self, client: AsyncClient, auth_headers: dict, db_session):
        """Test /calendar/range includes tracker deadlines in the date range."""
        now = datetime.now(timezone.utc)
        start_date = now.date()
        end_date = (now + timedelta(days=7)).date()
        
        # Create deadlines at different dates in the range
        deadline1_date = now + timedelta(days=1)
        deadline2_date = now + timedelta(days=4)
        deadline_outside = now + timedelta(days=10)  # Outside range
        
        deadline1 = TrackerEntry(
            id=str(uuid.uuid4()),
            type="deadline",
            raw_text="Deadline in 1 day",
            due_date=deadline1_date.replace(hour=23, minute=59),
            created_at=now,
            updated_at=now
        )
        deadline2 = TrackerEntry(
            id=str(uuid.uuid4()),
            type="deadline",
            raw_text="Deadline in 4 days",
            due_date=deadline2_date.replace(hour=23, minute=59),
            created_at=now,
            updated_at=now
        )
        deadline3 = TrackerEntry(
            id=str(uuid.uuid4()),
            type="deadline",
            raw_text="Deadline outside range",
            due_date=deadline_outside.replace(hour=23, minute=59),
            created_at=now,
            updated_at=now
        )
        
        db_session.add_all([deadline1, deadline2, deadline3])
        await db_session.flush()
        
        # Get calendar range
        response = await client.get(
            f"/api/calendar/range?start_date={start_date.isoformat()}&end_date={end_date.isoformat()}",
            headers=auth_headers
        )
        assert response.status_code == 200
        
        data = response.json()
        assert data["start_date"] == start_date.isoformat()
        assert data["end_date"] == end_date.isoformat()
        
        # Collect all events from all days
        all_events = []
        for day in data["days"]:
            all_events.extend(day["events"])
        
        # Should include deadline1 and deadline2
        deadline_titles = [e["title"] for e in all_events if e["event_type"] == "deadline"]
        assert "Deadline in 1 day" in deadline_titles
        assert "Deadline in 4 days" in deadline_titles
        
        # Should not include deadline outside range
        assert "Deadline outside range" not in deadline_titles
    
    @pytest.mark.asyncio
    async def test_deadline_without_due_date_not_shown(self, client: AsyncClient, auth_headers: dict, db_session):
        """Test that deadlines without a due_date are not included in calendar."""
        now = datetime.now(timezone.utc)
        
        # Create a deadline without due_date
        deadline = TrackerEntry(
            id=str(uuid.uuid4()),
            type="deadline",
            raw_text="Someday deadline",
            due_date=None,  # No due date
            created_at=now,
            updated_at=now
        )
        db_session.add(deadline)
        await db_session.flush()
        
        # Get upcoming events
        response = await client.get("/api/calendar/upcoming", headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        
        # Should not include deadline without due_date
        deadline_items = [item for item in data if "Someday deadline" in item["title"]]
        assert len(deadline_items) == 0
    
    @pytest.mark.asyncio
    async def test_non_deadline_tracker_entries_not_shown(self, client: AsyncClient, auth_headers: dict, db_session):
        """Test that non-deadline tracker entries (work_session, note) are not shown in calendar."""
        now = datetime.now(timezone.utc)
        
        # Create a work_session entry
        work_session = TrackerEntry(
            id=str(uuid.uuid4()),
            type="work_session",
            raw_text="Worked on project X for 2 hours",
            duration=120,
            created_at=now,
            updated_at=now
        )
        
        # Create a note entry
        note = TrackerEntry(
            id=str(uuid.uuid4()),
            type="note",
            raw_text="Remember to check email",
            created_at=now,
            updated_at=now
        )
        
        db_session.add_all([work_session, note])
        await db_session.flush()
        
        # Get upcoming events
        response = await client.get("/api/calendar/upcoming", headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        
        # Should not include work_session or note
        titles = [item["title"] for item in data]
        assert "Worked on project X for 2 hours" not in titles
        assert "Remember to check email" not in titles
    
    @pytest.mark.asyncio
    async def test_deadline_conversion_fields(self, client: AsyncClient, auth_headers: dict, db_session):
        """Test that deadline is correctly converted to ScheduledEventResponse format."""
        now = datetime.now(timezone.utc)
        due = now + timedelta(days=3)
        
        # Create a deadline with all fields
        deadline = TrackerEntry(
            id=str(uuid.uuid4()),
            type="deadline",
            raw_text="Complete project proposal",
            due_date=due,
            category="research",
            estimated_minutes=240,
            created_at=now,
            updated_at=now
        )
        db_session.add(deadline)
        await db_session.flush()
        
        # Get upcoming events
        response = await client.get("/api/calendar/upcoming", headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        deadline_item = next((item for item in data if item["event_type"] == "deadline"), None)
        
        assert deadline_item is not None
        
        # Check all converted fields
        assert deadline_item["title"] == "Complete project proposal"
        assert deadline_item["description"] == "Category: research"
        assert deadline_item["event_type"] == "deadline"
        assert deadline_item["all_day"] is True
        assert deadline_item["target_type"] == "self"
        assert deadline_item["status"] == "pending"
        assert deadline_item["fire_count"] == 0
        assert deadline_item["recurrence_rule"] is None
        assert deadline_item["end_at"] is None
        assert "deadline-" in deadline_item["id"]
    
    @pytest.mark.asyncio
    async def test_deadline_without_category(self, client: AsyncClient, auth_headers: dict, db_session):
        """Test deadline without category has None description."""
        now = datetime.now(timezone.utc)
        
        deadline = TrackerEntry(
            id=str(uuid.uuid4()),
            type="deadline",
            raw_text="Generic deadline",
            due_date=now + timedelta(days=1),
            category=None,
            created_at=now,
            updated_at=now
        )
        db_session.add(deadline)
        await db_session.flush()
        
        response = await client.get("/api/calendar/upcoming", headers=auth_headers)
        data = response.json()
        
        deadline_item = next((item for item in data if "Generic deadline" in item["title"]), None)
        assert deadline_item is not None
        assert deadline_item["description"] is None
    
    @pytest.mark.asyncio
    async def test_deadlines_sorted_with_events(self, client: AsyncClient, auth_headers: dict, db_session):
        """Test that deadlines are correctly sorted with regular events by scheduled_at."""
        now = datetime.now(timezone.utc)
        
        # Create events and deadlines at different times
        from app.models import ScheduledEvent
        
        event1 = ScheduledEvent(
            id=str(uuid.uuid4()),
            title="Event at T+1",
            event_type="reminder",
            scheduled_at=now + timedelta(days=1),
            target_type="self",
            status="pending",
            fire_count=0,
            created_at=now,
            updated_at=now
        )
        
        deadline1 = TrackerEntry(
            id=str(uuid.uuid4()),
            type="deadline",
            raw_text="Deadline at T+2",
            due_date=now + timedelta(days=2),
            created_at=now,
            updated_at=now
        )
        
        event2 = ScheduledEvent(
            id=str(uuid.uuid4()),
            title="Event at T+3",
            event_type="reminder",
            scheduled_at=now + timedelta(days=3),
            target_type="self",
            status="pending",
            fire_count=0,
            created_at=now,
            updated_at=now
        )
        
        db_session.add_all([event1, deadline1, event2])
        await db_session.flush()
        
        # Get upcoming events
        response = await client.get("/api/calendar/upcoming?limit=10", headers=auth_headers)
        data = response.json()
        
        # Extract titles in order
        titles = [item["title"] for item in data]
        
        # Should be sorted by scheduled_at/due_date
        assert titles.index("Event at T+1") < titles.index("Deadline at T+2")
        assert titles.index("Deadline at T+2") < titles.index("Event at T+3")
    
    @pytest.mark.asyncio
    async def test_create_deadline_event_type(self, client: AsyncClient, auth_headers: dict):
        """Test that 'deadline' is now a valid event_type for creating scheduled events."""
        event_id = str(uuid.uuid4())
        event_data = {
            "id": event_id,
            "title": "Project Deadline",
            "event_type": "deadline",
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "target_type": "self",
            "all_day": True
        }
        
        response = await client.post("/api/calendar/events", json=event_data, headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        assert data["event_type"] == "deadline"
        assert data["all_day"] is True
