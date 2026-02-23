# Handoff: Learning System Phase 1.1 - Database & Tracking

**Initiative:** agent-learning-system  
**Phase:** 1.1  
**To:** Programmer  
**Priority:** High  
**Estimated Complexity:** Medium (3-5 days)

---

## Context

Implementing the first phase of the agent learning system - structured outcome tracking. This creates the foundation for closed-loop agent improvement by capturing what happens after tasks complete and how humans respond.

**Design Document:** `/Users/lobs/lobs-server/docs/agent-learning-system.md`

---

## Objectives

1. Create `task_outcomes` table to track task completion results
2. Implement `OutcomeTracker` service class
3. Integrate outcome tracking into worker execution flow
4. Add API endpoint for human feedback
5. Write comprehensive tests

---

## Technical Specifications

### 1. Database Schema

**File:** Create new migration `alembic/versions/XXX_add_task_outcomes.py`

```python
"""Add task_outcomes table for learning system.

Revision ID: XXX
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

def upgrade():
    op.create_table(
        'task_outcomes',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('task_id', sa.String(), nullable=False),
        sa.Column('worker_run_id', sa.String(), nullable=True),
        sa.Column('agent_type', sa.String(50), nullable=False),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('task_category', sa.String(50), nullable=True),
        sa.Column('task_complexity', sa.String(20), nullable=True),
        sa.Column('context_hash', sa.String(64), nullable=True),
        sa.Column('human_feedback', sa.Text(), nullable=True),
        sa.Column('review_state', sa.String(50), nullable=True),
        sa.Column('applied_learnings', sa.JSON(), nullable=True),
        sa.Column('learning_disabled', sa.Boolean(), default=False),  # A/B test control group
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    op.create_index('idx_outcomes_task_id', 'task_outcomes', ['task_id'])
    op.create_index('idx_outcomes_agent_category', 'task_outcomes', ['agent_type', 'task_category'])
    op.create_index('idx_outcomes_context_hash', 'task_outcomes', ['context_hash'])
    op.create_index('idx_outcomes_success', 'task_outcomes', ['success', 'agent_type'])
    
    op.create_foreign_key(
        'fk_task_outcomes_task_id',
        'task_outcomes', 'tasks',
        ['task_id'], ['id'],
        ondelete='CASCADE'
    )

def downgrade():
    op.drop_table('task_outcomes')
```

**Model:** Add to `app/models.py`

```python
class TaskOutcome(Base):
    """Task outcome model for learning system."""
    __tablename__ = "task_outcomes"
    
    id = Column(String, primary_key=True)
    task_id = Column(String, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    worker_run_id = Column(String, ForeignKey("worker_runs.id"))
    agent_type = Column(String(50), nullable=False)
    success = Column(Boolean, nullable=False)
    task_category = Column(String(50))  # bug_fix, feature, test, refactor, docs
    task_complexity = Column(String(20))  # simple, medium, complex
    context_hash = Column(String(64))
    human_feedback = Column(Text)
    review_state = Column(String(50))  # snapshot of task.review_state
    applied_learnings = Column(JSON)  # array of learning IDs
    learning_disabled = Column(Boolean, default=False)  # A/B control group flag
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
```

---

### 2. OutcomeTracker Service

**File:** `app/orchestrator/outcome_tracker.py`

