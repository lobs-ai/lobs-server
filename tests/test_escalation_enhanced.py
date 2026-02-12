"""Tests for enhanced escalation functionality."""

import pytest
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, InboxItem
from app.orchestrator.escalation_enhanced import EscalationManagerEnhanced


class TestEscalationEnhanced:
    """Test multi-tier escalation system."""
    
    @pytest.mark.asyncio
    async def test_tier_1_auto_retry(self, db_session: AsyncSession):
        """Test tier 1 auto-retry escalation."""
        escalation = EscalationManagerEnhanced(db_session)
        
        # Create task
        task = Task(
            id="task_123",
            title="Test task",
            status="active",
            work_state="not_started",
            project_id="test-project",
            escalation_tier=0,
            retry_count=0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db_session.add(task)
        await db_session.commit()
        
        # Handle failure (should trigger tier 1)
        result = await escalation.handle_failure(
            task_id="task_123",
            project_id="test-project",
            agent_type="programmer",
            error_log="Error: some failure",
            exit_code=1
        )
        
        assert result["action"] == "retry"
        assert result["tier"] == 1
        
        # Check task was updated
        await db_session.refresh(task)
        assert task.escalation_tier == 1
        assert task.retry_count == 1
        assert task.work_state == "not_started"
    
    @pytest.mark.asyncio
    async def test_tier_2_agent_switch(self, db_session: AsyncSession):
        """Test tier 2 agent switch escalation."""
        escalation = EscalationManagerEnhanced(db_session)
        
        # Create task that already failed tier 1
        task = Task(
            id="task_456",
            title="Test task",
            status="active",
            work_state="blocked",
            project_id="test-project",
            agent="programmer",
            escalation_tier=1,
            retry_count=2,  # Max tier 1 retries reached
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db_session.add(task)
        await db_session.commit()
        
        # Handle failure (should trigger tier 2)
        result = await escalation.handle_failure(
            task_id="task_456",
            project_id="test-project",
            agent_type="programmer",
            error_log="Error: still failing",
            exit_code=1
        )
        
        assert result["action"] == "agent_switch"
        assert result["tier"] == 2
        assert result["old_agent"] == "programmer"
        assert result["new_agent"] in ["architect", "reviewer"]
        
        # Check task was updated
        await db_session.refresh(task)
        assert task.escalation_tier == 2
        assert task.agent in ["architect", "reviewer"]
        assert task.work_state == "not_started"
    
    @pytest.mark.asyncio
    async def test_tier_3_diagnostic(self, db_session: AsyncSession):
        """Test tier 3 diagnostic escalation."""
        escalation = EscalationManagerEnhanced(db_session)
        
        # Create task that already failed tier 2
        task = Task(
            id="task_789",
            title="Test task",
            status="active",
            work_state="blocked",
            project_id="test-project",
            agent="architect",
            escalation_tier=2,
            retry_count=3,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db_session.add(task)
        await db_session.commit()
        
        # Handle failure (should trigger tier 3)
        result = await escalation.handle_failure(
            task_id="task_789",
            project_id="test-project",
            agent_type="architect",
            error_log="Error: persistent failure",
            exit_code=1
        )
        
        assert result["action"] == "diagnostic"
        assert result["tier"] == 3
        assert "diagnostic_task_id" in result
        
        # Check diagnostic task was created
        diagnostic_id = result["diagnostic_task_id"]
        diagnostic_task = await db_session.get(Task, diagnostic_id)
        assert diagnostic_task is not None
        assert diagnostic_task.agent == "reviewer"
        assert "Diagnose failure" in diagnostic_task.title
        
        # Check original task was updated
        await db_session.refresh(task)
        assert task.escalation_tier == 3
        assert task.work_state == "blocked"
    
    @pytest.mark.asyncio
    async def test_tier_4_human_escalation(self, db_session: AsyncSession):
        """Test tier 4 human escalation."""
        escalation = EscalationManagerEnhanced(db_session)
        
        # Create task that already failed tier 3
        task = Task(
            id="task_abc",
            title="Test task",
            status="active",
            work_state="blocked",
            project_id="test-project",
            agent="reviewer",
            escalation_tier=3,
            retry_count=5,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db_session.add(task)
        await db_session.commit()
        
        # Handle failure (should trigger tier 4)
        result = await escalation.handle_failure(
            task_id="task_abc",
            project_id="test-project",
            agent_type="reviewer",
            error_log="Error: all tiers exhausted",
            exit_code=1
        )
        
        assert result["action"] == "human_escalation"
        assert result["tier"] == 4
        assert "alert_id" in result
        
        # Check inbox alert was created
        alert_result = await db_session.execute(
            select(InboxItem).where(InboxItem.id == result["alert_id"])
        )
        alert = alert_result.scalar_one_or_none()
        assert alert is not None
        assert "Task Escalation" in alert.title
        assert "Human Intervention Required" in alert.content
        
        # Check original task was updated
        await db_session.refresh(task)
        assert task.escalation_tier == 4
        assert task.work_state == "blocked"
    
    @pytest.mark.asyncio
    async def test_simple_alert_creation(self, db_session: AsyncSession):
        """Test simple alert creation for non-escalation failures."""
        escalation = EscalationManagerEnhanced(db_session)
        
        # Create task
        task = Task(
            id="task_def",
            title="Test task",
            status="active",
            work_state="in_progress",
            project_id="test-project",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db_session.add(task)
        await db_session.commit()
        
        # Create simple alert
        alert_id = await escalation.create_simple_alert(
            task_id="task_def",
            project_id="test-project",
            error_log="Error: timeout",
            severity="high"
        )
        
        assert alert_id is not None
        
        # Check alert was created
        alert = await db_session.get(InboxItem, alert_id)
        assert alert is not None
        assert "Task Failure" in alert.title
    
    @pytest.mark.asyncio
    async def test_escalation_progression(self, db_session: AsyncSession):
        """Test full escalation progression through all tiers."""
        escalation = EscalationManagerEnhanced(db_session)
        
        # Create task
        task = Task(
            id="task_xyz",
            title="Progression test",
            status="active",
            work_state="not_started",
            project_id="test-project",
            agent="programmer",
            escalation_tier=0,
            retry_count=0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db_session.add(task)
        await db_session.commit()
        
        # Tier 1: Auto-retry 1
        result = await escalation.handle_failure(
            task_id="task_xyz",
            project_id="test-project",
            agent_type="programmer",
            error_log="Error 1",
            exit_code=1
        )
        assert result["tier"] == 1
        await db_session.refresh(task)
        assert task.retry_count == 1
        
        # Tier 1: Auto-retry 2
        task.work_state = "blocked"
        await db_session.commit()
        result = await escalation.handle_failure(
            task_id="task_xyz",
            project_id="test-project",
            agent_type="programmer",
            error_log="Error 2",
            exit_code=1
        )
        assert result["tier"] == 1
        await db_session.refresh(task)
        assert task.retry_count == 2
        
        # Tier 2: Agent switch
        task.work_state = "blocked"
        await db_session.commit()
        result = await escalation.handle_failure(
            task_id="task_xyz",
            project_id="test-project",
            agent_type="programmer",
            error_log="Error 3",
            exit_code=1
        )
        assert result["tier"] == 2
        await db_session.refresh(task)
        assert task.escalation_tier == 2
        
        # Tier 3: Diagnostic
        task.work_state = "blocked"
        await db_session.commit()
        result = await escalation.handle_failure(
            task_id="task_xyz",
            project_id="test-project",
            agent_type=task.agent,
            error_log="Error 4",
            exit_code=1
        )
        assert result["tier"] == 3
        await db_session.refresh(task)
        assert task.escalation_tier == 3
        
        # Tier 4: Human escalation
        task.work_state = "blocked"
        await db_session.commit()
        result = await escalation.handle_failure(
            task_id="task_xyz",
            project_id="test-project",
            agent_type=task.agent,
            error_log="Error 5",
            exit_code=1
        )
        assert result["tier"] == 4
        await db_session.refresh(task)
        assert task.escalation_tier == 4
