# Phase 1 Consolidated Rescue Plan — Agent Learning MVP

**Initiative:** agent-learning-system  
**Task:** 498C8166-D5AA-4BF1-BFCD-54CE726F2707  
**Author:** Architect (rescue attempt #5)  
**Date:** 2026-02-24

---

## Executive Summary

After 5 retry attempts, the root cause is clear: **Phase 1.3 was attempted without completing Phases 1.1 and 1.2**. The database schema exists, but no service implementations exist for outcome tracking, lesson extraction, or prompt enhancement.

**Solution:** Implement all three phases as a single consolidated unit with a simplified MVP approach.

---

## What Exists (Verified)

✅ Database tables (`task_outcomes`, `outcome_learnings`) created  
✅ Models in `app/models.py` (TaskOutcome, OutcomeLearning)  
✅ API endpoints in `app/routers/learning.py` (stats, health)  
✅ Worker integration point identified in `app/orchestrator/worker.py` line 233  

## What's Missing

❌ No `OutcomeTracker` service (Phase 1.1)  
❌ No `LessonExtractor` service (Phase 1.2)  
❌ No `PromptEnhancer` service (Phase 1.3)  
❌ No integration code in worker.py  

---

## Consolidated MVP Architecture

### Design Principles

1. **Simplicity first** — Minimum code to close the learning loop
2. **Single file per service** — Easy to review and test
3. **Fail-safe** — Never break task execution, even if learning fails
4. **Feature flag** — Can disable entire system with one env var

### Three Service Classes

#### 1. OutcomeTracker (`app/orchestrator/outcome_tracker.py`)

**Purpose:** Track task completions and create outcome records

**Core Method:**
```python
@staticmethod
async def track_task_completion(
    db: AsyncSession,
    task: Task,
    success: bool,
) -> Optional[TaskOutcome]:
    """Create or update outcome record for a task."""
```

**Responsibilities:**
- Create `TaskOutcome` record when task completes
- Infer category (bug_fix, feature, test, refactor, docs) from title/notes
- Infer complexity (simple, medium, complex) from shape or description length
- Compute context_hash for similarity matching
- Handle A/B control group (20% get `learning_disabled=True`)

**Integration point:** Called from worker completion logic (after task status changes)

---

#### 2. LessonExtractor (`app/orchestrator/lesson_extractor.py`)

**Purpose:** Extract reusable lessons from failed/revised tasks

**Core Method:**
```python
@staticmethod
async def extract_lessons(
    db: AsyncSession,
    outcome: TaskOutcome,
) -> List[OutcomeLearning]:
    """Extract lessons from an outcome with human feedback."""
```

**Responsibilities:**
- Parse human feedback for common patterns (5-10 rules)
- Create `OutcomeLearning` records with confidence=0.5
- Update confidence when learnings help/hurt in future tasks
- Deactivate low-confidence learnings (<0.3 after 3 failures)

**Patterns (Programmer):**
- `missing_tests` — "Always include unit tests"
- `unclear_names` — "Use descriptive variable/function names"  
- `missing_error_handling` — "Add try/except blocks"
- `missing_docs` — "Add docstrings"
- `missing_validation` — "Validate inputs"

**Integration point:** Called manually via API `/api/learning/extract` or automatically from feedback endpoint

---

#### 3. PromptEnhancer (`app/orchestrator/prompt_enhancer.py`)

**Purpose:** Inject relevant lessons into task prompts

**Core Method:**
```python
@staticmethod
async def enhance_prompt(
    db: AsyncSession,
    base_prompt: str,
    task: Task,
    agent_type: str,
) -> tuple[str, List[str]]:
    """Add lessons to prompt, return (enhanced_prompt, learning_ids)."""
```

**Responsibilities:**
- Query relevant learnings (agent match, category match, active, confidence>0.3)
- Select top 3 learnings by confidence
- Inject as prefix: "=== Lessons from Past Tasks ===" section
- Return both enhanced prompt and list of applied learning IDs
- Track which learnings were applied to which tasks

**Integration point:** Called from `worker.py` before spawning agent

---

## Implementation Plan

### Step 1: Create Service Classes (3 files)

Create three simple service files:

**File:** `app/orchestrator/outcome_tracker.py` (~150 lines)
- `track_task_completion(db, task, success)` method
- Basic inference: category from title keywords, complexity from shape
- Simple context hash: hash of first 200 chars of notes
- A/B control group: `random.random() < 0.2`

**File:** `app/orchestrator/lesson_extractor.py` (~200 lines)  
- `extract_lessons(db, outcome)` method
- 5 pattern detectors for programmer (regex-based)
- Simple confidence update: +0.1 on success, -0.15 on failure
- Deactivate if confidence < 0.3 and failure_count > 3

**File:** `app/orchestrator/prompt_enhancer.py` (~150 lines)
- `enhance_prompt(db, base_prompt, task, agent_type)` method  
- Query learnings: WHERE agent=X AND is_active=1 AND confidence>0.3
- Sort by confidence DESC, limit 3
- Inject as prefix with clear separator

**Total:** ~500 lines of new code

---

### Step 2: Integrate with Worker (~30 lines)

**File:** `app/orchestrator/worker.py` (modify existing)

Integration points:

**A) Before spawning worker** (around line 233):
```python
# After building base prompt with Prompter
if LEARNING_ENABLED:  # env flag
    from app.orchestrator.prompt_enhancer import PromptEnhancer
    enhanced_prompt, learning_ids = await PromptEnhancer.enhance_prompt(
        db=self.db,
        base_prompt=prompt_content,
        task=task_obj,  # reconstruct from task dict
        agent_type=agent_type,
    )
    prompt_content = enhanced_prompt
else:
    learning_ids = []
```

