"""
Tests for _check_spawn_agent false-failure fix (FAD05A1F infinite loop).
"""

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
import pytest


def _make_db_task(work_state="in_progress", updated_at_offset_seconds=-120):
    t = MagicMock()
    t.work_state = work_state
    t.status = "active"
    t.updated_at = datetime.now(timezone.utc) + timedelta(seconds=updated_at_offset_seconds)
    return t


def _make_scalars_result(first_value):
    scalars = MagicMock()
    scalars.first.return_value = first_value
    result = MagicMock()
    result.scalars.return_value = scalars
    return result


def _make_run(task_id="task-abc-123"):
    run = MagicMock()
    run.context = {"task": {"id": task_id}}
    run.node_states = {}
    return run


def _make_node_def(node_id="spawn_agent_1"):
    return {"id": node_id, "config": {}}


def _make_worker_manager(has_active_worker=False, task_id="task-abc-123"):
    wm = MagicMock()
    if has_active_worker:
        wi = MagicMock()
        wi.task_id = task_id
        wm.active_workers = {"worker-1": wi}
    else:
        wm.active_workers = {}
    wm.check_session_alive = AsyncMock(return_value=False)
    return wm


@pytest.mark.asyncio
async def test_recently_succeeded_worker_run_returns_completed():
    """No stub but recently-succeeded WorkerRun → completed (not failed)."""
    from app.orchestrator.workflow_nodes import _check_spawn_agent

    task_id = "task-abc-123"
    db_task = _make_db_task(work_state="in_progress", updated_at_offset_seconds=-120)
    succeeded_wr = MagicMock()
    succeeded_wr.succeeded = True

    db = AsyncMock()
    db.get = AsyncMock(return_value=db_task)

    call_count = [0]

    async def mock_execute(stmt):
        call_count[0] += 1
        if call_count[0] == 1:
            return _make_scalars_result(None)   # no stub
        return _make_scalars_result(succeeded_wr)  # succeeded run

    db.execute = mock_execute

    wm = _make_worker_manager(has_active_worker=False, task_id=task_id)
    result = await _check_spawn_agent(_make_node_def(), _make_run(task_id), db=db, worker_manager=wm)

    assert result is not None
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_grace_period_returns_none_when_task_recently_updated():
    """task.updated_at within 60s, no stub, no succeeded run → None (still waiting)."""
    from app.orchestrator.workflow_nodes import _check_spawn_agent

    task_id = "task-grace"
    db_task = _make_db_task(work_state="in_progress", updated_at_offset_seconds=-10)

    db = AsyncMock()
    db.get = AsyncMock(return_value=db_task)
    db.execute = AsyncMock(return_value=_make_scalars_result(None))

    wm = _make_worker_manager(has_active_worker=False, task_id=task_id)
    result = await _check_spawn_agent(_make_node_def(), _make_run(task_id), db=db, worker_manager=wm)

    assert result is None


@pytest.mark.asyncio
async def test_truly_gone_worker_returns_failed():
    """No stub, no succeeded run, task updated > 60s ago → failed."""
    from app.orchestrator.workflow_nodes import _check_spawn_agent

    task_id = "task-gone"
    db_task = _make_db_task(work_state="in_progress", updated_at_offset_seconds=-300)

    db = AsyncMock()
    db.get = AsyncMock(return_value=db_task)
    db.execute = AsyncMock(return_value=_make_scalars_result(None))

    wm = _make_worker_manager(has_active_worker=False, task_id=task_id)
    result = await _check_spawn_agent(_make_node_def(), _make_run(task_id), db=db, worker_manager=wm)

    assert result is not None
    assert result.status == "failed"


@pytest.mark.asyncio
async def test_active_worker_returns_none():
    """Worker still in active_workers → None (still running)."""
    from app.orchestrator.workflow_nodes import _check_spawn_agent

    task_id = "task-running"
    wm = _make_worker_manager(has_active_worker=True, task_id=task_id)
    result = await _check_spawn_agent(_make_node_def(), _make_run(task_id), db=AsyncMock(), worker_manager=wm)
    assert result is None
