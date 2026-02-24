# Learning Loop MVP — Design Document

**Status:** Ready for Implementation  
**Date:** 2026-02-24 (updated 2026-02-24)  
**Initiative:** `agent-learning-system` (6ef17d1f)  
**Risk Tier:** B  
**Owner:** programmer

---

## Overview

The Learning Loop MVP closes the feedback gap in the agent system: today, agents repeat the same mistakes across tasks because there's no mechanism to capture what went wrong and act on it.

This document specifies the minimum production implementation:

1. **Outcome Ledger** — log every agent run with task intent, prompt version hash, and outcome label
2. **Prompt Variant Attribution** — track which prompt learnings were applied and whether they helped
3. **`/api/agent-learning/outcomes`** — write endpoint to submit or update outcomes
4. **`/api/agent-learning/summary`** — read endpoint to surface top failure patterns
5. **Daily Batch Job** — ranks failure patterns and queues prompt edit suggestions for human review

The database schema (`task_outcomes`, `outcome_learnings`) and SQLAlchemy models (`TaskOutcome`, `OutcomeLearning`) already exist. The implementation work is services + integration + two API endpoints + one scheduled job.

---

## What Already Exists

> **Updated 2026-02-24** — code audit shows more is done than initially tracked.

| Component | File | Status |
|-----------|------|--------|
| `TaskOutcome` model | `app/models.py:846` | ✅ Done |
| `OutcomeLearning` model | `app/models.py:866` | ✅ Done |
| Database tables (`task_outcomes`, `outcome_learnings`) | migrations | ✅ Migrated |
| `PromptEnhancer` service | `app/orchestrator/prompt_enhancer.py` | ✅ **Fully implemented** (not a stub) |
| Worker A/B test split (20% control group) | `app/orchestrator/worker.py:~234` | ✅ Live |
| Worker Hook 1 — pre-spawn prompt enhancement | `app/orchestrator/worker.py:~241` | ✅ Live — calls `Prompter.build_task_prompt_enhanced()` |

## What's Missing

> See [docs/handoffs/learning-loop-mvp-status.md](handoffs/learning-loop-mvp-status.md) for the authoritative current-state handoff.

| Component | File | Status |
|-----------|------|--------|
| `OutcomeTracker` service | `app/orchestrator/outcome_tracker.py` | ❌ Missing |
| Worker Hook 2 — post-completion tracking | `app/orchestrator/worker.py` | ❌ Missing |
| Agent learning API endpoints | `app/routers/agent_learning.py` | ❌ Missing |
| Daily pattern batch job | `app/orchestrator/learning_batch.py` | ❌ Missing |
| Engine timer for batch (2am ET) | `app/orchestrator/engine.py` | ❌ Missing |
| Router registration | `app/main.py` | ❌ Missing |

