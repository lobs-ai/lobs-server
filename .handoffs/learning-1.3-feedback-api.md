# Handoff: Task Feedback API Endpoint

**To:** programmer  
**From:** architect  
**Initiative:** agent-learning-system  
**Priority:** high  
**Depends on:** learning-1.2-outcome-tracker  
**Estimated effort:** 1 day  

---

## Context

Third task in agent learning system. Creates REST API endpoint for humans (Lobs) to provide feedback on completed tasks. This feedback drives the learning extraction.

**Design reference:** `/Users/lobs/lobs-server/docs/agent-learning-system.md`  
**Implementation plan:** `/Users/lobs/lobs-server/docs/agent-learning-implementation-plan.md` (Task 1.3)

---

## Task

Add `PATCH /api/tasks/{task_id}/feedback` endpoint to accept human review feedback and update task outcomes.

---

## Technical Spec

### 1. Add Pydantic Schema

Update `app/schemas.py`:

```python
class TaskFeedbackRequest(BaseModel):
    """Request schema for task feedback."""
    feedback: str = Field(..., min_length=1, max_length=5000, description="Human feedback text")
    review_state: str = Field(..., description="accepted/rejected/needs_revision")
    category: Optional[str] = Field(None, description="Feedback category (optional)")

    @validator("review_state")
    def validate_review_state(cls, v):
        allowed = ["accepted", "rejected", "needs_revision"]
        if v not in allowed:
            raise ValueError(f"review_state must be one of {allowed}")
        return v


class TaskOutcomeResponse(BaseModel):
    """Response schema for task outcome."""
    id: str
    task_id: str
    agent_type: str
    outcome_type: str
    human_feedback: Optional[str] = None
    review_state: Optional[str] = None
    feedback_category: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    task_category: Optional[str] = None
    task_complexity: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
```

### 2. Add Endpoint to Tasks Router

Update `app/routers/tasks.py`:

```python
from app.services.outcome_tracker import OutcomeTracker
from app.schemas import TaskFeedbackRequest, TaskOutcomeResponse


@router.patch(
    "/{task_id}/feedback",
    response_model=TaskOutcomeResponse,
    summary="Add human feedback to task outcome"
)
async def add_task_feedback(
    task_id: str,
    feedback_req: TaskFeedbackRequest,
    db: AsyncSession = Depends(get_db),
) -> TaskOutcomeResponse:
    """
    Add human review feedback to a task outcome.
    
    This endpoint is used by humans (primarily Lobs) to record their review
    of completed tasks. Feedback is used by the learning system to extract
    patterns and improve future task execution.
    
    **Review States:**
    - `accepted`: Task output is good, approve
    - `rejected`: Task output has issues, reject
    - `needs_revision`: Task output needs minor changes
    
    **Categories (optional, auto-inferred if not provided):**
    - `missing_tests`: Tests were missing or insufficient
    - `code_quality`: Style, naming, or formatting issues
    - `wrong_approach`: Implementation approach was incorrect
    - `unclear`: Code or docs were confusing
    - `security`: Security issues found
    - `other`: Other feedback
    
    Example:
    ```json
    {
      "feedback": "Missing unit tests for new auth functions. Need to test token generation and validation.",
      "review_state": "rejected",
      "category": "missing_tests"
    }
    ```
    """
    # Verify task exists
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Track feedback using OutcomeTracker
    tracker = OutcomeTracker(db)
    try:
        outcome = await tracker.track_human_feedback(
            task_id=task_id,
            feedback=feedback_req.feedback,
            review_state=feedback_req.review_state,
            category=feedback_req.category,
            reviewed_by="lobs",  # TODO: Get from auth context when multi-user
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    logger.info(
        f"[FEEDBACK] Added feedback for task {task_id[:8]}: "
        f"{feedback_req.review_state}"
    )
    
    return TaskOutcomeResponse.from_orm(outcome)


@router.get(
    "/{task_id}/outcome",
    response_model=Optional[TaskOutcomeResponse],
    summary="Get task outcome"
)
async def get_task_outcome(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> Optional[TaskOutcomeResponse]:
    """
    Get the outcome record for a task (if it exists).
    
    Useful for checking if feedback has already been provided.
    """
    result = await db.execute(
        select(TaskOutcome).where(TaskOutcome.task_id == task_id)
    )
    outcome = result.scalar_one_or_none()
    
    if not outcome:
        return None
    
    return TaskOutcomeResponse.from_orm(outcome)
```

### 3. Add API Tests

Create `tests/routers/test_tasks_feedback.py`:

