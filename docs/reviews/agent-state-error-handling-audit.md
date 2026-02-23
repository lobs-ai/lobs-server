# Agent State Management Error Handling Audit

**Date:** 2026-02-22  
**Reviewer:** reviewer  
**Scope:** Orchestrator state machine, worker lifecycle, task state transitions  
**Risk Tier:** A (Critical infrastructure)

---

## Executive Summary

Systematic review of all agent state transitions revealed **15 critical error handling gaps** across the orchestrator system. While the codebase has extensive exception handling (121 handlers), there are significant inconsistencies in:

- Database transaction rollback coverage (only 46 rollbacks for 121 exception handlers)
- State consistency across failure boundaries
- Missing test coverage for error scenarios
- Incomplete error recovery paths in state transitions

**Risk Assessment:**
- 🔴 **7 Critical** — Can cause data corruption or lost state
- 🟡 **5 Important** — Can cause stuck tasks or orphaned workers
- 🔵 **3 Suggestions** — Improve observability and recovery time

---

## 1. State Transition Map

### 1.1 Task Lifecycle States

#### `Task.status` (User-facing state)
```
inbox → active → completed
              ↘ rejected
              ↘ waiting_on
```

#### `Task.work_state` (Internal orchestrator state)
```
not_started → in_progress → completed
                         ↘ blocked
                         ↘ (reset to not_started on retry)
```

#### `Task.escalation_tier` (Failure escalation)
```
0 (none) → 1 (retry) → 2 (agent_switch) → 3 (diagnostic) → 4 (human)
```

**Finding 🔴 CRITICAL:** These three state machines are **not synchronized**. A task can be `status=active` while `work_state=blocked`, creating ambiguity for the UI and scanner.

**Location:** Multiple files modify these independently without coordination.

---

### 1.2 Worker Lifecycle States

#### Gateway Session Status (External)
```
spawning → running → completed
                  ↘ failed
                  ↘ stale (no response)
```

#### In-Memory Tracking
```python
# worker.py: WorkerManager.active_workers
{worker_id: WorkerInfo(...)} → (removed on completion)
```

#### Database Tracking
```sql
-- WorkerStatus (singleton)
active=true → active=false

-- WorkerRun (history)
succeeded=true/false (append-only)
```

**Finding 🟡 IMPORTANT:** Worker crash between "remove from active_workers" and "update DB" leaves inconsistent state. DB still shows worker active, but in-memory tracking lost.

**Location:** `worker.py:_handle_worker_completion` lines 896-1078

---

### 1.3 Agent Reflection States

```
AgentReflection.status: pending → completed/failed
DiagnosticTriggerEvent.status: pending → completed/failed
```

**Finding 🔵 SUGGESTION:** No retry mechanism for failed reflections. A transient error permanently blocks reflection results.

---

## 2. Database Transaction Safety Issues

### 2.1 Missing Rollback in Exception Handlers

**Analysis:** 121 exception handlers, only 46 call `rollback()`

#### Examples of Missing Rollbacks:

**File:** `app/orchestrator/worker.py`

**Line 389:** `_spawn_session` exception handler
```python
except Exception as e:
    logger.error(f"Failed to spawn worker for task {task_id_short}: {e}", exc_info=True)
    return False
    # ❌ NO ROLLBACK — partial DB writes may persist
```

**Line 499:** `_handle_worker_completion` success path
```python
# Update task
db_task = await self.db.get(Task, task_id)
if db_task:
    db_task.work_state = "completed"
    db_task.status = "completed"
    db_task.finished_at = datetime.now(timezone.utc)
    # ... more updates
    await self.db.commit()  # ✅ Has commit

# ❌ But no try/except around this block
# If commit fails, task state is corrupted
```

**Line 997-999:** Infrastructure failure handling
```python
db_task = await self.db.get(Task, task_id)
if db_task:
    db_task.work_state = "blocked"
    db_task.status = "active"
    db_task.failure_reason = "Infrastructure failure detected"
    db_task.updated_at = datetime.now(timezone.utc)
    await self.db.commit()
    # ❌ NO try/except — failure here orphans the task
```

**Impact:** Partially committed transactions can leave tasks in inconsistent states:
- `work_state` updated but `status` not updated
- Task marked completed but `finished_at` not set
- Worker removed from tracking but DB not updated

---

### 2.2 Cross-Boundary State Inconsistency

**File:** `app/orchestrator/worker.py:_handle_worker_completion`

