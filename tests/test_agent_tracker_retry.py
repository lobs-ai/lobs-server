"""Tests for agent_tracker retry-on-lock logic."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestrator.agent_tracker import AgentTracker
from app.models import AgentStatus


class TestAgentTrackerRetry:
    """Test retry-on-lock behavior for agent_tracker operations."""

    @pytest.mark.asyncio
    async def test_mark_working_success_first_attempt(self):
        """Test successful mark_working on first attempt."""
        db = AsyncMock(spec=AsyncSession)
        
        # Mock the execute call
        status_obj = AgentStatus(agent_type="programmer", status="idle")
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.add = MagicMock()
        
        tracker = AgentTracker(db)
        await tracker.mark_working(
            agent_type="programmer",
            task_id="task-1",
            project_id="proj-1",
            activity="Testing task"
        )
        
        # Verify commit was called exactly once
        assert db.commit.call_count == 1
        assert db.rollback.call_count == 0
        assert status_obj.status == "working"
        assert status_obj.current_task_id == "task-1"

    @pytest.mark.asyncio
    async def test_mark_working_retries_on_lock(self):
        """Test that mark_working retries when database is locked."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = AgentStatus(agent_type="programmer", status="idle")
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        db.flush = AsyncMock()
        db.add = MagicMock()
        
        # Fail first 2 times, succeed on 3rd
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        tracker = AgentTracker(db)
        await tracker.mark_working(
            agent_type="programmer",
            task_id="task-1",
            project_id="proj-1",
            activity="Testing"
        )
        
        # Verify retry behavior
        assert db.commit.call_count == 3
        assert db.rollback.call_count == 2

    @pytest.mark.asyncio
    async def test_mark_completed_retries_on_lock(self):
        """Test that mark_completed retries when database is locked."""
        db = AsyncMock(spec=AsyncSession)
        
        # Create fresh status objects for each execution to simulate DB behavior
        status_objs = [
            AgentStatus(agent_type="programmer", status="working", stats={}),
            AgentStatus(agent_type="programmer", status="working", stats={})
        ]
        
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(side_effect=status_objs)
        db.execute = AsyncMock(return_value=result)
        db.flush = AsyncMock()
        db.add = MagicMock()
        
        # Fail first time, succeed on 2nd
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        tracker = AgentTracker(db)
        await tracker.mark_completed(
            agent_type="programmer",
            task_id="task-1",
            duration_seconds=60.0
        )
        
        # Verify retry behavior
        assert db.commit.call_count == 2
        assert db.rollback.call_count == 1

    @pytest.mark.asyncio
    async def test_mark_failed_retries_on_lock(self):
        """Test that mark_failed retries when database is locked."""
        db = AsyncMock(spec=AsyncSession)
        
        # Create fresh status objects for each execution
        status_objs = [
            AgentStatus(agent_type="researcher", status="working", stats={}),
            AgentStatus(agent_type="researcher", status="working", stats={})
        ]
        
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(side_effect=status_objs)
        db.execute = AsyncMock(return_value=result)
        db.flush = AsyncMock()
        db.add = MagicMock()
        
        # Fail first time, succeed on 2nd
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        tracker = AgentTracker(db)
        await tracker.mark_failed(
            agent_type="researcher",
            task_id="task-1"
        )
        
        # Verify retry behavior
        assert db.commit.call_count == 2
        assert db.rollback.call_count == 1

    @pytest.mark.asyncio
    async def test_mark_idle_retries_on_lock(self):
        """Test that mark_idle retries when database is locked."""
        db = AsyncMock(spec=AsyncSession)
        
        # Create fresh status objects for each execution
        status_objs = [
            AgentStatus(agent_type="programmer", status="working", current_task_id="task-1"),
            AgentStatus(agent_type="programmer", status="working", current_task_id="task-1")
        ]
        
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(side_effect=status_objs)
        db.execute = AsyncMock(return_value=result)
        db.flush = AsyncMock()
        db.add = MagicMock()
        
        # Fail first time, succeed on 2nd
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        tracker = AgentTracker(db)
        await tracker.mark_idle(agent_type="programmer")
        
        # Verify retry behavior
        assert db.commit.call_count == 2
        assert db.rollback.call_count == 1

    @pytest.mark.asyncio
    async def test_mark_working_exponential_backoff(self):
        """Test that mark_working uses exponential backoff."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = AgentStatus(agent_type="programmer", status="idle")
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        db.flush = AsyncMock()
        db.add = MagicMock()
        
        # Fail 3 times to test backoff
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            Exception("database is locked"),
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        tracker = AgentTracker(db)
        
        # Mock asyncio.sleep to verify delays
        sleep_calls = []
        
        async def mock_sleep(delay):
            sleep_calls.append(delay)
        
        with patch('asyncio.sleep', side_effect=mock_sleep):
            await tracker.mark_working(
                agent_type="programmer",
                task_id="task-1",
                project_id="proj-1",
                activity="Test"
            )
        
        # Verify backoff delays: 0.5, 1.0, 1.5 seconds
        assert sleep_calls == [0.5, 1.0, 1.5]

    @pytest.mark.asyncio
    async def test_mark_completed_all_attempts_fail(self):
        """Test that mark_completed gives up after 5 attempts."""
        db = AsyncMock(spec=AsyncSession)
        
        # Create 5 fresh status objects (one for each attempt)
        status_objs = [
            AgentStatus(agent_type="programmer", status="working", stats={})
            for _ in range(5)
        ]
        
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(side_effect=status_objs)
        db.execute = AsyncMock(return_value=result)
        db.flush = AsyncMock()
        db.add = MagicMock()
        
        # Always fail
        db.commit = AsyncMock(side_effect=Exception("database is locked"))
        db.rollback = AsyncMock()
        
        tracker = AgentTracker(db)
        
        # Should not raise, but give up gracefully
        await tracker.mark_completed(
            agent_type="programmer",
            task_id="task-1",
            duration_seconds=60.0
        )
        
        # Verify all 5 attempts were made
        assert db.commit.call_count == 5
        assert db.rollback.call_count == 5  # 4 in loop + 1 in finally

    @pytest.mark.asyncio
    async def test_update_thinking_retries_on_lock(self):
        """Test that update_thinking retries when database is locked."""
        db = AsyncMock(spec=AsyncSession)
        
        # Create fresh status objects
        status_objs = [
            AgentStatus(agent_type="programmer", status="working", thinking=None),
            AgentStatus(agent_type="programmer", status="working", thinking=None)
        ]
        
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(side_effect=status_objs)
        db.execute = AsyncMock(return_value=result)
        db.flush = AsyncMock()
        db.add = MagicMock()
        
        # Fail first time, succeed on 2nd
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        tracker = AgentTracker(db)
        await tracker.update_thinking(
            agent_type="programmer",
            snippet="Processing task..."
        )
        
        # Verify retry behavior
        assert db.commit.call_count == 2
        assert db.rollback.call_count == 1
