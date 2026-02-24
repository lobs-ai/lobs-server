# Learning Loop MVP — Test Specification

**Date:** 2026-02-24  
**File to create:** `tests/test_agent_learning.py`  
**Run:** `source .venv/bin/activate && python -m pytest tests/test_agent_learning.py -v`

This doc specifies exactly what each required test should verify. Write tests in this order — they build on each other.

---

## Setup Pattern

All tests that touch the database use the standard async test fixture:

```python
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from app.models import Base, Task, TaskOutcome, OutcomeLearning
from app.orchestrator.outcome_tracker import OutcomeTracker

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

async def make_task(db, work_state="completed", review_state="approved") -> Task:
    """Helper: create a minimal Task row."""
    import uuid
    task = Task(
        id=str(uuid.uuid4()),
        title="Test task",
        work_state=work_state,
        review_state=review_state,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task
```

---

## Test 1: `test_track_completion_creates_outcome`

**What it verifies:** `OutcomeTracker.track_completion()` writes a `TaskOutcome` row.

```python
@pytest.mark.asyncio
async def test_track_completion_creates_outcome(db):
    task = await make_task(db, work_state="completed", review_state="approved")
    await OutcomeTracker.track_completion(
        db=db, task=task, success=True, agent_type="programmer"
    )
    result = await db.execute(
        select(TaskOutcome).where(TaskOutcome.task_id == task.id)
    )
    outcome = result.scalar_one_or_none()
    assert outcome is not None
    assert outcome.success is True
    assert outcome.agent_type == "programmer"
```

---

## Test 2: `test_track_completion_never_raises`

**What it verifies:** Even if the DB call fails, `track_completion()` never raises.

```python
@pytest.mark.asyncio
async def test_track_completion_never_raises(db):
    """Must not raise even if DB is broken."""
    from unittest.mock import AsyncMock, patch
    task = await make_task(db)

    # Simulate DB failure
    with patch.object(db, "add", side_effect=RuntimeError("DB error")):
        # Should NOT raise
        await OutcomeTracker.track_completion(
            db=db, task=task, success=True, agent_type="programmer"
        )
```

---

## Test 3: `test_track_failure_sets_success_false`

**What it verifies:** Failed tasks produce `success=False` in the outcome.

```python
@pytest.mark.asyncio
async def test_track_failure_sets_success_false(db):
    task = await make_task(db, work_state="failed", review_state=None)
    await OutcomeTracker.track_completion(
        db=db, task=task, success=False, agent_type="programmer"
    )
    result = await db.execute(
        select(TaskOutcome).where(TaskOutcome.task_id == task.id)
    )
    outcome = result.scalar_one()
    assert outcome.success is False
```

---

## Test 4: `test_feedback_updates_existing_outcome`

**What it verifies:** `record_feedback()` updates an existing `TaskOutcome` (upserts).

```python
@pytest.mark.asyncio
async def test_feedback_updates_existing_outcome(db):
    task = await make_task(db)
    # First: create a basic outcome
    await OutcomeTracker.track_completion(
        db=db, task=task, success=True, agent_type="programmer"
    )
    # Then: submit feedback
    outcome = await OutcomeTracker.record_feedback(
        db=db,
        task_id=task.id,
        outcome_label="user-corrected",
        human_feedback="Tests were missing.",
        reason_tags=["missing_tests"],
    )
    assert outcome.task_id == task.id
    # reason_tags stored in human_feedback or as JSON field
    # exact field depends on model — check app/models.py
    assert "missing_tests" in (outcome.human_feedback or "")
```

> **Note:** Verify how `reason_tags` are stored in `TaskOutcome`. If the model has a `reason_tags` JSON column, assert `outcome.reason_tags == ["missing_tests"]`. If they're embedded in `human_feedback`, adjust accordingly.

---

## Test 5: `test_confidence_increments_on_success`

**What it verifies:** When a task using an active learning succeeds, `confidence += 0.1`.

```python
@pytest.mark.asyncio
async def test_confidence_increments_on_success(db):
    import uuid
    learning = OutcomeLearning(
        id=str(uuid.uuid4()),
        agent_type="programmer",
        pattern_name="missing_tests",
        lesson_text="Always write tests.",
        confidence=0.5,
        success_count=0,
        failure_count=0,
        is_active=True,
    )
    db.add(learning)
    await db.commit()
    await db.refresh(learning)

    task = await make_task(db, work_state="completed", review_state="approved")
    await OutcomeTracker.track_completion(
        db=db,
        task=task,
        success=True,
        agent_type="programmer",
        applied_learning_ids=[learning.id],
    )

    await db.refresh(learning)
    assert learning.confidence == pytest.approx(0.6, abs=0.001)
    assert learning.success_count == 1
```

