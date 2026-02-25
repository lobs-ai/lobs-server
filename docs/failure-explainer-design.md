# Failure Explainer API — Design Document

**Status:** Approved for implementation  
**Task ID:** a3666bb7-b319-480a-905b-947ba6e00fc8  
**Date:** 2026-02-25

---

## 1. Problem Statement

When a task fails in Mission Control, Rafe has to do log archaeology: check WorkerRun records, read summaries, check escalation tier, look at failure_reason, cross-reference DiagnosticTriggerEvents. This takes minutes per failed task and doesn't scale.

**Goal:** A single endpoint that ingests a task ID and returns a structured, human-readable explanation of *why* it failed, what the failure code is, and what to do next. No LLM involved — pure deterministic rules over existing DB records.

---

## 2. Proposed Solution

### 2a. Architecture Overview

```
GET /api/intelligence/tasks/{task_id}/failure-explanation
                 │
                 ▼
     FailureExplainerService
     (app/services/failure_explainer.py)
                 │
     ┌───────────┴─────────────────────┐
     │  Loads: Task, WorkerRun[]       │
     │         ModelUsageEvent[]       │
     │         DiagnosticTriggerEvent[]│
     └───────────┬─────────────────────┘
                 │
     ┌───────────▼──────────────┐
     │  Rule Engine             │
     │  10 deterministic checks │
     │  in priority order       │
     └───────────┬──────────────┘
                 │
     ┌───────────▼──────────────────────┐
     │  FailureExplanation response     │
     │  • primary_failure_code          │
     │  • all_failure_codes[]           │
     │  • explanation (human text)      │
     │  • next_actions[]                │
     │  • severity (low/medium/high)    │
     │  • supporting_data               │
     └──────────────────────────────────┘
```

**Location:**
- Service: `app/services/failure_explainer.py`
- Router: `app/routers/intelligence.py` (new)
- Tests: `tests/test_failure_explainer.py`
- Registered in: `app/main.py`

### 2b. The 10 Failure Modes

Each rule is evaluated independently. All matching codes are returned. The highest-priority match becomes `primary_failure_code`.

| Priority | Code | Trigger Condition | Severity |
|----------|------|-------------------|----------|
| 1 | `worker_crash` | WorkerRun.ended_at set AND WorkerRun.succeeded IS NULL | high |
| 2 | `timeout_exceeded` | WorkerRun.timeout_reason is not null | medium |
| 3 | `budget_lane_exhausted` | ModelUsageEvent with error_code containing "budget" or "quota" in latest run window | medium |
| 4 | `repeated_failure` | 3+ WorkerRun records with succeeded=False for this task | high |
| 5 | `escalation_stuck` | Task.escalation_tier >= 3 AND work_state not "completed" AND updated_at < now-4h | high |
| 6 | `stuck_in_progress` | Task.work_state == "in_progress" AND updated_at < now-30min | medium |
| 7 | `no_artifact_evidence` | Latest WorkerRun.succeeded == True AND files_modified empty AND commit_shas empty | medium |
| 8 | `transcript_not_durable` | Latest WorkerRun.succeeded == True AND summary is null/empty | low |
| 9 | `SLA_miss_first_response` | Task.status == "active" AND work_state == "not_started" AND created_at < now-2h | medium |
| 10 | `cancelled_without_reason` | Task.status == "rejected" AND failure_reason IS NULL | low |

**"No failure found" case:** If no rule fires, return `primary_failure_code: null` and `explanation: "No failure signals detected. Task may have completed successfully or be in normal state."`.

### 2c. Response Schema

```json
{
  "task_id": "abc123",
  "task_title": "Implement auth middleware",
  "task_status": "active",
  "work_state": "failed",
  "primary_failure_code": "repeated_failure",
  "all_failure_codes": ["repeated_failure", "escalation_stuck"],
  "explanation": "This task has failed 4 times. Most recent failure: 'Could not find target module'. Escalation tier is 3 (diagnostic), unchanged for 5 hours.",
  "next_actions": [
    "Review the failure_reason field for recurring patterns",
    "Consider splitting the task into smaller pieces",
    "Manually assign to a stronger model tier (strong)"
  ],
  "severity": "high",
  "supporting_data": {
    "run_count": 4,
    "failed_run_count": 4,
    "escalation_tier": 3,
    "retry_count": 4,
    "last_failure_reason": "Could not find target module",
    "stuck_duration_minutes": 320,
    "latest_run_id": 42
  },
  "evaluated_at": "2026-02-25T08:00:00Z"
}
```

