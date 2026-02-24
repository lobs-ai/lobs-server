# Learning Loop MVP — Current Status & Remaining Work

**Date:** 2026-02-24  
**Status:** Partially Implemented — Hook 1 complete, Hook 2 and API missing  
**Spec:** [docs/learning-loop-mvp-design.md](../learning-loop-mvp-design.md)  
**Handoff JSON:** [learning-loop-mvp-handoff.json](learning-loop-mvp-handoff.json)

---

## What's Actually Done

The handoff JSON dated 2026-02-24 says `prompt_enhancer.py` is a stub. **It is not.** A code audit shows the following is fully implemented:

| Component | File | State |
|-----------|------|-------|
| `TaskOutcome` model | `app/models.py:846` | ✅ Complete |
| `OutcomeLearning` model | `app/models.py:866` | ✅ Complete |
| Database tables | migrations | ✅ Migrated |
| `PromptEnhancer` service | `app/orchestrator/prompt_enhancer.py` | ✅ Fully implemented |
| Worker A/B test split | `app/orchestrator/worker.py:~234` | ✅ Live |
| Worker Hook 1 (pre-spawn enhancement) | `app/orchestrator/worker.py:~241` | ✅ Live — calls `Prompter.build_task_prompt_enhanced()` → `PromptEnhancer.enhance_prompt()` |
| Applied learnings persisted pre-spawn | `app/orchestrator/worker.py:~382` | ✅ Partial — updates existing `TaskOutcome` if one exists, does nothing if it doesn't |

### What `prompt_enhancer.py` does

- Queries `OutcomeLearning` rows matching agent type + task category + complexity
- Filters by `is_active=True` and `confidence >= MIN_CONFIDENCE_THRESHOLD` (default 0.3)
- Selects top `MAX_LEARNINGS_PER_PROMPT` (default 3) by confidence
- Prepends a "Lessons from Past Tasks" section to the prompt
- Infers task category (bug_fix / feature / test / refactor / docs / research) from title/notes keywords
- Infers complexity (simple / medium / complex) from `task.shape` or title+notes length
- Never throws — any failure returns the base prompt unchanged

---

## What's Still Missing

These are the remaining pieces, in priority order:

### 1. `app/orchestrator/outcome_tracker.py` ← **highest priority**

The critical gap. Without this, no outcomes are ever written on completion, which means no data and no learning.

**Interface:**

```python
class OutcomeTracker:
    @staticmethod
    async def track_completion(
        db: AsyncSession,
        task: Task,                  # SQLAlchemy Task model object
        success: bool,
        agent_type: str,
        applied_learning_ids: list[str] = (),
        learning_disabled: bool = False,
    ) -> None:
        """
        Write or update a TaskOutcome row at task completion.
        FAIL-SAFE: must never raise — log errors and return.
        """
    
    @staticmethod
    async def record_feedback(
        db: AsyncSession,
        task_id: str,
        outcome_label: str,          # "success" | "failure" | "user-corrected"
        human_feedback: str = "",
        reason_tags: list[str] = (),
    ) -> "TaskOutcome":
        """
        Upsert outcome feedback (called by POST /api/agent-learning/outcomes).
        Returns the updated or created TaskOutcome.
        """
```

**Success detection logic** (from handoff JSON):
```python
success = (
    task.work_state == "completed"
    and task.review_state not in ("rejected", "failed")
)
# Failure if: work_state in ("failed", "cancelled") OR review_state in ("rejected", "failed") OR error_message set
```

**Context hash** (for grouping similar tasks):
```python
import hashlib

def _context_hash(agent_type: str, task_category: str, task_title: str) -> str:
    first_8_words = " ".join(task_title.lower().split()[:8])
    key = f"{agent_type}:{task_category}:{first_8_words}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
```

**Confidence updates when applied_learning_ids is non-empty** (on each completion):
- Success: `confidence = min(1.0, confidence + 0.1)`, increment `success_count`
- Failure: `confidence = max(0.0, confidence - 0.15)`, increment `failure_count`
- Auto-deactivate if `confidence < 0.3` AND `failure_count >= 3`

---

### 2. Worker Hook 2 — call `OutcomeTracker.track_completion()` in `_handle_worker_completion()`

