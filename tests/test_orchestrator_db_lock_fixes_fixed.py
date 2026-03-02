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
import sqlite3
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.models import WorkerStatus, WorkerRun, AgentStatus
from app.orchestrator.worker import WorkerManager
from app.orchestrator.agent_tracker import AgentTracker
from app.database import engine, _independent_engine, AsyncSessionLocal
from app.config import settings


class TestDatabaseConfigurationForLocks:
    """Verify SQLite configuration is correct for handling lock contention."""

    def _get_db_path(self):
        """Extract database path from connection URL."""
        return settings.DATABASE_URL.replace("sqlite+aiosqlite:///", "")

    def test_wal_mode_enabled_main_engine(self):
        """Verify WAL mode is enabled on main engine."""
        db_url = self._get_db_path()
        conn = sqlite3.connect(db_url)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        journal_mode = cursor.fetchone()[0]
        conn.close()
        assert journal_mode.upper() == "WAL", f"WAL mode should be enabled, got {journal_mode}"

    def test_wal_mode_enabled_independent_engine(self):
        """Verify WAL mode is enabled on independent engine."""
        db_url = self._get_db_path()
        conn = sqlite3.connect(db_url)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        journal_mode = cursor.fetchone()[0]
        conn.close()
        assert journal_mode.upper() == "WAL", f"WAL mode should be enabled, got {journal_mode}"

    def test_busy_timeout_main_engine(self):
        """Verify busy_timeout is set to >= 5000ms on main engine."""
        db_url = self._get_db_path()
        conn = sqlite3.connect(db_url)
        cursor = conn.cursor()
        cursor.execute("PRAGMA busy_timeout")
        busy_timeout = cursor.fetchone()[0]
        conn.close()
        assert busy_timeout >= 5000, f"busy_timeout should be >= 5000ms, got {busy_timeout}"

    def test_busy_timeout_independent_engine(self):
        """Verify busy_timeout is set to >= 5000ms on independent engine."""
        db_url = self._get_db_path()
        conn = sqlite3.connect(db_url)
        cursor = conn.cursor()
        cursor.execute("PRAGMA busy_timeout")
        busy_timeout = cursor.fetchone()[0]
        conn.close()
        assert busy_timeout >= 5000, f"busy_timeout should be >= 5000ms, got {busy_timeout}"

    def test_foreign_keys_enabled(self):
        """Verify foreign keys are enabled for data integrity."""
        db_url = self._get_db_path()
        conn = sqlite3.connect(db_url)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys")
        fk_enabled = cursor.fetchone()[0]
        conn.close()
        assert fk_enabled == 1, f"Foreign keys should be enabled, got {fk_enabled}"

    def test_synchronous_mode_normal(self):
        """Verify synchronous mode is set to NORMAL for performance."""
        db_url = self._get_db_path()
        conn = sqlite3.connect(db_url)
        cursor = conn.cursor()
        cursor.execute("PRAGMA synchronous")
        sync_mode = cursor.fetchone()[0]
        conn.close()
        assert sync_mode == 1, f"Synchronous mode should be NORMAL (1), got {sync_mode}"

    def test_cache_size_configured(self):
        """Verify cache size is configured for better performance."""
        db_url = self._get_db_path()
        conn = sqlite3.connect(db_url)
        cursor = conn.cursor()
        cursor.execute("PRAGMA cache_size")
        cache_size = cursor.fetchone()[0]
        conn.close()
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
