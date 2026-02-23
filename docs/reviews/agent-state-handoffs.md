# Programmer Handoffs — Agent State Error Handling Fixes

**Generated from:** docs/reviews/agent-state-error-handling-audit.md  
**Date:** 2026-02-22  
**Reviewer:** reviewer  
**Initiative:** code-review-fixes

---

## Handoff 1: Add transaction error handling to worker lifecycle

**To:** programmer  
**Priority:** 🔴 CRITICAL  
**Risk:** Data corruption, orphaned tasks  

**Context:**

Agent state audit found 3 critical gaps in `app/orchestrator/worker.py`:

1. `spawn_worker()` (lines 208-390) — 180-line method with exception handler at end but no rollbacks for intermediate DB failures
2. `_handle_worker_completion()` (lines 879-1078) — 200-line method with no exception handling around DB operations
3. Monitor recovery in `monitor_enhanced.py` — separate commits for task state and alert creation

**Current Behavior:**
- Spawn fails after updating task state → task stuck in "in_progress" with no worker
- Completion DB commit fails → worker removed from memory but task not marked completed
- Monitor alert creation fails → stuck task not flagged for human review

**Expected Behavior:**
- All DB state changes atomic within try/except blocks
- Rollback on any failure restores consistent state
- In-memory tracking reverted if DB update fails

**Files:**
- `app/orchestrator/worker.py` (spawn_worker, _handle_worker_completion)
- `app/orchestrator/monitor_enhanced.py` (_mark_task_stuck)
- `tests/test_worker_lifecycle.py` (new file)

**Acceptance Criteria:**
- [ ] `spawn_worker()` entire method wrapped in try/except with rollback
- [ ] If spawn succeeds but DB update fails, worker cleaned up
- [ ] `_handle_worker_completion()` DB operations wrapped in try/except
- [ ] If completion DB update fails, worker re-added to tracking (or marked for retry)
- [ ] Monitor alert creation atomic with task state update
- [ ] Integration test: inject DB commit failure during spawn, verify cleanup
- [ ] Integration test: inject DB commit failure during completion, verify recovery

**Audit References:** CRITICAL-1, CRITICAL-3, CRITICAL-4, CRITICAL-6

---

## Handoff 2: Fix independent session divergence in worker run recording

**To:** programmer  
**Priority:** 🔴 CRITICAL  
**Risk:** State divergence between WorkerRun and Task state  

**Context:**

`worker.py:_record_worker_run()` uses `_get_independent_session()` to commit worker history independently from task state updates. This creates a race condition:

```python
# In _handle_worker_completion:
db_task.work_state = "completed"
await self.db.commit()  # This may fail

# Then:
async with self._get_independent_session() as db:
    db.add(worker_run)
    await db.commit()  # This succeeds independently
```

**Problem:** WorkerRun shows completion but Task still in "in_progress".

**Current Behavior:**
- Task state rollback doesn't affect worker history
- Queries show mismatched state
- No automatic recovery mechanism

**Expected Behavior:**
- WorkerRun and Task state consistent
- Either single transaction OR compensation logic

**Files:**
- `app/orchestrator/worker.py` (_record_worker_run, _handle_worker_completion)
- `tests/test_worker_lifecycle.py`

**Acceptance Criteria:**

**Option A (Preferred):** Use same transaction
- [ ] `_record_worker_run()` no longer uses independent session
- [ ] WorkerRun added to same session as task update
- [ ] Both commit together or both roll back
- [ ] Test: verify rollback of task state also rolls back worker run

**Option B:** Compensation logic
- [ ] Add background job to detect divergence (WorkerRun exists but Task in_progress)
- [ ] Auto-repair: mark Task as completed OR delete WorkerRun
- [ ] Log divergence incidents for monitoring
- [ ] Test: create divergence, verify auto-repair

**Audit Reference:** CRITICAL-2

---

## Handoff 3: Add error handling to escalation manager

**To:** programmer  
**Priority:** 🔴 CRITICAL  
**Risk:** Lost failure records  

**Context:**

`escalation_enhanced.py:handle_failure()` has no outer try/except. Exceptions propagate to `worker.py:_handle_worker_completion()` which also lacks a handler for escalation errors.

**Call Stack:**
```
worker.py:_handle_worker_completion
  → escalation_enhanced.py:handle_failure
    → _tier_1_auto_retry (may raise)
    → _tier_2_agent_switch (may raise)
    → _tier_3_diagnostic (may raise)
    → _tier_4_human_escalation (may raise)
```

Any exception loses the entire failure record.

**Current Behavior:**
- Exception in escalation → worker completion handler crashes
- Task failure not recorded
- No retry, no alert, no human notification

**Expected Behavior:**
- Escalation errors caught and handled gracefully
- Fallback: create manual alert if escalation fails
- Never lose failure record

**Files:**
- `app/orchestrator/escalation_enhanced.py` (handle_failure, all tier methods)
- `app/orchestrator/worker.py` (_handle_worker_completion)
- `tests/test_escalation.py` (new or extend existing)

**Acceptance Criteria:**
- [ ] `handle_failure()` wrapped in top-level try/except
- [ ] Returns `{"action": "error", "reason": "..."}` instead of raising
- [ ] Each tier method wrapped in try/except
- [ ] Tier failure escalates to next tier or returns error
- [ ] `worker.py` checks for error result, creates fallback alert
- [ ] Test: inject exception in tier 1, verify escalation to tier 2
- [ ] Test: inject exception in all tiers, verify manual alert created
- [ ] Test: verify task marked as failed even if escalation errors

