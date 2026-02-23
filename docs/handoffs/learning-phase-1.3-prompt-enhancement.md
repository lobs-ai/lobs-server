# Handoff: Learning System Phase 1.3 - Prompt Enhancement

**Initiative:** agent-learning-system  
**Phase:** 1.3  
**To:** Programmer  
**Priority:** High  
**Estimated Complexity:** Medium (3-5 days)  
**Depends On:** Phase 1.1 (Database), Phase 1.2 (Pattern Extraction)

---

## Context

Implement prompt enhancement by injecting relevant learnings into task prompts before execution. This completes the learning loop: outcomes → learnings → enhanced prompts → better outcomes.

When a task is about to execute, query relevant learnings based on agent type, task category, complexity, and context hash, then inject the top N learnings into the prompt.

**Design Document:** `/Users/lobs/lobs-server/docs/agent-learning-system.md` (Section: Component 3)

---

## Objectives

1. Implement `PromptEnhancer` service class
2. Integrate with `prompter.py` to enhance all task prompts
3. Add feature flag for safe rollout
4. Track which learnings were applied to each task
5. Write comprehensive tests

---

## Technical Specifications

### 1. PromptEnhancer Service

**File:** `app/orchestrator/prompt_enhancer.py`

```python
"""Prompt enhancement for agent learning system."""

import logging
import os
from typing import List, Optional

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, OutcomeLearning

logger = logging.getLogger(__name__)

# Configuration
LEARNING_INJECTION_ENABLED = os.getenv("LEARNING_INJECTION_ENABLED", "true").lower() == "true"
MAX_LEARNINGS_PER_PROMPT = int(os.getenv("MAX_LEARNINGS_PER_PROMPT", "3"))
MIN_CONFIDENCE_THRESHOLD = float(os.getenv("MIN_CONFIDENCE_THRESHOLD", "0.3"))


class PromptEnhancer:
    """Enhances task prompts with relevant learnings."""
    
    @staticmethod
    async def enhance_prompt(
        db: AsyncSession,
        base_prompt: str,
        task: Task,
        agent_type: str,
        learning_disabled: bool = False,
    ) -> tuple[str, List[str]]:
        """
        Enhance prompt with relevant learnings.
        
        Args:
            db: Database session
            base_prompt: Original prompt from Prompter
            task: Task being executed
            agent_type: Agent type executing the task
            learning_disabled: If True, skip enhancement (A/B control group)
            
        Returns:
            Tuple of (enhanced_prompt, list of applied learning IDs)
        """
        # Feature flag check
        if not LEARNING_INJECTION_ENABLED:
            logger.debug("[LEARNING] Prompt enhancement disabled by feature flag")
            return base_prompt, []
        
        # A/B test control group
        if learning_disabled:
            logger.debug(f"[LEARNING] Skipping enhancement for task {task.id} (control group)")
            return base_prompt, []
        
        # Query relevant learnings
        learnings = await PromptEnhancer._query_relevant_learnings(
            db=db,
            agent_type=agent_type,
            task=task,
        )
        
        if not learnings:
            logger.debug(f"[LEARNING] No relevant learnings found for task {task.id}")
            return base_prompt, []
        
        # Select top N learnings
        selected = PromptEnhancer._select_learnings(learnings)
        
        if not selected:
            return base_prompt, []
        
        # Inject learnings into prompt
        enhanced = PromptEnhancer._inject_learnings(base_prompt, selected)
        
        learning_ids = [l.id for l in selected]
        
        logger.info(
            f"[LEARNING] PromptEnhancer: Injected {len(selected)} learnings into task {task.id}: "
            f"{[l.pattern_name for l in selected]}"
        )
        
        return enhanced, learning_ids
    
    @staticmethod
    async def _query_relevant_learnings(
        db: AsyncSession,
        agent_type: str,
        task: Task,
    ) -> List[OutcomeLearning]:
        """
        Query learnings relevant to this task.
        
        Matches on:
        - Agent type (exact match)
        - Task category (if learning specifies one)
        - Task complexity (if learning specifies one)
        - Context hash (if learning specifies one)
        - Active learnings only
        - Confidence above threshold
        """
        from app.orchestrator.outcome_tracker import OutcomeTracker
        
        # Infer task properties (same logic as OutcomeTracker)
        task_category = OutcomeTracker._infer_task_category(task)
        task_complexity = OutcomeTracker._infer_task_complexity(task)
        context_hash = OutcomeTracker._compute_context_hash(task)
        
        # Build query
        stmt = select(OutcomeLearning).where(
            and_(
                OutcomeLearning.agent_type == agent_type,
                OutcomeLearning.is_active == True,
                OutcomeLearning.confidence >= MIN_CONFIDENCE_THRESHOLD,
            )
        )
        
        # Match category: either learning has no category (applies to all) or matches task
        stmt = stmt.where(
            or_(
                OutcomeLearning.task_category.is_(None),
                OutcomeLearning.task_category == task_category,
            )
        )
        
        # Match complexity: either learning has no complexity or matches task
        stmt = stmt.where(
            or_(
                OutcomeLearning.task_complexity.is_(None),
                OutcomeLearning.task_complexity == task_complexity,
            )
        )
        
        # Sort by: context match first, then confidence, then success count
        # For V1, just sort by confidence and success
        stmt = stmt.order_by(
            OutcomeLearning.confidence.desc(),
            OutcomeLearning.success_count.desc(),
        )
        
        stmt = stmt.limit(MAX_LEARNINGS_PER_PROMPT * 3)  # Get more than we need, then filter
        
        result = await db.execute(stmt)
        learnings = result.scalars().all()
        
        logger.debug(
            f"[LEARNING] Query found {len(learnings)} candidate learnings "
            f"(agent={agent_type}, category={task_category}, complexity={task_complexity})"
        )
        
        return learnings
    
    @staticmethod
    def _select_learnings(learnings: List[OutcomeLearning]) -> List[OutcomeLearning]:
        """
        Select top N learnings to inject.
        
        V1: Simple top-N by confidence.
        Future: Could use diversity, recency, or other signals.
        """
        # Already sorted by confidence, just take top N
        selected = learnings[:MAX_LEARNINGS_PER_PROMPT]
        
        return selected
    
    @staticmethod
    def _inject_learnings(base_prompt: str, learnings: List[OutcomeLearning]) -> str:
        """
        Inject learnings into prompt using prefix style.
        
        Adds a "Lessons from Past Tasks" section at the top of the prompt.
        """
        if not learnings:
            return base_prompt
        
        lessons_section = "=== Lessons from Past Tasks ===\n\n"
        lessons_section += "Based on similar tasks, keep these in mind:\n\n"
        
        for i, learning in enumerate(learnings, 1):
            lessons_section += f"{i}. [{learning.pattern_name}] {learning.lesson_text}\n"
        
        lessons_section += "\n" + "=" * 32 + "\n\n"
        
        # Prepend to base prompt
        enhanced = lessons_section + base_prompt
        
        return enhanced
```

