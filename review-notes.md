# Post-Refactor Code Review: Orchestrator Modules

**Reviewer:** reviewer  
**Date:** 2026-02-23  
**Task ID:** 661e7dc4-269e-44f0-a314-23157252a9a3  
**Focus:** worker.py and engine.py split review

## Executive Summary

Reviewed the refactored split between `worker.py` (1916 LOC) and `engine.py` (915 LOC). Overall, the separation of concerns is **sound**, with no circular imports detected. However, several **important issues** were found:

- 🔴 **2 Critical** — Missing error handling, potential data loss
- 🟡 **5 Important** — Missing tests, unclear patterns, scalability concerns
- 🔵 **3 Suggestions** — Code quality improvements

**Verdict:** The refactor improves modularity, but needs fixes before production use.

---

## ✅ What Works Well

### 1. Clear Separation of Concerns
- **engine.py**: Orchestration loop, scheduling, coordination
- **worker.py**: Worker lifecycle, Gateway API integration, session management
- No circular imports: engine imports WorkerManager, worker doesn't import engine

### 2. Persistent State Management
- `WorkerManager` instance reused across ticks (good for stateful tracking)
- Provider health registry shared properly
- Worker info properly encapsulated in `WorkerInfo` dataclass

### 3. Comprehensive Error Handling (mostly)
- 25+ try/except blocks in worker.py
- Proper logging levels (debug/info/warning/error)
- Graceful degradation (e.g., transcript file lookup with multiple fallbacks)

---

## 🔴 Critical Issues

### 1. **Race Condition in Worker Completion Handling**
**Location:** `worker.py:_handle_worker_completion()` (line ~883)

**Issue:** The function reads and modifies task state without transaction isolation. If two workers complete simultaneously and both check `db_task.work_state`, they could both set contradictory states.

```python
# UNSAFE: No transaction isolation
db_task = await self.db.get(Task, task_id)
if db_task:
    db_task.work_state = "completed"
    db_task.status = "completed"
    # ... more updates
    await self.db.commit()
```

**Fix:**
```python
# Use optimistic locking with updated_at check
async with self.db.begin_nested():
    db_task = await self.db.get(Task, task_id, with_for_update=True)
    if db_task:
        # Verify task hasn't been modified by another process
        if db_task.work_state == "in_progress":
            db_task.work_state = "completed"
            # ... rest of updates
```

**Risk:** Task state corruption, lost updates, inconsistent DB state  
**Priority:** HIGH — create programmer handoff

---

### 2. **DB Session Management Violation**
**Location:** `worker.py:_persist_reflection_output()` (line ~1299) and `_process_sweep_review_results()` (line ~1432)

**Issue:** These methods create independent DB sessions (`_get_independent_session()`) to avoid conflicts with engine's session, but then commit changes that may conflict with engine's in-flight transaction. If engine's session is rolled back after worker commits, data becomes inconsistent.

```python
async def _persist_reflection_output(self, ...):
    try:
        async with self._get_independent_session() as db:
            await self._persist_reflection_output_impl(db, ...)
            await db.commit()  # ⚠️ Commits independently of engine session
    except Exception as e:
        logger.warning(...)
```

**Fix:** Either:
1. **Option A (Recommended):** Queue reflection results in-memory and let engine commit them on next tick
2. **Option B:** Use proper two-phase commit pattern with savepoints

**Risk:** Data inconsistency, lost reflections, orphaned initiatives  
**Priority:** HIGH — create programmer handoff

---

## 🟡 Important Issues

### 3. **Missing Tests for Critical Paths**
**Location:** `tests/test_worker.py`

**Issue:** Tests only cover basic API endpoints and session termination. Missing tests for:
- Worker spawning with model fallback chains
- Concurrent worker completion handling
- Reflection output persistence
- Sweep review processing
- Circuit breaker integration
- Provider health tracking

**Fix:** Add integration tests for:
```python
# Example needed test
async def test_worker_completion_concurrent():
    """Verify concurrent completion doesn't corrupt task state."""
    # Spawn 2 workers for different tasks
    # Complete both simultaneously
    # Verify both tasks marked completed correctly
```

**Risk:** Bugs in production, regressions on refactors  
**Priority:** MEDIUM — create programmer handoff

