# Handoff: Outcome Tracker Service

**To:** programmer  
**From:** architect  
**Initiative:** agent-learning-system  
**Priority:** high  
**Depends on:** learning-1.1-database-models  
**Estimated effort:** 2 days  

---

## Context

Second task in agent learning system implementation. Creates the service that tracks task outcomes - both automatic (on completion) and manual (human feedback).

**Design reference:** `/Users/lobs/lobs-server/docs/agent-learning-system.md`  
**Implementation plan:** `/Users/lobs/lobs-server/docs/agent-learning-implementation-plan.md` (Task 1.2)

---

## Task

Implement `OutcomeTracker` service with methods to:
1. Create TaskOutcome records when tasks complete
2. Update TaskOutcome with human feedback
3. Infer task category from title/notes
4. Compute context hash for similarity matching

---

## Technical Spec

### 1. Create `app/services/outcome_tracker.py`

```python
"""Service for tracking task outcomes and human feedback."""

import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, WorkerRun, TaskOutcome

logger = logging.getLogger(__name__)


class OutcomeTracker:
    """Tracks task outcomes for the learning system."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def track_completion(
        self,
        task_id: str,
        worker_run_id: Optional[int] = None,
        outcome_type: str = "success",
    ) -> TaskOutcome:
        """
        Create TaskOutcome record when a task completes.
        
        Args:
            task_id: Task ID
            worker_run_id: Optional WorkerRun ID
            outcome_type: success/failure/partial
            
        Returns:
            Created TaskOutcome record
        """
        # Get task details
        task = await self.db.get(Task, task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        # Check if outcome already exists
        result = await self.db.execute(
            select(TaskOutcome).where(TaskOutcome.task_id == task_id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            logger.info(f"[OUTCOME] Task {task_id[:8]} already has outcome, skipping")
            return existing

        # Get worker run details if available
        worker_run = None
        if worker_run_id:
            worker_run = await self.db.get(WorkerRun, worker_run_id)

        # Create outcome record
        outcome = TaskOutcome(
            id=str(uuid.uuid4()),
            task_id=task_id,
            worker_run_id=worker_run_id,
            agent_type=task.agent or "unknown",
            outcome_type=outcome_type,
            completed_at=datetime.now(timezone.utc),
            task_category=self.infer_task_category(task),
            task_complexity=self._infer_complexity(task),
            context_hash=self.compute_context_hash(task),
            extraction_status="pending",
        )

        # Copy metrics from task/worker_run
        if task.started_at and task.finished_at:
            delta = task.finished_at - task.started_at
            outcome.duration_seconds = int(delta.total_seconds())

        outcome.retry_count = task.retry_count or 0
        outcome.escalation_tier = task.escalation_tier or 0

        if worker_run:
            # Prefer worker_run metrics if available
            if worker_run.started_at and worker_run.ended_at:
                delta = worker_run.ended_at - worker_run.started_at
                outcome.duration_seconds = int(delta.total_seconds())

        self.db.add(outcome)
        await self.db.commit()
        await self.db.refresh(outcome)

        logger.info(
            f"[OUTCOME] Created outcome for task {task_id[:8]}: "
            f"type={outcome_type}, category={outcome.task_category}"
        )

        return outcome

    async def track_human_feedback(
        self,
        task_id: str,
        feedback: str,
        review_state: str,
        category: Optional[str] = None,
        reviewed_by: str = "lobs",
    ) -> TaskOutcome:
        """
        Update TaskOutcome with human review feedback.
        
        Args:
            task_id: Task ID
            feedback: Human feedback text
            review_state: accepted/rejected/needs_revision
            category: Optional feedback category (missing_tests, code_quality, etc.)
            reviewed_by: Who provided the feedback
            
        Returns:
            Updated TaskOutcome record
        """
        # Get or create outcome
        result = await self.db.execute(
            select(TaskOutcome).where(TaskOutcome.task_id == task_id)
        )
        outcome = result.scalar_one_or_none()

        if not outcome:
            # Task might not have completed via normal flow - create outcome now
            task = await self.db.get(Task, task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found")

            outcome = await self.track_completion(
                task_id=task_id,
                outcome_type="success" if review_state == "accepted" else "failure",
            )

        # Update with feedback
        outcome.human_feedback = feedback
        outcome.review_state = review_state
        outcome.feedback_category = category or self._infer_feedback_category(feedback)
        outcome.reviewed_at = datetime.now(timezone.utc)
        outcome.reviewed_by = reviewed_by

        # Update outcome_type based on review
        if review_state == "accepted":
            outcome.outcome_type = "human_approved"
        elif review_state == "rejected":
            outcome.outcome_type = "human_rejected"

        # Mark for extraction
        outcome.extraction_status = "pending"

        await self.db.commit()
        await self.db.refresh(outcome)

        logger.info(
            f"[OUTCOME] Added feedback for task {task_id[:8]}: "
            f"state={review_state}, category={outcome.feedback_category}"
        )

        return outcome

    def infer_task_category(self, task: Task) -> str:
        """
        Infer task category from title and notes.
        
        Returns: bug_fix/feature/refactor/test/docs/research/review/other
        """
        text = f"{task.title or ''} {task.notes or ''}".lower()

        # Order matters - check most specific first
        if any(kw in text for kw in ["review", "code review", "pr review"]):
            return "review"
        if any(kw in text for kw in ["test", "testing", "unit test", "integration test"]):
            return "test"
        if any(kw in text for kw in ["bug", "fix", "error", "issue", "broken"]):
            return "bug_fix"
        if any(kw in text for kw in ["refactor", "cleanup", "reorganize", "simplify"]):
            return "refactor"
        if any(kw in text for kw in ["docs", "documentation", "readme", "guide"]):
            return "docs"
        if any(kw in text for kw in ["research", "investigate", "explore", "spike"]):
            return "research"
        if any(kw in text for kw in ["feature", "add", "implement", "create", "build"]):
            return "feature"

        return "other"

    def _infer_complexity(self, task: Task) -> str:
        """
        Infer task complexity from task shape or content.
        
        Returns: simple/medium/complex
        """
        # Use task shape if available
        shape = task.shape
        if shape:
            shape_lower = shape.lower()
            if "small" in shape_lower or "tiny" in shape_lower:
                return "simple"
            if "large" in shape_lower or "big" in shape_lower:
                return "complex"
            return "medium"

        # Fallback: infer from text length
        text_len = len(f"{task.title or ''} {task.notes or ''}")
        if text_len < 200:
            return "simple"
        elif text_len > 800:
            return "complex"
        else:
            return "medium"

    def compute_context_hash(self, task: Task) -> str:
        """
        Compute hash of task context for similarity matching.
        
        Uses: category keywords, complexity, project_id
        """
        category = self.infer_task_category(task)
        complexity = self._infer_complexity(task)
        project = task.project_id or "unknown"

        # Extract key terms from title/notes
        text = f"{task.title or ''} {task.notes or ''}".lower()
        # Simple keyword extraction (words 4+ chars, common words removed)
        words = re.findall(r'\b\w{4,}\b', text)
        stopwords = {"this", "that", "with", "from", "have", "will", "your", "their"}
        keywords = [w for w in words if w not in stopwords][:10]  # Top 10 keywords

        # Create hash input
        hash_input = f"{category}:{complexity}:{project}:{'|'.join(sorted(keywords))}"
        hash_bytes = hash_input.encode('utf-8')
        return hashlib.sha256(hash_bytes).hexdigest()[:16]

    def _infer_feedback_category(self, feedback: str) -> Optional[str]:
        """
        Infer feedback category from feedback text.
        
        Returns: missing_tests/code_quality/wrong_approach/unclear/security/other
        """
        feedback_lower = feedback.lower()

        if any(kw in feedback_lower for kw in ["test", "testing", "unit test", "coverage"]):
            return "missing_tests"
        if any(kw in feedback_lower for kw in ["security", "vulnerable", "exploit", "sanitize"]):
            return "security"
        if any(kw in feedback_lower for kw in ["wrong approach", "different approach", "rethink"]):
            return "wrong_approach"
        if any(kw in feedback_lower for kw in ["unclear", "confusing", "hard to understand", "naming"]):
            return "unclear"
        if any(kw in feedback_lower for kw in ["quality", "style", "formatting", "convention"]):
            return "code_quality"

        return "other"
```

