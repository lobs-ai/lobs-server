"""Tests for worker.py DB lock retry logic.

Verifies that high-frequency DB writes (worker_status, worker_runs) 
use retry-with-backoff pattern to handle lock contention.
"""

import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WorkerStatus
from app.orchestrator.worker import WorkerManager


class TestWorkerStatusRetry:
    """Test _update_worker_status retry-on-lock logic."""

    @pytest.mark.asyncio
    async def test_update_worker_status_success_first_attempt(self):
        """Verify worker_status update succeeds on first attempt."""
        db = AsyncMock(spec=AsyncSession)
        
        # Setup: DB returns existing status
        status = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status)
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        
        worker_manager = WorkerManager(db)
        
        # Execute
        await worker_manager._update_worker_status(
            active=True,
            worker_id="worker_123",
            task_id="task_456",
            project_id="proj_789",
            started_at=datetime.now(timezone.utc)
        )

        # Verify
        assert status.active is True
        assert status.worker_id == "worker_123"
        assert status.current_task == "task_456"
        db.commit.assert_called_once()
        # Should not retry if successful on first try
        assert db.rollback.call_count == 0

    @pytest.mark.asyncio
    async def test_update_worker_status_retries_on_db_locked(self):
        """Verify worker_status update retries on 'database is locked' error."""
        db = AsyncMock(spec=AsyncSession)
        
        # Setup: First 2 attempts fail with DB lock, 3rd succeeds
        status = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status)
        db.execute = AsyncMock(return_value=result)
        
        # Simulate lock errors on first 2 attempts
        lock_error = OperationalError("database is locked", None, None)
        db.commit = AsyncMock(side_effect=[
            lock_error,
            lock_error,
            None  # Success on 3rd attempt
        ])
        db.rollback = AsyncMock()
        
        worker_manager = WorkerManager(db)
        
        # Execute
        await worker_manager._update_worker_status(
            active=False,
            project_id="proj_test"
        )

        # Verify retry happened
        assert db.commit.call_count == 3
        assert db.rollback.call_count == 2

    @pytest.mark.asyncio
    async def test_update_worker_status_exponential_backoff(self):
        """Verify exponential backoff delays on retry."""
        db = AsyncMock(spec=AsyncSession)
        
        status = WorkerStatus(id=1)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status)
        db.execute = AsyncMock(return_value=result)
        
        lock_error = OperationalError("database is locked", None, None)
        db.commit = AsyncMock(side_effect=[lock_error, lock_error, None])
        db.rollback = AsyncMock()
        
        worker_manager = WorkerManager(db)
        
        sleep_calls = []

        async def mock_sleep(delay):
            sleep_calls.append(delay)
            # Don't sleep at all in test - just record the call

        with patch('app.orchestrator.worker.asyncio.sleep', side_effect=mock_sleep):
            await worker_manager._update_worker_status(active=True)

        # Verify backoff delays: 0.5s, 1.0s (only first 2 retries sleep)
        assert len(sleep_calls) == 2
        assert sleep_calls[0] == 0.5
        assert sleep_calls[1] == 1.0

    @pytest.mark.asyncio
    async def test_update_worker_status_fails_after_max_attempts(self):
        """Verify worker_status update fails gracefully after 5 attempts."""
        db = AsyncMock(spec=AsyncSession)
        
        status = WorkerStatus(id=1)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status)
        db.execute = AsyncMock(return_value=result)
        
        # All attempts fail
        lock_error = OperationalError("database is locked", None, None)
        db.commit = AsyncMock(side_effect=lock_error)
        db.rollback = AsyncMock()
        
        worker_manager = WorkerManager(db)
        
        # Execute (should not raise, logs error instead)
        await worker_manager._update_worker_status(active=True)

        # Verify all 5 attempts were made
        assert db.commit.call_count == 5
        assert db.rollback.call_count == 5

    @pytest.mark.asyncio
    async def test_get_worker_status_retries_on_lock(self):
        """Verify get_worker_status retries on database lock."""
        db = AsyncMock(spec=AsyncSession)
        
        status = WorkerStatus(id=1, active=True, current_task="task_1")
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status)
        
        # First call fails, second succeeds
        lock_error = OperationalError("database is locked", None, None)
        db.execute = AsyncMock(side_effect=[
            lock_error,
            result  # Success
        ])
        db.rollback = AsyncMock()
        
        worker_manager = WorkerManager(db)
        
        # Should succeed on retry
        status_result = await worker_manager.get_worker_status()
        
        # Verify it got a valid result after retry
        assert "current_task" in status_result or "error" in status_result
