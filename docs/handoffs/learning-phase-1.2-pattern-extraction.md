# Handoff: Learning System Phase 1.2 - Pattern Extraction

**Initiative:** agent-learning-system  
**Phase:** 1.2  
**To:** Programmer  
**Priority:** High  
**Estimated Complexity:** Medium (3-5 days)  
**Depends On:** Phase 1.1 (Database & Tracking)

---

## Context

Implement pattern extraction from task outcomes to create actionable learnings. When a task fails or receives human feedback, this system analyzes the feedback text to detect patterns (e.g., "missing tests", "unclear names") and creates `OutcomeLearning` records that can be used to improve future tasks.

**Design Document:** `/Users/lobs/lobs-server/docs/agent-learning-system.md` (Section: Component 2)

---

## Objectives

1. Create `outcome_learnings` table
2. Implement `LessonExtractor` service class
3. Add 4+ programmer-specific pattern detectors
4. Create CLI command for manual extraction
5. Write comprehensive tests

---

## Technical Specifications

### 1. Database Schema

**File:** Create migration `alembic/versions/XXX_add_outcome_learnings.py`

```python
"""Add outcome_learnings table for learning system.

Revision ID: XXX
"""

from alembic import op
import sqlalchemy as sa

def upgrade():
    op.create_table(
        'outcome_learnings',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('agent_type', sa.String(50), nullable=False),
        sa.Column('pattern_name', sa.String(100), nullable=False),
        sa.Column('lesson_text', sa.Text(), nullable=False),
        sa.Column('task_category', sa.String(50), nullable=True),
        sa.Column('task_complexity', sa.String(20), nullable=True),
        sa.Column('context_hash', sa.String(64), nullable=True),
        sa.Column('confidence', sa.Float(), default=1.0, nullable=False),
        sa.Column('success_count', sa.Integer(), default=0, nullable=False),
        sa.Column('failure_count', sa.Integer(), default=0, nullable=False),
        sa.Column('source_outcome_ids', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), default=True, nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    op.create_index('idx_learnings_agent_active', 'outcome_learnings', ['agent_type', 'is_active'])
    op.create_index('idx_learnings_context', 'outcome_learnings', ['context_hash'])
    op.create_index('idx_learnings_confidence', 'outcome_learnings', ['confidence'], postgresql_ops={'confidence': 'DESC'})
    op.create_index('idx_learnings_pattern', 'outcome_learnings', ['agent_type', 'pattern_name'])

def downgrade():
    op.drop_table('outcome_learnings')
```

**Model:** Add to `app/models.py`

```python
class OutcomeLearning(Base):
    """Outcome learning model for agent learning system."""
    __tablename__ = "outcome_learnings"
    
    id = Column(String, primary_key=True)
    agent_type = Column(String(50), nullable=False)
    pattern_name = Column(String(100), nullable=False)  # e.g., 'missing_tests'
    lesson_text = Column(Text, nullable=False)  # What to do differently
    task_category = Column(String(50))  # Which task types this applies to
    task_complexity = Column(String(20))  # Complexity filter (optional)
    context_hash = Column(String(64))  # Similar task identifier
    confidence = Column(Float, default=1.0, nullable=False)  # 0.0-1.0
    success_count = Column(Integer, default=0, nullable=False)
    failure_count = Column(Integer, default=0, nullable=False)
    source_outcome_ids = Column(JSON)  # List of TaskOutcome IDs
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
```

---

### 2. LessonExtractor Service

**File:** `app/orchestrator/lesson_extractor.py`

