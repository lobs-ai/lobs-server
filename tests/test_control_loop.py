from datetime import datetime, timezone

import pytest

from app.models import ControlLoopEvent, ControlLoopHeartbeat
from app.orchestrator.control_loop import LobsControlLoopService


@pytest.mark.asyncio
async def test_task_created_event_routes_assignment(db_session):
    event = ControlLoopEvent(
        id="evt-1",
        event_type="TaskCreated",
        status="pending",
        payload={"task_id": "task-1"},
    )
    db_session.add(event)
    await db_session.commit()

    routed_payloads: list[dict] = []

    async def route_task_created(payload: dict) -> bool:
        routed_payloads.append(payload)
        return True

    service = LobsControlLoopService(
        db_session,
        reflection_interval_seconds=21600,
        reflection_last_run_at=datetime.now(timezone.utc).timestamp(),
        compression_hour_et=3,
        last_compression_date_et=None,
        run_reflection=lambda: _empty_result(),
        run_daily_compression=lambda: _empty_result(),
        route_task_created=route_task_created,
    )

    result = await service.run_once()
    assert result.events_processed == 1
    assert routed_payloads == [{"task_id": "task-1"}]

    stored = await db_session.get(ControlLoopEvent, "evt-1")
    assert stored is not None
    assert stored.status == "completed"
    assert stored.result == {"routed": True}


@pytest.mark.asyncio
async def test_reflection_and_daily_compression_cadence(db_session):
    reflection_runs = 0
    compression_runs = 0

    async def run_reflection() -> dict:
        nonlocal reflection_runs
        reflection_runs += 1
        return {"spawned": 1}

    async def run_compression() -> dict:
        nonlocal compression_runs
        compression_runs += 1
        return {"rewritten": 1}

    async def route_task_created(_: dict) -> bool:
        return False

    service = LobsControlLoopService(
        db_session,
        reflection_interval_seconds=21600,
        reflection_last_run_at=datetime(2026, 2, 17, 0, 0, tzinfo=timezone.utc).timestamp(),
        compression_hour_et=3,
        last_compression_date_et=None,
        run_reflection=run_reflection,
        run_daily_compression=run_compression,
        route_task_created=route_task_created,
    )

    before_window = datetime(2026, 2, 17, 7, 59, tzinfo=timezone.utc)  # 2:59 ET
    first = await service.run_once(now_utc=before_window)
    assert first.reflection_triggered is True
    assert first.compression_triggered is False

    trigger_window = datetime(2026, 2, 17, 8, 1, tzinfo=timezone.utc)  # 3:01 ET
    second = await service.run_once(now_utc=trigger_window)
    assert second.reflection_triggered is False
    assert second.compression_triggered is True

    same_day_again = datetime(2026, 2, 17, 13, 0, tzinfo=timezone.utc)
    third = await service.run_once(now_utc=same_day_again)
    assert third.compression_triggered is False

    assert reflection_runs == 1
    assert compression_runs == 1


@pytest.mark.asyncio
async def test_control_loop_heartbeat_persisted(db_session):
    async def _noop() -> dict:
        return {}

    async def _route(_: dict) -> bool:
        return False

    service = LobsControlLoopService(
        db_session,
        reflection_interval_seconds=999999,
        reflection_last_run_at=datetime.now(timezone.utc).timestamp(),
        compression_hour_et=3,
        last_compression_date_et="2026-02-17",
        run_reflection=_noop,
        run_daily_compression=_noop,
        route_task_created=_route,
    )

    await service.run_once(now_utc=datetime(2026, 2, 17, 12, 0, tzinfo=timezone.utc))

    heartbeat = await db_session.get(ControlLoopHeartbeat, "main")
    assert heartbeat is not None
    assert heartbeat.phase == "tick_complete"
    assert heartbeat.heartbeat_metadata["events_processed"] == 0


async def _empty_result() -> dict:
    return {}