```python
"""Outcome tracking for agent learning system."""

import hashlib
import logging
import random
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TaskOutcome, Task, WorkerRun

logger = logging.getLogger(__name__)

# A/B test configuration
LEARNING_CONTROL_GROUP_PCT = 0.20  # 20% control group


class OutcomeTracker:
    """Tracks task outcomes for learning system."""

    @staticmethod
    async def track_completion(
        db: AsyncSession,
        task: Task,
        worker_run: Optional[WorkerRun],
        success: bool,
    ) -> TaskOutcome:
        """
        Create outcome record when task completes.
        
        Args:
            db: Database session
            task: Completed task
            worker_run: Associated worker run (if any)
            success: Whether task succeeded
            
        Returns:
            Created TaskOutcome
        """
        # Determine if this should be in control group (no learnings applied)
        learning_disabled = random.random() < LEARNING_CONTROL_GROUP_PCT
        
        outcome = TaskOutcome(
            id=str(uuid4()),
            task_id=task.id,
            worker_run_id=worker_run.id if worker_run else None,
            agent_type=task.agent or "programmer",
            success=success,
            task_category=OutcomeTracker._infer_task_category(task),
            task_complexity=OutcomeTracker._infer_task_complexity(task),
            context_hash=OutcomeTracker._compute_context_hash(task),
            review_state=task.review_state,
            applied_learnings=[],  # Will be set by PromptEnhancer
            learning_disabled=learning_disabled,
        )
        
        db.add(outcome)
        await db.commit()
        await db.refresh(outcome)
        
        logger.info(
            f"[LEARNING] OutcomeTracker: Created outcome {outcome.id} for task {task.id} "
            f"(success={success}, agent={outcome.agent_type}, category={outcome.task_category}, "
            f"learning_disabled={learning_disabled})"
        )
        
        return outcome

    @staticmethod
    async def track_human_feedback(
        db: AsyncSession,
        task_id: str,
        feedback_text: str,
    ) -> Optional[TaskOutcome]:
        """
        Add human feedback to existing outcome.
        
        Args:
            db: Database session
            task_id: Task ID
            feedback_text: Human feedback text
            
        Returns:
            Updated TaskOutcome or None if not found
        """
        stmt = select(TaskOutcome).where(TaskOutcome.task_id == task_id)
        result = await db.execute(stmt)
        outcome = result.scalar_one_or_none()
        
        if not outcome:
            logger.warning(f"[LEARNING] No outcome found for task {task_id}")
            return None
        
        outcome.human_feedback = feedback_text
        outcome.success = False  # Human feedback typically means needs revision
        outcome.updated_at = datetime.utcnow()
        
        await db.commit()
        await db.refresh(outcome)
        
        logger.info(
            f"[LEARNING] OutcomeTracker: Added human feedback to outcome {outcome.id} "
            f"(task={task_id}, feedback_length={len(feedback_text)})"
        )
        
        return outcome

    @staticmethod
    def _infer_task_category(task: Task) -> str:
        """Infer task category from title and context."""
        title_lower = task.title.lower()
        notes_lower = (task.notes or "").lower()
        text = f"{title_lower} {notes_lower}"
        
        if any(word in text for word in ['bug', 'fix', 'broken', 'error']):
            return 'bug_fix'
        elif any(word in text for word in ['test', 'testing', 'spec']):
            return 'test'
        elif any(word in text for word in ['refactor', 'cleanup', 'improve']):
            return 'refactor'
        elif any(word in text for word in ['doc', 'readme', 'comment']):
            return 'docs'
        else:
            return 'feature'

    @staticmethod
    def _infer_task_complexity(task: Task) -> str:
        """Infer task complexity from shape or other signals."""
        # Use task.shape if available (small/medium/large)
        if task.shape in ['small', 'medium', 'large']:
            complexity_map = {'small': 'simple', 'medium': 'medium', 'large': 'complex'}
            return complexity_map[task.shape]
        
        # Fallback: estimate from title/notes length
        text_length = len(task.title) + len(task.notes or "")
        if text_length < 100:
            return 'simple'
        elif text_length < 300:
            return 'medium'
        else:
            return 'complex'

    @staticmethod
    def _compute_context_hash(task: Task) -> str:
        """
        Compute similarity hash for finding related tasks.
        
        V1: Simple hash of normalized title keywords.
        Future: Could use semantic embeddings.
        """
        # Extract keywords (simple version)
        title_lower = task.title.lower()
        # Remove common words
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for'}
        words = [w for w in title_lower.split() if w not in stop_words]
        
        # Sort and join to create stable hash
        normalized = ' '.join(sorted(words[:5]))  # Use first 5 keywords
        
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]
```

---

### 3. Integration with Worker

**File:** `app/orchestrator/worker.py`

Add outcome tracking after task completion. Find the section where worker status is updated and add:

```python
from app.orchestrator.outcome_tracker import OutcomeTracker

# In execute_task or similar function, after task completes:

async def execute_task(db: AsyncSession, task: Task, worker_run: WorkerRun):
    """Execute task and track outcome."""
    try:
        # ... existing task execution logic ...
        
        # Determine success based on review_state or work_state
        success = task.review_state in ['approved', 'auto_approved'] or \
                  (task.work_state == 'completed' and not task.review_state)
        
        # Track outcome
        await OutcomeTracker.track_completion(
            db=db,
            task=task,
            worker_run=worker_run,
            success=success,
        )
        
    except Exception as e:
        logger.error(f"Task execution failed: {e}")
        # Track failure outcome
        await OutcomeTracker.track_completion(
            db=db,
            task=task,
            worker_run=worker_run,
            success=False,
        )
        raise
```

---

### 4. API Endpoint for Human Feedback

**File:** `app/routers/learning.py` (create new file)

```python
"""Learning system API endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.orchestrator.outcome_tracker import OutcomeTracker

router = APIRouter(prefix="/api/learning", tags=["learning"])


class FeedbackRequest(BaseModel):
    feedback: str


class FeedbackResponse(BaseModel):
    task_id: str
    outcome_id: Optional[str]
    feedback_recorded: bool


@router.post("/tasks/{task_id}/feedback", response_model=FeedbackResponse)
async def add_task_feedback(
    task_id: str,
    request: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Add human feedback to a task outcome.
    
    Used when code review or manual review provides feedback.
    """
    if not request.feedback or len(request.feedback) < 10:
        raise HTTPException(400, "Feedback must be at least 10 characters")
    
    outcome = await OutcomeTracker.track_human_feedback(
        db=db,
        task_id=task_id,
        feedback_text=request.feedback,
    )
    
    if not outcome:
        raise HTTPException(404, f"No outcome found for task {task_id}")
    
    return FeedbackResponse(
        task_id=task_id,
        outcome_id=outcome.id,
        feedback_recorded=True,
    )
```