**Audit Reference:** CRITICAL-5

---

## Handoff 4: Add session isolation to engine tick

**To:** programmer  
**Priority:** 🟡 IMPORTANT  
**Risk:** Rollback storms  

**Context:**

`engine.py:_run_loop()` uses a single DB session across the entire tick. The tick includes:
1. Scanner (find eligible tasks)
2. Worker manager (spawn workers)
3. Monitor (check stuck tasks)
4. Scheduler (fire events)
5. Reflection cycle
6. Memory maintenance

A failure late in the tick (e.g., memory maintenance) can roll back all work done earlier (e.g., spawned workers).

**Current Behavior:**
- Single session for entire tick (multiple subsystems)
- Late failure rolls back all tick work
- Lost work includes spawned workers, alerts, events

**Expected Behavior:**
- Each subsystem uses independent session
- Failure in one subsystem doesn't affect others
- Clear transaction boundaries

**Files:**
- `app/orchestrator/engine.py` (_run_loop, all subsystem calls)
- `tests/test_orchestrator_engine.py`

**Acceptance Criteria:**
- [ ] Scanner creates and closes its own session
- [ ] Worker manager uses independent session (or keeps current singleton pattern)
- [ ] Monitor uses independent session
- [ ] Scheduler uses independent session
- [ ] Each session wrapped in try/except with rollback
- [ ] Failure in one subsystem logged but doesn't break others
- [ ] Test: inject failure in monitor, verify scanner work persists
- [ ] Test: verify worker spawns survive late-tick failure
- [ ] Document transaction boundaries in comments

**Trade-off Note:** Independent sessions increase DB load but improve fault isolation.

**Audit Reference:** CRITICAL-7

---

## Handoff 5: Add integration tests for worker state transitions

**To:** programmer  
**Priority:** 🟡 IMPORTANT  
**Risk:** Untested critical paths  

**Context:**

Current `tests/test_worker.py` only tests API endpoints (GET/PUT /api/worker/status). No coverage for actual worker lifecycle or error scenarios.

**Missing Coverage:**
- Worker spawn failure (Gateway API down, DB failure, etc.)
- Worker completion DB failure
- Escalation state transitions
- Monitor detection and recovery
- Concurrent spawn race conditions
- Crash recovery (process dies between state updates)

**Files:**
- `tests/test_worker_lifecycle.py` (new file)
- `tests/test_worker_error_recovery.py` (new file)
- `tests/test_escalation.py` (new or extend)
- `tests/conftest.py` (add DB failure injection fixtures)

**Acceptance Criteria:**

### Test Suite 1: Happy Path Lifecycle
- [ ] Test: spawn worker → worker runs → completion success → task marked completed
- [ ] Test: spawn worker → worker runs → completion failure → escalation tier 1
- [ ] Test: tier 1 retry → success → escalation reset

### Test Suite 2: DB Failure Scenarios
- [ ] Test: spawn worker but DB commit fails → verify worker cleaned up
- [ ] Test: completion success but DB commit fails → verify retry or recovery
- [ ] Test: escalation DB update fails → verify fallback alert created

### Test Suite 3: Concurrent Operations
- [ ] Test: spawn same task twice concurrently → only one succeeds
- [ ] Test: spawn two tasks on same project → second queued
- [ ] Test: completion race condition → consistent final state

### Test Suite 4: Crash Recovery
- [ ] Test: simulate crash after active_workers.pop() → verify monitor detects stuck task
- [ ] Test: simulate crash during escalation → verify task marked failed

### Test Suite 5: State Consistency
- [ ] Test: verify work_state and status stay synchronized
- [ ] Test: verify WorkerRun and Task state match
- [ ] Test: verify agent tracker state matches worker state

**Test Infrastructure Needed:**
- DB failure injection fixture (mock commit to raise exception)
- Gateway mock (simulate spawn/status responses)
- Time manipulation (fast-forward for timeout tests)
- State inspection helpers (verify DB and in-memory state)

**Coverage Target:** Minimum 80% of `worker.py` critical paths

**Audit Reference:** Section 4 — Missing Test Coverage

---

## Summary

| Handoff | Priority | Estimated Effort | Risk Reduction |
|---------|----------|------------------|----------------|
| 1. Transaction error handling | 🔴 CRITICAL | 2-3 days | High |
| 2. Session divergence fix | 🔴 CRITICAL | 1-2 days | Medium |
| 3. Escalation error handling | 🔴 CRITICAL | 1 day | Medium |
| 4. Engine session isolation | 🟡 IMPORTANT | 2 days | Medium |
| 5. Integration tests | 🟡 IMPORTANT | 3-4 days | High (prevents regressions) |

**Total Estimated Effort:** 9-12 days (1.5-2 sprints)

**Recommended Order:**
1. Handoff 3 (Escalation) — smallest, unblocks others
2. Handoff 1 (Transaction wrappers) — highest risk reduction
3. Handoff 5 (Tests) — prevents regressions during fixes
4. Handoff 2 (Session divergence) — requires design decision
5. Handoff 4 (Engine isolation) — largest scope, lowest immediate risk

---

**Review Complete.** Handoffs ready for assignment.
