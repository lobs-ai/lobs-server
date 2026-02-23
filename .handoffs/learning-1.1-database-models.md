# Handoff: Database Models for Agent Learning System

**To:** programmer  
**From:** architect  
**Initiative:** agent-learning-system  
**Priority:** high  
**Depends on:** None  
**Estimated effort:** 1 day  

---

## Context

Implementing the agent learning system foundation. This task creates the database schema for tracking task outcomes, extracted learnings, and prompt strategies.

**Design reference:** `/Users/lobs/lobs-server/docs/agent-learning-system.md` (section: Data Model)  
**Implementation plan:** `/Users/lobs/lobs-server/docs/agent-learning-implementation-plan.md` (Task 1.1)

---

## Task

Create SQLAlchemy models and Alembic migration for three new tables:
1. `task_outcomes` - Structured outcomes for completed tasks
2. `outcome_learnings` - Extracted lessons from outcomes
3. `prompt_strategies` - A/B testing framework (stub for future)

---

## Technical Spec

### 1. Update `app/models.py`

Add three new model classes following existing patterns (see `Task`, `WorkerRun`, etc.):

#### TaskOutcome Model
```python
class TaskOutcome(Base):
    """Structured outcome record for completed tasks."""
    __tablename__ = "task_outcomes"
    
    id = Column(String, primary_key=True)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False, index=True)
    worker_run_id = Column(Integer, ForeignKey("worker_runs.id"))
    agent_type = Column(String, nullable=False, index=True)
    
    # Outcome classification
    outcome_type = Column(String, nullable=False, index=True)  
    # Values: success/failure/partial/human_rejected/human_approved
    
    # Success metrics
    completed_at = Column(DateTime)
    duration_seconds = Column(Integer)
    retry_count = Column(Integer, default=0)
    escalation_tier = Column(Integer, default=0)
    
    # Human feedback (optional)
    human_feedback = Column(Text)
    review_state = Column(String)  # accepted/rejected/needs_revision
    feedback_category = Column(String, index=True)  # code_quality/wrong_approach/missing_tests/etc
    reviewed_at = Column(DateTime)
    reviewed_by = Column(String)
    
    # Learning system fields
    extracted_patterns = Column(JSON)  # Patterns detected in this outcome
    applied_learning_ids = Column(JSON)  # Learnings that were applied to this task
    extraction_status = Column(String, default="pending", index=True)  # pending/extracted/skipped
    
    # Metadata
    task_category = Column(String, index=True)  # bug_fix/feature/refactor/test/docs
    task_complexity = Column(String)  # simple/medium/complex
    context_hash = Column(String, index=True)  # For similarity matching
    
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
```

#### OutcomeLearning Model
```python
class OutcomeLearning(Base):
    """Extracted lessons from task outcomes."""
    __tablename__ = "outcome_learnings"
    
    id = Column(String, primary_key=True)
    agent_type = Column(String, nullable=False, index=True)
    
    # What did we learn?
    learning_type = Column(String, nullable=False)  # avoid_pattern/prefer_pattern/require_check/context_rule
    pattern_key = Column(String, nullable=False)  # Unique key (e.g., "missing_error_handling")
    description = Column(Text, nullable=False)  # Human-readable
    prompt_injection = Column(Text, nullable=False)  # What to inject into prompt
    
    # When to apply
    applies_to_categories = Column(JSON)  # Task categories (null = all)
    applies_to_complexity = Column(JSON)  # Complexity levels (null = all)
    trigger_keywords = Column(JSON)  # Keywords that activate this
    
    # Evidence
    source_outcome_ids = Column(JSON, nullable=False)  # TaskOutcome IDs
    evidence_strength = Column(Float, default=1.0, nullable=False)
    
    # Performance tracking
    times_applied = Column(Integer, default=0, nullable=False)
    success_when_applied = Column(Integer, default=0, nullable=False)
    failure_when_applied = Column(Integer, default=0, nullable=False)
    
    # Lifecycle
    active = Column(Boolean, default=True, nullable=False, index=True)
    confidence = Column(Float, default=0.5, nullable=False)  # 0-1
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    last_applied_at = Column(DateTime)
    
    # A/B testing
    strategy_variant = Column(String)
    
    __table_args__ = (
        UniqueConstraint("agent_type", "pattern_key", name="ix_outcome_learning_unique"),
    )
```

#### PromptStrategy Model (stub for future)
```python
class PromptStrategy(Base):
    """A/B testing framework for prompt approaches."""
    __tablename__ = "prompt_strategies"
    
    id = Column(String, primary_key=True)
    agent_type = Column(String, nullable=False, index=True)
    variant_name = Column(String, nullable=False)
    description = Column(Text)
    
    # Strategy configuration
    base_prompt_template = Column(Text, nullable=False)
    learning_injection_style = Column(String, nullable=False)  # inline/prefix/suffix/structured
    max_learnings_to_inject = Column(Integer, default=5, nullable=False)
    learning_selection_strategy = Column(String, default="confidence_weighted", nullable=False)
    
    # A/B testing
    active = Column(Boolean, default=True, nullable=False, index=True)
    weight = Column(Float, default=1.0, nullable=False)
    
    # Performance tracking
    tasks_executed = Column(Integer, default=0, nullable=False)
    tasks_succeeded = Column(Integer, default=0, nullable=False)
    avg_review_acceptance_rate = Column(Float)
    avg_duration_seconds = Column(Float)
    
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    
    __table_args__ = (
        UniqueConstraint("agent_type", "variant_name", name="ix_prompt_strategy_unique"),
    )
```