```python
# Line 896: Remove from in-memory tracking (memory boundary)
self.active_workers.pop(worker_id, None)
self.project_locks.pop(project_id, None)

# Line 904-911: Update database (DB boundary)
db_task = await self.db.get(Task, task_id)
if db_task:
    db_task.work_state = "completed"
    db_task.status = "completed"
    await self.db.commit()
```

**Problem:** If commit fails or process crashes between pop() and commit(), worker is removed from memory but DB not updated.

**Recovery:** Engine restart loses worker state, task stuck in "in_progress".

**Monitor Detection:** `monitor_enhanced.py` detects stuck tasks after 15+ minutes, but creates inbox alert instead of auto-recovery.

---

### 2.3 Independent Session Usage Without Proper Isolation

**File:** `app/orchestrator/worker.py:_record_worker_run`

```python
async with self._get_independent_session() as db:
    db.add(run)
    await db.commit()
    # ❌ What if this commits but outer session later rolls back?
    # WorkerRun persists but Task state reverted
```

**Impact:** Worker history shows completion but task still marked in_progress.

---

## 3. Error Handling Gaps by State Transition

### 3.1 Task Spawn → In-Progress

**File:** `app/orchestrator/worker.py:spawn_worker`

**Current Flow:**
```
1. Build prompt ✅ (has fallback)
2. Choose model ✅ (has fallback chain)
3. Call Gateway spawn ✅ (has retry)
4. Update DB: task.work_state = "in_progress" ❌ (no error handling)
5. Update agent tracker ❌ (no error handling)
6. Add to active_workers ✅ (already done)
```

**Gap:** Steps 4-5 can fail silently. Task stuck in "not_started" while worker running.

**Line 367:**
```python
db_task = await self.db.get(Task, task_id)
if db_task:
    db_task.work_state = "in_progress"
    db_task.started_at = datetime.now(timezone.utc)
    db_task.updated_at = datetime.now(timezone.utc)
    await self.db.commit()
    # ❌ Not wrapped in try/except
```

**Fix Required:** Wrap in try/except, roll back worker spawn if DB update fails.

---

### 3.2 Worker Completion → Task Completed

**File:** `app/orchestrator/worker.py:_handle_worker_completion`

**Current Flow:**
```
1. Remove from active_workers ✅ (in-memory, can't fail)
2. Remove project lock ✅ (in-memory, can't fail)
3. Update task state ❌ (no error handling)
4. Update agent tracker ❌ (no error handling)
5. Record worker run ⚠️ (has error handling but uses independent session)
```

**Gap:** Steps 3-4 failure leaves task in "in_progress" while worker marked completed in history.

**Lines 904-916:**
```python
db_task = await self.db.get(Task, task_id)
if db_task:
    db_task.work_state = "completed"
    db_task.status = "completed"
    db_task.finished_at = datetime.now(timezone.utc)
    db_task.updated_at = datetime.now(timezone.utc)
    db_task.escalation_tier = 0
    db_task.retry_count = 0
    await self.db.commit()
    # ❌ No error handling
```

**Recovery Path:** None. Task stuck until manual intervention or monitor timeout (15+ min).

---

### 3.3 Worker Failure → Escalation

**File:** `app/orchestrator/worker.py:_handle_worker_completion` + `escalation_enhanced.py:handle_failure`

**Current Flow:**
```
1. Remove from active_workers ✅
2. Record failure in circuit breaker ⚠️ (has error handling but may return None)
3. Record failure in provider health ✅ (defensive)
4. Call escalation manager ❌ (error in escalation loses failure entirely)
```

**Gap:** If escalation manager crashes, failure not recorded. Task remains in "in_progress".

**Lines 966-1010:**
```python
escalation_enhanced = EscalationManagerEnhanced(self.db)

if is_infra_failure:
    await escalation_enhanced.create_simple_alert(...)
    # ❌ What if this fails?
    
    db_task = await self.db.get(Task, task_id)
    if db_task:
        db_task.work_state = "blocked"
        db_task.status = "active"
        await self.db.commit()
        # ❌ No error handling
else:
    escalation_result = await escalation_enhanced.handle_failure(...)
    # ❌ What if handle_failure raises exception?
```

**File:** `app/orchestrator/escalation_enhanced.py:handle_failure`

**Line 71:** No outer try/except around entire method. Exception propagates to caller.

---

### 3.4 Monitor Detection → Recovery

