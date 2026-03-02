"""Comprehensive test for database lock retry logic and contention resilience."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestrator.worker import WorkerManager
from app.orchestrator.agent_tracker import AgentTracker
from app.models import WorkerStatus, AgentStatus


class TestDatabaseLockRetryUnderContention:
    """Test database lock retry behavior under simulated contention."""

    @pytest.mark.asyncio
    async def test_worker_manager_handles_lock_contention_on_status_update(self):
        """Test that WorkerManager._update_worker_status recovers from lock contention."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Simulate lock contention: fail 3 times with "database is locked", then succeed
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            Exception("database is locked"),
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # Should succeed despite 3 lock failures
        await manager._update_worker_status(
            active=True,
            worker_id="worker-1",
            task_id="task-1",
            project_id="proj-1",
            started_at=datetime.now(timezone.utc)
        )
        
        # Verify it retried and succeeded
        assert db.commit.call_count == 4  # 3 failures + 1 success
        assert status_obj.active is True

    @pytest.mark.asyncio
    async def test_agent_tracker_handles_lock_contention_on_work_mark(self):
        """Test that AgentTracker.mark_working recovers from lock contention."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = AgentStatus(agent_type="programmer", status="idle", stats={})
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Simulate lock contention: fail twice with database lock, then succeed
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        db.flush = AsyncMock()
        
        tracker = AgentTracker(db)
        
        # Should succeed despite lock failures
        await tracker.mark_working(
            agent_type="programmer",
            task_id="task-1",
            project_id="proj-1",
            activity="Running tests"
        )
        
        # Verify it retried and succeeded
        assert db.commit.call_count == 3  # 2 failures + 1 success
        assert status_obj.status == "working"

    @pytest.mark.asyncio
    async def test_exponential_backoff_increases_delays(self):
        """Test that exponential backoff delays increase between retries."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(id=1, active=False)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Fail 4 times to test all backoff delays
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            Exception("database is locked"),
            Exception("database is locked"),
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        # Record actual sleep calls
        sleep_calls = []
        
        async def mock_sleep(delay):
            sleep_calls.append(delay)
        
        with patch('asyncio.sleep', side_effect=mock_sleep):
            await manager._update_worker_status(active=True)
        
        # Verify exponential backoff: 0.5, 1.0, 1.5, 2.0 seconds
        assert sleep_calls == [0.5, 1.0, 1.5, 2.0], f"Expected [0.5, 1.0, 1.5, 2.0], got {sleep_calls}"

    @pytest.mark.asyncio
    async def test_lock_failure_recovery_preserves_data_integrity(self):
        """Test that lock failures don't corrupt data - rollbacks preserve consistency."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatus(
            id=1,
            active=False,
            worker_id=None,
            current_task="task-old",
            current_project="proj-old"
        )
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Fail once, then succeed
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        manager = WorkerManager(db)
        
        new_task_id = "task-new"
        new_project_id = "proj-new"
        
        await manager._update_worker_status(
            active=True,
            worker_id="worker-1",
            task_id=new_task_id,
            project_id=new_project_id,
            started_at=datetime.now(timezone.utc)
        )
        
        # Verify data was properly updated despite lock
        assert status_obj.active is True
        assert status_obj.current_task == new_task_id
        assert status_obj.current_project == new_project_id
        # Verify rollback was called on failure to maintain consistency
        assert db.rollback.call_count == 1

    @pytest.mark.asyncio
    async def test_all_critical_operations_have_retry_logic(self):
        """Test that all critical database write operations have retry logic."""
        import inspect
        
        # List of critical database write methods
        methods_to_check = [
            (WorkerManager, '_update_worker_status'),
            (WorkerManager, '_record_worker_run'),
            (WorkerManager, '_persist_reflection_output'),
            (AgentTracker, 'mark_working'),
            (AgentTracker, 'mark_completed'),
            (AgentTracker, 'mark_failed'),
            (AgentTracker, 'mark_idle'),
            (AgentTracker, 'update_thinking'),
        ]
        
        for cls, method_name in methods_to_check:
            method = getattr(cls, method_name)
            source = inspect.getsource(method)
            
            # Check for retry loop
            assert "for _attempt in range(5)" in source, (
                f"{cls.__name__}.{method_name} missing 5-attempt retry loop"
            )
            # Check for exponential backoff
            assert "await asyncio.sleep(_attempt * 0.5)" in source, (
                f"{cls.__name__}.{method_name} missing exponential backoff"
            )


class TestDatabaseConfigurationUnderContention:
    """Test that database configuration is optimal for handling contention."""

    @pytest.mark.asyncio
    async def test_main_engine_has_sufficient_busy_timeout(self):
        """Verify main engine busy_timeout is set to reasonable value (>= 10s)."""
        from app.database import engine
        
        async with engine.begin() as conn:
            result = await conn.exec_driver_sql("PRAGMA busy_timeout")
            timeout_ms = result.scalar()
            
            # Should be at least 10 seconds
            assert timeout_ms >= 10000, (
                f"busy_timeout should be >= 10000ms for handling contention, got {timeout_ms}ms"
            )

    @pytest.mark.asyncio
    async def test_independent_engine_has_sufficient_busy_timeout(self):
        """Verify independent engine busy_timeout is set to reasonable value (>= 15s)."""
        from app.database import _independent_engine
        
        async with _independent_engine.begin() as conn:
            result = await conn.exec_driver_sql("PRAGMA busy_timeout")
            timeout_ms = result.scalar()
            
            # Should be at least 15 seconds for fire-and-forget operations
            assert timeout_ms >= 15000, (
                f"busy_timeout should be >= 15000ms for independent operations, got {timeout_ms}ms"
            )

    @pytest.mark.asyncio
    async def test_wal_mode_prevents_reader_writer_conflicts(self):
        """Verify WAL mode is enabled to prevent reader-writer conflicts."""
        from app.database import engine, _independent_engine
        
        async with engine.begin() as conn:
            result = await conn.exec_driver_sql("PRAGMA journal_mode")
            mode = result.scalar()
            assert mode.upper() == "WAL", f"Main engine should use WAL mode, got {mode}"
        
        async with _independent_engine.begin() as conn:
            result = await conn.exec_driver_sql("PRAGMA journal_mode")
            mode = result.scalar()
            assert mode.upper() == "WAL", f"Independent engine should use WAL mode, got {mode}"
