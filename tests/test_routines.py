from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import RoutineAuditEvent, RoutineRegistry
from app.orchestrator.routine_runner import RoutineRunner


@pytest.mark.asyncio
async def test_governance_create_routine_initializes_next_run_at(client):
    payload = {
        "id": str(uuid.uuid4()),
        "name": "daily-noop",
        "description": "Test routine",
        "hook": "noop",
        "schedule": "0 9 * * *",
        "execution_policy": "auto",
        "enabled": True,
    }
    resp = await client.post("/api/governance/routines", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["schedule"] == "0 9 * * *"
    assert data["next_run_at"] is not None


@pytest.mark.asyncio
async def test_routine_runner_auto_executes_and_audits(db_session):
    now = datetime.now(timezone.utc)
    routine = RoutineRegistry(
        id=str(uuid.uuid4()),
        name="noop",
        hook="noop",
        schedule="0 9 * * *",
        execution_policy="auto",
        enabled=True,
        next_run_at=now - timedelta(minutes=1),
    )
    db_session.add(routine)
    await db_session.commit()

    async def noop(_routine: RoutineRegistry):
        return {"ok": True}

    runner = RoutineRunner(db_session, hooks={"noop": noop})
    result = await runner.process_due_routines(now=now)
    assert result.executed == 1
    await db_session.commit()

    refreshed = await db_session.get(RoutineRegistry, routine.id)
    assert refreshed is not None
    assert refreshed.run_count == 1
    assert refreshed.last_run_at is not None
    assert refreshed.next_run_at is not None
    assert refreshed.next_run_at > now

    audit_q = await db_session.execute(
        select(RoutineAuditEvent).where(RoutineAuditEvent.routine_id == routine.id)
    )
    events = audit_q.scalars().all()
    actions = [e.action for e in events]
    assert "executed" in actions


@pytest.mark.asyncio
async def test_routine_runner_confirm_requests_inbox_item_and_sets_pending(db_session):
    from app.models import InboxItem

    now = datetime.now(timezone.utc)
    routine = RoutineRegistry(
        id=str(uuid.uuid4()),
        name="confirm-me",
        hook="noop",
        schedule="0 9 * * *",
        execution_policy="confirm",
        enabled=True,
        next_run_at=now - timedelta(minutes=1),
    )
    db_session.add(routine)
    await db_session.commit()

    async def noop(_routine: RoutineRegistry):
        return {"ok": True}

    runner = RoutineRunner(db_session, hooks={"noop": noop})
    result = await runner.process_due_routines(now=now)
    assert result.confirmation_requested == 1
    await db_session.commit()

    refreshed = await db_session.get(RoutineRegistry, routine.id)
    assert refreshed.pending_confirmation is True

    inbox_q = await db_session.execute(
        select(InboxItem).where(InboxItem.filename == "routine_confirmation")
    )
    assert inbox_q.scalars().first() is not None