---

### 2. Integration with Prompter

**File:** `app/orchestrator/prompter.py`

Modify the `build_task_prompt` method to call PromptEnhancer:

```python
from app.orchestrator.prompt_enhancer import PromptEnhancer

class Prompter:
    @staticmethod
    async def build_task_prompt(
        db: AsyncSession,  # ADD THIS PARAMETER
        item: dict[str, Any],
        project_path: Path,
        agent_type: str | None = None,
        rules: str = "",
        learning_disabled: bool = False,  # ADD THIS PARAMETER
    ) -> tuple[str, List[str]]:  # RETURN TUPLE NOW
        """
        Build a complete prompt for an agent, enhanced with learnings.
        
        Returns:
            Tuple of (prompt_text, list of applied learning IDs)
        """
        # ... existing prompt building logic ...
        
        # Build base prompt (all existing logic)
        prompt = (
            agent_context
            + "# Work Assignment\n\n"
            # ... rest of existing prompt building ...
        )
        
        # Enhance with learnings
        task_obj = None  # Need to fetch from DB or reconstruct
        # Reconstruction approach (simpler, avoids extra query):
        from app.models import Task
        task_obj = Task(
            id=item.get("id", "unknown"),
            title=item.get("title", ""),
            notes=item.get("notes", ""),
            agent=agent_type,
            shape=item.get("shape"),
        )
        
        enhanced_prompt, learning_ids = await PromptEnhancer.enhance_prompt(
            db=db,
            base_prompt=prompt,
            task=task_obj,
            agent_type=normalized_agent_type,
            learning_disabled=learning_disabled,
        )
        
        return enhanced_prompt, learning_ids
```

**Note:** This is a breaking change to the Prompter API. All callers must be updated to:
1. Pass `db` parameter
2. Pass `learning_disabled` flag (from TaskOutcome)
3. Handle returned tuple `(prompt, learning_ids)`

---

### 3. Update Worker to Use Enhanced Prompts

**File:** `app/orchestrator/worker.py`

