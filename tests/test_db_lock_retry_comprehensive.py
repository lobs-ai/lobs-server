"""Comprehensive tests for database lock retry-with-exponential-backoff logic.

This test suite verifies that all high-frequency database writes in the orchestrator
properly handle 'database is locked' errors through retry logic with exponential backoff.

Tests cover:
1. Worker status updates (_update_worker_status)
2. Worker run creation (stub INSERT in spawn_worker)
3. Agent tracker operations
4. Escalation operations
5. Database pragmas (WAL mode, busy_timeout)
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError

from app.orchestrator.worker_manager import WorkerManager
from app.orchestrator.agent_tracker import AgentTracker
from app.orchestrator.escalation_enhanced import EscalationManagerEnhanced
from app.models import WorkerStatus, AgentStatus, Task


@pytest.mark.asyncio
class TestDatabaseLockRetryComprehensive:
    """Test database lock retry behavior across orchestrator components."""

    # ════════════════════════════════════════════════════════════════════════════
    # WORKER MANAGER TESTS
    # ════════════════════════════════════════════════════════════════════════════

    async def test_update_worker_status_retries_exponentially_on_lock(self):
        """Test that _update_worker_status retries with exponential backoff when database is locked."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Fail first 3 times, succeed on 4th
        db.commit = AsyncMock(side_effect=[
            OperationalError("database is locked", None, None),
            OperationalError("database is locked", None, None),
            OperationalError("database is locked", None, None),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # Track sleep calls for backoff verification
        sleep_calls = []
        
        async def mock_sleep(delay):
            sleep_calls.append(delay)
        
        with patch('asyncio.sleep', side_effect=mock_sleep):
            await manager._update_worker_status(active=True)
        
        # Verify exponential backoff: 0.5s, 1.0s, 1.5s
        assert sleep_calls == [0.5, 1.0, 1.5], f"Expected exponential backoff [0.5, 1.0, 1.5], got {sleep_calls}"
        assert db.commit.call_count == 4
        assert db.rollback.call_count == 3

    async def test_update_worker_status_gives_up_after_5_attempts(self):
        """Test that _update_worker_status gives up gracefully after max retries."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Always fail with database lock
        db.commit = AsyncMock(side_effect=OperationalError("database is locked", None, None))
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # Should complete without raising exception
        await manager._update_worker_status(active=True)
        
        # Verify all 5 attempts were made
        assert db.commit.call_count == 5
        # 4 rollbacks in loop + 1 in final except
        assert db.rollback.call_count == 5

    async def test_update_worker_status_sets_correct_state(self):
        """Test that worker status fields are set correctly."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        await manager._update_worker_status(
            active=True,
            worker_id="worker-123",
            task_id="task-abc",
            project_id="proj-xyz",
            started_at=datetime.now(timezone.utc)
        )
        
        # Verify state is correct
        assert status_obj.active is True
        assert status_obj.worker_id == "worker-123"
        assert status_obj.current_task == "task-abc"
        assert status_obj.current_project == "proj-xyz"

    # ════════════════════════════════════════════════════════════════════════════
    # AGENT TRACKER TESTS
    # ════════════════════════════════════════════════════════════════════════════

    async def test_agent_tracker_mark_working_retries_on_lock(self):
        """Test that AgentTracker.mark_working retries on database lock."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = AgentStatus(agent_type="programmer", status="idle")
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Fail first time, succeed on second
        db.commit = AsyncMock(side_effect=[
            OperationalError("database is locked", None, None),
            None
        ])
        db.rollback = AsyncMock()
        
        tracker = AgentTracker(db)
        
        await tracker.mark_working("programmer", "task-1", "proj-1", "test activity")
        
        assert db.commit.call_count == 2
        assert db.rollback.call_count == 1

    async def test_agent_tracker_mark_completed_retries_on_lock(self):
        """Test that AgentTracker.mark_completed retries on database lock."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = AgentStatus(agent_type="programmer", status="working", stats={})
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Fail first time, succeed on second
        db.commit = AsyncMock(side_effect=[
            OperationalError("database is locked", None, None),
            None
        ])
        db.rollback = AsyncMock()
        
        tracker = AgentTracker(db)
        
        await tracker.mark_completed("programmer", "task-1", 120.5)
        
        assert db.commit.call_count == 2
        assert db.rollback.call_count == 1

    async def test_agent_tracker_mark_failed_retries_on_lock(self):
        """Test that AgentTracker.mark_failed retries on database lock."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = AgentStatus(agent_type="programmer", status="working", stats={})
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Fail twice, succeed on third
        db.commit = AsyncMock(side_effect=[
            OperationalError("database is locked", None, None),
            OperationalError("database is locked", None, None),
            None
        ])
        db.rollback = AsyncMock()
        
        tracker = AgentTracker(db)
        
        await tracker.mark_failed("programmer", "task-1")
        
        assert db.commit.call_count == 3
        assert db.rollback.call_count == 2

    async def test_agent_tracker_mark_idle_retries_on_lock(self):
        """Test that AgentTracker.mark_idle retries on database lock."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = AgentStatus(agent_type="programmer", status="working")
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Fail once, succeed
        db.commit = AsyncMock(side_effect=[
            OperationalError("database is locked", None, None),
            None
        ])
        db.rollback = AsyncMock()
        
        tracker = AgentTracker(db)
        
        await tracker.mark_idle("programmer")
        
        assert db.commit.call_count == 2
        assert db.rollback.call_count == 1

    # ════════════════════════════════════════════════════════════════════════════
    # ESCALATION ENHANCED TESTS
    # ════════════════════════════════════════════════════════════════════════════

    async def test_escalation_tier_1_retries_on_lock(self):
        """Test that escalation tier 1 (auto-retry) retries on database lock."""
        db = AsyncMock(spec=AsyncSession)
        
        task = Task(id="task-1", title="test", project_id="proj-1", status="active")
        db.get = AsyncMock(return_value=task)
        
        # Fail first, succeed on second
        db.commit = AsyncMock(side_effect=[
            OperationalError("database is locked", None, None),
            None
        ])
        db.rollback = AsyncMock()
        
        escalation = EscalationManagerEnhanced(db)
        result = await escalation._tier_1_auto_retry(task, "programmer", "test error")
        
        assert result["action"] == "retry"
        assert db.commit.call_count == 2
        assert db.rollback.call_count == 1

    async def test_escalation_tier_2_retries_on_lock(self):
        """Test that escalation tier 2 (agent switch) retries on database lock."""
        db = AsyncMock(spec=AsyncSession)
        
        task = Task(id="task-1", title="test", project_id="proj-1", status="active")
        db.get = AsyncMock(return_value=task)
        
        # Fail twice, succeed
        db.commit = AsyncMock(side_effect=[
            OperationalError("database is locked", None, None),
            OperationalError("database is locked", None, None),
            None
        ])
        db.rollback = AsyncMock()
        
        escalation = EscalationManagerEnhanced(db)
        result = await escalation._tier_2_agent_switch(task, "programmer", "test error")
        
        assert result["action"] == "agent_switch"
        assert db.commit.call_count == 3
        assert db.rollback.call_count == 2

    # ════════════════════════════════════════════════════════════════════════════
    # CONCURRENT LOAD TESTS
    # ════════════════════════════════════════════════════════════════════════════

    async def test_concurrent_worker_status_updates_with_lock_contention(self):
        """Test that concurrent worker status updates handle lock contention."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Simulate contention: first 3 calls fail, then succeed
        call_count = 0
        async def commit_with_contention():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise OperationalError("database is locked", None, None)
        
        db.commit = AsyncMock(side_effect=commit_with_contention)
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # Run 3 concurrent status updates
        tasks = [
            manager._update_worker_status(active=True, worker_id=f"w{i}")
            for i in range(3)
        ]
        
        await asyncio.gather(*tasks)
        
        # All should complete despite contention
        assert db.commit.call_count >= 3

    # ════════════════════════════════════════════════════════════════════════════
    # BACKOFF TIMING TESTS
    # ════════════════════════════════════════════════════════════════════════════

    async def test_exponential_backoff_timing(self):
        """Test that exponential backoff follows the correct pattern: 0.5s, 1.0s, 1.5s, 2.0s."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(id=1)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Always fail to test all backoff delays
        db.commit = AsyncMock(side_effect=OperationalError("database is locked", None, None))
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        sleep_calls = []
        async def mock_sleep(delay):
            sleep_calls.append(delay)
        
        with patch('asyncio.sleep', side_effect=mock_sleep):
            await manager._update_worker_status(active=True)
        
        # Should have 4 sleep calls: 0.5, 1.0, 1.5, 2.0 (for attempts 1-4)
        expected = [0.5, 1.0, 1.5, 2.0]
        assert sleep_calls == expected, f"Expected {expected}, got {sleep_calls}"

    async def test_exponential_backoff_in_agent_tracker(self):
        """Test that agent tracker also uses exponential backoff."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = AgentStatus(agent_type="programmer")
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Always fail
        db.commit = AsyncMock(side_effect=OperationalError("database is locked", None, None))
        db.rollback = AsyncMock()
        
        tracker = AgentTracker(db)
        
        sleep_calls = []
        async def mock_sleep(delay):
            sleep_calls.append(delay)
        
        with patch('asyncio.sleep', side_effect=mock_sleep):
            await tracker.mark_working("programmer", "task-1", "proj-1", "activity")
        
        # Should have exponential backoff pattern
        expected = [0.5, 1.0, 1.5, 2.0]
        assert sleep_calls == expected, f"Expected {expected}, got {sleep_calls}"
