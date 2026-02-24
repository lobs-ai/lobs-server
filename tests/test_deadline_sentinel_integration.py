"""Tests for deadline sentinel workflow integration."""

from datetime import datetime, timedelta, timezone

import pytest

from sqlalchemy import select

from app.models import TrackerEntry, TrackerNotification, Task
from app.orchestrator.workflow_integrations import check_deadlines


@pytest.mark.asyncio
async def test_check_deadlines_uses_windows_and_escalates_t4(db_session):
    due_t4 = datetime.now(timezone.utc) + timedelta(hours=3)
    due_t24 = datetime.now(timezone.utc) + timedelta(hours=20)

    db_session.add_all([
        TrackerEntry(
            id="dl-t4",
            type="deadline",
            raw_text="Final interview presentation",
            due_date=due_t4,
            commitment_type="interview",
            estimated_minutes=180,
        ),
        TrackerEntry(
            id="dl-t24",
            type="deadline",
            raw_text="Class deliverable checkpoint",
            due_date=due_t24,
            commitment_type="class",
            estimated_minutes=60,
        ),
    ])
    await db_session.commit()

    result = await check_deadlines(db_session)

    assert result["deadlines"] == 2
    assert result["notified"] == 2
    assert result["escalated"] >= 1

    notif_rows = (await db_session.execute(select(TrackerNotification))).scalars().all()
    notif_types = {row.notification_type for row in notif_rows}
    assert "deadline_reminder_t-4" in notif_types
    assert "deadline_reminder_t-24" in notif_types

    task_rows = (await db_session.execute(select(Task))).scalars().all()
    assert len(task_rows) >= 1
