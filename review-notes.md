# Diagnostic Review: Cascading Failure Analysis

**Task ID:** `diag_diag_diag_diag_498C8166-D5AA-4BF1-BFCD-54CE726F2707_1771898507_1771899760_1771901561_1771905228`  
**Reviewer:** reviewer  
**Date:** 2026-02-24  
**Status:** 🔴 **CRITICAL** — System stuck in infinite diagnostic loop

---

## Executive Summary

This task has entered a **catastrophic failure cascade**—a diagnostic task spawned to analyze a failure, which itself failed, spawning another diagnostic, ad infinitum. We're now **4 levels deep** in nested diagnostics, all failing with the same error: **"Session not found"**.

**Root Cause:** OpenClaw worker sessions are terminating immediately or failing to start, preventing any agent from executing. The orchestrator's diagnostic system is spawning new reviewers to diagnose the failures, but reviewers themselves cannot execute, creating an infinite loop.

**Impact:**
- Original task (Phase 1.3: Prompt Enhancement) is blocked
- 4+ diagnostic tasks spawned, all failing identically
- System resources consumed by pointless retry loops
- Database under load (locking errors observed)

---

## Failure Timeline

```
Original Task: 498C8166-D5AA-4BF1-BFCD-54CE726F2707
   "Phase 1.3: Prompt Enhancement & Learning Injection"
   ↓ (failed with programmer 3x)
   ↓ (switched to architect)
   ↓ (failed with architect 3x)

Diagnostic #1: diag_498C8166..._1771898507
   "Diagnose failure: Phase 1.3..."
   ↓ (failed with reviewer 3x)
   ↓ (switched to architect)

Diagnostic #2: diag_diag_498C8166..._1771899760
   "Diagnose failure: Diagnose failure: Phase 1.3..."
   ↓ (failed with reviewer 3x)
   ↓ (switched to architect)

Diagnostic #3: diag_diag_diag_498C8166..._1771901561
   "Diagnose failure: Diagnose failure: Diagnose failure..."
   ↓ (failed with reviewer 3x)
   ↓ (switched to architect)

Diagnostic #4: diag_diag_diag_diag_498C8166..._1771905228  ← YOU ARE HERE
   "Diagnose failure: Diagnose failure: Diagnose failure: Diagnose failu"
   ↓ (currently failing with reviewer)
```

---

## Root Cause Analysis

### 1. Immediate Cause: "Session not found"

**What it means:**  
From `app/orchestrator/worker.py:_check_session_status()`:

```python
# Method 3: Check age-based fallback
if spawn_time is not None:
    age_minutes = (time.time() - spawn_time) / 60
    if age_minutes < 5:
        return {"completed": False, "success": False, "error": ""}
    return {"completed": True, "success": False, "error": "Session not found"}
return {"completed": True, "success": False, "error": "Session not found"}
```

This error triggers when:
1. No transcript file found on disk (`.jsonl` or `.deleted.*`)
2. OpenClaw Gateway `sessions_history` API returns empty/None
3. Session is >5 minutes old OR spawn_time is None

**Interpretation:**  
The OpenClaw session is either:
- Never starting (spawn fails silently)
- Starting but terminating immediately (<15 seconds)
- Starting but not writing any output to transcript

### 2. Why Sessions Aren't Completing

Likely causes (in order of probability):

#### A. **OpenClaw Gateway is down or unreachable**
- Worker spawns via `POST /tools/invoke` with `tool: sessions_spawn`
- If Gateway is offline, spawn may appear to succeed but session never runs
- Server logs show successful spawn: `"Spawned worker worker_1771907360_diag_dia... runId=22958af3..."`
- But no transcript is ever written → indicates session died immediately

#### B. **Model/provider failures**
- Task uses `anthropic/claude-sonnet-4-5`
- If Anthropic API is rate-limiting or rejecting requests, session terminates early
- No error propagates back to orchestrator (Gateway just stops session)
- Logs show `"Session not found"` consistently, suggesting systematic issue

#### C. **Prompt or task configuration issue**
- All diagnostic tasks use same reviewer agent prompt
- If prompt contains malformed instructions or missing context, agent might exit immediately
- Less likely (would expect error message in transcript)