---

### 4. **Unclear Responsibility: Git Operations in WorkerManager**
**Location:** `worker.py:_push_project_repo_if_needed()` (line ~1608)

**Issue:** WorkerManager handles Git operations (commit, push, rebase). This violates single responsibility principle — worker management should not own version control logic.

**Current Pattern:**
```
WorkerManager
  └─ _push_project_repo_if_needed()  # 80+ lines of git logic
      └─ subprocess.run(["git", ...])
```

**Recommendation:** Extract to `app/orchestrator/git_operations.py`:
```python
class GitOperations:
    async def auto_commit_and_push(project_path, task_id, agent_type, ...):
        # All git logic here
```

Then worker calls: `await GitOperations.auto_commit_and_push(...)`

**Risk:** Testing difficulty, unclear ownership, hard to mock  
**Priority:** MEDIUM — consider architect handoff if refactor needed

---

### 5. **No Timeout/Retry for Gateway API Calls**
**Location:** `worker.py:_spawn_session()` (line ~416), `_check_session_status()` (line ~732)

**Issue:** Gateway API calls use `aiohttp.ClientTimeout(total=10)` or `total=30`, but no retry logic. A transient network failure means task fails permanently.

**Current:**
```python
resp = await session.post(
    f"{GATEWAY_URL}/tools/invoke",
    timeout=aiohttp.ClientTimeout(total=30)
)
# If timeout, exception propagates up and task marked failed
```

**Fix:** Add exponential backoff retry wrapper:
```python
async def _gateway_call_with_retry(url, json_data, max_retries=3):
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(url, json=json_data, timeout=...)
                return resp
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
```

**Risk:** Task failures due to transient network issues  
**Priority:** MEDIUM — create programmer handoff

---

### 6. **Potential Memory Leak: Unbounded Active Workers Dict**
**Location:** `worker.py:WorkerManager.active_workers` (line ~136)

**Issue:** `active_workers` dict grows with each spawned worker. If worker completion handling fails (e.g., exception in `_handle_worker_completion`), workers are never removed from dict.

**Current:**
```python
self.active_workers[worker_id] = worker_info  # Added on spawn
# ...
self.active_workers.pop(worker_id, None)  # Removed on completion
```

**If exception before pop:** Worker stays in dict forever, preventing new spawns (capacity check fails).

**Fix:** Add cleanup mechanism:
```python
async def _cleanup_stale_workers(self):
    """Remove workers older than 2x kill timeout."""
    now = time.time()
    stale = [
        wid for wid, info in self.active_workers.items()
        if now - info.start_time > WORKER_KILL_TIMEOUT * 2
    ]
    for wid in stale:
        logger.error("[WORKER] Force-removing stale worker %s", wid)
        self.active_workers.pop(wid, None)
        # Also clear project lock
```

Call this in `check_workers()` periodically.

**Risk:** Orchestrator stops spawning workers after failures accumulate  
**Priority:** MEDIUM — create programmer handoff

---

### 7. **Missing Error Code Classification Coverage**
**Location:** `worker.py:classify_error_type()` (line ~67)

**Issue:** Function classifies Gateway API errors for provider health tracking, but doesn't handle:
- Ollama-specific errors (context length exceeded, model not found)
- OpenRouter errors (insufficient credits)
- Network errors (DNS failure, connection refused)

All these fall into "unknown" category, making provider health tracking less useful.

**Fix:** Add patterns:
```python
def classify_error_type(error_message: str, response_data: dict | None = None) -> str:
    error_lower = error_message.lower()
    
    # ... existing patterns ...
    
    # Ollama errors
    if "context length" in error_lower or "too many tokens" in error_lower:
        return "context_exceeded"
    if "model not found" in error_lower or "model not available" in error_lower:
        return "model_unavailable"
    
    # OpenRouter errors
    if "insufficient credits" in error_lower or "balance too low" in error_lower:
        return "quota_exceeded"
    
    # Network errors
    if any(k in error_lower for k in ("dns", "connection refused", "network unreachable")):
        return "network_error"
    
    return "unknown"
```

**Risk:** Poor provider health decisions, suboptimal fallback routing  
**Priority:** LOW-MEDIUM — create programmer handoff

