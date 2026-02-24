# Learning Loop MVP — Implementation Guide

**Date:** 2026-02-24  
**Status:** ✅ Ready to Build  
**Spec:** [docs/learning-loop-mvp-design.md](../learning-loop-mvp-design.md)  
**Precise status:** [learning-loop-mvp-status.md](learning-loop-mvp-status.md)

---

## What This Is

The Learning Loop MVP closes the feedback gap: agents currently repeat the same mistakes because nothing captures what went wrong. This feature logs every agent run with task intent, outcome label, and failure reason — then uses that data to inject relevant lessons into future prompts.

**Expected result:** >10% improvement in programmer code review acceptance rate within 4 weeks.

---

## Current State (2026-02-24)

### ✅ Already Done — Do Not Rebuild

| Component | File | What It Does |
|-----------|------|-------------|
| `TaskOutcome` model | `app/models.py:846` | DB record for one agent run outcome |
| `OutcomeLearning` model | `app/models.py:866` | Extracted lesson from patterns |
| DB tables | migrations | `task_outcomes`, `outcome_learnings` created |
| `PromptEnhancer` | `app/orchestrator/prompt_enhancer.py` | **Fully implemented** — queries active learnings, injects into prompts, handles A/B split |
| Worker Hook 1 (pre-spawn) | `app/orchestrator/worker.py:~241` | Calls `Prompter.build_task_prompt_enhanced()` → `PromptEnhancer.enhance_prompt()` |
| A/B control group (20%) | `app/orchestrator/worker.py:~234` | 20% of tasks get `learning_disabled=True`, skip enhancement |

> **Important:** `prompt_enhancer.py` is not a stub. It is fully implemented. Review it before writing anything.

### ❌ Still Missing — Build These

```
app/orchestrator/outcome_tracker.py    ← HIGHEST PRIORITY
app/routers/agent_learning.py          ← /api/agent-learning/* endpoints
app/orchestrator/learning_batch.py     ← daily 2am batch job
tests/test_agent_learning.py           ← 12 test cases
```

**Modify:**
```
app/orchestrator/worker.py             ← Hook 2: call OutcomeTracker after completion
app/orchestrator/engine.py             ← 2am ET timer for batch job
app/main.py                            ← register agent_learning router
```

**Do not touch:**
```
app/routers/learning.py                ← personal learning plans (different system)
app/models.py                          ← models are correct as-is
app/orchestrator/prompt_enhancer.py    ← fully implemented, no changes needed
```

---

## Build Order

Work in this order. Steps 1–4 give a working ledger and API surface. Steps 5–7 add the batch job and tests.

### Step 1 — `app/orchestrator/outcome_tracker.py`

The critical gap. Without this, no outcomes are ever recorded, and the system has nothing to learn from.

**Interface:**

```python
class OutcomeTracker:
    @staticmethod
    async def track_completion(
        db: AsyncSession,
        task: Task,
        success: bool,
        agent_type: str,
        applied_learning_ids: list[str] = (),
        learning_disabled: bool = False,
    ) -> None:
        """
        Write a TaskOutcome row at task completion.
        FAIL-SAFE: never raises — log errors and return.
        """

    @staticmethod
    async def record_feedback(
        db: AsyncSession,
        task_id: str,
        outcome_label: str,      # "success" | "failure" | "user-corrected"
        human_feedback: str = "",
        reason_tags: list[str] = (),
    ) -> TaskOutcome:
        """
        Upsert outcome feedback (called by POST /api/agent-learning/outcomes).
        Returns the updated or created TaskOutcome.
        """
```

**Success detection logic:**

```python
success = (
    task.work_state == "completed"
    and task.review_state not in ("rejected", "failed")
)
# Failure if: work_state in ("failed", "cancelled")
#         OR: review_state in ("rejected", "failed")
#         OR: error_message is set
```

**Context hash** (groups similar tasks for A/B comparison):

```python
def _context_hash(agent_type: str, task_category: str, task_title: str) -> str:
    first_8_words = " ".join(task_title.lower().split()[:8])
    key = f"{agent_type}:{task_category}:{first_8_words}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
```

**Confidence updates** (run when `applied_learning_ids` is non-empty):

| Event | Change |
|-------|--------|
| Success with learnings applied | `confidence += 0.1` (max 1.0), `success_count += 1` |
| Failure with learnings applied | `confidence -= 0.15` (min 0.0), `failure_count += 1` |
| Auto-deactivate | if `confidence < 0.3` AND `failure_count >= 3` → `is_active = False` |

---

### Step 2 — Worker Hook 2 in `worker.py`

Add this in `_handle_worker_completion()`, after task status is updated and committed:

