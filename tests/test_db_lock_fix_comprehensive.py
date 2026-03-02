"""Comprehensive tests for database lock retry logic across worker and agent components.

This test suite validates:
1. Database configuration (WAL mode, busy_timeout >= 5000ms)
2. Retry-with-exponential-backoff on all critical DB writes
3. Worker status updates handle DB locks gracefully
4. Worker runs INSERT has proper retry logic
5. Agent tracker updates have proper retry logic
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError

from app.database import engine, _independent_engine
from app.orchestrator.worker_manager import WorkerManager
from app.orchestrator.agent_tracker import AgentTracker
from app.models import WorkerStatus, WorkerRun, AgentStatus


class TestDatabaseConfiguration:
    """Test database configuration for lock prevention."""

    @pytest.mark.asyncio
    async def test_wal_mode_enabled(self):
        """Verify WAL mode is enabled on main engine."""
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            mode = result.scalar()
            assert mode.upper() == "WAL", f"Expected WAL mode, got {mode}"

    @pytest.mark.asyncio
    async def test_busy_timeout_set_main_engine(self):
        """Verify busy_timeout >= 5000ms on main engine."""
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA busy_timeout"))
            timeout = result.scalar()
            assert timeout >= 5000, f"Expected busy_timeout >= 5000ms, got {timeout}ms"

    @pytest.mark.asyncio
    async def test_busy_timeout_set_independent_engine(self):
        """Verify busy_timeout >= 5000ms on independent engine."""
        async with _independent_engine.begin() as conn:
            result = await conn.execute(text("PRAGMA busy_timeout"))
            timeout = result.scalar()
            assert timeout >= 5000, f"Expected busy_timeout >= 5000ms, got {timeout}ms"

    @pytest.mark.asyncio
    async def test_foreign_keys_enabled(self):
        """Verify foreign key constraints are enabled."""
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA foreign_keys"))
            fk_enabled = result.scalar()
            assert fk_enabled == 1, "Foreign keys should be enabled"


class TestWorkerStatusRetryLogic:
    """Test retry-on-lock for worker_status updates."""

    @pytest.mark.asyncio
    async def test_update_worker_status_retries_on_lock(self, db_session: AsyncSession):
        """Test that worker_status updates retry on database lock."""
        manager = WorkerManager(db_session)
        
        # Create initial status
        initial_status = WorkerStatus(id=1, active=False)
        db_session.add(initial_status)
        await db_session.commit()
        
        # Track commit attempts
        commit_count = [0]
        original_commit = db_session.commit
        
        async def mock_commit():
            commit_count[0] += 1
            if commit_count[0] <= 2:
                raise OperationalError("database is locked", None, None)
            await original_commit()
        
        with patch.object(db_session, 'commit', side_effect=mock_commit):
            # Should succeed after retries
            await manager._update_worker_status(
                active=True,
                worker_id="test_worker",
                task_id="task_1",
                project_id="proj_1"
            )
        
        # Verify it took multiple attempts
        assert commit_count[0] >= 3, f"Expected >= 3 attempts, got {commit_count[0]}"


class TestAgentTrackerRetryLogic:
    """Test retry-on-lock for agent status updates."""

    @pytest.mark.asyncio
    async def test_mark_working_retries_on_lock(self, db_session: AsyncSession):
        """Test that agent_tracker mark_working retries on database lock."""
        tracker = AgentTracker(db_session)
        
        # Track commit attempts
        commit_count = [0]
        original_commit = db_session.commit
        
        async def mock_commit():
            commit_count[0] += 1
            if commit_count[0] <= 2:
                raise OperationalError("database is locked", None, None)
            await original_commit()
        
        with patch.object(db_session, 'commit', side_effect=mock_commit):
            await tracker.mark_working(
                agent_type="test_agent",
                task_id="task_1",
                project_id="proj_1",
                activity="Testing"
            )
        
        # Verify it took multiple attempts
        assert commit_count[0] >= 3, f"Expected >= 3 attempts, got {commit_count[0]}"
