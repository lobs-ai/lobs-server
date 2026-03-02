"""Tests for GET /api/analytics/outcomes endpoint."""
import pytest
from datetime import datetime, timezone, timedelta

from app.models import WorkerRun


async def _seed_runs(db, runs_data):
    for data in runs_data:
        run = WorkerRun(**data)
        db.add(run)
    await db.commit()


@pytest.mark.asyncio
async def test_outcomes_empty(client):
    """Returns zeros when no runs exist."""
    response = await client.get("/api/analytics/outcomes?days=7")
    assert response.status_code == 200
    data = response.json()
    assert data["overall"]["total_runs"] == 0
    assert data["overall"]["success_rate"] == 0.0


@pytest.mark.asyncio
async def test_outcomes_basic(client, db_session):
    """Returns correct counts for seeded runs."""
    now = datetime.now(timezone.utc)
    await _seed_runs(db_session, [
        {
            "worker_id": "w1", "task_id": "t1", "agent_type": "programmer",
            "model": "gpt-4o", "succeeded": True, "started_at": now - timedelta(hours=1),
            "ended_at": now, "duration_seconds": 3600.0, "total_cost_usd": 0.05,
            "input_tokens": 100, "output_tokens": 200, "project_id": "proj-1",
        },
        {
            "worker_id": "w2", "task_id": "t2", "agent_type": "programmer",
            "model": "gpt-4o", "succeeded": False, "started_at": now - timedelta(hours=2),
            "ended_at": now - timedelta(hours=1), "duration_seconds": 3600.0, "total_cost_usd": 0.03,
            "input_tokens": 80, "output_tokens": 150, "project_id": "proj-1",
        },
        {
            "worker_id": "w3", "task_id": "t3", "agent_type": "researcher",
            "model": "claude-3-5-sonnet", "succeeded": True, "started_at": now - timedelta(hours=3),
            "ended_at": now - timedelta(hours=2), "duration_seconds": 3600.0, "total_cost_usd": 0.02,
            "input_tokens": 50, "output_tokens": 100, "project_id": "proj-2",
        },
    ])

    response = await client.get("/api/analytics/outcomes?days=7")
    assert response.status_code == 200
    data = response.json()

    assert data["overall"]["total_runs"] == 3
    assert data["overall"]["succeeded"] == 2
    assert data["overall"]["failed"] == 1
    assert abs(data["overall"]["success_rate"] - 0.6667) < 0.001
    assert abs(data["overall"]["total_cost_usd"] - 0.10) < 0.001

    agent = data["success_rate_by_agent"]
    assert agent["programmer"]["total"] == 2
    assert agent["programmer"]["succeeded"] == 1
    assert agent["researcher"]["total"] == 1
    assert agent["researcher"]["success_rate"] == 1.0

    assert "gpt-4o" in data["avg_duration_by_model"]
    assert data["avg_duration_by_model"]["gpt-4o"] == 3600.0

    assert "gpt-4o" in data["cost_breakdown_by_model"]
    assert abs(data["cost_breakdown_by_model"]["gpt-4o"] - 0.08) < 0.001


@pytest.mark.asyncio
async def test_outcomes_excludes_old_runs(client, db_session):
    """Runs older than the window are excluded."""
    now = datetime.now(timezone.utc)
    await _seed_runs(db_session, [
        {
            "worker_id": "w1", "task_id": "t1", "agent_type": "programmer",
            "model": "gpt-4o", "succeeded": True,
            "started_at": now - timedelta(days=10), "ended_at": now - timedelta(days=9),
        },
    ])

    response = await client.get("/api/analytics/outcomes?days=7")
    assert response.status_code == 200
    assert response.json()["overall"]["total_runs"] == 0


@pytest.mark.asyncio
async def test_outcomes_days_validation(client):
    """Invalid days parameter returns 422."""
    response = await client.get("/api/analytics/outcomes?days=0")
    assert response.status_code == 422
    response = await client.get("/api/analytics/outcomes?days=91")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_outcomes_duration_computed_from_timestamps(client, db_session):
    """If duration_seconds is None, it's computed from started_at/ended_at."""
    now = datetime.now(timezone.utc)
    run = WorkerRun(
        worker_id="w1", task_id="t1", agent_type="programmer", model="gpt-4o",
        succeeded=True, started_at=now - timedelta(seconds=120), ended_at=now,
        duration_seconds=None,
    )
    db_session.add(run)
    await db_session.commit()

    response = await client.get("/api/analytics/outcomes?days=1")
    assert response.status_code == 200
    data = response.json()
    assert "gpt-4o" in data["avg_duration_by_model"]
    assert abs(data["avg_duration_by_model"]["gpt-4o"] - 120.0) < 2.0