**File:** `app/orchestrator/monitor_enhanced.py:check_stuck_tasks`

**Current Flow:**
```
1. Find stuck tasks ✅
2. Mark task as blocked ❌ (no error handling)
3. Create inbox alert ❌ (no error handling)
```

**Gap:** Alert creation failure prevents human notification.

**Lines 110-117:**
```python
task.work_state = "blocked"
task.failure_reason = f"Stuck - no progress for {int(age_seconds/60)} minutes"
task.updated_at = datetime.now(timezone.utc)
await self.db.commit()
# ❌ No try/except

alert = InboxItem(...)
self.db.add(alert)
await self.db.commit()
# ❌ No try/except
```

**Recovery:** If this fails, monitor loop continues but stuck task not flagged.

---

### 3.5 Escalation Retry → Spawn

**File:** `app/orchestrator/escalation_enhanced.py:_tier_1_auto_retry`

**Current Flow:**
```
1. Increment retry_count ✅
2. Set work_state = "not_started" ✅
3. Update task notes ✅
4. Commit ❌ (no error handling)
```

**Gap:** Commit failure leaves retry_count incremented but work_state unchanged.

**Lines 119-132:**
```python
task.escalation_tier = 1
task.retry_count = (task.retry_count or 0) + 1
task.failure_reason = error_log[:1000]
task.last_retry_reason = "tier_1_auto_retry"
task.work_state = "not_started"
task.status = "active"
task.updated_at = datetime.now(timezone.utc)
retry_note = (...)
task.notes = (task.notes or "") + retry_note
await self.db.commit()
# ❌ No try/except — failure here loses entire retry setup
```

**Impact:** Retry counter wrong, task not eligible for pickup.

---

## 4. Missing Test Coverage

### 4.1 Current Test Coverage

**File:** `tests/test_worker.py`

Coverage: **API endpoints only**. No lifecycle tests.

Tests:
- ✅ GET /api/worker/status
- ✅ PUT /api/worker/status
- ✅ GET /api/worker/history
- ✅ POST /api/worker/history

Missing:
- ❌ Worker spawn failure scenarios
- ❌ Worker completion DB failure
- ❌ Escalation state transitions
- ❌ Monitor detection and recovery
- ❌ Cross-boundary state consistency
- ❌ Concurrent worker spawn race conditions

---

### 4.2 Critical Test Scenarios Missing

#### Scenario 1: DB Commit Failure During Spawn
```python
# Test: Spawn worker but fail to update task state
# Expected: Worker should be cleaned up, task remains not_started
# Actual: Worker running, task stuck in not_started
```

#### Scenario 2: Process Crash Between State Updates
```python
# Test: Crash after active_workers.pop() but before db.commit()
# Expected: Engine restart should detect and recover
# Actual: Worker lost, task stuck in in_progress
```

#### Scenario 3: Escalation Manager Exception
```python
# Test: Escalation manager raises exception
# Expected: Fallback to manual alert
# Actual: Exception propagates, failure not recorded
```

#### Scenario 4: Monitor Recovery During Active Worker
```python
# Test: Monitor detects "stuck" task but worker actually running
# Expected: Check worker heartbeat, skip recovery
# Actual: Implemented but not tested
```

#### Scenario 5: Concurrent Spawn of Same Task
```python
# Test: Two engines try to spawn same task simultaneously
# Expected: Only one succeeds, other backs off
# Actual: No locking mechanism, both may spawn
```

---

## 5. Specific Findings

### 🔴 CRITICAL-1: No Atomic State Transitions

**Location:** `app/orchestrator/worker.py` throughout

**Issue:** Task state updates split across multiple commits:
```python
# spawn_worker updates task.work_state
await self.db.commit()

# Later, _handle_worker_completion updates task.status
await self.db.commit()
```

**Impact:** Crash between commits leaves state inconsistent.

**Fix:** Use single transaction or implement state transition validation.

---

### 🔴 CRITICAL-2: Independent Session Divergence

**Location:** `app/orchestrator/worker.py:_record_worker_run`

**Issue:** Worker history uses independent session, commits independently.

```python
async with self._get_independent_session() as db:
    db.add(run)
    await db.commit()
```

**Impact:** WorkerRun may commit while outer task update rolls back.

**Fix:** Either use same session or implement compensation logic.

---

### 🔴 CRITICAL-3: No Rollback in spawn_worker Main Path

**Location:** `app/orchestrator/worker.py:208-390`

