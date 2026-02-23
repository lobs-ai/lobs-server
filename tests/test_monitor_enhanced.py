"""Tests for enhanced monitor functionality."""

import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, WorkerStatus
from app.orchestrator.monitor_enhanced import MonitorEnhanced


class TestMonitorEnhanced:
    """Test enhanced monitoring features."""
    
    @pytest.mark.asyncio
    async def test_detect_stuck_tasks(self, db_session: AsyncSession):
        """Test stuck task detection."""
        monitor = MonitorEnhanced(db_session)
        
        # Create a task stuck in progress
        stuck_time = datetime.now(timezone.utc) - timedelta(minutes=20)
        task = Task(
            id="stuck_task_123",
            title="Stuck task",
            status="active",
            work_state="in_progress",
            project_id="test-project",
            updated_at=stuck_time,
            created_at=stuck_time
        )
        db_session.add(task)
        await db_session.commit()
        
        # Check for stuck tasks
        stuck_tasks = await monitor.check_stuck_tasks()
        
        assert len(stuck_tasks) == 1
        assert stuck_tasks[0]["id"] == "stuck_task_123"
        assert stuck_tasks[0]["severity"] in ["medium", "high", "critical"]
        
        # Task should be marked as blocked
        await db_session.refresh(task)
        assert task.work_state == "blocked"
    
    @pytest.mark.asyncio
    async def test_auto_unblock_completed_dependencies(self, db_session: AsyncSession):
        """Test auto-unblock when dependencies are completed."""
        monitor = MonitorEnhanced(db_session)
        
        # Create dependency task (completed)
        dep_task = Task(
            id="dep_task_123",
            title="Dependency",
            status="completed",
            work_state="completed",
            project_id="test-project",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db_session.add(dep_task)
        
        # Create blocked task
        blocked_task = Task(
            id="blocked_task_456",
            title="Blocked task",
            status="active",
            work_state="blocked",
            project_id="test-project",
            blocked_by=["dep_task_123"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db_session.add(blocked_task)
        await db_session.commit()
        
        # Run auto-unblock
        unblocked_count = await monitor.auto_unblock_tasks()
        
        assert unblocked_count == 1
        
        # Task should be unblocked
        await db_session.refresh(blocked_task)
        assert blocked_task.work_state == "not_started"
        assert blocked_task.status == "active"
    
    @pytest.mark.asyncio
    async def test_detect_failure_patterns(self, db_session: AsyncSession):
        """Test failure pattern detection for tasks with high retry counts."""
        monitor = MonitorEnhanced(db_session)
        
        # Create task with high retry count
        failing_task = Task(
            id="failing_task_789",
            title="Failing task",
            status="active",
            work_state="blocked",
            project_id="test-project",
            retry_count=3,
            escalation_tier=2,
            failure_reason="Multiple failures",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db_session.add(failing_task)
        await db_session.commit()
        
        # Detect patterns
        patterns = await monitor.detect_failure_patterns()
        
        assert len(patterns) == 1
        assert patterns[0]["id"] == "failing_task_789"
        assert patterns[0]["retry_count"] == 3
    
    @pytest.mark.asyncio
    async def test_worker_health_check(self, db_session: AsyncSession):
        """Test worker health checking."""
        monitor = MonitorEnhanced(db_session)
        
        # Create healthy worker
        worker = WorkerStatus(
            id=1,
            active=True,
            worker_id="worker_123",
            current_task="task_123",
            current_project="test-project",
            started_at=datetime.now(timezone.utc),
            last_heartbeat=datetime.now(timezone.utc)
        )
        db_session.add(worker)
        await db_session.commit()
        
        # Check health
        health = await monitor.check_worker_health()
        
        assert health["healthy"] is True
        assert health["active"] is True
    
    @pytest.mark.asyncio
    async def test_worker_health_stale_heartbeat(self, db_session: AsyncSession):
        """Test detection of workers with stale heartbeats."""
        monitor = MonitorEnhanced(db_session)
        
        # Create worker with stale heartbeat
        stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        worker = WorkerStatus(
            id=1,
            active=True,
            worker_id="worker_123",
            current_task="task_123",
            current_project="test-project",
            started_at=datetime.now(timezone.utc),
            last_heartbeat=stale_time
        )
        db_session.add(worker)
        await db_session.commit()
        
        # Check health
        health = await monitor.check_worker_health()
        
        assert health["healthy"] is False
        assert health["unhealthy_count"] == 1
    
    @pytest.mark.asyncio
    async def test_system_health_summary(self, db_session: AsyncSession):
        """Test system health summary generation."""
        monitor = MonitorEnhanced(db_session)
        
        # Create some tasks
        for i in range(3):
            task = Task(
                id=f"task_{i}",
                title=f"Task {i}",
                status="active",
                work_state="not_started",
                project_id="test-project",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db_session.add(task)
        await db_session.commit()
        
        # Get summary
        summary = await monitor.get_system_health_summary()
        
        assert "timestamp" in summary
        assert "stats" in summary
        assert summary["stats"]["not_started"] >= 3
        assert "worker" in summary
        assert "stuck_tasks" in summary
    
    @pytest.mark.asyncio
    async def test_run_full_check(self, db_session: AsyncSession):
        """Test full monitoring check."""
        monitor = MonitorEnhanced(db_session)
        
        # Run full check
        result = await monitor.run_full_check()
        
        assert "stuck_tasks" in result
        assert "unblocked_tasks" in result
        assert "failure_patterns" in result
        assert "worker_healthy" in result
        assert "issues_found" in result
    
    @pytest.mark.asyncio
    async def test_kill_stuck_worker_no_manager(self, db_session: AsyncSession):
        """Test killing stuck worker when no worker manager is available."""
        monitor = MonitorEnhanced(db_session)  # No worker_manager
        
        result = await monitor._kill_stuck_worker("task_123", 3600)
        
        assert result is False  # Should return False without manager
    
    @pytest.mark.asyncio
    async def test_kill_stuck_worker_not_found(self, db_session: AsyncSession):
        """Test killing stuck worker when task has no active worker."""
        from unittest.mock import MagicMock
        
        # Create mock worker manager with no active workers
        mock_manager = MagicMock()
        mock_manager.active_workers = {}
        
        monitor = MonitorEnhanced(db_session, worker_manager=mock_manager)
        
        result = await monitor._kill_stuck_worker("task_123", 3600)
        
        assert result is False  # Should return False when worker not found
    
    @pytest.mark.asyncio
    async def test_critical_stuck_task_triggers_kill(self, db_session: AsyncSession):
        """Test that critical stuck tasks trigger worker termination."""
        from unittest.mock import MagicMock, AsyncMock
        
        # Create mock worker manager
        mock_manager = MagicMock()
        mock_manager.active_workers = {
            "worker_123": MagicMock(task_id="stuck_task_123")
        }
        mock_manager._kill_worker = AsyncMock()
        
        monitor = MonitorEnhanced(db_session, worker_manager=mock_manager)
        
        # Create a critically stuck task (over 1 hour)
        stuck_time = datetime.now(timezone.utc) - timedelta(hours=2)
        task = Task(
            id="stuck_task_123",
            title="Critically stuck task",
            status="active",
            work_state="in_progress",
            project_id="test-project",
            updated_at=stuck_time,
            created_at=stuck_time
        )
        db_session.add(task)
        await db_session.commit()
        
        # Check for stuck tasks
        stuck_tasks = await monitor.check_stuck_tasks()
        
        assert len(stuck_tasks) == 1
        assert stuck_tasks[0]["severity"] == "critical"
        
        # Verify worker was killed
        mock_manager._kill_worker.assert_called_once()
        call_args = mock_manager._kill_worker.call_args
        assert call_args[0][0] == "worker_123"  # worker_id
        assert "stuck" in call_args[0][1].lower()  # reason