Update task execution to:
1. Determine if task is in control group
2. Pass `learning_disabled` flag to prompter
3. Store applied `learning_ids` in TaskOutcome

```python
# In WorkerManager or similar location where prompts are built:

from app.orchestrator.prompter import Prompter
from app.orchestrator.prompt_enhancer import PromptEnhancer
import random

async def _build_and_spawn_worker(db, task, project):
    """Build prompt and spawn worker with learning enhancement."""
    
    # Determine A/B test group
    CONTROL_GROUP_PCT = 0.20
    learning_disabled = random.random() < CONTROL_GROUP_PCT
    
    # Build enhanced prompt
    prompt, learning_ids = await Prompter.build_task_prompt(
        db=db,
        item=task.__dict__,
        project_path=Path(project.repo_path),
        agent_type=task.agent,
        rules=await _load_engineering_rules(db),
        learning_disabled=learning_disabled,
    )
    
    # Spawn worker
    worker_run = await _spawn_worker(prompt, task, project)
    
    # Track outcome with applied learnings
    outcome = await OutcomeTracker.track_completion(
        db=db,
        task=task,
        worker_run=worker_run,
        success=False,  # Will update later when task completes
    )
    outcome.applied_learnings = learning_ids
    outcome.learning_disabled = learning_disabled
    await db.commit()
    
    return worker_run
```

---

### 4. Update Outcome Tracking Integration

**File:** `app/orchestrator/outcome_tracker.py`

Add method to update applied learnings when they help or hurt:

```python
class OutcomeTracker:
    @staticmethod
    async def update_outcome_with_learnings(
        db: AsyncSession,
        task_id: str,
        learning_ids: List[str],
    ) -> Optional[TaskOutcome]:
        """
        Update outcome with applied learning IDs.
        
        Called after prompt enhancement but before task execution.
        """
        stmt = select(TaskOutcome).where(TaskOutcome.task_id == task_id)
        result = await db.execute(stmt)
        outcome = result.scalar_one_or_none()
        
        if not outcome:
            return None
        
        outcome.applied_learnings = learning_ids
        await db.commit()
        await db.refresh(outcome)
        
        return outcome
    
    @staticmethod
    async def finalize_outcome(
        db: AsyncSession,
        task: Task,
    ) -> Optional[TaskOutcome]:
        """
        Finalize outcome when task completes, update learning confidences.
        
        Called after task moves to completed/rejected state.
        """
        stmt = select(TaskOutcome).where(TaskOutcome.task_id == task.id)
        result = await db.execute(stmt)
        outcome = result.scalar_one_or_none()
        
        if not outcome:
            return None
        
        # Update success based on final state
        success = task.review_state in ['approved', 'auto_approved'] or \
                  (task.work_state == 'completed' and not task.review_state)
        
        outcome.success = success
        outcome.review_state = task.review_state
        outcome.updated_at = datetime.utcnow()
        
        # Update confidence of applied learnings
        if outcome.applied_learnings:
            from app.orchestrator.lesson_extractor import LessonExtractor
            for learning_id in outcome.applied_learnings:
                await LessonExtractor.update_learning_confidence(
                    db=db,
                    learning_id=learning_id,
                    success=success,
                )
        
        await db.commit()
        await db.refresh(outcome)
        
        logger.info(
            f"[LEARNING] Finalized outcome {outcome.id} (task={task.id}, success={success}, "
            f"learnings_applied={len(outcome.applied_learnings or [])})"
        )
        
        return outcome
```

---

## Testing Requirements

### Unit Tests

**File:** `tests/test_prompt_enhancer.py`

