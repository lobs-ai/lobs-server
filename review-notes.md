# Diagnostic Review: Task bc10aeab — Daily Ops Brief Failure

**Date:** 2026-02-25  
**Reviewer:** reviewer-agent  
**Task ID:** bc10aeab-75e0-4ac8-9df3-e78bffcc1b77  
**Reported Error:** `Session not found`

---

## Root Cause Analysis

The task failed due to a **cascade of 3 compounding failures**, not a single issue:

### Failure 1 (Primary): SQLite DB Lock — Paralyzed the Orchestrator (Feb 24, ~19:49–20:10 UTC)

The orchestrator engine's control loop was crashing **every ~90 seconds** with:

```
sqlite3.OperationalError: database is locked
INSERT INTO agent_reflections ...
```

**Stack trace root:** `engine.py → _run_reflection() → reflection_cycle.py → model_chooser.py → _load_runtime_config()` triggers an autoflush of a pending `agent_reflections` INSERT, which conflicts with another DB writer. This locked up the entire control loop for ~20 minutes.

**Impact:** During this window, no tasks could be dispatched, no sessions could be spawned. The architect task for bc10aeab was queued during this outage period.

### Failure 2: Architect Workflow Missing — Tasks Blocked (`legacy spawn disabled`)

At 20:18 UTC, when the control loop recovered, it logged:
```
[ENGINE] Task bc10aeab (agent=architect) has no matching workflow; blocking task (legacy spawn disabled)
```

Six architect tasks hit this at once — meaning the workflow registry didn't have a matching workflow for architect-type tasks at that moment. This is likely a timing issue (workflow seeds not re-loaded after DB lock recovery) or a missing workflow definition for this agent type.

### Failure 3: Session Spawn Failed — Both Model Attempts Rejected

At 20:33 UTC, the worker finally tried to spawn a session:
```json
{
  "attempts": [
    {"model": "anthropic/claude-sonnet-4-6", "ok": false, "error_type": "unknown"},
    {"model": "anthropic/claude-opus-4-6",  "ok": false, "error_type": "unknown"}
  ]
}
```

Both fallback models failed. Earlier (20:28), we also see:
```
model not allowed: openai-codex/gpt-4
model not allowed: openai-codex/gpt-4-sub
```

The model allowlist rejected certain models, and the fallback chain exhausted without success. The worker's age-based fallback then returned `"Session not found"` — this is the error surfaced to the task system.

### Failure 4 (Current, Active Bug): MissingGreenlet in workflow_executor.py

As of today (08:25 ET), a new error is recurring:
```
[WORKFLOW] Node spawn_architect execution error: greenlet_spawn has not been called
```

At `workflow_executor.py:348`:
```python
ctx = dict(run.context or {})
```

This is a **lazy-load of an ORM attribute outside an async greenlet context**. SQLAlchemy async sessions can't do lazy loads synchronously. `run.context` is being accessed after the session's greenlet context has exited.

---

## Priority Breakdown

### 🔴 Critical: MissingGreenlet bug (active, blocking all spawn_architect attempts)

**File:** `app/orchestrator/workflow_executor.py` around line 348  
**Fix:** Eagerly load `run.context` when the WorkerRun is fetched (using `selectinload` or `options(load_only(...))` in the DB query that populates `run`), or use `with db.no_autoflush:` / `await db.refresh(run)` before accessing it. The attribute must be loaded while still in an async context.

### 🔴 Critical: SQLite lock in reflection cycle (systemic)

**File:** `app/orchestrator/reflection_cycle.py` / `model_chooser.py`  
**Fix:** The `agent_reflections` INSERT is being staged in the session before `model_chooser._load_runtime_config()` runs a SELECT, triggering autoflush. Options:
1. Add `with db.no_autoflush:` block around the DB query in `_load_runtime_config()`
2. Commit or expire the pending INSERT before the SELECT
3. More broadly: review the reflection cycle's session lifecycle — it may need a separate session

### 🟡 Important: "No matching workflow" for architect tasks

**File:** `app/orchestrator/engine.py` / workflow registry  
**Fix:** Confirm there's a workflow definition registered for `agent=architect`. The 6-task block suggests the workflow registry was empty or stale post-recovery. Add logging to show registered workflows at startup and after reload.

### 🟡 Important: Model allowlist blocking fallback

**Logs show:** `model not allowed: openai-codex/gpt-4` and `gpt-4-sub`  
These appear to be stale model IDs the router is attempting. The model tier config may have leftover entries for deprecated model identifiers. The allowlist check is working correctly — but the model chooser is trying invalid models.

---

## What Should Happen With This Task

**Recommendation: Modify + Retry**

The task itself (Daily Ops Brief) is valid. The infrastructure was the failure point, not the task's design. However:

1. **Do NOT retry with `architect` agent** — architect tasks are currently blocked by the MissingGreenlet bug in workflow_executor.py (see Failure 4). They'll keep failing until that's fixed.

2. **Retry with `programmer` agent** — this is the right move (already in progress per the task's history). The handoffs in `docs/handoffs/daily-ops-brief-handoffs.json` are detailed and correct. A programmer can implement without architecture input since the design doc exists.

3. **Fix the infrastructure first** — the MissingGreenlet bug needs a programmer fix or it will block the next attempt too.

---

## Handoffs

### Handoff 1: MissingGreenlet in workflow_executor.py

```json
{
  "to": "programmer",
  "initiative": "infra-bugfix",
  "title": "Fix MissingGreenlet: workflow_executor.py lazy-loads ORM attribute outside async context",
  "context": "workflow_executor.py line 348: `ctx = dict(run.context or {})` triggers SQLAlchemy lazy load of WorkerRun.context outside the async greenlet. Logs show this error recurring every time spawn_architect is retried. Fix: eagerly load run.context when the WorkerRun is fetched from DB (e.g., add options(selectinload(WorkerRun.context)) or load_only). See logs/error.log timestamps 2026-02-25T02:04 and T12:46.",
  "acceptance": "spawn_architect workflow node executes without MissingGreenlet error. WorkerRun.context is loaded eagerly in the query that fetches run.",
  "files": ["app/orchestrator/workflow_executor.py"]
}
```

### Handoff 2: SQLite DB Lock in Reflection Cycle

```json
{
  "to": "programmer",
  "initiative": "infra-bugfix",
  "title": "Fix SQLite lock: agent_reflections autoflush conflicts during reflection cycle",
  "context": "engine.py reflection cycle stages an INSERT into agent_reflections, then model_chooser._load_runtime_config() does a SELECT which triggers autoflush, causing sqlite3.OperationalError: database is locked. This crashed the control loop every ~90 seconds for 20+ minutes (see error.log 2026-02-24T19:49–20:10). Fix: wrap the SELECT in model_chooser._load_runtime_config() with `async with db.no_autoflush:` or commit/expire the pending INSERT before the SELECT runs.",
  "acceptance": "No more 'database is locked' errors from the reflection cycle. Control loop runs stably for 30+ minutes under reflection load.",
  "files": ["app/orchestrator/model_chooser.py", "app/orchestrator/reflection_cycle.py"]
}
```

---

## Additional Notes

- The `brief_service.py` file already exists in `app/services/` — Task 1 of the daily-ops-brief handoffs is complete or partially complete. The programmer retrying this task should check what's already implemented before re-doing work.
- The workflow_executor is seeing recurring `spawn_architect` retry attempts today (08:27–08:28 ET) — this is an active loop that will consume resources. The MissingGreenlet fix is urgent.
- The "Session not found" error message in the task failure log is misleading — the actual root cause is infrastructure failure, not a missing session per se.
