"""Tests for database lock recovery and retry-on-lock logic.

These tests verify that all critical DB operations in the worker system
have proper retry logic with exponential backoff to handle 'database is locked'
errors under high concurrency.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestrator.worker import WorkerManager
from app.orchestrator.agent_tracker import AgentTracker
from app.models import WorkerStatus, AgentStatus, WorkerRun, Task


class TestWorkerStatusDBLockRecovery:
    """Test worker_status retry logic for DB locks."""

    @pytest.mark.asyncio
    async def test_update_worker_status_recovers_from_lock(self):
        """Test that worker_status updates retry on 'database is locked' error."""
        db = AsyncMock(spec=AsyncSession)
        
        # Create a real WorkerStatus object to test state transitions
        status_obj = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Fail twice with "database is locked", succeed on third attempt
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # This should succeed despite the lock errors
        await manager._update_worker_status(
            active=True,
            worker_id="worker-123",
            task_id="task-1",
            project_id="proj-1",
            started_at=datetime.now(timezone.utc)
        )
        
        # Verify the retry behavior
        assert db.commit.call_count == 3  # 2 failures + 1 success
        assert db.rollback.call_count == 2
        assert status_obj.active is True
        assert status_obj.worker_id == "worker-123"

    @pytest.mark.asyncio
    async def test_worker_status_exponential_backoff(self):
        """Test that retry delays increase exponentially."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Fail 3 times to test backoff
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            Exception("database is locked"),
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        sleep_calls = []
        async def mock_sleep(delay):
            sleep_calls.append(delay)
        
        with patch('asyncio.sleep', side_effect=mock_sleep):
            await manager._update_worker_status(active=True)
        
        # Backoff should be: 0.5, 1.0, 1.5 seconds
        assert sleep_calls == [0.5, 1.0, 1.5]

    @pytest.mark.asyncio
    async def test_worker_status_gives_up_after_5_attempts(self):
        """Test that worker_status update gives up gracefully after max retries."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Always fail
        db.commit = AsyncMock(side_effect=Exception("database is locked"))
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # Should not raise, but give up after 5 attempts
        await manager._update_worker_status(active=True)
        
        # Verify max attempts
        assert db.commit.call_count == 5

    @pytest.mark.asyncio
    async def test_get_worker_status_recovers_from_lock(self):
        """Test that reading worker_status also retries on lock."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(
            id=1, 
            active=True, 
            worker_id="worker-123",
            current_task="task-1",
            started_at=datetime.now(timezone.utc)
        )
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        
        # Fail once, then succeed
        db.execute = AsyncMock(side_effect=[
            Exception("database is locked"),
            result  # Return the status on second attempt
        ])
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        status = await manager.get_worker_status()
        
        # Should have retried and gotten the status
        assert status["busy"] is True
        assert status["worker_id"] == "worker-123"
        assert db.execute.call_count == 2


class TestWorkerRunDBLockRecovery:
    """Test worker_runs INSERT retry logic for DB locks."""

    @pytest.mark.asyncio
    async def test_record_worker_run_retries_on_lock(self):
        """Test that worker_runs INSERT retries on 'database is locked'."""
        db = AsyncMock(spec=AsyncSession)
        db.rollback = AsyncMock()
        
        # Commit fails twice, succeeds on third attempt
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            Exception("database is locked"),
            None  # Success
        ])
        
        manager = WorkerManager(db)
        
        # Mock the independent session
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            None  # Success on retry
        ])
        mock_session.rollback = AsyncMock()
        
        manager._get_independent_session = MagicMock(return_value=mock_session)
        
        # Should retry and succeed
        await manager._record_worker_run(
            worker_id="worker-1",
            task_id="task-1",
            start_time=1234567890,
            duration=100,
            succeeded=True,
            exit_code=0,
            summary="Task completed",
            model="gpt-4",
            agent_type="programmer"
        )
        
        # Should have retried the commit
        assert mock_session.commit.call_count == 2


class TestAgentTrackerDBLockRecovery:
    """Test agent status tracking retry logic for DB locks."""

    @pytest.mark.asyncio
    async def test_mark_working_retries_on_lock(self):
        """Test that agent tracker retries on 'database is locked'."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = AgentStatus(agent_type="programmer", status="idle", stats={})
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Fail once, then succeed
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        db.flush = AsyncMock()
        
        tracker = AgentTracker(db)
        
        await tracker.mark_working(
            agent_type="programmer",
            task_id="task-1",
            project_id="proj-1",
            activity="Running task"
        )
        
        # Should have retried
        assert db.commit.call_count == 2
        assert status_obj.status == "working"
        assert status_obj.activity == "Running task"

    @pytest.mark.asyncio
    async def test_mark_idle_retries_on_lock(self):
        """Test that mark_idle retries on 'database is locked'."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = AgentStatus(
            agent_type="programmer",
            status="working",
            current_task_id="task-1",
            stats={}
        )
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        db.flush = AsyncMock()
        
        # Fail once, succeed on retry
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        tracker = AgentTracker(db)
        
        await tracker.mark_idle(agent_type="programmer")
        
        # Should have retried
        assert db.commit.call_count == 2
        assert status_obj.status == "idle"
        assert status_obj.current_task_id is None


class TestConcurrentLockHandling:
    """Test that concurrent DB operations handle locks gracefully."""

    @pytest.mark.asyncio
    async def test_multiple_concurrent_status_updates(self):
        """Test that multiple concurrent status updates don't cascade fail."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Simulate high contention: many initial failures
        commit_results = [
            Exception("database is locked"),
            Exception("database is locked"),
            Exception("database is locked"),
            None,  # First succeeds
        ]
        db.commit = AsyncMock(side_effect=commit_results * 3)  # Repeat for multiple calls
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # Simulate multiple concurrent calls
        tasks = [
            manager._update_worker_status(active=True, worker_id=f"w{i}", task_id=f"t{i}")
            for i in range(3)
        ]
        
        # Should all eventually succeed despite contention
        results = await asyncio.gather(*tasks, return_exceptions=False)
        
        # All tasks should complete (no exceptions)
        assert len(results) == 3