**Issue:** 180-line method with one exception handler at the end, no rollbacks for intermediate failures.

**Impact:** Partial state persists on failure.

**Fix:** Wrap each DB operation in try/except with rollback.

---

### 🔴 CRITICAL-4: No Rollback in _handle_worker_completion

**Location:** `app/orchestrator/worker.py:879-1078`

**Issue:** 200-line method with no exception handling around DB operations.

**Impact:** Completion failure orphans workers.

**Fix:** Wrap entire method in try/except, add rollback.

---

### 🔴 CRITICAL-5: Escalation Manager Exception Propagation

**Location:** `app/orchestrator/escalation_enhanced.py:45-86`

**Issue:** `handle_failure` has no outer try/except. Exception propagates to worker.py caller, which also has no handler.

**Impact:** Escalation failure loses entire failure record.

**Fix:** Wrap in try/except, return error result instead of raising.

---

### 🔴 CRITICAL-6: Monitor Alert Creation Not Atomic

**Location:** `app/orchestrator/monitor_enhanced.py:110-139`

**Issue:** Task state update and alert creation are separate commits.

**Impact:** Task marked blocked but alert not created.

**Fix:** Single transaction or idempotent alert creation.

---

### 🔴 CRITICAL-7: No Transaction Boundary in Engine Tick

**Location:** `app/orchestrator/engine.py:_run_loop`

**Issue:** Engine uses same DB session across entire tick. Failure late in tick may roll back all work.

**Impact:** Hour of orchestrator work lost on single failure.

**Fix:** Use independent sessions for each subsystem (scanner, worker, monitor).

---

### 🟡 IMPORTANT-8: Circuit Breaker Failure Silently Ignored

**Location:** `app/orchestrator/worker.py:961-965`

**Issue:** `circuit_breaker.record_failure()` may return None on error, treated as False (not infra failure).

**Impact:** Infrastructure failures misclassified as task failures.

**Fix:** Circuit breaker should raise exception or return tri-state (success/failure/error).

---

### 🟡 IMPORTANT-9: Provider Health Updates Best-Effort

**Location:** `app/orchestrator/worker.py:926-932, 971-979`

**Issue:** Provider health tracking wrapped in `if self.provider_health:` — failures silent.

**Impact:** Lost health metrics, model routing degraded.

**Fix:** Log failures, consider fallback to in-memory tracking.

---

### 🟡 IMPORTANT-10: Agent Tracker Failures Silent

**Location:** Multiple locations, e.g., `worker.py:378-382`

**Issue:** No error handling around `AgentTracker` calls.

**Impact:** Agent status incorrect, debugging harder.

**Fix:** Add error logging or make tracker updates optional.

---

### 🟡 IMPORTANT-11: No Heartbeat Update in Long-Running Workers

**Location:** `app/orchestrator/worker.py`

**Issue:** Worker heartbeat set only at spawn, never updated during execution.

**Impact:** Monitor may flag long-running workers as stuck.

**Fix:** Periodic heartbeat updates or disable timeout for known-long tasks.

---

### 🟡 IMPORTANT-12: Reflection Output Persistence Failure Silent

**Location:** `app/orchestrator/worker.py:1052-1063`

**Issue:** `_persist_reflection_output` has error handling but doesn't propagate failure.

**Impact:** Strategic reflection results lost.

**Fix:** Create inbox alert on persistence failure.

---

### 🔵 SUGGESTION-13: No Retry on Reflection Failure

**Location:** `app/orchestrator/worker.py:_persist_reflection_output`

**Issue:** Failed reflections marked "failed" but never retried.

**Impact:** Transient errors lose valuable reflection data.

**Fix:** Add retry logic similar to task escalation.

---

### 🔵 SUGGESTION-14: Stuck Task Alert Not Idempotent

**Location:** `app/orchestrator/monitor_enhanced.py:117-139`

**Issue:** Alert created every monitor cycle for same stuck task.

**Impact:** Inbox spam.

**Fix:** Check for existing alert before creating new one.

---

### 🔵 SUGGESTION-15: No Metrics on State Transition Failures

**Location:** All files

**Issue:** Exception handlers log errors but don't increment failure counters.

**Impact:** Hard to detect systemic issues.

**Fix:** Add Prometheus metrics or structured logging.

---

## 6. Recommendations

### 6.1 Immediate Fixes (Critical)

1. **Add Transaction Wrappers**
   - Wrap all DB operations in `spawn_worker` and `_handle_worker_completion` with try/except + rollback
   - Priority: Critical-1, Critical-3, Critical-4