**B) After task completion** (find where task.status changes to completed/rejected):
```python
# After task finishes
if LEARNING_ENABLED:
    from app.orchestrator.outcome_tracker import OutcomeTracker
    success = task.review_state in ['approved', 'auto_approved']
    outcome = await OutcomeTracker.track_task_completion(
        db=self.db,
        task=task,
        success=success,
    )
    if outcome and learning_ids:
        outcome.applied_learnings = json.dumps(learning_ids)
        await self.db.commit()
```

---

### Step 3: Add API Endpoints (~50 lines)

**File:** `app/routers/learning.py` (extend existing)

Add two endpoints:

```python
@router.post("/extract")
async def trigger_extraction(
    agent: str = "programmer",
    since_hours: int = 24,
    db: AsyncSession = Depends(get_db),
):
    """
    Manually trigger lesson extraction from recent outcomes.
    Returns count of lessons created.
    """
    from app.orchestrator.lesson_extractor import LessonExtractor
    # Query outcomes with feedback, call extract_lessons
    ...

@router.post("/tasks/{task_id}/feedback")
async def add_feedback(
    task_id: str,
    feedback: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Add human feedback to a task outcome.
    Optionally trigger extraction immediately.
    """
    from app.orchestrator.outcome_tracker import OutcomeTracker
    # Find outcome, set human_feedback, call extract_lessons
    ...
```

---

### Step 4: Basic Tests (~100 lines)

**File:** `tests/test_learning_mvp.py`

Minimal test coverage:

1. `test_track_outcome` — creates record
2. `test_infer_category` — recognizes "bug" → bug_fix
3. `test_extract_missing_tests` — detects pattern in feedback
4. `test_enhance_prompt` — injects lesson text
5. `test_control_group_skips_enhancement` — respects A/B flag
6. `test_full_cycle` — outcome → learning → enhanced prompt

---

## Configuration (Environment Variables)

```bash
# Feature flag (master switch)
LEARNING_ENABLED=true

# Prompt enhancement
MAX_LEARNINGS_PER_PROMPT=3
MIN_CONFIDENCE_THRESHOLD=0.3

# A/B testing
CONTROL_GROUP_PCT=0.20  # 20% get no learnings

# Extraction
AUTO_EXTRACT_ON_FEEDBACK=false  # Manual extraction by default
```

