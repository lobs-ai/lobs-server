"""Tests for database lock retry logic and configuration."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import engine, _independent_engine


class TestDatabaseConfiguration:
    """Verify SQLite WAL mode and busy_timeout are correctly configured."""

    @pytest.mark.asyncio
    async def test_wal_mode_enabled_main_engine(self):
        """Verify WAL mode is enabled in main database configuration."""
        async with engine.begin() as conn:
            result = await conn.exec_driver_sql("PRAGMA journal_mode")
            mode = result.scalar()
            assert mode.upper() == "WAL", f"Expected WAL mode, got {mode}"

    @pytest.mark.asyncio
    async def test_wal_mode_enabled_independent_engine(self):
        """Verify WAL mode is enabled in independent engine."""
        async with _independent_engine.begin() as conn:
            result = await conn.exec_driver_sql("PRAGMA journal_mode")
            mode = result.scalar()
            assert mode.upper() == "WAL", f"Expected WAL mode, got {mode}"

    @pytest.mark.asyncio
    async def test_busy_timeout_set_main_engine(self):
        """Verify busy_timeout >= 5000ms for main engine."""
        async with engine.begin() as conn:
            result = await conn.exec_driver_sql("PRAGMA busy_timeout")
            timeout_ms = result.scalar()
            assert timeout_ms >= 5000, f"Expected busy_timeout >= 5000ms, got {timeout_ms}ms"

    @pytest.mark.asyncio
    async def test_busy_timeout_set_independent_engine(self):
        """Verify busy_timeout >= 5000ms for independent engine."""
        async with _independent_engine.begin() as conn:
            result = await conn.exec_driver_sql("PRAGMA busy_timeout")
            timeout_ms = result.scalar()
            assert timeout_ms >= 5000, f"Expected busy_timeout >= 5000ms, got {timeout_ms}ms"


class TestRetryLogicPresence:
    """Validate that retry logic exists in all critical database operations."""

    def test_worker_status_update_has_retry_logic(self):
        """Verify _update_worker_status contains retry loop."""
        from app.orchestrator.worker import WorkerManager
        import inspect
        
        source = inspect.getsource(WorkerManager._update_worker_status)
        assert "for _attempt in range(5)" in source, "_update_worker_status must have 5-attempt retry loop"
        assert "await asyncio.sleep(_attempt * 0.5)" in source, "_update_worker_status must have exponential backoff"

    def test_record_worker_run_has_retry_logic(self):
        """Verify _record_worker_run commit has retry loop."""
        from app.orchestrator.worker import WorkerManager
        import inspect
        
        source = inspect.getsource(WorkerManager._record_worker_run)
        assert "for _attempt in range(5)" in source, "_record_worker_run must have 5-attempt retry loop"
        assert "await asyncio.sleep(_attempt * 0.5)" in source, "_record_worker_run must have exponential backoff"

    def test_persist_reflection_output_has_retry_logic(self):
        """Verify _persist_reflection_output has retry loop."""
        from app.orchestrator.worker import WorkerManager
        import inspect
        
        source = inspect.getsource(WorkerManager._persist_reflection_output)
        assert "for _attempt in range(5)" in source, "_persist_reflection_output must have 5-attempt retry loop"
        assert "await asyncio.sleep(_attempt * 0.5)" in source, "_persist_reflection_output must have exponential backoff"

    def test_agent_tracker_mark_working_has_retry_logic(self):
        """Verify AgentTracker.mark_working has retry loop."""
        from app.orchestrator.agent_tracker import AgentTracker
        import inspect
        
        source = inspect.getsource(AgentTracker.mark_working)
        assert "for _attempt in range(5)" in source, "mark_working must have 5-attempt retry loop"
        assert "await asyncio.sleep(_attempt * 0.5)" in source, "mark_working must have exponential backoff"

    def test_agent_tracker_mark_completed_has_retry_logic(self):
        """Verify AgentTracker.mark_completed has retry loop."""
        from app.orchestrator.agent_tracker import AgentTracker
        import inspect
        
        source = inspect.getsource(AgentTracker.mark_completed)
        assert "for _attempt in range(5)" in source, "mark_completed must have 5-attempt retry loop"
        assert "await asyncio.sleep(_attempt * 0.5)" in source, "mark_completed must have exponential backoff"

    def test_agent_tracker_mark_failed_has_retry_logic(self):
        """Verify AgentTracker.mark_failed has retry loop."""
        from app.orchestrator.agent_tracker import AgentTracker
        import inspect
        
        source = inspect.getsource(AgentTracker.mark_failed)
        assert "for _attempt in range(5)" in source, "mark_failed must have 5-attempt retry loop"
        assert "await asyncio.sleep(_attempt * 0.5)" in source, "mark_failed must have exponential backoff"

    def test_agent_tracker_mark_idle_has_retry_logic(self):
        """Verify AgentTracker.mark_idle has retry loop."""
        from app.orchestrator.agent_tracker import AgentTracker
        import inspect
        
        source = inspect.getsource(AgentTracker.mark_idle)
        assert "for _attempt in range(5)" in source, "mark_idle must have 5-attempt retry loop"
        assert "await asyncio.sleep(_attempt * 0.5)" in source, "mark_idle must have exponential backoff"

    def test_monitor_enhanced_commits_have_retry_logic(self):
        """Verify monitor_enhanced.py has retry loops on all commits."""
        from app.orchestrator.monitor_enhanced import MonitorEnhanced
        import inspect
        
        source = inspect.getsource(MonitorEnhanced)
        # Count occurrences of "for _attempt in range(5):" for db.commit()
        retry_loops = source.count("for _attempt in range(5):")
        assert retry_loops >= 4, f"monitor_enhanced should have at least 4 retry loops, found {retry_loops}"