### 2. Create Unit Tests

Create `tests/services/test_outcome_tracker.py`:

```python
import pytest
from datetime import datetime, timezone, timedelta
from app.models import Task, WorkerRun, TaskOutcome
from app.services.outcome_tracker import OutcomeTracker


@pytest.mark.asyncio
async def test_track_completion_creates_outcome(db_session):
    """Test that track_completion creates a TaskOutcome."""
    # Create a task
    task = Task(
        id="test-task-1",
        title="Add login feature",
        notes="Implement user login with JWT",
        status="completed",
        agent="programmer",
        project_id="test-project",
        started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(task)
    await db_session.commit()

    # Track completion
    tracker = OutcomeTracker(db_session)
    outcome = await tracker.track_completion(
        task_id="test-task-1",
        outcome_type="success",
    )

    # Verify
    assert outcome.task_id == "test-task-1"
    assert outcome.agent_type == "programmer"
    assert outcome.outcome_type == "success"
    assert outcome.task_category == "feature"  # "Add login" → feature
    assert outcome.duration_seconds is not None


@pytest.mark.asyncio
async def test_track_completion_idempotent(db_session):
    """Test that calling track_completion twice doesn't create duplicates."""
    task = Task(
        id="test-task-2",
        title="Fix bug",
        status="completed",
        agent="programmer",
        project_id="test-project",
    )
    db_session.add(task)
    await db_session.commit()

    tracker = OutcomeTracker(db_session)
    
    # First call
    outcome1 = await tracker.track_completion("test-task-2")
    
    # Second call
    outcome2 = await tracker.track_completion("test-task-2")
    
    # Should return same outcome
    assert outcome1.id == outcome2.id


@pytest.mark.asyncio
async def test_track_human_feedback_updates_outcome(db_session):
    """Test that human feedback updates the outcome."""
    task = Task(
        id="test-task-3",
        title="Refactor auth",
        status="completed",
        agent="programmer",
        project_id="test-project",
    )
    db_session.add(task)
    await db_session.commit()

    tracker = OutcomeTracker(db_session)
    
    # Create outcome
    outcome = await tracker.track_completion("test-task-3")
    assert outcome.human_feedback is None

    # Add feedback
    updated = await tracker.track_human_feedback(
        task_id="test-task-3",
        feedback="Missing unit tests for new functions",
        review_state="rejected",
    )

    # Verify
    assert updated.id == outcome.id
    assert updated.human_feedback == "Missing unit tests for new functions"
    assert updated.review_state == "rejected"
    assert updated.feedback_category == "missing_tests"
    assert updated.outcome_type == "human_rejected"


@pytest.mark.asyncio
async def test_infer_task_category(db_session):
    """Test task category inference."""
    tracker = OutcomeTracker(db_session)

    test_cases = [
        ("Add login feature", "", "feature"),
        ("Fix broken auth", "", "bug_fix"),
        ("Refactor database layer", "", "refactor"),
        ("Write unit tests", "", "test"),
        ("Update documentation", "", "docs"),
        ("Research best practices", "", "research"),
        ("Code review for PR #123", "", "review"),
        ("Random task", "", "other"),
    ]

    for title, notes, expected in test_cases:
        task = Task(
            id=f"test-{title[:10]}",
            title=title,
            notes=notes,
            status="inbox",
            project_id="test",
        )
        category = tracker.infer_task_category(task)
        assert category == expected, f"Failed for '{title}': expected {expected}, got {category}"


@pytest.mark.asyncio
async def test_compute_context_hash(db_session):
    """Test context hash computation."""
    tracker = OutcomeTracker(db_session)

    task1 = Task(
        id="test-1",
        title="Add login feature",
        notes="Implement JWT authentication",
        project_id="proj-1",
    )
    task2 = Task(
        id="test-2",
        title="Add signup feature",  # Similar
        notes="Implement user registration",
        project_id="proj-1",
    )
    task3 = Task(
        id="test-3",
        title="Fix database connection",  # Different
        notes="Debug connection pooling",
        project_id="proj-1",
    )

    hash1 = tracker.compute_context_hash(task1)
    hash2 = tracker.compute_context_hash(task2)
    hash3 = tracker.compute_context_hash(task3)

    # Similar tasks should have similar (but not identical) hashes
    # Different tasks should have different hashes
    assert len(hash1) == 16  # Truncated SHA256
    assert hash1 != hash2  # Not identical (different keywords)
    assert hash1 != hash3  # Very different
```