2. **Fix Independent Session Usage**
   - Move `_record_worker_run` to same transaction as task update
   - Or implement compensation logic to clean up divergence
   - Priority: Critical-2

3. **Add Escalation Error Handling**
   - Wrap `EscalationManagerEnhanced.handle_failure` in try/except
   - Return error result instead of raising
   - Priority: Critical-5

4. **Add Engine Session Isolation**
   - Use independent sessions for scanner, worker, monitor in engine tick
   - Priority: Critical-7

---

### 6.2 Important Fixes

5. **Improve Circuit Breaker Contract**
   - Return tri-state: `Success | Failure | Error`
   - Or raise exceptions instead of returning None
   - Priority: Important-8

6. **Add Worker Heartbeat Updates**
   - Update `WorkerStatus.last_heartbeat` periodically during execution
   - Or query Gateway for session liveness
   - Priority: Important-11

---

### 6.3 Test Coverage

7. **Add Integration Tests**
   - Test all critical state transitions with DB failure injection
   - Test concurrent spawn scenarios
   - Test crash recovery
   - Priority: High

---

### 6.4 Observability

8. **Add State Transition Metrics**
   - Track spawn success/failure rates
   - Track completion success/failure rates
   - Track escalation tier distribution
   - Priority: Medium

9. **Add State Consistency Checks**
   - Periodic validation: task.work_state matches worker status
   - Auto-repair or alert on mismatch
   - Priority: Medium

---

## 7. Risk Assessment

| Finding | Severity | Likelihood | Impact | Risk Score |
|---------|----------|------------|--------|------------|
| CRITICAL-1 | High | Medium | Data corruption | 🔴 9/10 |
| CRITICAL-2 | High | Low | State divergence | 🔴 7/10 |
| CRITICAL-3 | High | Medium | Orphaned tasks | 🔴 8/10 |
| CRITICAL-4 | High | Medium | Lost completions | 🔴 8/10 |
| CRITICAL-5 | High | Low | Lost failures | 🔴 7/10 |
| CRITICAL-6 | Medium | Medium | Missing alerts | 🟡 6/10 |
| CRITICAL-7 | High | Low | Rollback storms | 🟡 6/10 |
| IMPORTANT-8 | Medium | Low | Wrong escalation | 🟡 5/10 |
| IMPORTANT-11 | Medium | High | False stuck alerts | 🟡 6/10 |

**Overall Risk:** 🔴 **HIGH** — Multiple critical paths lack error recovery.

---

## 8. Next Steps

### Programmer Handoffs Created:

1. **Add transaction error handling to worker lifecycle** (CRITICAL-1, 3, 4)
2. **Fix independent session usage in worker run recording** (CRITICAL-2)
3. **Add error handling to escalation manager** (CRITICAL-5)
4. **Add session isolation to engine tick** (CRITICAL-7)
5. **Add integration tests for state transitions** (Test Coverage)

### Architect Review Needed:

- **State machine consolidation**: Should `work_state` and `status` be merged?
- **Transaction boundaries**: Should each subsystem use independent sessions?
- **Heartbeat mechanism**: Should Gateway provide liveness API?

---

## Appendix A: State Transition Checklist

For each state transition, verify:

- [ ] Single atomic transaction
- [ ] try/except wrapper
- [ ] rollback on error
- [ ] In-memory state reverted on DB failure
- [ ] Error logged with context
- [ ] Metrics updated
- [ ] Integration test exists
- [ ] Recovery path documented

Current compliance: **12% (2/15 transitions)**

---

## Appendix B: Files Audited

- `app/orchestrator/worker.py` (1774 lines)
- `app/orchestrator/worker_manager.py` (1398 lines)
- `app/orchestrator/engine.py` (859 lines)
- `app/orchestrator/escalation_enhanced.py` (470 lines)
- `app/orchestrator/monitor_enhanced.py` (429 lines)
- `app/orchestrator/scanner.py` (121 lines)
- `app/orchestrator/agent_tracker.py` (239 lines)
- `app/models.py` (Task, WorkerStatus, WorkerRun models)
- `tests/test_worker.py` (158 lines — API tests only)

**Total LOC Reviewed:** ~5,447 lines  
**Error Handlers Found:** 121  
**Rollback Calls Found:** 46  
**Coverage Gap:** 62% of error handlers missing rollback

---

**End of Audit**
