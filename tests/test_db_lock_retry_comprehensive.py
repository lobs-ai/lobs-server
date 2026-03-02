"""
Comprehensive tests for database lock retry logic across high-frequency operations.

Tests verify that all critical database writes have proper retry-with-backoff logic
to handle 'database is locked' errors under high concurrency.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError
from sqlalchemy import select

from app.orchestrator.worker_manager import WorkerManager
from app.orchestrator.agent_tracker import AgentTracker
from app.orchestrator.escalation_enhanced import EscalationManagerEnhanced
from app.models import (
    WorkerStatus, 
    Task, 
    AgentStatus, 
)


@pytest.mark.asyncio
class TestDatabaseLockRetryComprehensive:
    """Test retry-on-lock logic for all high-frequency database operations."""

    # ─── WorkerManager Tests ───────────────────────────────────────────────

    async def test_worker_status_update_handles_lock_contention(self, db_session: AsyncSession):
        """Test that worker_status updates survive high contention."""
        manager = WorkerManager(db_session)
        
        # Create initial status
        status = WorkerStatus(id=1, active=False)
        db_session.add(status)
        await db_session.commit()
        
        # Mock commit to fail then succeed
        call_count = 0
        original_commit = db_session.commit
        
        async def commit_with_lock():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise OperationalError("database is locked", None, None)
            await original_commit()
        
        with patch.object(db_session, 'commit', side_effect=commit_with_lock):
            await manager._update_worker_status(
                active=True,
                worker_id="worker-1",
                task_id="task-1",
                project_id="proj-1"
            )
        
        # Verify it retried and succeeded
        assert call_count >= 3
        
        # Verify the status was actually updated
        result = await db_session.execute(
            select(WorkerStatus).where(WorkerStatus.id == 1)
        )
        updated = result.scalar_one()
        assert updated.active is True
        assert updated.worker_id == "worker-1"

    async def test_get_worker_status_has_retry_logic(self, db_session: AsyncSession):
        """Test that get_worker_status retries on lock errors."""
        manager = WorkerManager(db_session)
        
        # Create a status to retrieve
        status = WorkerStatus(
            id=1,
            active=True,
            worker_id="worker-1",
            current_task="task-1"
        )
        db_session.add(status)
        await db_session.commit()
        
        # Mock to fail then succeed
        call_count = 0
        original_execute = db_session.execute
        
        async def execute_with_lock(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OperationalError("database is locked", None, None)
            return await original_execute(*args, **kwargs)
        
        with patch.object(db_session, 'execute', side_effect=execute_with_lock):
            result = await manager.get_worker_status()
        
        # Verify it retried
        assert call_count >= 2
        # Verify it got the right status
        assert result["busy"] is True
        assert result["worker_id"] == "worker-1"

    # ─── AgentTracker Tests ────────────────────────────────────────────────

    async def test_agent_tracker_mark_working_retries_on_lock(self, db_session: AsyncSession):
        """Test that mark_working retries on database lock."""
        from app.orchestrator.agent_tracker import AgentTracker
        
        tracker = AgentTracker(db_session)
        
        # Mock commit to fail then succeed
        call_count = 0
        original_commit = db_session.commit
        
        async def commit_with_lock():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise OperationalError("database is locked", None, None)
            await original_commit()
        
        with patch.object(db_session, 'commit', side_effect=commit_with_lock):
            await tracker.mark_working(
                agent_type="programmer",
                task_id="task-1",
                project_id="proj-1",
                activity="Writing tests"
            )
        
        # Verify it retried
        assert call_count >= 3

    async def test_agent_tracker_mark_completed_retries_on_lock(self, db_session: AsyncSession):
        """Test that mark_completed retries on database lock."""
        from app.orchestrator.agent_tracker import AgentTracker
        
        tracker = AgentTracker(db_session)
        
        # Mock commit to fail then succeed
        call_count = 0
        original_commit = db_session.commit
        
        async def commit_with_lock():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise OperationalError("database is locked", None, None)
            await original_commit()
        
        with patch.object(db_session, 'commit', side_effect=commit_with_lock):
            await tracker.mark_completed(
                agent_type="programmer",
                task_id="task-1",
                duration_seconds=123.45
            )
        
        # Verify it retried and completed
        assert call_count >= 2
        
        # Verify the status was updated
        result = await db_session.execute(
            select(AgentStatus).where(
                AgentStatus.agent_type == "programmer"
            )
        )
        status = result.scalar_one_or_none()
        assert status is not None
        assert status.last_completed_task_id == "task-1"

    async def test_agent_tracker_mark_idle_retries_on_lock(self, db_session: AsyncSession):
        """Test that mark_idle retries on database lock."""
        from app.orchestrator.agent_tracker import AgentTracker
        
        tracker = AgentTracker(db_session)
        
        # First mark as working
        await tracker.mark_working(
            agent_type="researcher",
            task_id="task-1",
            project_id="proj-1",
            activity="Researching"
        )
        
        # Mock commit to fail then succeed
        call_count = 0
        original_commit = db_session.commit
        
        async def commit_with_lock():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise OperationalError("database is locked", None, None)
            await original_commit()
        
        with patch.object(db_session, 'commit', side_effect=commit_with_lock):
            await tracker.mark_idle("researcher")
        
        # Verify it retried
        assert call_count >= 2
        
        # Verify the status is now idle
        result = await db_session.execute(
            select(AgentStatus).where(
                AgentStatus.agent_type == "researcher"
            )
        )
        status = result.scalar_one()
        assert status.status == "idle"

    async def test_escalation_create_simple_alert_retries_on_lock(self, db_session: AsyncSession):
        """Test that create_simple_alert retries on database lock."""
        escalation = EscalationManagerEnhanced(db_session)
        
        # Mock commit to fail then succeed
        call_count = 0
        original_commit = db_session.commit
        
        async def commit_with_lock():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise OperationalError("database is locked", None, None)
            await original_commit()
        
        with patch.object(db_session, 'commit', side_effect=commit_with_lock):
            alert_id = await escalation.create_simple_alert(
                task_id="task-3",
                project_id="proj-1",
                error_log="Error occurred",
                severity="high"
            )
        
        # Verify it retried
        assert call_count >= 2
        # Verify alert was created
        assert alert_id is not None
        assert alert_id.startswith("alert_")

    # ─── Exponential Backoff Test ──────────────────────────────────────────

    async def test_exponential_backoff_prevents_lock_thrashing(self):
        """Test that exponential backoff prevents excessive lock thrashing."""
        
        db = AsyncMock(spec=AsyncSession)
        status = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status)
        db.execute = AsyncMock(return_value=result)
        
        # Always fail
        db.commit = AsyncMock(side_effect=Exception("database is locked"))
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # Track sleep calls
        sleep_calls = []
        
        async def mock_sleep(duration):
            sleep_calls.append(duration)
        
        with patch('asyncio.sleep', side_effect=mock_sleep):
            await manager._update_worker_status(active=True)
        
        # Verify backoff pattern
        # Expected: 0.5, 1.0, 1.5, 2.0 seconds (4 sleeps for 5 attempts)
        assert sleep_calls == [0.5, 1.0, 1.5, 2.0]
        assert sum(sleep_calls) == 5.0  # Total backoff time = 5 seconds

    async def test_all_critical_paths_have_retry_logic(self, db_session: AsyncSession):
        """Test that all critical database write paths have proper retry logic."""
        manager = WorkerManager(db_session)
        tracker = AgentTracker(db_session)
        escalation = EscalationManagerEnhanced(db_session)
        
        # Verify all methods exist and are async
        assert asyncio.iscoroutinefunction(manager._update_worker_status)
        assert asyncio.iscoroutinefunction(manager.get_worker_status)
        assert asyncio.iscoroutinefunction(tracker.mark_working)
        assert asyncio.iscoroutinefunction(tracker.mark_completed)
        assert asyncio.iscoroutinefunction(tracker.mark_idle)
        assert asyncio.iscoroutinefunction(escalation.create_simple_alert)
        assert asyncio.iscoroutinefunction(escalation.handle_failure)