**API prefix:** Use `/api/agent-learning` (not `/api/learning` — that's personal learning plans, a different system).

---

## Event Schema

### What Gets Logged on Every Run

When a worker completes (success or failure), the orchestrator writes a `TaskOutcome` record. This is the atomic unit of the learning ledger.

```
TaskOutcome {
  id:                UUID
  task_id:           UUID (FK → tasks.id)
  worker_run_id:     UUID (optional reference to worker run)
  agent_type:        "programmer" | "researcher" | "writer" | "specialist"
  success:           bool
  task_category:     "bug_fix" | "feature" | "test" | "refactor" | "docs" | null
  task_complexity:   "simple" | "medium" | "complex" | null
  context_hash:      SHA-256 of (agent_type + task_category + task_complexity)
  human_feedback:    text (from review comments or manual feedback API)
  review_state:      snapshot of tasks.review_state at completion time
  applied_learnings: JSON array of OutcomeLearning IDs that were injected
  learning_disabled: bool (true = A/B control group, received no learnings)
  created_at:        timestamp
  updated_at:        timestamp
}
```

**Key fields for attribution:**

- `context_hash` — used to match this outcome to similar future tasks
- `applied_learnings` — the IDs of what was injected; enables success/failure attribution
- `learning_disabled` — marks control group for A/B comparison

### Prompt Version Hash

When the `PromptEnhancer` injects learnings, it builds a deterministic hash of the learning IDs applied:

```python
import hashlib, json

def prompt_variant_hash(applied_learning_ids: list[str]) -> str:
    key = json.dumps(sorted(applied_learning_ids))
    return hashlib.sha256(key.encode()).hexdigest()[:16]
```

This hash is stored alongside `applied_learnings` in the outcome and appears in logs with the `[LEARNING]` prefix, making it easy to correlate prompt variants with outcomes.

### Integration Point in worker.py

The orchestrator's worker completion handler is in `app/orchestrator/worker.py`. Two hooks are needed:

**Hook 1 — Before spawn** (prompt enhancement): ✅ **Already live**

`worker.py` calls `Prompter.build_task_prompt_enhanced()` at ~line 241, which calls `PromptEnhancer.enhance_prompt()`. The A/B split (20% control group via `LEARNING_CONTROL_GROUP_PCT`) is also live.

**Hook 2 — After completion** (outcome tracking): ❌ **Still needed**

```python
# In _handle_worker_completion(), after updating task status
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

Note: `WorkerInfo` needs `applied_learning_ids` and `learning_disabled` fields added so the pre-spawn values are available at completion time.

Both hooks must be wrapped in `try/except` — a learning failure must never crash a worker.

---

## API Endpoints

### POST /api/agent-learning/outcomes

Submit or update an outcome. Used by the orchestrator internally and available for manual correction via the UI.

> **Note:** The prefix is `/api/agent-learning`, not `/api/learning`. The `/api/learning` prefix belongs to personal learning plans (a separate system in `app/routers/learning.py`).

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

**Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `task_id` | Yes | Which task this outcome belongs to |
| `outcome` | Yes | `success`, `failure`, or `user-corrected` |
| `agent_type` | No | Inferred from task if omitted |
| `human_feedback` | No | Free text from reviewer/user |
| `reason_tags` | No | Pre-classified pattern names (see below) |

**Response:**
```json
{
  "outcome_id": "uuid",
  "task_id": "uuid",
  "outcome": "failure",
  "lessons_extracted": 2,
  "reason_tags": ["missing_tests", "missing_error_handling"]
}
```

**Outcome labels:**

| Label | Meaning |
|-------|---------|
| `success` | Task accepted without revision |
| `failure` | Task explicitly rejected or cancelled |
| `user-corrected` | Task completed but required human follow-up edits |

**Reason tags (programmer):**

| Tag | Triggered by feedback containing |
|-----|----------------------------------|
| `missing_tests` | "test", "spec", "coverage" |
| `missing_error_handling` | "error handling", "exception", "try/except" |
| `unclear_names` | "naming", "variable name", "unclear" |
| `missing_docs` | "docstring", "comment", "documentation" |
| `missing_validation` | "validate", "input check", "assertion" |
| `wrong_approach` | "wrong", "different approach", "rethink" |

**Status codes:**

- `201 Created` — new outcome record created
- `200 OK` — existing outcome updated
- `404 Not Found` — task_id not found
- `422 Unprocessable Entity` — invalid outcome label

---

### GET /api/agent-learning/summary

Returns a human-readable summary of the top failure patterns and current learning effectiveness.

**Query params:**

| Param | Default | Description |
|-------|---------|-------------|
| `agent_type` | all | Filter by agent |
| `days` | 30 | Lookback window |
| `min_confidence` | 0.3 | Minimum confidence for active learnings |

**Response:**
```json
{
  "generated_at": "2026-02-24T16:00:00Z",
  "period_days": 30,
  "agent_filter": "programmer",
  "totals": {
    "tasks_tracked": 142,
    "success_rate": 0.73,
    "control_group_success_rate": 0.65,
    "treatment_group_success_rate": 0.78,
    "lift": 0.20
  },
  "top_failure_patterns": [
    {
      "pattern": "missing_tests",
      "occurrences": 18,
      "pct_of_failures": 0.45,
      "active_learning": {
        "id": "uuid",
        "lesson_text": "Always include unit tests. Add pytest test file alongside implementation.",
        "confidence": 0.72,
        "success_count": 9,
        "failure_count": 2
      }
    },
    {
      "pattern": "missing_error_handling",
      "occurrences": 12,
      "pct_of_failures": 0.30,
      "active_learning": null
    }
  ],
  "active_learnings": [
    {
      "id": "uuid",
      "agent_type": "programmer",
      "pattern_name": "missing_tests",
      "lesson_text": "Always include unit tests...",
      "confidence": 0.72,
      "success_count": 9,
      "failure_count": 2,
      "is_active": true
    }
  ],
  "pending_suggestions": [
    {
      "pattern": "missing_error_handling",
      "suggested_lesson": "Add try/except blocks around file I/O, network calls, and external API calls. Log exceptions with context.",
      "evidence_count": 12,
      "awaiting_approval": true
    }
  ]
}
```

**Use cases:**

- Dashboard: show agents' improvement over time
- Daily ops brief: surface top failure patterns for attention
- Manual review: approve/reject pending prompt suggestions

---

## Daily Batch Job

### Purpose

Runs once per day to:
1. Aggregate new failure outcomes from the last 24 hours
2. Rank failure patterns by frequency
3. For patterns with no active learning: generate a candidate lesson and queue for human approval
4. For patterns with a learning already active: compare success/failure rate and flag if confidence is falling

### Schedule

```
Daily at 06:00 ET (before the 08:00 ops brief, so summary is fresh)
```

Implemented the same way as `memory_maintenance` — registered in `routine_runner.py` and triggered by the orchestrator engine's timer loop.

### Batch Logic

```
1. Query task_outcomes WHERE created_at > NOW() - 1 day AND success = false
2. Group by (agent_type, pattern inferred from reason_tags or human_feedback)
3. Sort by count DESC
4. For each pattern:
   a. If no active OutcomeLearning → generate candidate lesson text → save as inactive
   b. If active OutcomeLearning:
      - If confidence < 0.3 after 3+ applications → deactivate
      - If confidence > 0.7 → log as high-performing
5. Generate daily summary and append to today's ops brief via BriefService
6. Queue inbox item for any new pending lessons (type=suggestion, author=system)
```

### Candidate Lesson Generation

For patterns with no active learning, the batch job generates a template lesson:

```python
LESSON_TEMPLATES = {
    "missing_tests": (
        "Always include unit tests. Create a pytest test file alongside the "
        "implementation with at least one test per public function."
    ),
    "missing_error_handling": (
        "Add try/except blocks around I/O, network calls, and external APIs. "
        "Log exceptions with enough context to debug without re-running."
    ),
    "unclear_names": (
        "Use descriptive variable and function names. Avoid abbreviations. "
        "Names should read like English: get_user_by_email(), not get_u()."
    ),
    "missing_docs": (
        "Add docstrings to all public functions and classes. Include what the "
        "function does, its parameters, and what it returns."
    ),
    "missing_validation": (
        "Validate all inputs at function boundaries. Raise ValueError with a "
        "clear message, or return an error response. Don't assume callers validate."
    ),
    "wrong_approach": (
        "Re-read the task requirements carefully before choosing an approach. "
        "When uncertain, pick the simplest solution that satisfies the spec."
    ),
}
```

If the pattern has no template, the batch job logs it as `unclassified` and includes the raw feedback in the inbox suggestion for human review.

### Inbox Suggestion Format

Each pending lesson queued for approval looks like:

```
Title: Approve new learning: missing_error_handling (programmer)
Type: suggestion
Body:
  Pattern: missing_error_handling
  Agent: programmer
  Evidence: 12 failures in last 30 days (30% of all failures)
  
  Suggested lesson:
  "Add try/except blocks around I/O, network calls, and external APIs.
  Log exceptions with enough context to debug without re-running."
  
  Sources: [task-abc123, task-def456, ...]
  
  Approve → lesson becomes active and will be injected into future prompts
  Reject → pattern is logged but no injection happens
```

---

## Confidence Model

`OutcomeLearning.confidence` tracks whether a learning is actually helping.

| Event | Confidence change |
|-------|------------------|
| Task succeeded WITH learning applied | +0.1 (max 1.0) |
| Task failed WITH learning applied | -0.15 |
| Task succeeded WITHOUT learning (control) | no change |

**Auto-deactivation:** If `confidence < 0.3` AND `failure_count >= 3` → `is_active = False`. The daily batch logs deactivations and adds them to the ops brief.

---

## A/B Control Group

20% of tasks receive no learning injection (`learning_disabled = True`). This creates a baseline for measuring whether the learning system is actually helping.

The `/api/agent-learning/summary` endpoint computes lift as:

```
lift = (treatment_success_rate - control_success_rate) / control_success_rate
```

A positive lift confirms the system is working. A negative lift is a signal to audit the active learnings.

The control group percentage is controlled by `LEARNING_CONTROL_GROUP_PCT` (default `0.20`).

---

## Feature Flags

| Variable | Default | Effect |
|----------|---------|--------|
| `LEARNING_ENABLED` | `true` | Master switch — disables all learning if false |
| `LEARNING_INJECTION_ENABLED` | `true` | Disable prompt injection without disabling tracking |
| `LEARNING_AB_TEST_ENABLED` | `true` | Disable control group (100% get learnings) |
| `LEARNING_CONTROL_GROUP_PCT` | `0.20` | Fraction of tasks in control group |
| `MAX_LEARNINGS_PER_PROMPT` | `3` | Max lessons injected per prompt |
| `MIN_CONFIDENCE_THRESHOLD` | `0.3` | Minimum confidence to inject |

---

## Implementation Files

### New Files

```
app/orchestrator/outcome_tracker.py     OutcomeTracker.track_completion(), record_feedback()
app/routers/agent_learning.py           POST /api/agent-learning/outcomes + GET /summary + PATCH /learnings/{id}
app/orchestrator/learning_batch.py      Daily batch job (pattern aggregation)
tests/test_agent_learning.py            Unit + integration tests
```

### Modified Files

```
app/orchestrator/worker.py
  • Add applied_learning_ids + learning_disabled to WorkerInfo
  • Add Hook 2 in _handle_worker_completion() (call OutcomeTracker.track_completion)

app/orchestrator/engine.py              Add 2am ET timer for learning batch

app/main.py                             Register agent_learning router at /api/agent-learning
```

### Already Done (no changes needed)

```
app/models.py                           TaskOutcome, OutcomeLearning defined and correct
migrations/                             Tables created
app/orchestrator/prompt_enhancer.py     Fully implemented — query, select, inject, A/B logic
app/orchestrator/worker.py (Hook 1)     Pre-spawn enhancement already live at ~line 241
```

---

## Testing

### Minimum test coverage

```
tests/test_learning_mvp.py
├── test_track_completion_creates_outcome()
├── test_track_failure_creates_outcome_with_success_false()
├── test_extract_lessons_from_feedback()
├── test_extract_lessons_missing_tests_pattern()
├── test_prompt_enhancer_injects_learnings()
├── test_prompt_enhancer_respects_control_group()
├── test_prompt_enhancer_never_throws()
├── test_outcomes_endpoint_creates_record()
├── test_summary_endpoint_returns_patterns()
└── test_full_cycle_track_extract_inject()
```

Run with:
```bash
source .venv/bin/activate
python -m pytest tests/test_learning_mvp.py -v
```

---

## Acceptance Criteria

The MVP is complete when:

- [x] `PromptEnhancer` service implemented (already done)
- [x] Worker Hook 1: pre-spawn enhancement live (already done)
- [ ] `OutcomeTracker` service exists — `track_completion()` and `record_feedback()`
- [ ] Worker Hook 2: `OutcomeTracker.track_completion()` called in `_handle_worker_completion()`
- [ ] `LEARNING_ENABLED=false` disables all learning without crashing workers
- [ ] `POST /api/agent-learning/outcomes` creates/updates outcome records
- [ ] `GET /api/agent-learning/summary` returns success rate, A/B lift, and top failure patterns
- [ ] `PATCH /api/agent-learning/learnings/{id}` approves/rejects suggestions
- [ ] Daily batch job runs at 2am ET and posts summary to ops brief
- [ ] New pending lessons appear as inbox suggestions for human approval
- [ ] All learning code wrapped in `try/except` — no worker crashes
- [ ] 12+ tests pass in `tests/test_agent_learning.py`
- [ ] `GET /api/agent-learning/summary` shows non-zero data after tasks run

---

## Related Docs

| Document | Purpose |
|----------|---------|
| [docs/handoffs/learning-loop-mvp-status.md](handoffs/learning-loop-mvp-status.md) | **Current state + precise remaining work** (start here) |
| [docs/handoffs/learning-loop-mvp-handoff.json](handoffs/learning-loop-mvp-handoff.json) | Programmer handoff JSON (acceptance criteria, key details) |
| [docs/agent-learning-system.md](agent-learning-system.md) | Full 40KB design with all phases |
| [docs/agent-learning-READY.md](agent-learning-READY.md) | Architect sign-off and readiness checklist |
| [docs/PHASE_1.3_RESCUE_FINDINGS.md](PHASE_1.3_RESCUE_FINDINGS.md) | Root cause analysis of previous attempts |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | System overview and component diagram |
| [docs/agent-learning-operator-guide.md](agent-learning-operator-guide.md) | Human operator guide — how to review, approve, and interpret learnings |

---

*This document defines the minimum implementation required to close the learning loop. The broader Phase 2 strategy (multi-strategy optimization, researcher/writer patterns) is documented in `docs/agent-learning-system.md`.*