---

## Acceptance Criteria

- [ ] `OutcomeTracker` class implemented in `app/services/outcome_tracker.py`
- [ ] All methods work as specified
- [ ] `track_completion()` creates TaskOutcome records
- [ ] `track_completion()` is idempotent (doesn't create duplicates)
- [ ] `track_human_feedback()` updates existing outcomes
- [ ] `infer_task_category()` correctly categorizes tasks
- [ ] `compute_context_hash()` creates stable hashes
- [ ] All unit tests pass
- [ ] Code follows existing service patterns (see `app/services/`)

---

## Integration Notes

Will be called from:
1. `app/orchestrator/worker.py` - After task completion
2. `app/routers/tasks.py` - Feedback endpoint (next task)

Don't implement those integrations yet - this handoff is just the service layer.

---

## Testing Commands

```bash
cd /Users/lobs/lobs-server
source .venv/bin/activate

# Run unit tests
python -m pytest tests/services/test_outcome_tracker.py -v

# Test coverage
python -m pytest tests/services/test_outcome_tracker.py --cov=app/services/outcome_tracker --cov-report=term-missing
```

---

## Notes

- Use existing patterns from `app/services/` (e.g., `chat_manager.py`, `openclaw_bridge.py`)
- All datetime objects should use `timezone.utc`
- Log with `[OUTCOME]` prefix for easy filtering
- Handle missing task/worker_run gracefully (don't crash)
- Category inference is best-effort (keyword matching is fine)
- Context hash doesn't need to be perfect - just reasonable similarity signal

---

## Questions?

Ping architect if:
- Category inference logic needs refinement
- Context hash approach seems flawed
- Integration points unclear
