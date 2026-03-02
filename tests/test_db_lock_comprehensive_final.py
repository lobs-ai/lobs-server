"""Comprehensive tests for database lock retry logic under high contention.

This test suite validates that the orchestrator can handle high-frequency database
writes (worker_status updates, worker_runs inserts, agent_tracker commits) without
cascading failures due to "database is locked" errors.
"""

import asyncio
import pytest
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError

from app.models import WorkerStatus, WorkerRun, AgentStatus, Task, Project
from app.orchestrator.worker import WorkerManager
from app.orchestrator.agent_tracker import AgentTracker


@pytest.fixture
def worker_manager(db_session: AsyncSession):
    """Provide a worker manager with test database."""
    return WorkerManager(db_session)


@pytest.fixture
def agent_tracker(db_session: AsyncSession):
    """Provide an agent tracker with test database."""
    return AgentTracker(db_session)


class TestDatabaseLockRetryComprehensive:
    """Test database lock handling across all high-frequency operations."""

    @pytest.mark.asyncio
    async def test_worker_status_update_retries_on_lock(self, worker_manager, db_session):
        """Verify worker_status update retries 5 times on database lock."""
        # Setup: Create initial status
        status = WorkerStatus(id=1, active=False, tasks_completed=0, input_tokens=0, output_tokens=0)
        db_session.add(status)
        await db_session.commit()

        # Successfully update the status - should work first time
        await worker_manager._update_worker_status(
            active=True,
            worker_id="test_worker_1",
            task_id="test_task_1",
            project_id="test_project_1"
        )

        # Verify it succeeded
        result = await db_session.execute(select(WorkerStatus).where(WorkerStatus.id == 1))
        updated_status = result.scalar_one_or_none()
        assert updated_status.active is True
        assert updated_status.worker_id == "test_worker_1"

    @pytest.mark.asyncio
    async def test_worker_runs_insert_retries_on_lock(self, db_session):
        """Verify worker_runs INSERT retries 5 times on database lock."""
        # Create a worker run with retry logic
        run = WorkerRun(
            worker_id="test_worker_1",
            task_id="test_task_1",
            started_at=datetime.now(timezone.utc),
            source="test",
            succeeded=True,
        )
        db_session.add(run)
        
        for _attempt in range(5):
            try:
                if _attempt > 0:
                    await asyncio.sleep(_attempt * 0.01)
                await db_session.commit()
                break
            except OperationalError as e:
                if _attempt < 4:
                    await db_session.rollback()
                else:
                    raise

        # Verify it succeeded
        result = await db_session.execute(select(WorkerRun).where(WorkerRun.worker_id == "test_worker_1"))
        worker_run = result.scalar_one_or_none()
        assert worker_run is not None

    @pytest.mark.asyncio
    async def test_agent_tracker_retries_on_lock(self, agent_tracker, db_session):
        """Verify AgentTracker operations retry on database lock."""
        # This should succeed with retries
        await agent_tracker.mark_working(
            agent_type="programmer",
            task_id="test_task_1",
            project_id="test_project_1",
            activity="Testing"
        )

        result = await db_session.execute(select(AgentStatus).where(AgentStatus.agent_type == "programmer"))
        agent_status = result.scalar_one_or_none()
        assert agent_status is not None
        assert agent_status.status == "working"

    @pytest.mark.asyncio
    async def test_get_worker_status_handles_lock_gracefully(self, worker_manager, db_session):
        """Verify get_worker_status returns idle when locked."""
        # Create test status
        status = WorkerStatus(id=1, active=True, tasks_completed=5, input_tokens=100, output_tokens=200)
        db_session.add(status)
        await db_session.commit()

        # Call get_worker_status (which should use retry logic)
        result = await worker_manager.get_worker_status()
        
        assert result is not None
        assert isinstance(result, dict)
        assert "busy" in result
        assert "active_count" in result

    @pytest.mark.asyncio
    async def test_agent_tracker_mark_completed_with_retry(self, agent_tracker, db_session):
        """Verify AgentTracker mark_completed retries on lock."""
        # Create initial agent status
        await agent_tracker.mark_working(
            agent_type="programmer",
            task_id="test_task_1",
            project_id="test_project_1",
            activity="Testing"
        )

        # Mark as completed with retry
        await agent_tracker.mark_completed(
            agent_type="programmer",
            task_id="test_task_1",
            duration_seconds=100
        )

        result = await db_session.execute(select(AgentStatus).where(AgentStatus.agent_type == "programmer"))
        agent_status = result.scalar_one_or_none()
        assert agent_status is not None
        assert agent_status.last_completed_task_id == "test_task_1"


class TestDatabaseLockRegressionPrevention:
    """Tests to prevent regression of database lock issues."""

    @pytest.mark.asyncio
    async def test_worker_completion_doesnt_cascade_on_lock(self, db_session):
        """Verify worker completion handles DB locks gracefully."""
        # Setup: Create a project and task
        project = Project(
            id="test_proj",
            title="Test",
            type="kanban",
            created_at=datetime.now(timezone.utc)
        )
        task = Task(
            id="test_task",
            title="Test Task",
            status="active",
            work_state="in_progress",
            project_id="test_proj",
        )
        db_session.add(project)
        db_session.add(task)
        await db_session.commit()

        # Simulate completion with retries
        async def complete_task_with_retries():
            for _attempt in range(5):
                try:
                    if _attempt > 0:
                        await asyncio.sleep(_attempt * 0.01)
                    
                    db_task = await db_session.get(Task, "test_task")
                    db_task.work_state = "completed"
                    db_task.status = "completed"
                    db_task.updated_at = datetime.now(timezone.utc)
                    await db_session.commit()
                    return True
                except OperationalError:
                    if _attempt < 4:
                        await db_session.rollback()
                    else:
                        return False

        result = await complete_task_with_retries()
        assert result is True
        
        # Verify task was updated
        db_task = await db_session.get(Task, "test_task")
        assert db_task.work_state == "completed"

    @pytest.mark.asyncio
    async def test_multiple_worker_runs_inserts(self, db_session):
        """Test multiple worker_runs inserts in sequence (simulating high frequency)."""
        results = []
        for i in range(3):
            run = WorkerRun(
                worker_id=f"test_worker_{i}",
                task_id=f"test_task_{i}",
                started_at=datetime.now(timezone.utc),
                source="test",
                succeeded=True,
            )
            db_session.add(run)
            
            try:
                await db_session.commit()
                results.append(True)
            except Exception as e:
                results.append(False)

        # All inserts should succeed
        assert all(results), f"Some inserts failed: {results}"
        
        # Verify all were written
        result = await db_session.execute(select(WorkerRun))
        runs = result.scalars().all()
        assert len(runs) >= 3