### 2. Create Alembic Migration

```bash
cd /Users/lobs/lobs-server
source .venv/bin/activate
alembic revision -m "add_agent_learning_tables"
```

Edit the generated migration file to create all three tables with proper indexes.

**Migration should:**
- Create tables in order (no circular dependencies)
- Create all indexes specified above
- Use `batch_alter_table` for SQLite compatibility if adding FKs
- Be reversible (implement `downgrade()`)

### 3. Test Migration

```bash
# Upgrade
alembic upgrade head

# Verify tables exist
sqlite3 lobs.db ".tables" | grep -E "(task_outcomes|outcome_learnings|prompt_strategies)"

# Verify schema
sqlite3 lobs.db ".schema task_outcomes"

# Downgrade
alembic downgrade -1

# Upgrade again
alembic upgrade head
```

### 4. Add Unit Tests

Create `tests/models/test_learning_models.py`:

```python
import pytest
from datetime import datetime, timezone
from app.models import TaskOutcome, OutcomeLearning, PromptStrategy

@pytest.mark.asyncio
async def test_task_outcome_creation(db_session):
    """Test creating a TaskOutcome record."""
    outcome = TaskOutcome(
        id="test-outcome-1",
        task_id="test-task-1",
        agent_type="programmer",
        outcome_type="success",
        task_category="feature",
        context_hash="abc123",
    )
    db_session.add(outcome)
    await db_session.commit()
    
    # Verify
    result = await db_session.get(TaskOutcome, "test-outcome-1")
    assert result.agent_type == "programmer"
    assert result.outcome_type == "success"

@pytest.mark.asyncio
async def test_outcome_learning_creation(db_session):
    """Test creating an OutcomeLearning record."""
    learning = OutcomeLearning(
        id="test-learning-1",
        agent_type="programmer",
        learning_type="require_check",
        pattern_key="require_tests",
        description="Always write tests",
        prompt_injection="IMPORTANT: Write unit tests.",
        source_outcome_ids=["outcome-1", "outcome-2"],
    )
    db_session.add(learning)
    await db_session.commit()
    
    # Verify
    result = await db_session.get(OutcomeLearning, "test-learning-1")
    assert result.pattern_key == "require_tests"
    assert result.times_applied == 0

@pytest.mark.asyncio
async def test_learning_unique_constraint(db_session):
    """Test that agent_type + pattern_key is unique."""
    learning1 = OutcomeLearning(
        id="test-learning-1",
        agent_type="programmer",
        pattern_key="require_tests",
        description="Test 1",
        prompt_injection="Test 1",
        source_outcome_ids=[],
    )
    db_session.add(learning1)
    await db_session.commit()
    
    # Try to create duplicate
    learning2 = OutcomeLearning(
        id="test-learning-2",
        agent_type="programmer",
        pattern_key="require_tests",  # Same pattern_key
        description="Test 2",
        prompt_injection="Test 2",
        source_outcome_ids=[],
    )
    db_session.add(learning2)
    
    with pytest.raises(Exception):  # Should raise IntegrityError
        await db_session.commit()
```

---

## Acceptance Criteria

- [ ] Three new models added to `app/models.py`
- [ ] Alembic migration created and tested
- [ ] `alembic upgrade head` runs successfully
- [ ] All tables and indexes created correctly
- [ ] `alembic downgrade -1` works (reversible)
- [ ] Unit tests pass
- [ ] Can create records for all three models
- [ ] Unique constraint on `OutcomeLearning` works
- [ ] Foreign keys work (TaskOutcome -> Task, WorkerRun)

---

## Testing Commands

```bash
# Setup
cd /Users/lobs/lobs-server
source .venv/bin/activate

# Create migration
alembic revision -m "add_agent_learning_tables"

# Test migration
alembic upgrade head
alembic downgrade -1
alembic upgrade head

# Run unit tests
python -m pytest tests/models/test_learning_models.py -v

# Inspect database
sqlite3 lobs.db ".tables"
sqlite3 lobs.db ".schema task_outcomes"
```

---

## Notes

- Follow existing model patterns (see `Task`, `WorkerRun`, `AgentReflection`)
- Use `func.now()` for timestamps (existing pattern)
- JSON columns for arrays (SQLite doesn't have native array type)
- All string IDs use UUID (generate with `str(uuid.uuid4())`)
- Index on frequently queried columns (agent_type, outcome_type, etc.)
- Foreign keys should use `nullable=True` where optional

---

## Questions?

Ping architect if:
- Schema constraints need clarification
- Index strategy needs review
- Migration has SQLite compatibility issues