```python
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

`WorkerInfo` needs two new fields (add to `worker_models.py` or inline in `worker.py`):

```python
applied_learning_ids: list[str] = field(default_factory=list)
learning_disabled: bool = False
```

These are already populated at spawn time (Hook 1 is live). Making them part of `WorkerInfo` lets you pass them to the completion handler without re-querying.

---

### Step 3 — `app/routers/agent_learning.py`

> **API prefix:** `/api/agent-learning` — NOT `/api/learning`. That prefix belongs to personal learning plans (`app/routers/learning.py`).

#### POST /api/agent-learning/outcomes

Accepts manual outcome corrections from the UI.

**Request:**
```json
{
  "task_id": "uuid",
  "outcome": "success" | "failure" | "user-corrected",
  "agent_type": "programmer",
  "human_feedback": "Missing tests and no error handling",
  "reason_tags": ["missing_tests", "missing_error_handling"]
}
```

**Response (201 Created / 200 OK):**
```json
{
  "outcome_id": "uuid",
  "task_id": "uuid",
  "outcome": "failure",
  "reason_tags": ["missing_tests", "missing_error_handling"]
}
```

Returns `201` for new records, `200` for updates, `404` if task not found.

**Standard reason tags:**

| Tag | Triggered by feedback containing |
|-----|----------------------------------|
| `missing_tests` | "test", "spec", "coverage" |
| `missing_error_handling` | "error handling", "exception", "try/except" |
| `unclear_names` | "naming", "variable name", "unclear" |
| `missing_docs` | "docstring", "comment", "documentation" |
| `missing_validation` | "validate", "input check", "assertion" |
| `wrong_approach` | "wrong", "different approach", "rethink" |

---

#### GET /api/agent-learning/summary

Returns failure patterns and learning effectiveness for the dashboard and ops brief.

**Query params:**

| Param | Default | Description |
|-------|---------|-------------|
| `since_days` | 30 | Lookback window |
| `agent_type` | all | Filter by agent |
| `task_category` | all | Filter by category |

**Response:**
```json
{
  "generated_at": "2026-02-24T16:00:00Z",
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
      "active_learning": {
        "id": "uuid",
        "lesson_text": "Always include unit tests...",
        "confidence": 0.72,
        "success_count": 9,
        "failure_count": 2
      }
    }
  ],
  "active_learnings": [...],
  "pending_suggestions": [
    {
      "id": "uuid",
      "pattern": "missing_error_handling",
      "suggested_lesson": "Add try/except blocks around I/O...",
      "evidence_count": 12,
      "awaiting_approval": true
    }
  ]
}
```

Lift is computed as:
```
lift = (treatment_success_rate - control_success_rate) / control_success_rate
```

---

#### PATCH /api/agent-learning/learnings/{id}

Approve, reject, or edit a pending lesson suggestion.

**Request:**
```json
{
  "action": "approve" | "reject" | "edit",
  "lesson_text": "..."   // required for "edit"
}
```

**Effects:**

| Action | What happens |
|--------|-------------|
| `approve` | `is_active = True`, `confidence = 0.5` |
| `reject` | `is_active = False`, `confidence = 0.0` |
| `edit` | `lesson_text` updated; active state unchanged |

---

### Step 4 — Register router in `app/main.py`

```python
from app.routers import agent_learning
app.include_router(
    agent_learning.router,
    prefix="/api/agent-learning",
    tags=["agent-learning"],
)
```

Steps 1–4 are the minimum viable ledger. The system can track outcomes, surface patterns, and accept manual corrections. Stop here if time-boxed — steps 5–7 add automation.

---

### Step 5 — `app/orchestrator/learning_batch.py`

Runs daily at 2am ET. Aggregates failure patterns and queues lesson suggestions for human approval.

**Core logic:**

```python
async def run_learning_batch(db: AsyncSession) -> dict:
    """
    1. Query task_outcomes WHERE success=False AND created_at > 14 days ago
    2. Group by (agent_type, reason_tags)
    3. For patterns with >=3 failures and no active OutcomeLearning:
       a. Generate candidate lesson from LESSON_TEMPLATES
       b. Create OutcomeLearning with is_active=False, confidence=0.5
       c. Queue inbox suggestion for human approval
    4. For active learnings with confidence < 0.3 AND failure_count >= 3:
       - Set is_active=False, log deactivation
    5. Return {new_suggestions: N, deactivated: N, patterns_analyzed: N}
    """