```python
"""Lesson extraction for agent learning system."""

import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TaskOutcome, OutcomeLearning

logger = logging.getLogger(__name__)

# Minimum feedback length to process
MIN_FEEDBACK_LENGTH = 20


class PatternDetector:
    """Pattern detection rules for programmer agent."""
    
    PATTERNS = {
        'missing_tests': {
            'triggers': [
                r'\bno tests?\b',
                r'\badd tests?\b',
                r'\bmissing tests?\b',
                r'\bneeds? tests?\b',
                r'\btest coverage\b',
            ],
            'lesson': (
                "Always include unit tests for new functions and classes. "
                "Cover the happy path and at least 2-3 edge cases. "
                "Use descriptive test names that explain what is being tested."
            ),
            'categories': ['feature', 'bug_fix'],
        },
        'unclear_names': {
            'triggers': [
                r'\bunclear\b.*\bname',
                r'\bnaming\b',
                r'\bvariable name',
                r'\bbetter name',
                r'\brename\b',
                r'\bconfusing\b.*\bname',
            ],
            'lesson': (
                "Use descriptive variable and function names that clearly explain their purpose. "
                "Avoid single-letter names except for loop counters. "
                "Prefer clarity over brevity."
            ),
            'categories': None,  # Applies to all categories
        },
        'missing_error_handling': {
            'triggers': [
                r'\berror handling\b',
                r'\bexception\b',
                r'\btry.?catch\b',
                r'\btry.?except\b',
                r'\bhandle errors?\b',
                r'\bwhat if.*fails?\b',
            ],
            'lesson': (
                "Add try/except blocks for operations that can fail (file I/O, network, parsing). "
                "Handle errors gracefully with informative messages. "
                "Don't let exceptions crash the program unexpectedly."
            ),
            'categories': None,
        },
        'missing_docstrings': {
            'triggers': [
                r'\bdocstring',
                r'\bdocumentation\b',
                r'\bcomment\b',
                r'\bdescribe\b.*\bfunction',
                r'\bexplain\b.*\bwhat',
            ],
            'lesson': (
                "Add docstrings to all public functions and classes. "
                "Include: brief description, parameters (with types), return value, and any exceptions raised. "
                "Follow PEP 257 conventions."
            ),
            'categories': None,
        },
        'missing_validation': {
            'triggers': [
                r'\bvalidation\b',
                r'\bvalidate\b.*\binput',
                r'\bcheck\b.*\binput',
                r'\binvalid\b.*\bdata',
                r'\bsanitize\b',
            ],
            'lesson': (
                "Always validate user inputs before processing. "
                "Check for required fields, correct types, valid ranges, and expected formats. "
                "Return clear error messages for invalid input."
            ),
            'categories': ['feature', 'bug_fix'],
        },
    }
    
    @classmethod
    def detect(cls, feedback_text: str) -> List[str]:
        """
        Detect patterns in feedback text.
        
        Returns:
            List of pattern names detected
        """
        if not feedback_text or len(feedback_text) < MIN_FEEDBACK_LENGTH:
            return []
        
        detected = []
        feedback_lower = feedback_text.lower()
        
        for pattern_name, config in cls.PATTERNS.items():
            for trigger_regex in config['triggers']:
                if re.search(trigger_regex, feedback_lower, re.IGNORECASE):
                    detected.append(pattern_name)
                    break  # Only count pattern once
        
        return detected


class LessonExtractor:
    """Extracts lessons from task outcomes."""
    
    @staticmethod
    async def extract_from_outcome(
        db: AsyncSession,
        outcome: TaskOutcome,
    ) -> List[OutcomeLearning]:
        """
        Extract learnings from a single outcome.
        
        Args:
            db: Database session
            outcome: TaskOutcome to analyze
            
        Returns:
            List of created OutcomeLearning records
        """
        if not outcome.human_feedback:
            return []
        
        if len(outcome.human_feedback) < MIN_FEEDBACK_LENGTH:
            logger.debug(
                f"[LEARNING] Skipping outcome {outcome.id}: feedback too short "
                f"({len(outcome.human_feedback)} chars)"
            )
            return []
        
        # Detect patterns
        patterns = PatternDetector.detect(outcome.human_feedback)
        if not patterns:
            logger.debug(
                f"[LEARNING] No patterns detected in outcome {outcome.id}"
            )
            return []
        
        logger.info(
            f"[LEARNING] LessonExtractor: Detected {len(patterns)} patterns in outcome {outcome.id}: "
            f"{patterns}"
        )
        
        learnings = []
        for pattern_name in patterns:
            learning = await LessonExtractor._create_or_update_learning(
                db=db,
                agent_type=outcome.agent_type,
                pattern_name=pattern_name,
                task_category=outcome.task_category,
                task_complexity=outcome.task_complexity,
                context_hash=outcome.context_hash,
                source_outcome_id=outcome.id,
            )
            if learning:
                learnings.append(learning)
        
        return learnings
    
    @staticmethod
    async def _create_or_update_learning(
        db: AsyncSession,
        agent_type: str,
        pattern_name: str,
        task_category: Optional[str],
        task_complexity: Optional[str],
        context_hash: Optional[str],
        source_outcome_id: str,
    ) -> Optional[OutcomeLearning]:
        """
        Create new learning or update existing one.
        
        If a learning for this pattern + category already exists, update it.
        Otherwise, create new.
        """
        pattern_config = PatternDetector.PATTERNS.get(pattern_name)
        if not pattern_config:
            logger.warning(f"[LEARNING] Unknown pattern: {pattern_name}")
            return None
        
        # Check for existing learning
        stmt = select(OutcomeLearning).where(
            and_(
                OutcomeLearning.agent_type == agent_type,
                OutcomeLearning.pattern_name == pattern_name,
                OutcomeLearning.task_category == task_category,
            )
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        
        if existing:
            # Update existing learning
            source_ids = existing.source_outcome_ids or []
            if source_outcome_id not in source_ids:
                source_ids.append(source_outcome_id)
            existing.source_outcome_ids = source_ids
            existing.confidence = min(1.0, existing.confidence + 0.05)  # Slight boost
            existing.updated_at = datetime.utcnow()
            
            await db.commit()
            await db.refresh(existing)
            
            logger.info(
                f"[LEARNING] Updated learning {existing.id} (pattern={pattern_name}, "
                f"confidence={existing.confidence:.2f})"
            )
            return existing
        
        # Create new learning
        learning = OutcomeLearning(
            id=str(uuid4()),
            agent_type=agent_type,
            pattern_name=pattern_name,
            lesson_text=pattern_config['lesson'],
            task_category=task_category if pattern_config['categories'] else None,
            task_complexity=task_complexity,
            context_hash=context_hash,
            confidence=0.5,  # Start conservative
            success_count=0,
            failure_count=0,
            source_outcome_ids=[source_outcome_id],
            is_active=True,
        )
        
        db.add(learning)
        await db.commit()
        await db.refresh(learning)
        
        logger.info(
            f"[LEARNING] Created learning {learning.id} (pattern={pattern_name}, "
            f"category={task_category}, agent={agent_type})"
        )
        
        return learning
    
    @staticmethod
    async def extract_recent(
        db: AsyncSession,
        agent_type: Optional[str] = None,
        since_hours: int = 24,
    ) -> List[OutcomeLearning]:
        """
        Extract learnings from recent outcomes.
        
        Args:
            db: Database session
            agent_type: Filter by agent type (optional)
            since_hours: Look back this many hours
            
        Returns:
            List of all learnings created
        """
        since_time = datetime.utcnow() - timedelta(hours=since_hours)
        
        # Query recent outcomes with feedback
        stmt = select(TaskOutcome).where(
            and_(
                TaskOutcome.created_at >= since_time,
                TaskOutcome.human_feedback.isnot(None),
            )
        )
        
        if agent_type:
            stmt = stmt.where(TaskOutcome.agent_type == agent_type)
        
        result = await db.execute(stmt)
        outcomes = result.scalars().all()
        
        logger.info(
            f"[LEARNING] LessonExtractor: Processing {len(outcomes)} recent outcomes "
            f"(agent={agent_type or 'all'}, since={since_hours}h)"
        )
        
        all_learnings = []
        for outcome in outcomes:
            learnings = await LessonExtractor.extract_from_outcome(db, outcome)
            all_learnings.extend(learnings)
        
        logger.info(
            f"[LEARNING] LessonExtractor: Created/updated {len(all_learnings)} learnings"
        )
        
        return all_learnings
    
    @staticmethod
    async def update_learning_confidence(
        db: AsyncSession,
        learning_id: str,
        success: bool,
    ) -> Optional[OutcomeLearning]:
        """
        Update learning confidence based on outcome.
        
        Called when a task that used this learning completes.
        """
        stmt = select(OutcomeLearning).where(OutcomeLearning.id == learning_id)
        result = await db.execute(stmt)
        learning = result.scalar_one_or_none()
        
        if not learning:
            return None
        
        if success:
            learning.success_count += 1
            learning.confidence = min(1.0, learning.confidence + 0.1)
        else:
            learning.failure_count += 1
            learning.confidence = max(0.0, learning.confidence - 0.05)
        
        # Deactivate if confidence too low
        if learning.confidence < 0.2 and learning.failure_count > 5:
            learning.is_active = False
            logger.warning(
                f"[LEARNING] Deactivated learning {learning_id} due to low confidence "
                f"({learning.confidence:.2f})"
            )
        
        learning.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(learning)
        
        return learning
```

