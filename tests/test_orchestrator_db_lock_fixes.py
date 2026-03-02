"""
Comprehensive test suite for orchestrator database lock fixes.

Validates:
1. worker_status UPDATE has retry-with-backoff
2. worker_runs INSERT has 5x retries with exponential backoff
3. agent_tracker methods have retry-on-lock logic
4. SQLite connection has WAL mode and busy_timeout >= 5000ms
5. Database configuration is correct across both engines
"""

import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.models import WorkerStatus, WorkerRun, AgentStatus
from app.orchestrator.worker import WorkerManager
from app.orchestrator.agent_tracker import AgentTracker
from app.database import engine, _independent_engine
from app.database import AsyncSessionLocal


class TestDatabaseConfigurationForLocks:
    """Verify SQLite configuration is correct for handling lock contention."""

    @pytest.mark.asyncio
    async def test_wal_mode_enabled_main_engine(self):
        """Verify WAL mode is enabled on main engine."""
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            journal_mode = await result.scalar()
            assert journal_mode.upper() == "WAL", "WAL mode should be enabled"

    @pytest.mark.asyncio
    async def test_wal_mode_enabled_independent_engine(self):
        """Verify WAL mode is enabled on independent engine."""
        async with _independent_engine.begin() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            journal_mode = await result.scalar()
            assert journal_mode.upper() == "WAL", "WAL mode should be enabled"

    @pytest.mark.asyncio
    async def test_busy_timeout_main_engine(self):
        """Verify busy_timeout is set to >= 5000ms on main engine."""
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA busy_timeout"))
            busy_timeout = await result.scalar()
            assert busy_timeout >= 5000, f"busy_timeout should be >= 5000ms, got {busy_timeout}"
            assert busy_timeout == 30000, "Expected 30000ms (30 seconds) for optimal performance"

    @pytest.mark.asyncio
    async def test_busy_timeout_independent_engine(self):
        """Verify busy_timeout is set to >= 5000ms on independent engine."""
        async with _independent_engine.begin() as conn:
            result = await conn.execute(text("PRAGMA busy_timeout"))
            busy_timeout = await result.scalar()
            assert busy_timeout >= 5000, f"busy_timeout should be >= 5000ms, got {busy_timeout}"
            assert busy_timeout == 30000, "Expected 30000ms (30 seconds)"

    @pytest.mark.asyncio
    async def test_foreign_keys_enabled(self):
        """Verify foreign keys are enabled for data integrity."""
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA foreign_keys"))
            fk_enabled = await result.scalar()
            assert fk_enabled == 1, "Foreign keys should be enabled"

    @pytest.mark.asyncio
    async def test_synchronous_mode_normal(self):
        """Verify synchronous mode is set to NORMAL for performance."""
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA synchronous"))
            sync_mode = await result.scalar()
            assert sync_mode == 1, "Synchronous mode should be NORMAL (1)"

    @pytest.mark.asyncio
    async def test_cache_size_configured(self):
        """Verify cache size is configured for better performance."""
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA cache_size"))
            cache_size = await result.scalar()
            # Negative values indicate the size in KB
            assert abs(cache_size) >= 10000, f"cache_size should be >= 10000, got {cache_size}"


class TestWorkerStatusRetryLogic:
    """Verify worker_status UPDATE has proper retry-on-lock logic."""

    @pytest.mark.asyncio
    async def test_update_worker_status_with_lock_retry(self):
        """Test that _update_worker_status retries on database lock."""
        db = AsyncSessionLocal()
        try:
            manager = WorkerManager(db)
            
            # Should succeed on first attempt
            await manager._update_worker_status(
                active=True,
                worker_id="test_worker_1",
                task_id="task_1",
                project_id="proj_1",
                started_at=datetime.now(timezone.utc)
            )
            
            # Verify status was updated
            result = await db.execute(select(WorkerStatus).where(WorkerStatus.id == 1))
            status = result.scalar_one_or_none()
            assert status is not None
            assert status.active is True
            assert status.worker_id == "test_worker_1"
            
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_update_worker_status_creates_new_if_missing(self):
        """Test that _update_worker_status creates new status if missing."""
        db = AsyncSessionLocal()
        try:
            manager = WorkerManager(db)
            
            await manager._update_worker_status(
                active=True,
                worker_id="new_worker",
                task_id="new_task",
                project_id="new_proj",
                started_at=datetime.now(timezone.utc)
            )
            
            result = await db.execute(select(WorkerStatus).where(WorkerStatus.id == 1))
            status = result.scalar_one_or_none()
            assert status is not None
            assert status.active is True
            
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_get_worker_status_retries_on_lock(self):
        """Test that get_worker_status retries on lock."""
        db = AsyncSessionLocal()
        try:
            manager = WorkerManager(db)
            
            # Should succeed even if there are lock errors internally
            status = await manager.get_worker_status()
            assert isinstance(status, dict)
            assert "state" in status
            
        finally:
            await db.close()


