"""Tests for status API endpoints."""

import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient
from sqlalchemy import select

from app.models import WorkerRun, AgentStatus, Memory


@pytest.mark.asyncio
async def test_overview_structure(client: AsyncClient):
    """Test that overview endpoint returns valid structure."""
    response = await client.get("/api/status/overview")
    assert response.status_code == 200
    
    data = response.json()
    
    # Check server section
    assert "server" in data
    assert data["server"]["status"] == "ok"
    assert "uptime_seconds" in data["server"]
    assert data["server"]["version"] == "0.1.0"
    assert isinstance(data["server"]["uptime_seconds"], int)
    assert data["server"]["uptime_seconds"] >= 0
    
    # Check orchestrator section
    assert "orchestrator" in data
    assert "running" in data["orchestrator"]
    assert "paused" in data["orchestrator"]
    assert isinstance(data["orchestrator"]["running"], bool)
    assert isinstance(data["orchestrator"]["paused"], bool)
    
    # Check workers section
    assert "workers" in data
    assert "active" in data["workers"]
    assert "total_completed" in data["workers"]
    assert "total_failed" in data["workers"]
    assert isinstance(data["workers"]["active"], int)
    assert isinstance(data["workers"]["total_completed"], int)
    assert isinstance(data["workers"]["total_failed"], int)
    
    # Check agents section
    assert "agents" in data
    assert isinstance(data["agents"], list)
    
    # Check tasks section
    assert "tasks" in data
    assert "active" in data["tasks"]
    assert "waiting" in data["tasks"]
    assert "blocked" in data["tasks"]
    assert "completed_today" in data["tasks"]
    
    # Check memories section
    assert "memories" in data
    assert "total" in data["memories"]
    assert "today_entries" in data["memories"]
    
    # Check inbox section
    assert "inbox" in data
    assert "unread" in data["inbox"]


@pytest.mark.asyncio
async def test_overview_with_data(client: AsyncClient, sample_task, sample_inbox_item, db_session):
    """Test overview with actual data in the database."""
    # Create a memory
    from app.models import Memory
    memory = Memory(
        path="memory/test.md",
        title="Test Memory",
        content="Test content",
        memory_type="daily",
        date=datetime.now(timezone.utc)
    )
    db_session.add(memory)
    await db_session.commit()
    
    # Create worker runs
    worker_run_completed = WorkerRun(
        worker_id="worker-1",
        started_at=datetime.now(timezone.utc) - timedelta(hours=1),
        ended_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        succeeded=True,
        tasks_completed=1,
        input_tokens=100,
        output_tokens=200,
        total_tokens=300
    )
    worker_run_failed = WorkerRun(
        worker_id="worker-2",
        started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        ended_at=datetime.now(timezone.utc) - timedelta(hours=1, minutes=30),
        succeeded=False,
        tasks_completed=0,
        input_tokens=50,
        output_tokens=100,
        total_tokens=150
    )
    db_session.add(worker_run_completed)
    db_session.add(worker_run_failed)
    await db_session.commit()
    
    # Create agent status
    agent_status = AgentStatus(
        agent_type="programmer",
        status="idle",
        last_active_at=datetime.now(timezone.utc) - timedelta(minutes=5)
    )
    db_session.add(agent_status)
    await db_session.commit()
    
    response = await client.get("/api/status/overview")
    assert response.status_code == 200
    
    data = response.json()
    
    # Verify workers counts
    assert data["workers"]["total_completed"] == 1
    assert data["workers"]["total_failed"] == 1
    
    # Verify agents
    assert len(data["agents"]) == 1
    assert data["agents"][0]["type"] == "programmer"
    assert data["agents"][0]["status"] == "idle"
    
    # Verify tasks
    assert data["tasks"]["active"] == 0  # sample_task is inbox status
    
    # Verify memories
    assert data["memories"]["total"] >= 1
    
    # Verify inbox
    assert data["inbox"]["unread"] >= 1