---

### 3. CLI Command

**File:** `app/cli/extract_learnings.py`

```python
"""CLI command for extracting learnings from outcomes."""

import asyncio
import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.database import AsyncSessionLocal
from app.orchestrator.lesson_extractor import LessonExtractor

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


async def main(agent_type: str | None, since_hours: int):
    """Extract learnings from recent outcomes."""
    async with AsyncSessionLocal() as db:
        learnings = await LessonExtractor.extract_recent(
            db=db,
            agent_type=agent_type,
            since_hours=since_hours,
        )
        
        print(f"\n✅ Extracted {len(learnings)} learnings\n")
        
        for learning in learnings:
            print(f"• {learning.pattern_name} (confidence={learning.confidence:.2f})")
            print(f"  {learning.lesson_text[:80]}...")
            print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract learnings from task outcomes")
    parser.add_argument(
        "--agent",
        help="Filter by agent type (programmer, researcher, etc.)",
        default=None,
    )
    parser.add_argument(
        "--since",
        help="Look back this many hours (default: 24)",
        type=int,
        default=24,
    )
    
    args = parser.parse_args()
    
    asyncio.run(main(args.agent, args.since))
```

**Make executable:**
```bash
chmod +x app/cli/extract_learnings.py
```

---

### 4. API Endpoints