---

## Test 6: `test_confidence_decrements_on_failure`

**What it verifies:** When a task using an active learning fails, `confidence -= 0.15`.

```python
@pytest.mark.asyncio
async def test_confidence_decrements_on_failure(db):
    import uuid
    learning = OutcomeLearning(
        id=str(uuid.uuid4()),
        agent_type="programmer",
        pattern_name="missing_tests",
        lesson_text="Always write tests.",
        confidence=0.5,
        success_count=0,
        failure_count=0,
        is_active=True,
    )
    db.add(learning)
    await db.commit()
    await db.refresh(learning)

    task = await make_task(db, work_state="failed")
    await OutcomeTracker.track_completion(
        db=db,
        task=task,
        success=False,
        agent_type="programmer",
        applied_learning_ids=[learning.id],
    )

    await db.refresh(learning)
    assert learning.confidence == pytest.approx(0.35, abs=0.001)
    assert learning.failure_count == 1
```

---

## Test 7: `test_auto_deactivate_low_confidence`

**What it verifies:** A learning with `confidence < 0.3` AND `failure_count >= 3` is auto-deactivated.

```python
@pytest.mark.asyncio
async def test_auto_deactivate_low_confidence(db):
    import uuid
    learning = OutcomeLearning(
        id=str(uuid.uuid4()),
        agent_type="programmer",
        pattern_name="missing_tests",
        lesson_text="Always write tests.",
        confidence=0.31,   # Just above threshold
        success_count=0,
        failure_count=3,   # Already at 3
        is_active=True,
    )
    db.add(learning)
    await db.commit()
    await db.refresh(learning)

    task = await make_task(db, work_state="failed")
    await OutcomeTracker.track_completion(
        db=db,
        task=task,
        success=False,
        agent_type="programmer",
        applied_learning_ids=[learning.id],
    )

    # confidence = 0.31 - 0.15 = 0.16 → below 0.3 with failure_count=4 → deactivate
    await db.refresh(learning)
    assert learning.is_active is False
```

---

## Test 8: `test_post_outcomes_endpoint_creates_record`

**What it verifies:** `POST /api/agent-learning/outcomes` creates a `TaskOutcome` record.

```python
@pytest.mark.asyncio
async def test_post_outcomes_endpoint_creates_record(client, db):
    """'client' is an httpx AsyncClient pointed at the FastAPI app."""
    task = await make_task(db)
    response = await client.post(
        "/api/agent-learning/outcomes",
        headers={"Authorization": "Bearer test-token"},
        json={
            "task_id": task.id,
            "outcome": "failure",
            "human_feedback": "Missing tests",
            "reason_tags": ["missing_tests"],
        },
    )
    assert response.status_code in (200, 201)
    data = response.json()
    assert data["task_id"] == task.id
    assert data["outcome"] == "failure"
```

---

## Test 9: `test_post_outcomes_endpoint_returns_200_on_update`

**What it verifies:** A second `POST` to the same task returns 200 (update), not 201 (create).

```python
@pytest.mark.asyncio
async def test_post_outcomes_endpoint_returns_200_on_update(client, db):
    task = await make_task(db)
    # First call → 201
    r1 = await client.post(
        "/api/agent-learning/outcomes",
        headers={"Authorization": "Bearer test-token"},
        json={"task_id": task.id, "outcome": "success"},
    )
    assert r1.status_code == 201

    # Second call → 200
    r2 = await client.post(
        "/api/agent-learning/outcomes",
        headers={"Authorization": "Bearer test-token"},
        json={"task_id": task.id, "outcome": "user-corrected"},
    )
    assert r2.status_code == 200
```

---

## Test 10: `test_summary_endpoint_returns_data`

**What it verifies:** `GET /api/agent-learning/summary` returns a well-formed response with required fields.

```python
@pytest.mark.asyncio
async def test_summary_endpoint_returns_data(client, db):
    # Create some outcomes
    for _ in range(3):
        task = await make_task(db, work_state="completed")
        await OutcomeTracker.track_completion(
            db=db, task=task, success=True, agent_type="programmer"
        )

    response = await client.get(
        "/api/agent-learning/summary",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "generated_at" in data
    assert "totals" in data
    assert "tasks_tracked" in data["totals"]
    assert "success_rate" in data["totals"]
    assert "top_failure_patterns" in data
    assert "active_learnings" in data
    assert "pending_suggestions" in data
```

---

## Test 11: `test_summary_shows_ab_lift`

**What it verifies:** The summary distinguishes control group (learning disabled) from treatment group and computes lift.

