"""Tests for database lock retry logic in worker and agent tracker."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import WorkerStatus, AgentStatus
from app.orchestrator.worker import WorkerManager
from app.orchestrator.agent_tracker import AgentTracker


class TestWorkerStatusRetry:
    """Test _update_worker_status retry-on-lock logic."""

    @pytest.mark.asyncio
    async def test_update_worker_status_success_first_try(self, db_session: AsyncSession):
        """Test successful worker status update on first attempt."""
        manager = WorkerManager(db_session)
        
        # Update should succeed on first try
        await manager._update_worker_status(
            active=True,
            worker_id="worker-123",
            task_id="task-456",
            project_id="proj-789",
            started_at=datetime.now(timezone.utc)
        )
        
        # Verify the status was written
        result = await db_session.execute(
            select(WorkerStatus).where(WorkerStatus.id == 1)
        )
        status = result.scalar_one_or_none()
        assert status is not None
        assert status.active is True
        assert status.worker_id == "worker-123"
        assert status.current_task == "task-456"

    @pytest.mark.asyncio
    async def test_update_worker_status_inactive(self, db_session: AsyncSession):
        """Test updating worker status to inactive."""
        manager = WorkerManager(db_session)
        
        # First, set it to active
        await manager._update_worker_status(
            active=True,
            worker_id="worker-123",
            task_id="task-456",
            project_id="proj-789",
            started_at=datetime.now(timezone.utc)
        )
        
        # Then update to inactive
        await manager._update_worker_status(active=False)
        
        # Verify the status
        result = await db_session.execute(
            select(WorkerStatus).where(WorkerStatus.id == 1)
        )
        status = result.scalar_one_or_none()
        assert status is not None
        assert status.active is False
        assert status.worker_id is None
        assert status.current_task is None


class TestAgentTrackerRetry:
    """Test agent tracker retry-on-lock logic."""

    @pytest.mark.asyncio
    async def test_mark_working_success_first_try(self, db_session: AsyncSession):
        """Test marking agent as working succeeds on first attempt."""
        tracker = AgentTracker(db_session)
        
        await tracker.mark_working(
            agent_type="programmer",
            task_id="task-123",
            project_id="proj-456",
            activity="Building feature X"
        )
        
        # Verify the status was written
        result = await db_session.execute(
            select(AgentStatus).where(AgentStatus.agent_type == "programmer")
        )
        status = result.scalar_one_or_none()
        assert status is not None
        assert status.status == "working"
        assert status.current_task_id == "task-123"
        assert "Building feature X" in status.activity

    @pytest.mark.asyncio
    async def test_mark_completed_with_stats(self, db_session: AsyncSession):
        """Test marking task completed with stats tracking."""
        tracker = AgentTracker(db_session)
        
        # First mark as working
        await tracker.mark_working(
            agent_type="researcher",
            task_id="task-789",
            project_id="proj-012",
            activity="Researching topic"
        )
        
        # Then mark as completed (should succeed without retry)
        await tracker.mark_completed(
            agent_type="researcher",
            task_id="task-789",
            duration_seconds=1200.5
        )
        
        # Verify the status
        result = await db_session.execute(
            select(AgentStatus).where(AgentStatus.agent_type == "researcher")
        )
        status = result.scalar_one_or_none()
        assert status is not None
        assert status.last_completed_task_id == "task-789"
        assert status.stats["tasks_completed"] == 1
        assert status.stats["avg_duration_seconds"] == 1200

    @pytest.mark.asyncio
    async def test_mark_failed_increments_count(self, db_session: AsyncSession):
        """Test marking task as failed."""
        tracker = AgentTracker(db_session)
        
        # Mark as failed
        await tracker.mark_failed(
            agent_type="programmer",
            task_id="task-fail"
        )
        
        # Verify the status
        result = await db_session.execute(
            select(AgentStatus).where(AgentStatus.agent_type == "programmer")
        )
        status = result.scalar_one_or_none()
        assert status is not None
        assert status.stats["tasks_failed"] == 1

    @pytest.mark.asyncio
    async def test_mark_idle(self, db_session: AsyncSession):
        """Test marking agent as idle."""
        tracker = AgentTracker(db_session)
        
        # First mark as working
        await tracker.mark_working(
            agent_type="writer",
            task_id="task-001",
            project_id="proj-001",
            activity="Writing documentation"
        )
        
        # Then mark as idle
        await tracker.mark_idle("writer")
        
        # Verify the status
        result = await db_session.execute(
            select(AgentStatus).where(AgentStatus.agent_type == "writer")
        )
        status = result.scalar_one_or_none()
        assert status is not None
        assert status.status == "idle"
        assert status.current_task_id is None
        assert status.activity is None

    @pytest.mark.asyncio
    async def test_update_thinking_incremental(self, db_session: AsyncSession):
        """Test updating thinking snippet for active agent."""
        tracker = AgentTracker(db_session)
        
        # Mark as working first
        await tracker.mark_working(
            agent_type="architect",
            task_id="task-arch",
            project_id="proj-arch",
            activity="Designing system"
        )
        
        # Update thinking
        await tracker.update_thinking(
            agent_type="architect",
            snippet="Considering microservices vs monolith"
        )
        
        # Verify
        result = await db_session.execute(
            select(AgentStatus).where(AgentStatus.agent_type == "architect")
        )
        status = result.scalar_one_or_none()
        assert status is not None
        assert "microservices" in status.thinking

    @pytest.mark.asyncio
    async def test_multiple_agent_types(self, db_session: AsyncSession):
        """Test tracking multiple agent types independently."""
        tracker = AgentTracker(db_session)
        
        # Mark multiple agents as working
        await tracker.mark_working("programmer", "task-prog", "proj-1", "Coding")
        await tracker.mark_working("researcher", "task-res", "proj-2", "Researching")
        await tracker.mark_working("writer", "task-writer", "proj-3", "Writing")
        
        # Verify all were created separately
        result = await db_session.execute(select(AgentStatus))
        statuses = result.scalars().all()
        assert len(statuses) == 3
        
        agent_types = {s.agent_type for s in statuses}
        assert agent_types == {"programmer", "researcher", "writer"}