```python
"""Tests for PromptEnhancer."""

import pytest
from app.orchestrator.prompt_enhancer import PromptEnhancer
from app.models import Task, OutcomeLearning

@pytest.mark.asyncio
async def test_query_relevant_learnings(db, sample_task):
    """Test querying learnings relevant to a task."""
    # Create some learnings
    learning1 = OutcomeLearning(
        id="learn-1",
        agent_type="programmer",
        pattern_name="missing_tests",
        lesson_text="Always add tests",
        task_category="feature",
        confidence=0.8,
        is_active=True,
    )
    learning2 = OutcomeLearning(
        id="learn-2",
        agent_type="programmer",
        pattern_name="unclear_names",
        lesson_text="Use clear names",
        confidence=0.6,
        is_active=True,
    )
    learning3 = OutcomeLearning(
        id="learn-3",
        agent_type="researcher",  # Different agent
        pattern_name="bad_sources",
        lesson_text="Avoid forums",
        confidence=0.9,
        is_active=True,
    )
    db.add_all([learning1, learning2, learning3])
    await db.commit()
    
    # Query for programmer
    learnings = await PromptEnhancer._query_relevant_learnings(
        db=db,
        agent_type="programmer",
        task=sample_task,
    )
    
    # Should only get programmer learnings
    assert len(learnings) == 2
    assert all(l.agent_type == "programmer" for l in learnings)
    assert learning3 not in learnings

@pytest.mark.asyncio
async def test_select_learnings_limits_count(db):
    """Test that only top N learnings are selected."""
    # Create 10 learnings
    learnings = [
        OutcomeLearning(
            id=f"learn-{i}",
            agent_type="programmer",
            pattern_name=f"pattern{i}",
            lesson_text=f"Lesson {i}",
            confidence=0.9 - (i * 0.05),  # Decreasing confidence
            is_active=True,
        )
        for i in range(10)
    ]
    
    selected = PromptEnhancer._select_learnings(learnings)
    
    # Should respect MAX_LEARNINGS_PER_PROMPT (default=3)
    assert len(selected) <= 3
    # Should be highest confidence
    assert selected[0].confidence > selected[-1].confidence

def test_inject_learnings_prefix_style():
    """Test learning injection with prefix style."""
    learnings = [
        OutcomeLearning(
            id="1",
            pattern_name="missing_tests",
            lesson_text="Always include unit tests.",
            confidence=0.8,
        ),
        OutcomeLearning(
            id="2",
            pattern_name="error_handling",
            lesson_text="Add try/except blocks.",
            confidence=0.7,
        ),
    ]
    
    base_prompt = "## Your Task\n\nImplement user login."
    enhanced = PromptEnhancer._inject_learnings(base_prompt, learnings)
    
    # Check structure
    assert "=== Lessons from Past Tasks ===" in enhanced
    assert "[missing_tests]" in enhanced
    assert "Always include unit tests." in enhanced
    assert "[error_handling]" in enhanced
    assert "Add try/except blocks." in enhanced
    assert "## Your Task" in enhanced  # Original prompt preserved

@pytest.mark.asyncio
async def test_enhance_prompt_full_flow(db, sample_task):
    """Test full enhancement flow."""
    # Create learning
    learning = OutcomeLearning(
        id="learn-1",
        agent_type="programmer",
        pattern_name="missing_tests",
        lesson_text="Always add tests",
        confidence=0.8,
        is_active=True,
    )
    db.add(learning)
    await db.commit()
    
    base_prompt = "Implement feature X"
    
    enhanced, learning_ids = await PromptEnhancer.enhance_prompt(
        db=db,
        base_prompt=base_prompt,
        task=sample_task,
        agent_type="programmer",
        learning_disabled=False,
    )
    
    assert "Lessons from Past Tasks" in enhanced
    assert "Always add tests" in enhanced
    assert len(learning_ids) == 1
    assert learning_ids[0] == "learn-1"

@pytest.mark.asyncio
async def test_enhance_prompt_respects_control_group(db, sample_task):
    """Test that control group gets unmodified prompt."""
    base_prompt = "Implement feature X"
    
    enhanced, learning_ids = await PromptEnhancer.enhance_prompt(
        db=db,
        base_prompt=base_prompt,
        task=sample_task,
        agent_type="programmer",
        learning_disabled=True,  # Control group
    )
    
    # Should be unchanged
    assert enhanced == base_prompt
    assert learning_ids == []

@pytest.mark.asyncio
async def test_enhance_prompt_respects_feature_flag(db, sample_task, monkeypatch):
    """Test that feature flag disables enhancement."""
    monkeypatch.setattr("app.orchestrator.prompt_enhancer.LEARNING_INJECTION_ENABLED", False)
    
    base_prompt = "Implement feature X"
    
    enhanced, learning_ids = await PromptEnhancer.enhance_prompt(
        db=db,
        base_prompt=base_prompt,
        task=sample_task,
        agent_type="programmer",
        learning_disabled=False,
    )
    
    # Should be unchanged
    assert enhanced == base_prompt
    assert learning_ids == []
```

### Integration Tests

**File:** `tests/test_learning_integration.py`