Add to `app/routers/learning.py`:

```python
from app.models import OutcomeLearning
from app.orchestrator.lesson_extractor import LessonExtractor

@router.get("/learnings", response_model=List[dict])
async def list_learnings(
    agent: Optional[str] = None,
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """List all learnings for an agent."""
    stmt = select(OutcomeLearning)
    
    if agent:
        stmt = stmt.where(OutcomeLearning.agent_type == agent)
    if active_only:
        stmt = stmt.where(OutcomeLearning.is_active == True)
    
    stmt = stmt.order_by(OutcomeLearning.confidence.desc())
    
    result = await db.execute(stmt)
    learnings = result.scalars().all()
    
    return [
        {
            "id": l.id,
            "pattern_name": l.pattern_name,
            "lesson_text": l.lesson_text,
            "task_category": l.task_category,
            "confidence": l.confidence,
            "success_count": l.success_count,
            "failure_count": l.failure_count,
            "is_active": l.is_active,
            "created_at": l.created_at.isoformat(),
        }
        for l in learnings
    ]


@router.post("/extract", response_model=dict)
async def trigger_extraction(
    agent: Optional[str] = None,
    since_hours: int = 24,
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger learning extraction."""
    learnings = await LessonExtractor.extract_recent(
        db=db,
        agent_type=agent,
        since_hours=since_hours,
    )
    
    return {
        "extracted": len(learnings),
        "agent": agent or "all",
        "since_hours": since_hours,
    }
```

---

## Testing Requirements

### Unit Tests

**File:** `tests/test_lesson_extractor.py`