**Register Router:** Add to `app/main.py`:

```python
from app.routers import learning

app.include_router(learning.router)
```

---

## Testing Requirements

### Unit Tests

**File:** `tests/test_outcome_tracker.py`

```python
"""Tests for OutcomeTracker."""

import pytest
from app.orchestrator.outcome_tracker import OutcomeTracker
from app.models import Task, TaskOutcome

@pytest.mark.asyncio
async def test_track_completion_creates_outcome(db, sample_task, sample_worker_run):
    """Test that track_completion creates an outcome record."""
    outcome = await OutcomeTracker.track_completion(
        db=db,
        task=sample_task,
        worker_run=sample_worker_run,
        success=True,
    )
    
    assert outcome.id is not None
    assert outcome.task_id == sample_task.id
    assert outcome.agent_type == sample_task.agent
    assert outcome.success == True
    assert outcome.task_category in ['bug_fix', 'feature', 'test', 'refactor', 'docs']

@pytest.mark.asyncio
async def test_infer_task_category():
    """Test category inference from task title."""
    task_bug = Task(id="1", title="Fix login bug", status="active")
    assert OutcomeTracker._infer_task_category(task_bug) == 'bug_fix'
    
    task_feature = Task(id="2", title="Add user profile page", status="active")
    assert OutcomeTracker._infer_task_category(task_feature) == 'feature'
    
    task_test = Task(id="3", title="Write tests for auth", status="active")
    assert OutcomeTracker._infer_task_category(task_test) == 'test'

@pytest.mark.asyncio
async def test_track_human_feedback(db, sample_task, sample_outcome):
    """Test adding human feedback to outcome."""
    feedback = "Missing input validation for email field"
    
    outcome = await OutcomeTracker.track_human_feedback(
        db=db,
        task_id=sample_task.id,
        feedback_text=feedback,
    )
    
    assert outcome.human_feedback == feedback
    assert outcome.success == False  # Feedback implies needs revision

@pytest.mark.asyncio
async def test_context_hash_stable():
    """Test that context hash is stable for similar tasks."""
    task1 = Task(id="1", title="Add user authentication endpoint", status="active")
    task2 = Task(id="2", title="Implement user endpoint for authentication", status="active")
    
    hash1 = OutcomeTracker._compute_context_hash(task1)
    hash2 = OutcomeTracker._compute_context_hash(task2)
    
    # Should be similar (not guaranteed equal with simple hash, but check it works)
    assert hash1 is not None
    assert hash2 is not None
    assert len(hash1) == 16
```

### Integration Tests

**File:** `tests/test_learning_api.py`

```python
"""Integration tests for learning API."""

import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_add_feedback_endpoint(client: AsyncClient, sample_task):
    """Test POST /api/learning/tasks/{id}/feedback."""
    # First create an outcome
    # ... (setup outcome for task)
    
    response = await client.post(
        f"/api/learning/tasks/{sample_task.id}/feedback",
        json={"feedback": "Missing tests and input validation"},
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == sample_task.id
    assert data["feedback_recorded"] == True
    assert data["outcome_id"] is not None

@pytest.mark.asyncio
async def test_add_feedback_requires_minimum_length(client: AsyncClient, sample_task):
    """Test that feedback must be at least 10 characters."""
    response = await client.post(
        f"/api/learning/tasks/{sample_task.id}/feedback",
        json={"feedback": "Too short"},
    )
    
    assert response.status_code == 400
```

---

## Acceptance Criteria

- ✅ `task_outcomes` table created with all required columns and indexes
- ✅ `TaskOutcome` model added to `app/models.py`
- ✅ `OutcomeTracker` service class implemented with all methods
- ✅ Worker integration: outcomes created automatically on task completion
- ✅ POST `/api/learning/tasks/{id}/feedback` endpoint working
- ✅ A/B test control group: 20% of outcomes have `learning_disabled=True`
- ✅ Task category inference working for bug_fix/feature/test/refactor/docs
- ✅ Context hash computed for task similarity
- ✅ Unit tests pass (>80% coverage for OutcomeTracker)
- ✅ Integration tests pass for API endpoint
- ✅ Logging: All operations log with `[LEARNING]` prefix
- ✅ No errors in manual testing: create task → complete → add feedback

---

## Dependencies

- None (this is phase 1)

---

## Notes

- Keep OutcomeTracker simple and defensive - if it fails, don't break task execution
- The `learning_disabled` flag is critical for A/B testing - make sure it's set correctly
- Context hash is V1 (simple) - can be improved later with embeddings
- Human feedback is optional - most tasks won't have it initially

---

## Questions?

Contact architect or reference design doc: `/Users/lobs/lobs-server/docs/agent-learning-system.md`