---

## Acceptance Criteria

### Must Have
- ✅ Three service files exist and follow the interface above
- ✅ Worker integration: prompts are enhanced before task spawn
- ✅ Worker integration: outcomes are tracked after task completion
- ✅ Feature flag `LEARNING_ENABLED` controls everything
- ✅ Control group (20%) gets unmodified prompts and `learning_disabled=True`
- ✅ Applied learning IDs stored in `TaskOutcome.applied_learnings` (JSON array)
- ✅ At least 5 programmer patterns in LessonExtractor
- ✅ Basic tests pass
- ✅ No exceptions break worker execution (all learning code is defensive)

### Nice to Have (defer if needed)
- Confidence updates on task success/failure
- Automatic extraction trigger
- CLI tools for bulk extraction/analysis

---

## Risk Mitigation

### Risk: Learning code breaks worker
**Mitigation:** Wrap all learning calls in try/except, log and continue

### Risk: Database writes conflict
**Mitigation:** Use separate commits for learning data, don't block main task commit

### Risk: Prompt too long
**Mitigation:** Limit to 3 learnings max, truncate lesson_text to 200 chars each

### Risk: Bad pattern matching
**Mitigation:** Start with simple keyword regex, iterate based on real feedback

---

## Success Metrics (Observable After 1 Week)

1. **Outcomes created:** > 20 task_outcomes in DB
2. **Learnings created:** > 5 outcome_learnings for programmer
3. **Application rate:** > 50% of tasks get enhanced prompts (treatment group)
4. **No crashes:** Zero worker failures due to learning system
5. **Stats endpoint works:** `/api/learning/stats?agent=programmer` returns valid data

---

## Timeline Estimate

- **Service implementation:** 4-6 hours (straightforward CRUD + logic)
- **Worker integration:** 2 hours (two small integration points)
- **API endpoints:** 1 hour (simple CRUD wrappers)
- **Tests:** 2 hours (basic happy path coverage)

**Total:** 1-2 days for a focused programmer

---

## Simplified Handoff

```json
{
  "to": "programmer",
  "initiative": "agent-learning-system",
  "title": "Implement Learning System MVP (Phases 1.1-1.3 consolidated)",
  "context": "Complete minimal viable agent learning system in one cohesive implementation. Create 3 service classes (OutcomeTracker, LessonExtractor, PromptEnhancer), integrate with worker.py at 2 points, add 2 API endpoints, write basic tests. See consolidated rescue plan: docs/handoffs/learning-phase-1-consolidated-rescue.md",
  "acceptance": "1) Three service files exist with core methods. 2) Worker.py enhanced prompts before spawn and tracks outcomes after completion. 3) LEARNING_ENABLED flag works. 4) Control group (20%) gets unmodified prompts. 5) Applied learning IDs stored in outcomes. 6) 5+ programmer patterns implemented. 7) Basic tests pass. 8) No worker crashes from learning code. 9) /api/learning/stats returns valid data.",
  "files": [
    "docs/handoffs/learning-phase-1-consolidated-rescue.md",
    "app/models.py",
    "app/orchestrator/worker.py",
    "app/routers/learning.py"
  ],
  "estimated_complexity": "medium"
}
```

---

## Why This Will Work

1. **Single cohesive unit** — No cross-phase coordination issues
2. **Dependencies in place** — Tables and models already exist
3. **Clear integration points** — Exact line numbers and code snippets provided
4. **Minimal scope** — ~500 lines of new code, focused and testable
5. **Fail-safe design** — Cannot break existing worker functionality
6. **Observable success** — Can verify with queries and API calls immediately

---

## Next Steps

1. Programmer implements all three services
2. Programmer adds worker integration (2 small changes)
3. Programmer extends learning API (2 endpoints)
4. Programmer writes basic tests
5. Deploy and observe for 1 week
6. Iterate on pattern detection based on real feedback

---

**Status:** Ready for implementation  
**Blockers:** None (all dependencies verified present)