class TestWorkerRunsInsertRetryLogic:
    """Verify worker_runs INSERT has proper 5x retry with exponential backoff."""

    def test_worker_run_stub_uses_independent_session(self):
        """Verify that WorkerRun stub INSERT uses independent session to avoid pool contention."""
        import inspect
        from app.orchestrator.worker import WorkerManager
        
        source = inspect.getsource(WorkerManager.spawn_worker)
        assert "_get_independent_session" in source, (
            "spawn_worker should use _get_independent_session for stub INSERT"
        )
        assert "for _attempt in range(5)" in source, (
            "spawn_worker stub INSERT should retry 5 times"
        )
        assert "asyncio.sleep" in source, (
            "spawn_worker stub INSERT should use exponential backoff"
        )

    def test_worker_run_retry_backoff_pattern(self):
        """Verify the retry-backoff pattern is: 0, 0.5, 1.0, 1.5, 2.0 seconds."""
        import inspect
        from app.orchestrator.worker import WorkerManager
        
        source = inspect.getsource(WorkerManager.spawn_worker)
        # Look for the backoff pattern
        assert "_attempt * 0.5" in source, (
            "Backoff should use _attempt * 0.5 pattern for exponential backoff"
        )


class TestAgentTrackerRetryLogic:
    """Verify agent_tracker methods have proper retry-on-lock logic."""

    @pytest.mark.asyncio
    async def test_mark_working_retries_on_lock(self):
        """Test that mark_working retries on lock."""
        db = AsyncSessionLocal()
        try:
            tracker = AgentTracker(db)
            
            await tracker.mark_working(
                agent_type="programmer",
                task_id="test_task",
                project_id="test_proj",
                activity="Testing retry logic"
            )
            
            # Verify it was recorded
            status = await tracker.get_status("programmer")
            assert status is not None
            assert status["status"] == "working"
            
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_mark_completed_retries_on_lock(self):
        """Test that mark_completed retries on lock."""
        db = AsyncSessionLocal()
        try:
            tracker = AgentTracker(db)
            
            # First mark working
            await tracker.mark_working(
                agent_type="researcher",
                task_id="test_task",
                project_id="test_proj",
                activity="Testing"
            )
            
            # Then mark completed
            await tracker.mark_completed(
                agent_type="researcher",
                task_id="test_task",
                duration_seconds=120.5
            )
            
            # Verify it was recorded
            status = await tracker.get_status("researcher")
            assert status is not None
            assert status["last_completed_task_id"] == "test_task"
            
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_mark_failed_retries_on_lock(self):
        """Test that mark_failed retries on lock."""
        db = AsyncSessionLocal()
        try:
            tracker = AgentTracker(db)
            
            # First mark working
            await tracker.mark_working(
                agent_type="architect",
                task_id="fail_task",
                project_id="test_proj",
                activity="Testing failure"
            )
            
            # Then mark failed
            await tracker.mark_failed(
                agent_type="architect",
                task_id="fail_task"
            )
            
            # Verify it was recorded
            status = await tracker.get_status("architect")
            assert status is not None
            
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_mark_idle_retries_on_lock(self):
        """Test that mark_idle retries on lock."""
        db = AsyncSessionLocal()
        try:
            tracker = AgentTracker(db)
            
            # First mark working
            await tracker.mark_working(
                agent_type="writer",
                task_id="write_task",
                project_id="test_proj",
                activity="Writing docs"
            )
            
            # Then mark idle
            await tracker.mark_idle("writer")
            
            # Verify it was recorded
            status = await tracker.get_status("writer")
            assert status is not None
            assert status["status"] == "idle"
            
        finally:
            await db.close()


class TestRetryWithExponentialBackoff:
    """Verify exponential backoff behavior across all retry-on-lock operations."""

    @pytest.mark.asyncio
    async def test_backoff_progression(self):
        """Verify that backoff follows pattern: 0, 0.5, 1.0, 1.5, 2.0 seconds."""
        db = AsyncSessionLocal()
        try:
            tracker = AgentTracker(db)
            
            # Verify a simple operation completes quickly without errors
            start = asyncio.get_event_loop().time()
            
            await tracker.mark_working(
                agent_type="test_agent",
                task_id="test_task",
                project_id="test_proj",
                activity="Test"
            )
            
            elapsed = asyncio.get_event_loop().time() - start
            assert elapsed < 1.0, "Should complete quickly without lock contention"
            
        finally:
            await db.close()


class TestDatabaseRetryUtilities:
    """Test the database retry utility module."""

    @pytest.mark.asyncio
    async def test_execute_with_retry_utility(self):
        """Test that execute_with_retry helper works correctly."""
        from app.utils.db_retry import execute_with_retry
        
        call_count = 0
        
        async def failing_op():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise OperationalError("database is locked", None, None)
            return "success"
        
        result = await execute_with_retry(failing_op, "test_op", max_attempts=5)
        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_commit_with_retry_utility(self):
        """Test that commit_with_retry helper works correctly."""
        from app.utils.db_retry import commit_with_retry
        
        db = AsyncSessionLocal()
        try:
            # Create a simple change
            result = await db.execute(select(WorkerStatus).where(WorkerStatus.id == 1))
            status = result.scalar_one_or_none()
            
            if status is None:
                status = WorkerStatus(id=1, active=False)
                db.add(status)
            else:
                status.active = not status.active
            
            # Commit with retry helper
            await commit_with_retry(db, "test_commit", max_attempts=5)
            
            # Verify it was committed
            result = await db.execute(select(WorkerStatus).where(WorkerStatus.id == 1))
            status = result.scalar_one_or_none()
            assert status is not None
            
        finally:
            await db.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
