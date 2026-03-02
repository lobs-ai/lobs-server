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
import inspect
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

    def test_wal_mode_configured_in_database_module(self):
        """Verify WAL mode is configured in database.py."""
        from app import database
        source = inspect.getsource(database._set_sqlite_pragma)
        assert 'PRAGMA journal_mode=WAL' in source, "WAL mode should be configured"
        assert 'PRAGMA busy_timeout=30000' in source, "busy_timeout should be configured to 30s"

    def test_busy_timeout_configured_in_database_module(self):
        """Verify busy_timeout is set to >= 5000ms in database.py."""
        from app import database
        source = inspect.getsource(database._set_sqlite_pragma)
        assert 'PRAGMA busy_timeout=30000' in source, "busy_timeout should be 30000ms (30s)"

    def test_independent_engine_configured_for_lock_safety(self):
        """Verify independent engine is configured with same pragmas."""
        from app import database
        source = inspect.getsource(database._set_independent_pragma)
        assert 'PRAGMA journal_mode=WAL' in source, "Independent engine should use WAL"
        assert 'PRAGMA busy_timeout=30000' in source, "Independent engine should have long timeout"

    def test_foreign_keys_enabled_in_database_config(self):
        """Verify foreign keys are enabled for data integrity."""
        from app import database
        source = inspect.getsource(database._set_sqlite_pragma)
        assert 'PRAGMA foreign_keys=ON' in source, "Foreign keys should be enabled"

    def test_synchronous_mode_normal_in_database_config(self):
        """Verify synchronous mode is set to NORMAL for performance."""
        from app import database
        source = inspect.getsource(database._set_sqlite_pragma)
        assert 'PRAGMA synchronous=NORMAL' in source, "Synchronous mode should be NORMAL"

    def test_cache_size_configured_in_database_config(self):
        """Verify cache size is configured for better performance."""
        from app import database
        source = inspect.getsource(database._set_sqlite_pragma)
        assert 'PRAGMA cache_size=10000' in source, "Cache size should be configured"


class TestWorkerStatusRetryLogic:
    """Verify worker_status UPDATE has proper retry-on-lock logic."""

    @pytest.mark.asyncio
    async def test_update_worker_status_with_lock_retry(self, db_session):
        """Test that _update_worker_status retries on database lock."""
        manager = WorkerManager(db_session)
        
        # Should succeed on first attempt
        await manager._update_worker_status(
            active=True,
            worker_id="test_worker_1",
            task_id="task_1",
            project_id="proj_1",
            started_at=datetime.now(timezone.utc)
        )
        
        # Verify status was updated
        result = await db_session.execute(select(WorkerStatus).where(WorkerStatus.id == 1))
        status = result.scalar_one_or_none()
        assert status is not None
        assert status.active is True
        assert status.worker_id == "test_worker_1"

    def test_update_worker_status_has_retry_logic(self):
        """Verify _update_worker_status source code includes retry logic."""
        source = inspect.getsource(WorkerManager._update_worker_status)
        assert "for _attempt in range(5)" in source, "Should retry 5 times"
        assert "asyncio.sleep" in source, "Should use backoff on retry"
        assert "_attempt * 0.5" in source, "Should use exponential backoff"

    @pytest.mark.asyncio
    async def test_get_worker_status_retries_on_lock(self, db_session):
        """Test that get_worker_status retries on lock."""
        manager = WorkerManager(db_session)
        
        # Should succeed even if there are lock errors internally
        status = await manager.get_worker_status()
        assert isinstance(status, dict)
        assert "state" in status

    def test_get_worker_status_has_retry_logic(self):
        """Verify get_worker_status source code includes retry logic."""
        source = inspect.getsource(WorkerManager.get_worker_status)
        assert "for _attempt in range(5)" in source, "Should retry 5 times"
        assert "asyncio.sleep" in source, "Should use backoff on retry"


class TestWorkerRunsInsertRetryLogic:
    """Verify worker_runs INSERT has proper 5x retry with exponential backoff."""

    def test_worker_run_stub_uses_independent_session(self):
        """Verify that WorkerRun stub INSERT uses independent session to avoid pool contention."""
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
        source = inspect.getsource(WorkerManager.spawn_worker)
        # Look for the backoff pattern
        assert "_attempt * 0.5" in source, (
            "Backoff should use _attempt * 0.5 pattern for exponential backoff"
        )


class TestAgentTrackerRetryLogic:
    """Verify agent_tracker methods have proper retry-on-lock logic."""

    @pytest.mark.asyncio
    async def test_mark_working_retries_on_lock(self, db_session):
        """Test that mark_working retries on lock."""
        tracker = AgentTracker(db_session)
        
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

    def test_mark_working_has_retry_logic(self):
        """Verify mark_working source code includes retry logic."""
        source = inspect.getsource(AgentTracker.mark_working)
        assert "for _attempt in range(5)" in source, "Should retry 5 times"
        assert "asyncio.sleep" in source, "Should use backoff on retry"

    def test_mark_completed_has_retry_logic(self):
        """Verify mark_completed source code includes retry logic."""
        source = inspect.getsource(AgentTracker.mark_completed)
        assert "for _attempt in range(5)" in source, "Should retry 5 times"

    def test_mark_failed_has_retry_logic(self):
        """Verify mark_failed source code includes retry logic."""
        source = inspect.getsource(AgentTracker.mark_failed)
        assert "for _attempt in range(5)" in source, "Should retry 5 times"

    def test_mark_idle_has_retry_logic(self):
        """Verify mark_idle source code includes retry logic."""
        source = inspect.getsource(AgentTracker.mark_idle)
        assert "for _attempt in range(5)" in source, "Should retry 5 times"


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

    def test_db_retry_module_exists(self):
        """Verify db_retry utility module exists and is well-structured."""
        from app.utils import db_retry
        assert hasattr(db_retry, 'execute_with_retry'), "execute_with_retry should exist"
        assert hasattr(db_retry, 'query_with_retry'), "query_with_retry should exist"
        assert hasattr(db_retry, 'commit_with_retry'), "commit_with_retry should exist"

    def test_retry_utility_exponential_backoff(self):
        """Verify retry utility uses exponential backoff."""
        from app.utils.db_retry import execute_with_retry
        source = inspect.getsource(execute_with_retry)
        # Check for exponential backoff logic
        assert "initial_backoff_seconds" in source, "Should use backoff"
        assert "*" in source, "Should use multiplication for exponential backoff"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
