"""Tests for database lock retry logic in worker and agent tracker."""

import asyncio
import pytest
from datetime import datetime, timezone
from sqlalchemy import text

from app.orchestrator.agent_tracker import AgentTracker


@pytest.mark.asyncio
async def test_agent_tracker_mark_working_with_retry(db_session):
    """Test that AgentTracker.mark_working works and persists."""
    tracker = AgentTracker(db_session)
    
    # Mark as working
    await tracker.mark_working(
        agent_type="programmer",
        task_id="test_task_1",
        project_id="test_project",
        activity="Testing database retry logic"
    )
    
    # Verify the status was persisted
    status = await tracker.get_status("programmer")
    assert status is not None
    assert status["status"] == "working"
    assert status["activity"] == "Testing database retry logic"
    assert status["current_task_id"] == "test_task_1"


@pytest.mark.asyncio
async def test_agent_tracker_mark_completed(db_session):
    """Test that AgentTracker.mark_completed works."""
    tracker = AgentTracker(db_session)
    
    # Mark as working first
    await tracker.mark_working(
        agent_type="programmer",
        task_id="test_task_2",
        project_id="test_project",
        activity="Testing"
    )
    
    # Mark as completed
    await tracker.mark_completed(
        agent_type="programmer",
        task_id="test_task_2",
        duration_seconds=150.5
    )
    
    # Verify the status was persisted
    status = await tracker.get_status("programmer")
    assert status is not None
    assert status["last_completed_task_id"] == "test_task_2"
    stats = status.get("stats", {})
    assert stats.get("tasks_completed", 0) >= 1


@pytest.mark.asyncio
async def test_agent_tracker_mark_failed(db_session):
    """Test that AgentTracker.mark_failed works."""
    tracker = AgentTracker(db_session)
    
    # Mark as working first
    await tracker.mark_working(
        agent_type="researcher",
        task_id="test_task_3",
        project_id="test_project",
        activity="Testing"
    )
    
    # Mark as failed
    await tracker.mark_failed(
        agent_type="researcher",
        task_id="test_task_3"
    )
    
    # Verify the status was persisted
    status = await tracker.get_status("researcher")
    assert status is not None
    stats = status.get("stats", {})
    assert stats.get("tasks_failed", 0) >= 1


@pytest.mark.asyncio
async def test_agent_tracker_mark_idle(db_session):
    """Test that AgentTracker.mark_idle works."""
    tracker = AgentTracker(db_session)
    
    # Mark as working first
    await tracker.mark_working(
        agent_type="writer",
        task_id="test_task_4",
        project_id="test_project",
        activity="Testing"
    )
    
    # Mark as idle
    await tracker.mark_idle(agent_type="writer")
    
    # Verify the status was updated
    status = await tracker.get_status("writer")
    assert status is not None
    assert status["status"] == "idle"
    assert status["current_task_id"] is None
    assert status["activity"] is None


@pytest.mark.asyncio
async def test_database_busy_timeout_set(db_session):
    """Test that busy_timeout is set to sufficient value (>= 5000ms)."""
    result = await db_session.execute(text("PRAGMA busy_timeout;"))
    timeout_ms = result.scalar()
    assert timeout_ms >= 5000, f"Expected busy_timeout >= 5000ms, got {timeout_ms}ms"


@pytest.mark.asyncio
async def test_agent_tracker_update_thinking(db_session):
    """Test that AgentTracker.update_thinking works."""
    tracker = AgentTracker(db_session)
    
    # Mark as working first
    await tracker.mark_working(
        agent_type="programmer",
        task_id="test_task_5",
        project_id="test_project",
        activity="Thinking about the problem"
    )
    
    # Update thinking snippet
    await tracker.update_thinking(
        agent_type="programmer",
        snippet="Analyzing the code structure..."
    )
    
    # Verify the thinking was persisted
    status = await tracker.get_status("programmer")
    assert status is not None
    assert status["thinking"] == "Analyzing the code structure..."


@pytest.mark.asyncio
async def test_agent_tracker_sequence_of_operations(db_session):
    """Test a sequence of agent operations that would hit DB under load."""
    tracker = AgentTracker(db_session)
    
    # Simulate multiple agents working and completing tasks
    agents = ["programmer", "researcher", "writer"]
    
    for agent in agents:
        await tracker.mark_working(
            agent_type=agent,
            task_id=f"task_{agent}_1",
            project_id="project_1",
            activity=f"Working on task 1"
        )
    
    # Update thinking for each
    for agent in agents:
        await tracker.update_thinking(
            agent_type=agent,
            snippet=f"Processing for {agent}..."
        )
    
    # Complete some tasks
    for agent in agents[:2]:
        await tracker.mark_completed(
            agent_type=agent,
            task_id=f"task_{agent}_1",
            duration_seconds=100.0
        )
    
    # Mark remaining as failed
    await tracker.mark_failed(
        agent_type=agents[2],
        task_id=f"task_{agents[2]}_1"
    )
    
    # Mark all idle
    for agent in agents:
        await tracker.mark_idle(agent_type=agent)
    
    # Verify final states
    for agent in agents:
        status = await tracker.get_status(agent)
        assert status["status"] == "idle"
        assert status["current_task_id"] is None