**File:** `app/orchestrator/worker.py`  
**Location:** Inside `_handle_worker_completion()`, after task status update (around line 960 for success path, corresponding failure path)

```python
# After: db_task.work_state = "completed" / await self.db.commit()
try:
    from app.orchestrator.outcome_tracker import OutcomeTracker
    await OutcomeTracker.track_completion(
        db=self.db,
        task=db_task,
        success=succeeded,
        agent_type=agent_type,
        applied_learning_ids=worker_info.applied_learning_ids or [],
        learning_disabled=worker_info.learning_disabled or False,
    )
except Exception as e:
    logger.warning(f"[LEARNING] OutcomeTracker.track_completion failed: {e}")
```

**Note:** `WorkerInfo` needs two new fields to carry the A/B state from spawn to completion:
- `applied_learning_ids: list[str] = field(default_factory=list)`
- `learning_disabled: bool = False`

Currently the spawn code (line ~382) tries to persist these to a `TaskOutcome` that may not exist yet. With `OutcomeTracker.track_completion()` calling at completion, those fields can be passed directly.

---

### 3. `app/routers/agent_learning.py` — two endpoints

**Important:** Use prefix `/api/agent-learning` NOT `/api/learning` — that prefix belongs to personal learning plans (a different system). Also register in `app/main.py`.

**POST /api/agent-learning/outcomes**

```python
class OutcomeFeedbackRequest(BaseModel):
    task_id: str
    outcome: Literal["success", "failure", "user-corrected"]
    agent_type: Optional[str] = None
    human_feedback: Optional[str] = None
    reason_tags: Optional[list[str]] = None

@router.post("/outcomes", dependencies=[Depends(require_auth)], status_code=201)
async def submit_outcome(req: OutcomeFeedbackRequest, db: AsyncSession = Depends(get_db)):
    """Submit or update an outcome for a task. Used by UI for manual corrections."""
    from app.orchestrator.outcome_tracker import OutcomeTracker
    outcome = await OutcomeTracker.record_feedback(
        db=db,
        task_id=req.task_id,
        outcome_label=req.outcome,
        human_feedback=req.human_feedback or "",
        reason_tags=req.reason_tags or [],
    )
    return {"outcome_id": outcome.id, "task_id": req.task_id, "outcome": req.outcome}
```

**GET /api/agent-learning/summary**

Query params: `since_days` (default 30), `agent_type` (default all), `task_category` (default all)

Response shape (align with design doc):
```json
{
  "generated_at": "ISO timestamp",
  "period_days": 30,
  "totals": {
    "tasks_tracked": 142,
    "success_rate": 0.73,
    "treatment_success_rate": 0.78,
    "control_success_rate": 0.65,
    "lift": 0.20
  },
  "top_failure_patterns": [
    {
      "pattern": "missing_tests",
      "occurrences": 18,
      "pct_of_failures": 0.45,
      "active_learning": { "id": "...", "lesson_text": "...", "confidence": 0.72 }
    }
  ],
  "active_learnings": [...],
  "pending_suggestions": [...]
}
```

**PATCH /api/agent-learning/learnings/{id}**

```python
class LearningApprovalRequest(BaseModel):
    action: Literal["approve", "reject", "edit"]
    lesson_text: Optional[str] = None  # required for "edit"

# approve → is_active=True, confidence=0.5
# reject  → is_active=False, confidence=0.0
# edit    → update lesson_text; keep is_active state
```

---

### 4. `app/orchestrator/learning_batch.py` — daily pattern aggregation

Runs at 2am ET. Queries failures from last 14 days, groups by `(agent_type, reason_tags)`, creates `OutcomeLearning` rows with `is_active=False` (pending human approval) for patterns with ≥3 failures that don't already have an active learning.

**Core logic:**

```python
async def run_learning_batch(db: AsyncSession) -> dict:
    """
    1. Query task_outcomes WHERE success=False AND created_at > 14 days ago
    2. Group by agent_type + reason_tags (JSON array)
    3. For patterns with >=3 failures and no active OutcomeLearning:
       a. Generate candidate lesson text from LESSON_TEMPLATES (see design doc)
       b. Create OutcomeLearning with is_active=False, confidence=0.5
    4. For active learnings with confidence < 0.3 AND failure_count >= 3:
       - Set is_active=False
    5. Return summary dict {new_suggestions: N, deactivated: N, patterns_analyzed: N}
    """
```