### 2d. Endpoint Definition

```
GET /api/intelligence/tasks/{task_id}/failure-explanation
```

- **Auth:** Bearer token (standard)
- **Params:** `task_id` (path)
- **Returns:** `FailureExplanation` as above
- **Errors:** 404 if task not found

**Future:** `GET /api/intelligence/runs/{run_id}/failure-explanation` — same logic scoped to a single WorkerRun (post-MVP, not in this handoff).

---

## 3. Tradeoffs

### Why deterministic rules, not LLM?
- LLM adds latency (~2-5s), cost, and unpredictability for a triage tool
- Rules are testable, auditable, and fast (<100ms)
- The 10 failure modes cover 90% of real cases from existing monitor/digest data
- Rules can be extended incrementally

### Why a new `intelligence.py` router?
- The brief mentions "mission-control intelligence routes" — this is a meaningful namespace for future analytics endpoints
- Keeps intelligence/triage logic separate from CRUD routers
- Alternative was `status.py` but that's already crowded and has different semantics

### Why not extend the existing reliability digest?
- Digest is batch/report-oriented (time window, multiple failures)
- This is task-specific, synchronous, instant
- Different use case: digest for daily review, explainer for real-time triage

### Why return `all_failure_codes[]` not just primary?
- A task can simultaneously be stuck, have repeated failures, and lack artifacts
- Rafe needs the full picture, not just one label
- Primary is the highest-priority signal for at-a-glance reading

---

## 4. Implementation Plan

### Task 1 — Service layer (programmer, medium)
Build `app/services/failure_explainer.py` with:
- `FailureExplainerService` class, async, takes `AsyncSession`
- `explain(task_id: str) -> FailureExplanation | None` method
- Loads Task, all WorkerRuns for task, relevant ModelUsageEvents
- Evaluates all 10 rules in priority order
- Returns typed Pydantic model

### Task 2 — Router (programmer, small)
Build `app/routers/intelligence.py` with:
- `GET /intelligence/tasks/{task_id}/failure-explanation` endpoint
- Register in `app/main.py` under `/api` prefix
- Standard auth dependency

### Task 3 — Tests with acceptance cases (programmer, medium)
Build `tests/test_failure_explainer.py` with:
- 10 fixture-driven unit tests, one per failure mode
- Each test creates minimal in-memory DB state that triggers the rule
- Each test verifies: correct code, correct severity, next_actions non-empty
- 1 negative test: no signals → no failure code

See **Acceptance Test Cases** section below for exact specs.

---

## 5. Testing Strategy

### Unit tests (per failure mode)
Each test creates a fresh async SQLite DB session, inserts minimal fixture data, calls `FailureExplainerService.explain()`, and asserts.

### Integration smoke test
One integration test hits the real HTTP endpoint against a test DB with a seeded failed task.

### No LLM mocking needed
All rules are deterministic — no external calls, no mocks required beyond DB fixtures.

---

## 6. Acceptance Test Cases (Top 10 Failure Modes)

These are the reviewer-authored acceptance cases. Each should become a pytest function.

---

### AC-01: `worker_crash`
**Setup:** Task active/in_progress. WorkerRun with `ended_at` set, `succeeded=None`.  
**Expect:** `primary_failure_code == "worker_crash"`, severity == "high".  
**Next action must include:** "Check worker logs" or "worker may have been killed".

---

### AC-02: `timeout_exceeded`
**Setup:** Task with one WorkerRun where `timeout_reason="max_runtime_exceeded"`, `succeeded=False`.  
**Expect:** `primary_failure_code == "timeout_exceeded"`, severity == "medium".  
**Next action must include:** "Consider breaking task" or "increase timeout".

---

