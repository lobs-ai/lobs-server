"""Tests for worker router endpoint retry-on-lock logic."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WorkerStatus as WorkerStatusModel, WorkerRun as WorkerRunModel
from app.schemas import WorkerStatusUpdate, WorkerRunCreate
from app.routers.worker import update_worker_status, create_worker_run


class TestWorkerRouterRetry:
    """Test retry-on-lock behavior for worker router endpoints."""

    @pytest.mark.asyncio
    async def test_update_worker_status_endpoint_success_first_attempt(self):
        """Test successful update on first attempt."""
        db = AsyncMock(spec=AsyncSession)
        
        # Mock the execute call to return a status
        status_obj = WorkerStatusModel(id=1, active=False, tasks_completed=0, input_tokens=0, output_tokens=0)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.refresh = AsyncMock()
        
        status_update = WorkerStatusUpdate(active=True, worker_id="worker-123")
        
        # Call the endpoint
        response = await update_worker_status(status_update, db)
        
        # Verify commit was called exactly once
        assert db.commit.call_count == 1
        assert db.rollback.call_count == 0
        assert status_obj.active is True
        assert status_obj.worker_id == "worker-123"

    @pytest.mark.asyncio
    async def test_update_worker_status_endpoint_retries_on_lock(self):
        """Test that update retries when database is locked."""
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatusModel(id=1, active=False, tasks_completed=0, input_tokens=0, output_tokens=0)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Fail first 2 times, succeed on 3rd
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        db.refresh = AsyncMock()
        
        status_update = WorkerStatusUpdate(active=True, worker_id="worker-123")
        
        # Call the endpoint
        response = await update_worker_status(status_update, db)
        
        # Verify retry behavior
        assert db.commit.call_count == 3  # 2 failures + 1 success
        assert db.rollback.call_count == 2  # 2 rollbacks for failures
        assert status_obj.active is True

    @pytest.mark.asyncio
    async def test_update_worker_status_endpoint_creates_new_if_missing(self):
        """Test that a new status record is created if one doesn't exist."""
        db = AsyncMock(spec=AsyncSession)
        
        # Return None initially (no status record)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        db.execute = AsyncMock(return_value=result)
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.refresh = AsyncMock()
        
        status_update = WorkerStatusUpdate(active=True)
        
        # Call the endpoint
        await update_worker_status(status_update, db)
        
        # Verify that db.add was called (new record created)
        assert db.add.call_count == 1
        assert db.commit.call_count == 1

    @pytest.mark.asyncio
    async def test_create_worker_run_endpoint_success_first_attempt(self):
        """Test successful worker run creation on first attempt."""
        db = AsyncMock(spec=AsyncSession)
        
        # Mock the database operations
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        
        # Mock refresh to simulate auto-increment id assignment
        async def mock_refresh(obj):
            obj.id = 1
        
        db.refresh = mock_refresh
        
        run_create = WorkerRunCreate(
            worker_id="worker-123",
            task_id="task-1",
            started_at=datetime.now(timezone.utc),
            source="orchestrator-gateway",
            model="gpt-4",
            agent_type="programmer"
        )
        
        # Call the endpoint
        response = await create_worker_run(run_create, db)
        
        # Verify commit was called exactly once
        assert db.commit.call_count == 1
        assert db.rollback.call_count == 0
        assert db.add.call_count == 1

    @pytest.mark.asyncio
    async def test_create_worker_run_endpoint_retries_on_lock(self):
        """Test that create_worker_run retries when database is locked."""
        db = AsyncMock(spec=AsyncSession)
        
        # Mock the database operations
        db.add = MagicMock()
        
        # Fail first 2 times, succeed on 3rd
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        # Mock refresh to simulate auto-increment id assignment
        async def mock_refresh(obj):
            obj.id = 1
        
        db.refresh = mock_refresh
        
        run_create = WorkerRunCreate(
            worker_id="worker-123",
            task_id="task-1",
            started_at=datetime.now(timezone.utc),
            source="orchestrator-gateway",
            model="gpt-4",
            agent_type="programmer"
        )
        
        # Call the endpoint
        response = await create_worker_run(run_create, db)
        
        # Verify retry behavior
        assert db.commit.call_count == 3  # 2 failures + 1 success
        assert db.rollback.call_count == 2  # 2 rollbacks for failures

    @pytest.mark.asyncio
    async def test_update_worker_status_endpoint_exponential_backoff(self):
        """Test that backoff delays increase exponentially."""
        import asyncio
        
        db = AsyncMock(spec=AsyncSession)
        
        status_obj = WorkerStatusModel(id=1, active=False, tasks_completed=0, input_tokens=0, output_tokens=0)
        result = AsyncMock()
        result.scalar_one_or_none = MagicMock(return_value=status_obj)
        db.execute = AsyncMock(return_value=result)
        
        # Fail 3 times to test backoff delays
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            Exception("database is locked"),
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        db.refresh = AsyncMock()
        
        status_update = WorkerStatusUpdate(active=True)
        
        # Mock asyncio.sleep to verify it's called with correct delays
        original_sleep = asyncio.sleep
        sleep_calls = []
        
        async def mock_sleep(delay):
            sleep_calls.append(delay)
        
        with patch('asyncio.sleep', side_effect=mock_sleep):
            await update_worker_status(status_update, db)
        
        # Verify backoff delays: 0.5, 1.0, 1.5 seconds
        assert sleep_calls == [0.5, 1.0, 1.5], f"Expected [0.5, 1.0, 1.5], got {sleep_calls}"

    @pytest.mark.asyncio
    async def test_create_worker_run_endpoint_exponential_backoff(self):
        """Test that create_worker_run has exponential backoff delays."""
        import asyncio
        
        db = AsyncMock(spec=AsyncSession)
        
        db.add = MagicMock()
        
        # Fail 3 times to test backoff delays
        db.commit = AsyncMock(side_effect=[
            Exception("database is locked"),
            Exception("database is locked"),
            Exception("database is locked"),
            None  # Success
        ])
        db.rollback = AsyncMock()
        
        # Mock refresh to simulate auto-increment id assignment
        async def mock_refresh(obj):
            obj.id = 1
        
        db.refresh = mock_refresh
        
        run_create = WorkerRunCreate(
            worker_id="worker-123",
            task_id="task-1",
            started_at=datetime.now(timezone.utc),
            source="orchestrator-gateway",
            model="gpt-4",
            agent_type="programmer"
        )
        
        # Mock asyncio.sleep to verify it's called with correct delays
        sleep_calls = []
        
        async def mock_sleep(delay):
            sleep_calls.append(delay)
        
        with patch('asyncio.sleep', side_effect=mock_sleep):
            await create_worker_run(run_create, db)
        
        # Verify backoff delays: 0.5, 1.0, 1.5 seconds
        assert sleep_calls == [0.5, 1.0, 1.5], f"Expected [0.5, 1.0, 1.5], got {sleep_calls}"