@pytest.mark.asyncio
async def test_activity_returns_events(client: AsyncClient, sample_task):
    """Test that activity endpoint returns events sorted by timestamp."""
    response = await client.get("/api/status/activity")
    assert response.status_code == 200
    
    data = response.json()
    assert isinstance(data, list)
    
    # Should have at least one event from the sample task
    assert len(data) >= 1
    
    # Check event structure
    if len(data) > 0:
        event = data[0]
        assert "type" in event
        assert "title" in event
        assert "timestamp" in event
        assert "details" in event


@pytest.mark.asyncio
async def test_activity_sorted_by_timestamp(client: AsyncClient, db_session):
    """Test that activity events are sorted by timestamp descending."""
    now = datetime.now(timezone.utc)
    
    # Create worker runs at different times
    for i in range(3):
        worker_run = WorkerRun(
            worker_id=f"worker-{i}",
            started_at=now - timedelta(hours=i),
            ended_at=now - timedelta(hours=i, minutes=-30),
            succeeded=True,
            tasks_completed=1
        )
        db_session.add(worker_run)
    
    await db_session.commit()
    
    response = await client.get("/api/status/activity")
    assert response.status_code == 200
    
    data = response.json()
    
    # Events should be in descending order (most recent first)
    timestamps = [datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")) for event in data]
    
    for i in range(len(timestamps) - 1):
        assert timestamps[i] >= timestamps[i + 1], "Events should be sorted by timestamp descending"


@pytest.mark.asyncio
async def test_activity_limit_parameter(client: AsyncClient, db_session):
    """Test activity limit parameter."""
    now = datetime.now(timezone.utc)
    
    # Create 10 worker runs
    for i in range(10):
        worker_run = WorkerRun(
            worker_id=f"worker-{i}",
            started_at=now - timedelta(hours=i),
            succeeded=True,
            tasks_completed=1
        )
        db_session.add(worker_run)
    
    await db_session.commit()
    
    # Request with limit=5
    response = await client.get("/api/status/activity?limit=5")
    assert response.status_code == 200
    
    data = response.json()
    # Should return no more than 5 events
    assert len(data) <= 5


@pytest.mark.asyncio
async def test_activity_since_parameter(client: AsyncClient, db_session):
    """Test activity since parameter."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=1)
    
    # Create worker runs before and after cutoff
    old_run = WorkerRun(
        worker_id="worker-old",
        started_at=now - timedelta(hours=2),
        succeeded=True,
        tasks_completed=1
    )
    new_run = WorkerRun(
        worker_id="worker-new",
        started_at=now - timedelta(minutes=30),
        succeeded=True,
        tasks_completed=1
    )
    db_session.add(old_run)
    db_session.add(new_run)
    await db_session.commit()
    
    # Request events since cutoff
    since_param = cutoff.isoformat().replace("+00:00", "Z")
    response = await client.get(f"/api/status/activity?since={since_param}")
    assert response.status_code == 200
    
    data = response.json()
    
    # All events should be after cutoff
    # Make cutoff timezone-naive for comparison (SQLite stores as naive)
    cutoff_naive = cutoff.replace(tzinfo=None)
    for event in data:
        # Parse timestamp and make it naive
        event_time_str = event["timestamp"].replace("Z", "")
        # Handle both with and without microseconds
        try:
            event_time = datetime.fromisoformat(event_time_str)
        except ValueError:
            # Try without microseconds
            event_time = datetime.strptime(event_time_str, "%Y-%m-%dT%H:%M:%S")
        assert event_time >= cutoff_naive


@pytest.mark.asyncio
async def test_activity_includes_different_event_types(client: AsyncClient, sample_task, sample_inbox_item, db_session):
    """Test that activity includes different types of events."""
    # Create various types of data
    worker_run = WorkerRun(
        worker_id="worker-1",
        started_at=datetime.now(timezone.utc),
        succeeded=True,
        tasks_completed=1
    )
    db_session.add(worker_run)
    
    memory = Memory(
        path="memory/test.md",
        title="Test Memory",
        content="Test content",
        memory_type="daily"
    )
    db_session.add(memory)
    await db_session.commit()
    
    response = await client.get("/api/status/activity")
    assert response.status_code == 200
    
    data = response.json()
    
    # Collect event types
    event_types = {event["type"] for event in data}
    
    # Should have multiple event types
    assert len(event_types) > 1


@pytest.mark.asyncio
async def test_costs_structure(client: AsyncClient):
    """Test that costs endpoint returns valid structure."""
    response = await client.get("/api/status/costs")
    assert response.status_code == 200
    
    data = response.json()
    
    # Check structure
    assert "today" in data
    assert "week" in data
    assert "month" in data
    assert "by_agent" in data
    
    # Check period structure
    for period in ["today", "week", "month"]:
        assert "tokens_in" in data[period]
        assert "tokens_out" in data[period]
        assert "estimated_cost" in data[period]
        assert isinstance(data[period]["tokens_in"], int)
        assert isinstance(data[period]["tokens_out"], int)
        assert isinstance(data[period]["estimated_cost"], (int, float))
    
    # Check by_agent structure
    assert isinstance(data["by_agent"], list)


@pytest.mark.asyncio
async def test_costs_with_worker_data(client: AsyncClient, db_session):
    """Test costs calculation with actual worker run data."""
    now = datetime.now(timezone.utc)
    
    # Create worker runs with token usage
    today_run = WorkerRun(
        worker_id="worker-today",
        started_at=now - timedelta(hours=1),
        ended_at=now - timedelta(minutes=30),
        succeeded=True,
        input_tokens=1000,
        output_tokens=2000,
        total_tokens=3000,
        total_cost_usd=0.15,
        source="programmer"
    )
    
    last_week_run = WorkerRun(
        worker_id="worker-week",
        started_at=now - timedelta(days=3),
        ended_at=now - timedelta(days=3, hours=-1),
        succeeded=True,
        input_tokens=500,
        output_tokens=1000,
        total_tokens=1500,
        total_cost_usd=0.075,
        source="writer"
    )
    
    last_month_run = WorkerRun(
        worker_id="worker-month",
        started_at=now - timedelta(days=15),
        ended_at=now - timedelta(days=15, hours=-1),
        succeeded=True,
        input_tokens=2000,
        output_tokens=4000,
        total_tokens=6000,
        total_cost_usd=0.30,
        source="researcher"
    )
    
    db_session.add(today_run)
    db_session.add(last_week_run)
    db_session.add(last_month_run)
    await db_session.commit()
    
    response = await client.get("/api/status/costs")
    assert response.status_code == 200
    
    data = response.json()
    
    # Today should include only today's run
    assert data["today"]["tokens_in"] == 1000
    assert data["today"]["tokens_out"] == 2000
    assert data["today"]["estimated_cost"] == 0.15
    
    # Week should include today + last week
    assert data["week"]["tokens_in"] == 1500
    assert data["week"]["tokens_out"] == 3000
    assert abs(data["week"]["estimated_cost"] - 0.225) < 0.01
    
    # Month should include all
    assert data["month"]["tokens_in"] == 3500
    assert data["month"]["tokens_out"] == 7000
    assert abs(data["month"]["estimated_cost"] - 0.525) < 0.01
    
    # Check by_agent breakdown
    assert len(data["by_agent"]) == 3
    agent_types = {agent["type"] for agent in data["by_agent"]}
    assert "programmer" in agent_types
    assert "writer" in agent_types
    assert "researcher" in agent_types


@pytest.mark.asyncio
async def test_costs_empty_database(client: AsyncClient):
    """Test costs with no worker runs."""
    response = await client.get("/api/status/costs")
    assert response.status_code == 200
    
    data = response.json()
    
    # All costs should be zero
    assert data["today"]["tokens_in"] == 0
    assert data["today"]["tokens_out"] == 0
    assert data["today"]["estimated_cost"] == 0.0
    
    assert data["week"]["tokens_in"] == 0
    assert data["week"]["tokens_out"] == 0
    assert data["week"]["estimated_cost"] == 0.0
    
    assert data["month"]["tokens_in"] == 0
    assert data["month"]["tokens_out"] == 0
    assert data["month"]["estimated_cost"] == 0.0
    
    # No agents
    assert data["by_agent"] == []