#### D. **OpenClaw workspace/permission issue**
- Sessions may fail to write transcripts due to disk permissions
- Less likely (we'd see errors in OpenClaw logs)

### 3. Why This Became an Infinite Loop

**Escalation system design flaw:**

From the task notes history:
```
Auto-retry #1 → Auto-retry #2 → Agent Switch → Diagnostic Spawned
```

The escalation system (`app/orchestrator/escalation_enhanced.py`) spawns diagnostic tasks when agents fail repeatedly. But:

1. **Diagnostic tasks are also tasks** → they can fail too
2. **No circuit breaker for diagnostics** → diagnostic failures spawn more diagnostics
3. **No depth limit** → infinite nesting is possible
4. **Same root cause affects all agents** → if infrastructure is broken, diagnostics fail identically

**Database locking as secondary symptom:**

Error log shows:
```
[ENGINE] Diagnostic triggers failed: (sqlite3.OperationalError) database is locked
```

High retry volume is causing SQLite write contention. Not the root cause, but a symptom of the loop.

---

## Evidence

### From Server Logs

**Successful spawn:**
```
[2026-02-23 23:29:24] [WORKER] Spawned worker worker_1771907360_diag_dia 
for task diag_dia (project=lobs-server, agent=reviewer, 
model=anthropic/claude-sonnet-4-5, runId=22958af3-deb...)
```

**But no completion:**  
No matching `[WORKER] Worker worker_1771907360_diag_dia completed` message in logs.

**Database lock under load:**
```
(sqlite3.OperationalError) database is locked
[SQL: INSERT INTO diagnostic_trigger_events ...]
```

### From Code Analysis

**Worker status check logic** (`worker.py:735-790`):
- Tries 3 methods: disk transcript, Gateway API, age-based fallback
- All 3 failing → no transcript exists, Gateway can't find session

**No safety limit on diagnostic depth:**  
Searched `app/orchestrator/escalation_enhanced.py` — no check for task ID prefix `diag_diag_diag_...`

---

## Recommendations

### 🔴 Immediate Actions

#### 1. **STOP THE LOOP**
- **Manually cancel all diagnostic tasks** with IDs starting with `diag_`
- SQL: `UPDATE tasks SET status='cancelled', work_state='cancelled' WHERE id LIKE 'diag_%'`
- Prevents further cascading

#### 2. **Check OpenClaw Gateway health**
```bash
curl -H "Authorization: Bearer $GATEWAY_TOKEN" \
     http://localhost:8000/api/health
```
- If Gateway is down/unresponsive, restart it
- Check OpenClaw logs for session spawn failures

#### 3. **Verify Anthropic API access**
```bash
# Test direct API call
curl https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "content-type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-sonnet-4-5","max_tokens":1024,"messages":[{"role":"user","content":"test"}]}'
```
- Check for rate limits, quota exhaustion, key issues

#### 4. **Cancel the original task**
- Task `498C8166-D5AA-4BF1-BFCD-54CE726F2707` cannot be completed until infrastructure is fixed
- Mark as `blocked` with blocker: `"OpenClaw infrastructure failure - sessions not starting"`

---

### 🟡 Short-term Fixes

#### 1. **Add diagnostic depth limit**

**File:** `app/orchestrator/escalation_enhanced.py`

Add check before spawning diagnostic:
```python
def _get_diagnostic_depth(task_id: str) -> int:
    """Count how many times 'diag_' appears in task ID."""
    return task_id.count("diag_")

async def _spawn_diagnostic_task(...):
    depth = _get_diagnostic_depth(task_id)
    if depth >= 2:  # Max 2 levels: diag_diag_<original>
        logger.error(
            f"[ESCALATION] Diagnostic depth limit reached ({depth}) "
            f"for task {task_id}. Creating alert instead."
        )
        await self.create_simple_alert(
            task_id=task_id,
            project_id=project_id,
            error_log=f"Diagnostic cascade blocked at depth {depth}",
            severity="critical"
        )
        return None
    # ... existing spawn logic
```

**Acceptance:** Diagnostic tasks fail no more than 2 levels deep.

#### 2. **Add infrastructure health check before spawning**

**File:** `app/orchestrator/worker.py`

Before `_spawn_session()`, add:
```python
async def _check_gateway_health(self) -> bool:
    """Quick health check before spawning expensive sessions."""
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(
                f"{GATEWAY_URL}/health",
                timeout=aiohttp.ClientTimeout(total=5)
            )
            return resp.status == 200
    except:
        return False

async def spawn_worker(...):
    if not await self._check_gateway_health():
        logger.error("[WORKER] Gateway health check failed, aborting spawn")
        return False
    # ... existing spawn logic
```

**Acceptance:** Workers don't spawn when Gateway is unreachable.

#### 3. **Better error propagation from OpenClaw**

**Issue:** "Session not found" is too vague.

**Fix:** Enhance `_check_session_status()` to distinguish:
- Gateway unreachable (connection error)
- Session never created (spawn returned error)
- Session created but died immediately (transcript exists but empty)
- Session still running (age <5 min)

Return structured error:
```python
{
    "completed": True,
    "success": False,
    "error": "session_not_created",  # or "gateway_unreachable", "session_died_early"
    "error_details": "..."
}
```

**Acceptance:** Error messages are actionable.

---

### 🔵 Long-term Improvements

#### 1. **Diagnostic task circuit breaker**
- Track diagnostic task failure rate
- If >50% of diagnostics fail within 1 hour → stop spawning, alert human
- **Priority:** High (prevents future cascades)

#### 2. **OpenClaw session monitoring**
- Add `/api/orchestrator/sessions` endpoint showing active sessions
- Include: spawn time, last heartbeat, transcript path, session state
- Helps debug "Session not found" issues faster
- **Priority:** Medium

#### 3. **Separate diagnostic task queue**
- Don't count diagnostic tasks against `MAX_WORKERS`
- Prevents diagnostics from blocking real work
- **Priority:** Low

---

## Testing Gaps

### Missing Tests

1. **No tests for diagnostic depth limits**  
   → Add: `test_diagnostic_cascade_stops_at_depth_2()`

2. **No tests for worker spawn when Gateway is down**  
   → Add: `test_spawn_fails_gracefully_when_gateway_offline()`

3. **No tests for "Session not found" error handling**  
   → Add: `test_worker_handles_missing_session_gracefully()`

4. **No integration test for escalation loop**  
   → Add: `test_escalation_does_not_infinite_loop()`

---

## Security & Data Integrity

### No Critical Issues Identified

- ✅ No secrets leaked in error logs
- ✅ No SQL injection vectors
- ✅ Database locking is concurrency issue, not corruption
- ⚠️ High retry volume could lead to cost issues if provider charges per failed request

---

## Actionable Handoffs

### 🔴 Critical: Stop the Loop (Human Intervention Required)

**Action:** Manual DB update or API call to cancel diagnostic tasks

```bash
# Option 1: Direct SQL
sqlite3 lobs.db "UPDATE tasks SET status='cancelled', work_state='cancelled', 
                 updated_at=CURRENT_TIMESTAMP 
                 WHERE id LIKE 'diag_%' AND status != 'completed';"

# Option 2: Via API (if endpoint exists)
curl -X PATCH http://localhost:8000/api/tasks/bulk-cancel \
     -H "Authorization: Bearer $TOKEN" \
     -d '{"id_prefix": "diag_"}'
```

**Verify:** `SELECT COUNT(*) FROM tasks WHERE id LIKE 'diag_%' AND status='active';` returns 0

---

### 🟡 Important: Fix Diagnostic Depth Limit (Programmer)

**Title:** Add 2-level depth limit for diagnostic task spawning  
**Files:** `app/orchestrator/escalation_enhanced.py`  
**Context:** See "Short-term Fixes #1" above  
**Acceptance:**
- ✅ Diagnostic tasks fail no more than 2 levels deep
- ✅ Alert created when depth limit hit
- ✅ Unit test `test_diagnostic_depth_limit()` passes

---

### 🟡 Important: Improve Error Messages (Programmer)

**Title:** Distinguish types of "Session not found" errors  
**Files:** `app/orchestrator/worker.py:_check_session_status()`  
**Context:** See "Short-term Fixes #3" above  
**Acceptance:**
- ✅ Error field contains specific code: `session_not_created`, `gateway_unreachable`, `session_died_early`, `session_timeout`
- ✅ Logs include actionable next steps
- ✅ Unit test `test_session_error_types()` passes

---

### 🔵 Nice-to-have: Add Gateway Health Check (Programmer)

**Title:** Pre-flight health check before spawning workers  
**Files:** `app/orchestrator/worker.py`  
**Context:** See "Short-term Fixes #2" above  
**Acceptance:**
- ✅ Workers don't spawn when Gateway `/health` returns non-200
- ✅ Error logged: `"Gateway health check failed, aborting spawn"`
- ✅ Circuit breaker records infrastructure failure

---

## Conclusion

**This is a systems failure, not a code defect in the original task.**

The Phase 1.3 implementation task is fine. The orchestrator's escalation system has a **design flaw**: diagnostic tasks are treated as normal tasks, which can themselves fail and spawn more diagnostics, creating an infinite loop.

**Immediate priority:**
1. Stop the diagnostic cascade (human action)
2. Fix Gateway/Anthropic connectivity issue (if any)
3. Add depth limit to diagnostic spawning (code fix)

**Do NOT retry the original task** until infrastructure is verified healthy and depth limit is in place.

---

## Work Summary

```
CRITICAL: Diagnostic cascade detected (4 levels deep). Root cause: OpenClaw sessions 
failing immediately with "Session not found". Escalation system lacks depth limit. 
Requires immediate manual intervention to cancel all diag_* tasks and fix 
escalation logic. Original task (Phase 1.3) is innocent bystander.
```
