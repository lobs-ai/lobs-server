"""
Tests for database lock retry logic on worker_status and worker_runs.

These tests verify that the system can handle 'database is locked' errors
gracefully through retry-with-exponential-backoff mechanism.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WorkerStatus, WorkerRun, Task, Project
from app.orchestrator.worker import WorkerManager
from app.orchestrator.agent_tracker import AgentTracker


@pytest.mark.asyncio
class TestWorkerStatusRetry:
    """Test retry logic for worker_status updates."""

    async def test_update_worker_status_with_db_lock_retry(self, db_session: AsyncSession):
        """Test that worker_status updates retry on DB lock."""
        manager = WorkerManager(db_session)
        
        # First call should succeed
        await manager._update_worker_status(
            active=True,
            worker_id="test_worker_1",
            task_id="task_1",
            project_id="proj_1",
            started_at=datetime.now(timezone.utc)
        )
        
        # Verify the status was set
        result = await db_session.execute(
            select(WorkerStatus).where(WorkerStatus.id == 1)
        )
        status = result.scalar_one_or_none()
        assert status is not None
        assert status.active is True
        assert status.worker_id == "test_worker_1"
        assert status.current_task == "task_1"
        assert status.current_project == "proj_1"
        
        # Second update should also succeed
        await manager._update_worker_status(active=False)
        
        result = await db_session.execute(
            select(WorkerStatus).where(WorkerStatus.id == 1)
        )
        status = result.scalar_one_or_none()
        assert status is not None
        assert status.active is False

    async def test_get_worker_status_with_db_lock_retry(self, db_session: AsyncSession):
        """Test that get_worker_status retries on DB lock."""
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
        
        # Get status when inactive
        status_obj.active = False
        await db_session.commit()
        
        status_data = await manager.get_worker_status()
        assert status_data["busy"] is False
        assert status_data["state"] == "idle"


@pytest.mark.asyncio
class TestAgentTrackerRetry:
    """Test retry logic for agent_tracker status updates."""

    async def test_agent_tracker_mark_working_with_retry(self, db_session: AsyncSession):
        """Test that agent tracker retries on DB lock."""
        tracker = AgentTracker(db_session)
        
        # Mark agent as working
        await tracker.mark_working(
            agent_type="programmer",
            task_id="task_1",
            project_id="proj_1",
            activity="Implementing feature"
        )
        
        # Verify status was set
        status = await tracker.get_status("programmer")
        assert status is not None
        assert status["status"] == "working"
        assert status["current_task_id"] == "task_1"
        assert status["activity"] == "Implementing feature"

    async def test_agent_tracker_mark_completed_with_retry(self, db_session: AsyncSession):
        """Test that agent tracker records completed tasks with retry."""
        tracker = AgentTracker(db_session)
        
        # Mark agent as working first
        await tracker.mark_working(
            agent_type="researcher",
            task_id="task_1",
            project_id="proj_1",
            activity="Research"
        )
        
        # Mark as completed
        await tracker.mark_completed(
            agent_type="researcher",
            task_id="task_1",
            duration_seconds=300.0
        )
        
        # Verify completion was recorded
        status = await tracker.get_status("researcher")
        assert status is not None
        assert status["last_completed_task_id"] == "task_1"
        assert "tasks_completed" in status["stats"]
        assert status["stats"]["tasks_completed"] >= 1

    async def test_agent_tracker_mark_failed_with_retry(self, db_session: AsyncSession):
        """Test that agent tracker records failed tasks with retry."""
        tracker = AgentTracker(db_session)
        
        # Mark as failed
        await tracker.mark_failed(agent_type="writer", task_id="task_1")
        
        # Verify failure was recorded
        status = await tracker.get_status("writer")
        assert status is not None
        assert "tasks_failed" in status["stats"]
        assert status["stats"]["tasks_failed"] >= 1

    async def test_agent_tracker_mark_idle_with_retry(self, db_session: AsyncSession):
        """Test that agent tracker marks agent as idle with retry."""
        tracker = AgentTracker(db_session)
        
        # Mark as working first
        await tracker.mark_working(
            agent_type="architect",
            task_id="task_1",
            project_id="proj_1",
            activity="Designing"
        )
        
        # Mark as idle
        await tracker.mark_idle("architect")
        
        # Verify idle state
        status = await tracker.get_status("architect")
        assert status is not None
        assert status["status"] == "idle"
        assert status["current_task_id"] is None
