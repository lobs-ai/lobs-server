"""Tests for worker_status update retry-on-lock logic."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestrator.worker_manager import WorkerManager
from app.models import WorkerStatus


class TestWorkerStatusRetry:
    """Test retry-on-lock behavior for worker_status updates."""

    @pytest.mark.asyncio
    async def test_update_worker_status_success_first_attempt(self):
        """Test successful update on first attempt."""
        # Create a mock session
        db = AsyncMock(spec=AsyncSession)
        
        # Mock the execute call to return a status
        status_obj = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # Call the method
        await manager._update_worker_status(
            active=True,
            worker_id="worker-123",
            task_id="task-1",
            project_id="proj-1",
            started_at=datetime.now(timezone.utc)
        )
        
        # Verify commit was called exactly once
        assert db.commit.call_count == 1
        assert db.rollback.call_count == 0
        assert status_obj.active is True
        assert status_obj.worker_id == "worker-123"

    @pytest.mark.asyncio
    async def test_update_worker_status_retries_on_lock(self):
        """Test that update retries when database is locked."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Fail first 2 times, succeed on 3rd
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # Call the method
        await manager._update_worker_status(
            active=True,
            worker_id="worker-123",
            task_id="task-1",
            project_id="proj-1",
            started_at=datetime.now(timezone.utc)
        )
        
        # Verify retry behavior
        assert db.commit.call_count == 3  # 2 failures + 1 success
        assert db.rollback.call_count == 2  # 2 rollbacks for failures
        assert status_obj.active is True

    @pytest.mark.asyncio
    async def test_update_worker_status_gives_up_after_5_attempts(self):
        """Test that update gives up after 5 attempts."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Always fail
        db.commit = AsyncMock(side_effect=Exception("database is locked"))
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # Call the method - should not raise, but give up gracefully
        await manager._update_worker_status(
            active=True,
            worker_id="worker-123",
            task_id="task-1",
            project_id="proj-1",
            started_at=datetime.now(timezone.utc)
        )
        
        # Verify all 5 attempts were made
        assert db.commit.call_count == 5
        # 5 rollbacks: 4 in the loop + 1 in the final except block
        assert db.rollback.call_count == 5

    @pytest.mark.asyncio
    async def test_update_worker_status_inactive(self):
        """Test updating worker status to inactive."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(id=1, active=True, worker_id="worker-123")
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # Call the method to mark as inactive
        await manager._update_worker_status(active=False)
        
        # Verify inactive state
        assert status_obj.active is False
        assert status_obj.worker_id is None
        assert status_obj.current_task is None
        assert db.commit.call_count == 1

    @pytest.mark.asyncio
    async def test_update_worker_status_creates_new_if_missing(self):
        """Test that a new status record is created if one doesn't exist."""
        db = AsyncMock(spec=AsyncSession)
        
        # Return None initially (no status record)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        db.execute = AsyncMock(return_value=result)
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # Call the method
        await manager._update_worker_status(
            active=True,
            worker_id="worker-123",
            task_id="task-1",
            project_id="proj-1",
            started_at=datetime.now(timezone.utc)
        )
        
        # Verify that db.add was called (new record created)
        assert db.add.call_count == 1
        assert db.commit.call_count == 1

    @pytest.mark.asyncio
    async def test_update_worker_status_exponential_backoff(self):
        """Test that backoff delays increase exponentially."""
        import asyncio
        
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Fail 3 times to test backoff delays
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            Exception("database is locked"),
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # Mock asyncio.sleep to verify it's called with correct delays
        original_sleep = asyncio.sleep
        sleep_calls = []
        
        async def mock_sleep(delay):
            sleep_calls.append(delay)
        
        with patch('asyncio.sleep', side_effect=mock_sleep):
            await manager._update_worker_status(active=True)
        
        # Verify backoff delays: 0.5, 1.0, 1.5 seconds
        assert sleep_calls == [0.5, 1.0, 1.5], f"Expected [0.5, 1.0, 1.5], got {sleep_calls}"