```python
"""Integration tests for full learning cycle."""

import pytest
from app.orchestrator.outcome_tracker import OutcomeTracker
from app.orchestrator.lesson_extractor import LessonExtractor
from app.orchestrator.prompt_enhancer import PromptEnhancer
from app.models import Task

@pytest.mark.asyncio
async def test_full_learning_cycle(db):
    """Test complete flow: failure → feedback → learning → enhanced prompt."""
    
    # 1. Task fails with feedback
    task1 = Task(
        id="task-1",
        title="Add user endpoint",
        agent="programmer",
        status="completed",
        work_state="completed",
        review_state="needs_revision",
    )
    db.add(task1)
    await db.commit()
    
    outcome1 = await OutcomeTracker.track_completion(
        db=db, task=task1, worker_run=None, success=False
    )
    
    await OutcomeTracker.track_human_feedback(
        db=db,
        task_id=task1.id,
        feedback_text="Missing tests and input validation for email field",
    )
    
    # 2. Extract learnings
    learnings = await LessonExtractor.extract_from_outcome(db, outcome1)
    assert len(learnings) >= 1
    assert any(l.pattern_name == 'missing_tests' for l in learnings)
    
    # 3. Next task gets enhanced prompt
    task2 = Task(
        id="task-2",
        title="Add delete user endpoint",
        agent="programmer",
        status="active",
    )
    db.add(task2)
    await db.commit()
    
    base_prompt = "Implement delete user endpoint"
    enhanced, learning_ids = await PromptEnhancer.enhance_prompt(
        db=db,
        base_prompt=base_prompt,
        task=task2,
        agent_type="programmer",
        learning_disabled=False,
    )
    
    # Should contain learning about tests
    assert "test" in enhanced.lower()
    assert len(learning_ids) >= 1
    
    # 4. Task succeeds, confidence increases
    outcome2 = await OutcomeTracker.track_completion(
        db=db, task=task2, worker_run=None, success=True
    )
    outcome2.applied_learnings = learning_ids
    await db.commit()
    
    await OutcomeTracker.finalize_outcome(db, task2)
    
    # Check that learning confidence increased
    from sqlalchemy import select
    from app.models import OutcomeLearning
    stmt = select(OutcomeLearning).where(OutcomeLearning.id == learning_ids[0])
    result = await db.execute(stmt)
    learning = result.scalar_one()
    
    assert learning.success_count == 1
    assert learning.confidence > 0.5  # Should have increased
```

---

## Acceptance Criteria

- ✅ `PromptEnhancer` class implemented with all methods
- ✅ Integration with `prompter.py` - prompts are enhanced before task execution
- ✅ Feature flag `LEARNING_INJECTION_ENABLED` controls enhancement (default=true)
- ✅ Configuration via environment variables (MAX_LEARNINGS_PER_PROMPT, MIN_CONFIDENCE_THRESHOLD)
- ✅ A/B testing: control group gets unmodified prompts
- ✅ Applied learning IDs tracked in TaskOutcome
- ✅ Learning confidence updated when tasks complete
- ✅ Prefix injection style: clean separator, numbered list
- ✅ Query logic: matches agent, category, complexity, active status, confidence threshold
- ✅ Unit tests pass (>80% coverage)
- ✅ Integration test demonstrates full learning cycle
- ✅ Logging: All operations log with `[LEARNING]` prefix
- ✅ Performance: Enhancement adds <200ms to prompt building
- ✅ Graceful degradation: If PromptEnhancer fails, return base prompt (don't break task)

---

## Breaking Changes

**Prompter API Change:**

```python
# OLD:
prompt = Prompter.build_task_prompt(item, project_path, agent_type, rules)

# NEW:
prompt, learning_ids = await Prompter.build_task_prompt(
    db, item, project_path, agent_type, rules, learning_disabled
)
```

**All callers must be updated:**
- `app/orchestrator/worker.py` - main integration point
- Any tests that call `build_task_prompt`
- Any other services that build prompts

**Migration Strategy:**
1. Make `db` parameter optional initially (with default None)
2. If `db=None`, skip enhancement and return `(prompt, [])`
3. Update callers incrementally
4. Remove backward compatibility once all callers updated

---

## Dependencies

- **Required:** Phase 1.1 (TaskOutcome tracking)
- **Required:** Phase 1.2 (OutcomeLearning creation)

---

## Notes

- **Defensive coding:** If PromptEnhancer fails, catch exception and return base prompt
- **Performance:** Query should be fast (<100ms) - indexes are critical
- **Prompt size:** Monitor total prompt length, may need to truncate if learnings make it too large
- **Testing strategy:** Focus on integration test that proves end-to-end flow works
- **Feature flag:** Allows quick disable if problems arise in production

---

## Questions?

Contact architect or reference design doc: `/Users/lobs/lobs-server/docs/agent-learning-system.md`
