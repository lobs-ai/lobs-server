"""
Tests for database lock retry logic in WorkerManager.

These tests verify that the get_worker_status method in WorkerManager
can handle 'database is locked' errors gracefully through retry-with-exponential-backoff.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError

from app.models import WorkerStatus
from app.orchestrator.worker_manager import WorkerManager


@pytest.mark.asyncio
class TestWorkerManagerRetry:
    """Test retry logic for WorkerManager.get_worker_status."""

    async def test_get_worker_status_succeeds_without_lock(self, db_session: AsyncSession):
        """Test that get_worker_status works normally without DB lock."""
        manager = WorkerManager(db_session)
        
        # Create initial status
        status_obj = WorkerStatus(
            id=1,
            active=True,
            worker_id="test_worker",
            current_task="task_1",
            current_project="proj_1",
        )
        db_session.add(status_obj)
        await db_session.commit()
        
        # Get status should work
        status_data = await manager.get_worker_status()
        assert status_data["busy"] is True
        assert status_data["worker_id"] == "test_worker"
        assert status_data["current_task"] == "task_1"
        assert status_data["current_project"] == "proj_1"
        assert status_data["state"] == "working"

    async def test_get_worker_status_returns_idle_when_no_status(self, db_session: AsyncSession):
        """Test that get_worker_status returns idle when no status exists."""
        manager = WorkerManager(db_session)
        
        # Don't create any status
        status_data = await manager.get_worker_status()
        assert status_data["busy"] is False
        assert status_data["state"] == "idle"
        assert status_data["active_count"] == 0
        assert status_data["current_task"] is None

    async def test_get_worker_status_returns_idle_when_inactive(self, db_session: AsyncSession):
        """Test that get_worker_status returns idle when status is inactive."""
        manager = WorkerManager(db_session)
        
        # Create inactive status
        status_obj = WorkerStatus(
            id=1,
            active=False,
            worker_id=None,
        )
        db_session.add(status_obj)
        await db_session.commit()
        
        # Get status should return idle
        status_data = await manager.get_worker_status()
        assert status_data["busy"] is False
        assert status_data["state"] == "idle"

    async def test_get_worker_status_includes_started_at(self, db_session: AsyncSession):
        """Test that get_worker_status includes started_at timestamp."""
        manager = WorkerManager(db_session)
        
        start_time = datetime.now(timezone.utc)
        status_obj = WorkerStatus(
            id=1,
            active=True,
            worker_id="test_worker",
            current_task="task_1",
            current_project="proj_1",
            started_at=start_time,
        )
        db_session.add(status_obj)
        await db_session.commit()
        
        # Get status should include started_at
        status_data = await manager.get_worker_status()
        assert status_data["busy"] is True
        assert status_data["started_at"] is not None
        assert "T" in status_data["started_at"]  # ISO format check

    async def test_get_worker_status_gracefully_handles_errors(self, db_session: AsyncSession):
        """Test that get_worker_status gracefully handles DB errors after retries."""
        manager = WorkerManager(db_session)
        
        # Mock the execute method to always fail
        with patch.object(db_session, 'execute') as mock_execute:
            mock_execute.side_effect = OperationalError("database is locked", None, None)
            
            # Should return error response instead of crashing
            status_data = await manager.get_worker_status()
            assert status_data["busy"] is False
            assert "error" in status_data
            
            # Verify it attempted 5 times
            assert mock_execute.call_count == 5

    async def test_get_worker_status_retries_on_operational_error(self, db_session: AsyncSession):
        """Test that get_worker_status retries on OperationalError (database is locked)."""
        manager = WorkerManager(db_session)
        
        # Create status
        status_obj = WorkerStatus(
            id=1,
            active=True,
            worker_id="test_worker",
            current_task="task_1",
            current_project="proj_1",
        )
        db_session.add(status_obj)
        await db_session.commit()
        
        # Track retry attempts
        attempt_count = 0
        original_execute = db_session.execute
        
        async def execute_with_failures(*args, **kwargs):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:  # Fail first 2 times, succeed on 3rd
                raise OperationalError("database is locked", None, None)
            return await original_execute(*args, **kwargs)
        
        with patch.object(db_session, 'execute', side_effect=execute_with_failures):
            # Should succeed after retries
            status_data = await manager.get_worker_status()
            assert status_data["busy"] is True
            assert status_data["worker_id"] == "test_worker"
            assert attempt_count >= 3

    async def test_get_worker_status_retries_gracefully_fail(self, db_session: AsyncSession):
        """Test that get_worker_status gracefully fails after max retries."""
        manager = WorkerManager(db_session)
        
        # Track retry attempts
        attempt_count = 0
        
        async def execute_always_fails(*args, **kwargs):
            nonlocal attempt_count
            attempt_count += 1
            raise OperationalError("database is locked", None, None)
        
        with patch.object(db_session, 'execute', side_effect=execute_always_fails):
            # Should fail gracefully after 5 attempts
            status_data = await manager.get_worker_status()
            assert status_data["busy"] is False
            assert "error" in status_data
            assert attempt_count == 5

    async def test_get_worker_status_with_multiple_concurrent_calls(self, db_session: AsyncSession):
        """Test that get_worker_status works correctly with concurrent calls."""
        manager = WorkerManager(db_session)
        
        # Create status
        status_obj = WorkerStatus(
            id=1,
            active=True,
            worker_id="test_worker",
            current_task="task_1",
            current_project="proj_1",
        )
        db_session.add(status_obj)
        await db_session.commit()
        
        # Make multiple concurrent calls
        tasks = [manager.get_worker_status() for _ in range(5)]
        results = await asyncio.gather(*tasks)
        
        # All should succeed with same data
        for result in results:
            assert result["busy"] is True
            assert result["worker_id"] == "test_worker"