```

**Lesson templates** (copy from `docs/learning-loop-mvp-design.md`):

```python
LESSON_TEMPLATES = {
    "missing_tests": "Always include unit tests. Create a pytest test file alongside the implementation with at least one test per public function.",
    "missing_error_handling": "Add try/except blocks around I/O, network calls, and external APIs. Log exceptions with enough context to debug without re-running.",
    "unclear_names": "Use descriptive variable and function names. Avoid abbreviations. Names should read like English: get_user_by_email(), not get_u().",
    "missing_docs": "Add docstrings to all public functions and classes. Include what the function does, its parameters, and what it returns.",
    "missing_validation": "Validate all inputs at function boundaries. Raise ValueError with a clear message, or return an error response. Don't assume callers validate.",
    "wrong_approach": "Re-read the task requirements carefully before choosing an approach. When uncertain, pick the simplest solution that satisfies the spec.",
}
```

**Inbox suggestion format:**
```
Title: "Approve new learning: missing_error_handling (programmer)"
Type: suggestion
Body: includes pattern, agent, evidence count, suggested lesson text, source task IDs
```

After the batch, post a summary to the ops brief (see `BriefService` usage in `engine.py` for the pattern).

---

### Step 6 — Engine timer in `app/orchestrator/engine.py`

Same pattern as `_memory_maintenance_hour`. Add:

```python
_learning_batch_hour_et: int = 2       # 2am ET
_last_learning_batch_date_et: Optional[str] = None
```

Load/persist `_last_learning_batch_date_et` via `OrchestratorSetting` key `"last_learning_batch_date_et"`. Trigger `run_learning_batch(db)` when due.

---

### Step 7 — Tests in `tests/test_agent_learning.py`

Minimum 12 test cases:

```
test_track_completion_creates_outcome()
test_track_completion_never_raises()         ← inject DB error, assert no exception
test_track_failure_sets_success_false()
test_feedback_updates_existing_outcome()
test_confidence_increments_on_success()
test_confidence_decrements_on_failure()
test_auto_deactivate_low_confidence()
test_post_outcomes_endpoint_creates_record()
test_post_outcomes_endpoint_returns_200_on_update()
test_summary_endpoint_returns_data()
test_summary_shows_ab_lift()
test_batch_creates_suggestions_for_patterns()
test_batch_skips_already_covered_patterns()
```

Run:
```bash
source .venv/bin/activate
python -m pytest tests/test_agent_learning.py -v
```

---

## Acceptance Criteria

- [ ] `OutcomeTracker.track_completion()` writes a `TaskOutcome` row on every worker completion
- [ ] `OutcomeTracker.track_completion()` never raises — errors are logged only
- [ ] `POST /api/agent-learning/outcomes` creates or updates a `TaskOutcome`
- [ ] `GET /api/agent-learning/summary` returns success rate, A/B lift, and top failure patterns
- [ ] `PATCH /api/agent-learning/learnings/{id}` sets `is_active=True`, `confidence=0.5` on approve
- [ ] Daily batch at 2am ET creates `OutcomeLearning` suggestions for patterns with ≥3 failures
- [ ] Batch skips patterns that already have an active learning
- [ ] Batch deactivates learnings with `confidence < 0.3` AND `failure_count >= 3`
- [ ] `LEARNING_ENABLED=false` disables all hooks without crashing workers
- [ ] All 12+ tests in `tests/test_agent_learning.py` pass
- [ ] `GET /api/agent-learning/summary` returns non-zero data after tasks run

---

## Environment Variables

| Variable | Default | Effect |
|----------|---------|--------|
| `LEARNING_ENABLED` | `true` | Master switch — disables all learning |
| `LEARNING_INJECTION_ENABLED` | `true` | Disable prompt injection only |
| `LEARNING_AB_TEST_ENABLED` | `true` | Disable control group (100% get learnings) |
| `LEARNING_CONTROL_GROUP_PCT` | `0.20` | Fraction in control group |
| `MAX_LEARNINGS_PER_PROMPT` | `3` | Max lessons per prompt |
| `MIN_CONFIDENCE_THRESHOLD` | `0.3` | Inject threshold |
| `LEARNING_BATCH_ENABLED` | `true` | Daily batch job |

---

## Quick Reference

| File | Action | Priority |
|------|--------|----------|
| `app/orchestrator/outcome_tracker.py` | **Create** | 🔴 Critical |
| `app/orchestrator/worker.py` | **Modify** — add Hook 2 + WorkerInfo fields | 🔴 Critical |
| `app/routers/agent_learning.py` | **Create** — 3 endpoints | 🟠 High |
| `app/main.py` | **Modify** — register router | 🟠 High |
| `app/orchestrator/learning_batch.py` | **Create** | 🟡 Medium |
| `app/orchestrator/engine.py` | **Modify** — add 2am timer | 🟡 Medium |
| `tests/test_agent_learning.py` | **Create** | 🟡 Medium |

---

## Related Docs

| Document | When to Read |
|----------|-------------|
| [learning-loop-mvp-design.md](../learning-loop-mvp-design.md) | Full spec with all design decisions |
| [learning-loop-mvp-status.md](learning-loop-mvp-status.md) | Detailed code snippets for each piece |
| [learning-loop-mvp-handoff.json](learning-loop-mvp-handoff.json) | Acceptance criteria in JSON |
| [learning-loop-mvp-test-spec.md](learning-loop-mvp-test-spec.md) | Detailed test setup + assertions for all 12 tests |
| [LEARNING-DOCS-MAP.md](LEARNING-DOCS-MAP.md) | Which docs are current vs stale (read if confused) |
| [PHASE_1.3_RESCUE_FINDINGS.md](../PHASE_1.3_RESCUE_FINDINGS.md) | Why previous attempts failed (avoid repeating) |

---

*Start with `outcome_tracker.py`. Everything else depends on it.*