```python
import pytest
from httpx import AsyncClient
from app.models import Task, TaskOutcome


@pytest.mark.asyncio
async def test_add_task_feedback_creates_outcome(client: AsyncClient, db_session):
    """Test adding feedback to a task."""
    # Create a task
    task = Task(
        id="test-task-feedback-1",
        title="Test task",
        status="completed",
        agent="programmer",
        project_id="test-project",
    )
    db_session.add(task)
    await db_session.commit()

    # Add feedback
    response = await client.patch(
        f"/api/tasks/test-task-feedback-1/feedback",
        json={
            "feedback": "Missing unit tests for new functions",
            "review_state": "rejected",
            "category": "missing_tests",
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "test-task-feedback-1"
    assert data["human_feedback"] == "Missing unit tests for new functions"
    assert data["review_state"] == "rejected"
    assert data["feedback_category"] == "missing_tests"
    assert data["outcome_type"] == "human_rejected"


@pytest.mark.asyncio
async def test_add_feedback_task_not_found(client: AsyncClient):
    """Test feedback for non-existent task."""
    response = await client.patch(
        "/api/tasks/nonexistent/feedback",
        json={
            "feedback": "Test",
            "review_state": "accepted",
        }
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_add_feedback_invalid_review_state(client: AsyncClient, db_session):
    """Test feedback with invalid review_state."""
    task = Task(
        id="test-task-feedback-2",
        title="Test task",
        status="completed",
        agent="programmer",
        project_id="test-project",
    )
    db_session.add(task)
    await db_session.commit()

    response = await client.patch(
        "/api/tasks/test-task-feedback-2/feedback",
        json={
            "feedback": "Test",
            "review_state": "invalid_state",  # Invalid
        }
    )
    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_get_task_outcome(client: AsyncClient, db_session):
    """Test getting task outcome."""
    task = Task(
        id="test-task-outcome-1",
        title="Test task",
        status="completed",
        agent="programmer",
        project_id="test-project",
    )
    db_session.add(task)
    await db_session.commit()

    # No outcome yet
    response = await client.get("/api/tasks/test-task-outcome-1/outcome")
    assert response.status_code == 200
    assert response.json() is None

    # Add feedback (creates outcome)
    await client.patch(
        "/api/tasks/test-task-outcome-1/feedback",
        json={
            "feedback": "Looks good",
            "review_state": "accepted",
        }
    )

    # Now outcome exists
    response = await client.get("/api/tasks/test-task-outcome-1/outcome")
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "test-task-outcome-1"
    assert data["review_state"] == "accepted"


@pytest.mark.asyncio
async def test_add_feedback_multiple_times_updates(client: AsyncClient, db_session):
    """Test that adding feedback multiple times updates the same outcome."""
    task = Task(
        id="test-task-feedback-3",
        title="Test task",
        status="completed",
        agent="programmer",
        project_id="test-project",
    )
    db_session.add(task)
    await db_session.commit()

    # First feedback
    response1 = await client.patch(
        "/api/tasks/test-task-feedback-3/feedback",
        json={
            "feedback": "First feedback",
            "review_state": "needs_revision",
        }
    )
    outcome_id_1 = response1.json()["id"]

    # Second feedback (update)
    response2 = await client.patch(
        "/api/tasks/test-task-feedback-3/feedback",
        json={
            "feedback": "Updated feedback",
            "review_state": "accepted",
        }
    )
    outcome_id_2 = response2.json()["id"]

    # Should be same outcome, just updated
    assert outcome_id_1 == outcome_id_2
    assert response2.json()["human_feedback"] == "Updated feedback"
    assert response2.json()["review_state"] == "accepted"
```

---

## Acceptance Criteria

- [ ] `PATCH /api/tasks/{task_id}/feedback` endpoint implemented
- [ ] `GET /api/tasks/{task_id}/outcome` endpoint implemented
- [ ] Pydantic schemas added for request/response
- [ ] Calls `OutcomeTracker.track_human_feedback()`
- [ ] Returns 404 if task not found
- [ ] Validates review_state (accepted/rejected/needs_revision)
- [ ] Auto-infers category if not provided
- [ ] All API tests pass
- [ ] Manual testing via curl/Postman works

---

## Manual Testing

```bash
cd /Users/lobs/lobs-server
source .venv/bin/activate

# Start server
./bin/run

# In another terminal:
# Get auth token
export TOKEN="your-api-token"

# Create a test task (if needed)
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test task for feedback",
    "status": "completed",
    "agent": "programmer",
    "project_id": "test-project"
  }'

# Add feedback (use actual task ID)
curl -X PATCH http://localhost:8000/api/tasks/<task-id>/feedback \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "feedback": "Missing unit tests for new functions",
    "review_state": "rejected",
    "category": "missing_tests"
  }'

# Get outcome
curl http://localhost:8000/api/tasks/<task-id>/outcome \
  -H "Authorization: Bearer $TOKEN"

# Verify in DB
sqlite3 lobs.db "SELECT * FROM task_outcomes WHERE task_id='<task-id>';"
```

---

## API Documentation

Once implemented, endpoint will appear in:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

Verify documentation is clear and includes examples.

---

## Notes

- Follow existing router patterns (see `app/routers/tasks.py`)
- Use existing auth middleware (already on `/api/tasks` router)
- Log with `[FEEDBACK]` prefix
- Return 404 for non-existent tasks (not 500)
- Return 422 for validation errors (Pydantic handles this)
- `reviewed_by` hardcoded to "lobs" for now (multi-user support later)
- Category auto-inference happens in `OutcomeTracker` if not provided

---

## Questions?

Ping architect if:
- Schema design needs adjustment
- Error handling approach unclear
- Integration with existing tasks router has issues