```python
"""Tests for LessonExtractor."""

import pytest
from app.orchestrator.lesson_extractor import LessonExtractor, PatternDetector
from app.models import TaskOutcome

def test_pattern_detection_missing_tests():
    """Test detection of missing tests pattern."""
    feedback = "Code looks good but please add tests for the new functions"
    patterns = PatternDetector.detect(feedback)
    assert 'missing_tests' in patterns

def test_pattern_detection_multiple():
    """Test detection of multiple patterns."""
    feedback = "Missing tests, unclear variable names, and no error handling"
    patterns = PatternDetector.detect(feedback)
    assert 'missing_tests' in patterns
    assert 'unclear_names' in patterns
    assert 'missing_error_handling' in patterns

def test_pattern_detection_no_match():
    """Test that feedback without patterns returns empty."""
    feedback = "Looks great, approved!"
    patterns = PatternDetector.detect(feedback)
    assert patterns == []

def test_pattern_detection_too_short():
    """Test that very short feedback is ignored."""
    feedback = "Fix this"
    patterns = PatternDetector.detect(feedback)
    assert patterns == []

@pytest.mark.asyncio
async def test_extract_from_outcome_creates_learning(db):
    """Test extracting learning from outcome with feedback."""
    outcome = TaskOutcome(
        id="test-outcome",
        task_id="test-task",
        agent_type="programmer",
        success=False,
        task_category="feature",
        human_feedback="Please add unit tests for the new authentication logic",
    )
    db.add(outcome)
    await db.commit()
    
    learnings = await LessonExtractor.extract_from_outcome(db, outcome)
    
    assert len(learnings) == 1
    assert learnings[0].pattern_name == 'missing_tests'
    assert learnings[0].agent_type == 'programmer'
    assert learnings[0].confidence == 0.5  # Initial confidence

@pytest.mark.asyncio
async def test_update_learning_confidence(db, sample_learning):
    """Test updating learning confidence based on success/failure."""
    initial_confidence = sample_learning.confidence
    
    # Success increases confidence
    updated = await LessonExtractor.update_learning_confidence(
        db, sample_learning.id, success=True
    )
    assert updated.confidence > initial_confidence
    assert updated.success_count == 1
    
    # Failure decreases confidence
    updated = await LessonExtractor.update_learning_confidence(
        db, sample_learning.id, success=False
    )
    assert updated.failure_count == 1

@pytest.mark.asyncio
async def test_deactivate_low_confidence_learning(db, sample_learning):
    """Test that low-confidence learnings get deactivated."""
    sample_learning.confidence = 0.1
    sample_learning.failure_count = 6
    await db.commit()
    
    updated = await LessonExtractor.update_learning_confidence(
        db, sample_learning.id, success=False
    )
    
    assert updated.is_active == False
```

### Integration Tests

```python
"""Integration test for full extraction flow."""

@pytest.mark.asyncio
async def test_full_extraction_flow(db):
    """Test complete flow: outcome -> feedback -> extraction -> learning."""
    # Create task outcome with feedback
    outcome = TaskOutcome(
        id=str(uuid4()),
        task_id=str(uuid4()),
        agent_type="programmer",
        success=False,
        task_category="feature",
        human_feedback="Missing input validation and error handling for the API endpoint",
    )
    db.add(outcome)
    await db.commit()
    
    # Extract learnings
    learnings = await LessonExtractor.extract_from_outcome(db, outcome)
    
    # Should detect 2 patterns
    assert len(learnings) == 2
    patterns = {l.pattern_name for l in learnings}
    assert 'missing_validation' in patterns
    assert 'missing_error_handling' in patterns
    
    # Both should be active
    assert all(l.is_active for l in learnings)
    
    # Both should have low initial confidence
    assert all(0.4 <= l.confidence <= 0.6 for l in learnings)
```

---

## Acceptance Criteria

- ✅ `outcome_learnings` table created with all columns and indexes
- ✅ `OutcomeLearning` model added to `app/models.py`
- ✅ `LessonExtractor` class implemented with all methods
- ✅ `PatternDetector` class with 5+ patterns for programmer agent
- ✅ CLI command `python app/cli/extract_learnings.py --agent programmer` works
- ✅ GET `/api/learning/learnings?agent=programmer` endpoint works
- ✅ POST `/api/learning/extract` endpoint works
- ✅ Pattern detection correctly identifies patterns in feedback
- ✅ Learning creation: new learnings start at confidence=0.5
- ✅ Learning updates: existing learnings get confidence boost
- ✅ Confidence updates: success increases, failure decreases
- ✅ Low-confidence deactivation: confidence < 0.2 + failures > 5
- ✅ Unit tests pass (>80% coverage)
- ✅ Integration tests pass
- ✅ Logging: All operations log with `[LEARNING]` prefix

---

## Dependencies

- **Required:** Phase 1.1 (Database & Tracking) must be complete
  - `task_outcomes` table exists
  - `OutcomeTracker` is functional

---

## Notes

- Start with rule-based pattern detection (simple regex matching)
- Can upgrade to ML-based extraction later if needed
- Confidence starts conservative (0.5) and adjusts based on actual outcomes
- Patterns are hardcoded for V1 - can be made configurable later
- Focus on programmer agent first; researcher patterns come in Milestone 3

---

## Questions?

Contact architect or reference design doc: `/Users/lobs/lobs-server/docs/agent-learning-system.md`