### AC-03: `budget_lane_exhausted`
**Setup:** Task with WorkerRun (succeeded=False). ModelUsageEvent in same time window with `error_code="budget_lane_cap"`.  
**Expect:** `all_failure_codes` includes "budget_lane_exhausted".  
**Next action must include:** "Check daily budget lanes" or "/api/usage/budget-lanes".

---

### AC-04: `repeated_failure`
**Setup:** Task with 3 WorkerRun records all having `succeeded=False`.  
**Expect:** `primary_failure_code == "repeated_failure"`, `supporting_data.failed_run_count == 3`.  
**Next action must include:** "escalation" or "split" or "stronger model".

---

### AC-05: `escalation_stuck`
**Setup:** Task with `escalation_tier=3`, `work_state="failed"`, `updated_at` = 5 hours ago.  
**Expect:** `all_failure_codes` includes "escalation_stuck", severity == "high".  
**Next action must include:** "human review" or "manual intervention".

---

### AC-06: `stuck_in_progress`
**Setup:** Task with `work_state="in_progress"`, `updated_at` = 45 minutes ago. No recent WorkerRun.  
**Expect:** `all_failure_codes` includes "stuck_in_progress", severity == "medium".  
**Next action must include:** "Check if worker is still running" or "force-fail".

---

### AC-07: `no_artifact_evidence`
**Setup:** Task with one WorkerRun where `succeeded=True`, `files_modified=[]`, `commit_shas=[]`.  
**Expect:** `all_failure_codes` includes "no_artifact_evidence".  
**Next action must include:** "Verify commit" or "check if work was committed".

---

### AC-08: `transcript_not_durable`
**Setup:** Task with one WorkerRun where `succeeded=True`, `summary=None`, `files_modified=["app/main.py"]`.  
**Expect:** `all_failure_codes` includes "transcript_not_durable".  
**Note:** Should NOT fire `no_artifact_evidence` since files_modified is non-empty.

---

### AC-09: `SLA_miss_first_response`
**Setup:** Task with `status="active"`, `work_state="not_started"`, `created_at` = 3 hours ago. No WorkerRun records.  
**Expect:** `primary_failure_code == "SLA_miss_first_response"`, severity == "medium".  
**Next action must include:** "Check orchestrator" or "scanner may be stuck".

---

### AC-10: `cancelled_without_reason`
**Setup:** Task with `status="rejected"`, `failure_reason=None`, `cancel_reason=None` (or field absent).  
**Expect:** `all_failure_codes` includes "cancelled_without_reason".  
**Next action must include:** "Add a cancellation reason" or "audit rejection".

---

### AC-11: No failure signals (negative case)
**Setup:** Task with `status="completed"`, `work_state="completed"`. WorkerRun with `succeeded=True`, non-empty `files_modified`, non-empty `summary`.  
**Expect:** `primary_failure_code == None`, `all_failure_codes == []`.

---

## 7. Risk Flags

1. **`cancel_reason` field existence** — Task model in models.py shows `failure_reason` but `cancel_reason` was mentioned in inbox-remediation-tracking design. Programmer should verify if `cancel_reason` is a column on Task or if it's only `failure_reason`. If missing, AC-10 should check `failure_reason` only. See `docs/inbox-remediation-tracking-design.md`.

2. **ModelUsageEvent time-window correlation** — There's no direct FK from ModelUsageEvent to Task. Correlation must be done via WorkerRun time windows (started_at/ended_at). This is approximate. If a task has no WorkerRun but has budget issues, AC-03 won't fire. Acceptable for MVP.

3. **`DiagnosticTriggerEvent`** not used in V1 — The model has a `status` field with values fired/suppressed/spawned/failed/completed. This is a richer signal but adds complexity. Reserve for V2 rule: `diagnostic_trigger_failed` when a DiagnosticTriggerEvent for this task has status="failed".

---

## 8. What's Out of Scope (V1)

- Per-run explanation endpoint (future)
- Batch endpoint for multiple tasks at once (future)
- LLM-enhanced explanation text (future)
- Frontend UI changes (separate Mission Control ticket)
- DiagnosticTriggerEvent-based rules (V2)