**LESSON_TEMPLATES** are defined in `docs/learning-loop-mvp-design.md`. Copy them into `learning_batch.py`.

After running, post a summary to the ops brief via BriefService (or log a chat message — see how `memory_maintenance` does this in engine.py).

---

### 5. Engine timer integration

**File:** `app/orchestrator/engine.py`

Add after the memory maintenance timer pattern:

```python
_learning_batch_hour_et: int = 2  # 2am ET
_last_learning_batch_date_et: Optional[str] = None
```

Load/persist via `OrchestratorSetting` with key `"last_learning_batch_date_et"` (same as `last_memory_maintenance_date_et`). Call `run_learning_batch(db)` when due.

---

### 6. Tests — `tests/test_agent_learning.py`

Minimum test cases:

```
test_track_completion_creates_outcome()
test_track_completion_never_raises()           ← inject DB error, assert no exception
test_track_failure_success_false()
test_feedback_updates_outcome()
test_confidence_increments_on_success()
test_confidence_decrements_on_failure()
test_auto_deactivate_low_confidence()
test_post_outcomes_endpoint()
test_summary_endpoint_returns_data()
test_summary_shows_both_ab_groups()
test_batch_creates_suggestions_for_patterns()
test_batch_skips_covered_patterns()
```

---

## Files to Create

```
app/orchestrator/outcome_tracker.py     ← OutcomeTracker (highest priority)
app/routers/agent_learning.py           ← /api/agent-learning/* endpoints
app/orchestrator/learning_batch.py      ← daily batch job
tests/test_agent_learning.py            ← tests
```

## Files to Modify

```
app/orchestrator/worker.py
  • Add applied_learning_ids and learning_disabled fields to WorkerInfo
  • Call OutcomeTracker.track_completion() in _handle_worker_completion()

app/orchestrator/engine.py
  • Add 2am ET timer for learning_batch

app/main.py
  • Register agent_learning router at prefix /api/agent-learning
```

## Do Not Touch

```
app/routers/learning.py        — personal learning plans (different system)
app/models.py                  — TaskOutcome and OutcomeLearning are correct as-is
app/orchestrator/prompt_enhancer.py  — fully implemented, no changes needed
```

---

## Environment Variables

| Variable | Default | Effect |
|----------|---------|--------|
| `LEARNING_ENABLED` | `true` | Master switch |
| `LEARNING_INJECTION_ENABLED` | `true` | Prompt injection only |
| `LEARNING_CONTROL_GROUP_PCT` | `0.2` | A/B control group fraction |
| `MAX_LEARNINGS_PER_PROMPT` | `3` | Max lessons per prompt |
| `MIN_CONFIDENCE_THRESHOLD` | `0.3` | Inject threshold |
| `LEARNING_BATCH_ENABLED` | `true` | Daily batch |

---

## Acceptance Criteria

- [ ] `OutcomeTracker.track_completion()` writes a `TaskOutcome` row on every worker completion
- [ ] `OutcomeTracker.track_completion()` never raises — errors are logged only
- [ ] `POST /api/agent-learning/outcomes` upserts a `TaskOutcome`
- [ ] `GET /api/agent-learning/summary` returns success rate, A/B lift, and top failure patterns
- [ ] `PATCH /api/agent-learning/learnings/{id}` sets `is_active=True` and `confidence=0.5` on approve
- [ ] Daily batch at 2am ET creates `OutcomeLearning` suggestions for patterns with ≥3 failures
- [ ] Batch skips patterns that already have an active `OutcomeLearning`
- [ ] Deactivates learnings with `confidence < 0.3` AND `failure_count >= 3`
- [ ] `LEARNING_ENABLED=false` disables all hooks without crashing workers
- [ ] All 12 tests in `test_agent_learning.py` pass

---

*This document replaces the "PromptEnhancer is a stub" note in learning-loop-mvp-handoff.json. Hook 1 (pre-spawn enhancement) is live. Build Hook 2 (post-completion tracking) and the API next.*