---

## 🔵 Suggestions

### 8. **Inconsistent Logging Format**
**Location:** Throughout worker.py

**Issue:** Mix of f-strings, %-formatting, and `.format()`:
```python
logger.info(f"[WORKER] Spawned worker {worker_id}")  # f-string
logger.info("[WORKER] Token usage for %s: %d in", worker_id, tokens)  # %-format
logger.error("[GATEWAY] Error: {}".format(error))  # .format()
```

**Fix:** Standardize on one format (recommend %-formatting for structured logging):
```python
logger.info("[WORKER] Spawned worker %s", worker_id)
```

Benefits: Better performance (lazy evaluation), structured logging compatibility

**Priority:** LOW — code quality improvement

---

### 9. **Magic Numbers in Transcript File Search**
**Location:** `worker.py:_check_session_status()` (line ~751)

**Issue:**
```python
if age_seconds < 15:  # Magic number
    return {"completed": False, ...}
if age_seconds > 300:  # Magic number
    return {"completed": True, "error": "Session stale"}
```

**Fix:** Extract to constants:
```python
TRANSCRIPT_ACTIVE_THRESHOLD_SECONDS = 15  # Still being written
TRANSCRIPT_STALE_THRESHOLD_SECONDS = 300  # No activity = stale
```

**Priority:** LOW — code quality improvement

---

### 10. **Opportunity: Extract Transcript Reading to Utility Class**
**Location:** `worker.py:_find_transcript_file()` (line ~599), `_read_transcript_assistant_messages()` (line ~639)

**Issue:** Transcript handling logic (finding files, parsing JSONL, extracting messages) is embedded in WorkerManager. This logic could be useful elsewhere (e.g., debugging tools, analytics).

**Suggestion:** Extract to `app/orchestrator/transcript_utils.py`:
```python
class TranscriptReader:
    @staticmethod
    def find_transcript(session_key: str, hint: str | None = None) -> Path | None:
        ...
    
    @staticmethod
    def read_assistant_messages(transcript_path: Path) -> list[str]:
        ...
    
    @staticmethod
    def extract_summary(session_key: str) -> str | None:
        ...
```

**Benefits:** Reusability, testability, clearer worker.py responsibilities

**Priority:** LOW — nice-to-have refactor

---

## Test Coverage Gaps

### Missing Tests (High Priority)
- [ ] Concurrent worker completion (race conditions)
- [ ] Model fallback chain with provider failures
- [ ] Reflection output persistence error handling
- [ ] Sweep review JSON parsing edge cases
- [ ] Project lock enforcement (one worker per project)
- [ ] Circuit breaker integration

### Missing Tests (Medium Priority)
- [ ] Git auto-commit/push with merge conflicts
- [ ] Transcript file search with deleted files
- [ ] Provider health error classification
- [ ] Worker timeout handling
- [ ] Session termination retry logic

### Missing Tests (Low Priority)
- [ ] Token usage extraction from various transcript formats
- [ ] Work summary file reading
- [ ] Diagnostic event outcome persistence

---

## Architecture Review

### Boundaries Analysis

| Responsibility | Current Owner | Correct? | Notes |
|---------------|---------------|----------|-------|
| Task scanning | Scanner | ✅ Yes | Well separated |
| Worker spawning | WorkerManager | ✅ Yes | Core responsibility |
| Session lifecycle | WorkerManager | ✅ Yes | Owns Gateway API calls |
| Git operations | WorkerManager | ⚠️ No | Should be in GitOperations |
| Reflection persistence | WorkerManager | ⚠️ Unclear | Mixing data and control logic |
| Provider health | ProviderHealthRegistry | ✅ Yes | Well separated |
| Circuit breaker | CircuitBreaker | ✅ Yes | Well separated |
| Escalation | EscalationManagerEnhanced | ✅ Yes | Well separated |

### Coupling Analysis

```
engine.py (coordinator)
  ├─ imports WorkerManager ✓ (explicit dependency)
  ├─ imports Scanner ✓
  ├─ imports MonitorEnhanced ✓
  └─ imports 15+ orchestrator modules ⚠️ (high coupling)

worker.py (worker management)
  ├─ imports EscalationManagerEnhanced ✓
  ├─ imports CircuitBreaker ✓
  ├─ imports AgentTracker ✓
  ├─ imports ModelChooser ✓
  ├─ imports Prompter ✓
  ├─ imports PolicyEngine ✓
  └─ NO imports from engine ✓ (good!)
```

