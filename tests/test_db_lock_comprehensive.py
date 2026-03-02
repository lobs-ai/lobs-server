"""
Comprehensive tests for database lock handling in critical paths.

Verifies that high-frequency DB writes (worker_status, worker_runs, agent_tracker)
properly handle 'database is locked' errors through retry-with-exponential-backoff.
"""

import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError

from app.models import WorkerStatus, WorkerRun, AgentStatus, Task
from app.orchestrator.worker_manager import WorkerManager
from app.orchestrator.agent_tracker import AgentTracker
from app.routers.worker import router as worker_router


class TestWorkerStatusDBLock:
    """Test worker_status update under DB lock stress."""

    @pytest.mark.asyncio
    async def test_worker_status_update_with_database_locked_error(self):
        """Test that 'database is locked' errors are properly retried in worker_status update."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Simulate database lock on first 3 attempts, succeed on 4th
        db.commit = AsyncMock(side_effect=[
            OperationalError("database is locked", None, None),
            OperationalError("database is locked", None, None),
            OperationalError("database is locked", None, None),
            None  # Success on 4th attempt
        ])
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # This should succeed after retries
        await manager._update_worker_status(
            active=True,
            worker_id="worker-test-123",
            task_id="task-123",
            project_id="proj-123",
            started_at=datetime.now(timezone.utc)
        )
        
        # Verify the update succeeded
        assert status_obj.active is True
        assert status_obj.worker_id == "worker-test-123"
        
        # Verify retry behavior: 4 commits (3 failures + 1 success)
        assert db.commit.call_count == 4
        # 3 rollbacks for failures
        assert db.rollback.call_count == 3

    @pytest.mark.asyncio
    async def test_worker_status_update_exponential_backoff_delays(self):
        """Verify exponential backoff: 0.5s, 1.0s, 1.5s, 2.0s, etc."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Always fail to test backoff delays
        db.commit = AsyncMock(side_effect=OperationalError("database is locked", None, None))
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # Track sleep calls
        sleep_delays = []
        original_sleep = asyncio.sleep
        
        async def mock_sleep(delay):
            sleep_delays.append(delay)
        
        with patch('asyncio.sleep', side_effect=mock_sleep):
            await manager._update_worker_status(active=True)
        
        # Verify exponential backoff: [0.5, 1.0, 1.5, 2.0]
        assert sleep_delays == [0.5, 1.0, 1.5, 2.0], f"Expected [0.5, 1.0, 1.5, 2.0], got {sleep_delays}"


class TestAgentTrackerDBLock:
    """Test agent_tracker operations under DB lock stress."""

    @pytest.mark.asyncio
    async def test_mark_working_with_database_locked_error(self):
        """Test that mark_working retries on database lock."""
        db = AsyncMock(spec=AsyncSession)
        
        # Mock the status object
        status_obj = AgentStatus(agent_type="programmer", status="idle")
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        db.flush = AsyncMock()
        
        # Fail first 2 times, succeed on 3rd
        db.commit = AsyncMock(side_effect=[
            OperationalError("database is locked", None, None),
            OperationalError("database is locked", None, None),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        tracker = AgentTracker(db)
        
        # Should succeed after retries
        await tracker.mark_working(
            agent_type="programmer",
            task_id="task-789",
            project_id="proj-789",
            activity="Running task 789"
        )
        
        # Verify success
        assert status_obj.status == "working"
        assert status_obj.activity == "Running task 789"
        assert db.commit.call_count == 3

    @pytest.mark.asyncio
    async def test_mark_completed_with_database_locked_error(self):
        """Test that mark_completed retries on database lock."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = AgentStatus(agent_type="programmer", status="working", stats={})
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        db.flush = AsyncMock()
        
        # Fail once, succeed on 2nd
        db.commit = AsyncMock(side_effect=[
            OperationalError("database is locked", None, None),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        tracker = AgentTracker(db)
        
        # Should succeed after retry
        await tracker.mark_completed(
            agent_type="programmer",
            task_id="task-999",
            duration_seconds=300.0
        )
        
        # Verify stats were updated
        assert status_obj.last_completed_task_id == "task-999"
        assert status_obj.stats["tasks_completed"] == 1

    @pytest.mark.asyncio
    async def test_all_agent_tracker_methods_have_retry_logic(self):
        """Test that all AgentTracker methods implement retry logic."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = AgentStatus(agent_type="programmer", status="idle", stats={})
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        db.flush = AsyncMock()
        
        # Test each method independently with DB lock
        methods_to_test = [
            ("mark_working", {
                "agent_type": "programmer",
                "task_id": "task-1",
                "project_id": "proj-1",
                "activity": "testing"
            }),
            ("update_thinking", {
                "agent_type": "programmer",
                "snippet": "thinking about task"
            }),
            ("mark_completed", {
                "agent_type": "programmer",
                "task_id": "task-2",
                "duration_seconds": 100.0
            }),
            ("mark_failed", {
                "agent_type": "programmer",
                "task_id": "task-3"
            }),
            ("mark_idle", {
                "agent_type": "programmer"
            }),
        ]
        
        tracker = AgentTracker(db)
        
        for method_name, kwargs in methods_to_test:
            # Reset mocks
            db.commit.reset_mock()
            db.rollback.reset_mock()
            db.execute.reset_mock()
            
            # Simulate one lock failure then success
            db.commit.side_effect = [
                OperationalError("database is locked", None, None),
                None
            ]
            db.execute.return_value = result
            
            # Get and call the method
            method = getattr(tracker, method_name)
            await method(**kwargs)
            
            # Verify retry happened
            assert db.commit.call_count == 2, f"{method_name} did not retry properly"


class TestConcurrentDBLockHandling:
    """Test concurrent operations under DB lock stress."""

    @pytest.mark.asyncio
    async def test_multiple_concurrent_worker_status_updates(self):
        """Test that multiple concurrent worker_status updates don't cause cascading failures."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Simulate occasional locks
        attempt_count = 0
        async def commit_with_occasional_locks():
            nonlocal attempt_count
            attempt_count += 1
            # Lock on every 3rd call
            if attempt_count % 3 == 1:
                raise OperationalError("database is locked", None, None)
        
        db.commit = AsyncMock(side_effect=commit_with_occasional_locks)
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # Simulate 5 concurrent worker status updates
        tasks = [
            manager._update_worker_status(
                active=True,
                worker_id=f"worker-{i}",
                task_id=f"task-{i}",
                project_id=f"proj-{i}",
                started_at=datetime.now(timezone.utc)
            )
            for i in range(5)
        ]
        
        # All should complete successfully despite locks
        await asyncio.gather(*tasks)
        
        # Verify worker status was updated
        assert status_obj.active is True