```python
@pytest.mark.asyncio
async def test_summary_shows_ab_lift(client, db):
    # Control group: 2 tasks, 1 success (50%)
    for i in range(2):
        task = await make_task(db, work_state="completed" if i == 0 else "failed")
        await OutcomeTracker.track_completion(
            db=db, task=task, success=(i == 0),
            agent_type="programmer", learning_disabled=True
        )

    # Treatment group: 4 tasks, 3 success (75%)
    for i in range(4):
        task = await make_task(db, work_state="completed" if i < 3 else "failed")
        await OutcomeTracker.track_completion(
            db=db, task=task, success=(i < 3),
            agent_type="programmer", learning_disabled=False
        )

    response = await client.get(
        "/api/agent-learning/summary",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    totals = response.json()["totals"]
    assert totals["control_success_rate"] == pytest.approx(0.5, abs=0.01)
    assert totals["treatment_success_rate"] == pytest.approx(0.75, abs=0.01)
    # lift = (0.75 - 0.5) / 0.5 = 0.5
    assert totals["lift"] == pytest.approx(0.5, abs=0.05)
```

---

## Test 12: `test_batch_creates_suggestions_for_patterns`

**What it verifies:** The daily batch job creates an `OutcomeLearning` row for patterns with ≥3 failures.

```python
@pytest.mark.asyncio
async def test_batch_creates_suggestions_for_patterns(db):
    from app.orchestrator.learning_batch import run_learning_batch
    from sqlalchemy import select

    # Create 3 failure outcomes with the same reason tag
    for _ in range(3):
        task = await make_task(db, work_state="failed")
        outcome = TaskOutcome(
            id=str(uuid.uuid4()),
            task_id=task.id,
            agent_type="programmer",
            success=False,
            human_feedback="missing_tests",  # or reason_tags field
        )
        db.add(outcome)
    await db.commit()

    result = await run_learning_batch(db)
    assert result["new_suggestions"] >= 1

    # Verify an OutcomeLearning row was created with is_active=False
    rows = (await db.execute(select(OutcomeLearning))).scalars().all()
    assert any(r.pattern_name == "missing_tests" and r.is_active is False for r in rows)
```

---

## Test 13: `test_batch_skips_already_covered_patterns`

**What it verifies:** The batch does not create a second `OutcomeLearning` for a pattern that already has an active one.

```python
@pytest.mark.asyncio
async def test_batch_skips_already_covered_patterns(db):
    from app.orchestrator.learning_batch import run_learning_batch
    import uuid

    # Create active learning for missing_tests
    existing = OutcomeLearning(
        id=str(uuid.uuid4()),
        agent_type="programmer",
        pattern_name="missing_tests",
        lesson_text="Always write tests.",
        confidence=0.7,
        is_active=True,
    )
    db.add(existing)
    await db.commit()

    # Create 5 failures with missing_tests
    for _ in range(5):
        task = await make_task(db, work_state="failed")
        outcome = TaskOutcome(
            id=str(uuid.uuid4()),
            task_id=task.id,
            agent_type="programmer",
            success=False,
            human_feedback="missing_tests",
        )
        db.add(outcome)
    await db.commit()

    result = await run_learning_batch(db)
    # Should NOT create a duplicate for missing_tests
    assert result["new_suggestions"] == 0
```

---

## Running Individual Tests

```bash
source .venv/bin/activate

# All learning tests
python -m pytest tests/test_agent_learning.py -v

# One test
python -m pytest tests/test_agent_learning.py::test_track_completion_creates_outcome -v

# Stop on first failure
python -m pytest tests/test_agent_learning.py -x -v
```

---

## Known Challenges

**Async test setup:** Use `pytest-asyncio` with `asyncio_mode = "auto"` in `pytest.ini` or mark each test with `@pytest.mark.asyncio`.

**DB fixture isolation:** Each test gets a fresh in-memory SQLite DB (created + dropped per test). This avoids state leakage between tests.

**Model field names:** The `reason_tags` field storage depends on how `TaskOutcome` is modeled. Check `app/models.py:846` to see whether it's a JSON column, a text column, or stored in `human_feedback`. Adjust assertions accordingly.

**API client fixture:** Tests 8–11 need an `httpx.AsyncClient` pointed at the FastAPI app. Use the pattern from other test files in `tests/` (e.g., `test_learning_stats.py`) as a template.

---

## Related Docs

| Doc | Purpose |
|-----|---------|
| [`learning-loop-mvp-implementation-guide.md`](learning-loop-mvp-implementation-guide.md) | How to build each component |
| [`learning-loop-mvp-status.md`](learning-loop-mvp-status.md) | Current state, precise file references |
| [`../learning-loop-mvp-design.md`](../learning-loop-mvp-design.md) | Full spec with confidence model details |