**Finding:** Engine has high fan-out (imports many modules), which is acceptable for a coordinator. Worker has moderate coupling, acceptable for its complexity.

**Recommendation:** Consider facade pattern if engine's import list grows beyond 20 modules.

---

## Duplicated Logic Analysis

### Potential Duplication (Not Found)

Checked for:
- ❌ No duplicate Git operations across files
- ❌ No duplicate session status checking
- ❌ No duplicate transcript parsing
- ❌ No duplicate error classification

**Verdict:** No significant duplication found. Refactor did well here.

---

## Recommendations Summary

### Immediate Fixes (Before Next Release)
1. ✅ Fix race condition in `_handle_worker_completion()` (Critical)
2. ✅ Fix DB session management in reflection persistence (Critical)
3. ✅ Add retry logic to Gateway API calls (Important)
4. ✅ Add cleanup for stale workers in active_workers dict (Important)

### Next Sprint
5. ✅ Write integration tests for critical paths (Important)
6. ✅ Extract Git operations to separate class (Important)
7. ✅ Expand error classification patterns (Medium)

### Future Improvements
8. Standardize logging format (Low)
9. Extract transcript utilities to reusable class (Low)
10. Extract magic numbers to constants (Low)

---

## Handoffs Created

### Handoff 1: Fix Race Condition in Worker Completion
**To:** programmer  
**Priority:** HIGH  
**Files:** `app/orchestrator/worker.py`  
**Task:** Implement transaction isolation in `_handle_worker_completion()` using `with_for_update=True` or row-level locking to prevent concurrent updates to task state.  
**Acceptance:** Concurrent worker completion test passes without state corruption.

### Handoff 2: Fix DB Session Management in Reflection Persistence
**To:** programmer  
**Priority:** HIGH  
**Files:** `app/orchestrator/worker.py`  
**Task:** Refactor `_persist_reflection_output()` and `_process_sweep_review_results()` to either queue results for engine to commit, or use proper two-phase commit.  
**Acceptance:** No DB session conflicts, reflections always persisted correctly.

### Handoff 3: Add Missing Integration Tests
**To:** programmer  
**Priority:** MEDIUM  
**Files:** `tests/test_worker.py`, `tests/test_orchestrator_integration.py`  
**Task:** Add integration tests for worker spawning, concurrent completion, reflection persistence, sweep review, circuit breaker integration, and provider health tracking.  
**Acceptance:** Test coverage for worker.py critical paths reaches 80%+.

### Handoff 4: Add Gateway API Retry Logic
**To:** programmer  
**Priority:** MEDIUM  
**Files:** `app/orchestrator/worker.py`  
**Task:** Implement exponential backoff retry wrapper for all Gateway API calls in `_spawn_session()`, `_check_session_status()`, `_get_session_history()`, `_fetch_session_summary()`.  
**Acceptance:** Transient network failures trigger retry, permanent failures escalate after 3 attempts.

### Handoff 5: Add Stale Worker Cleanup
**To:** programmer  
**Priority:** MEDIUM  
**Files:** `app/orchestrator/worker.py`  
**Task:** Implement `_cleanup_stale_workers()` method and call it periodically in `check_workers()` to remove workers that exceed 2x kill timeout without completing.  
**Acceptance:** Orchestrator recovers from worker completion handler exceptions without manual intervention.

---

## Final Assessment

**Refactor Quality:** 7/10  
- ✅ Clear module separation  
- ✅ No circular imports  
- ✅ Good use of dependency injection  
- ⚠️ Critical bugs in concurrent handling  
- ⚠️ Missing tests for complex paths  
- ⚠️ Some responsibility bleed (Git in WorkerManager)

**Recommendation:** Merge after fixing critical issues (handoffs 1-2). Address important issues in next sprint.

---

**Review completed:** 2026-02-23  
**Reviewer:** reviewer  
**Next steps:** Create 5 programmer handoffs, schedule fix review
