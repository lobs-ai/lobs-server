# Error Handling Review — Orchestrator Modules

**Review Date:** 2026-02-23  
**Reviewer:** reviewer  
**Task:** Audit error handling in refactored server modules  
**Files Reviewed:**
- `app/orchestrator/worker.py`
- `app/orchestrator/monitor_enhanced.py`
- `app/orchestrator/sweep_arbitrator.py`
- `app/orchestrator/monitor.py`

---

## Summary

Reviewed error handling patterns across recently refactored orchestrator modules. Found **5 high-priority issues** related to swallowed errors, missing context, and inconsistent error recovery patterns. Overall code quality is good with comprehensive logging, but several patterns need hardening for production reliability.

---

## 🔴 Critical Issues (2)

### 1. Bare `except Exception` without error type specification — swallows critical errors

**Location:** `worker.py:110-111`

```python
except Exception as e:
    logger.warning("[USAGE] Skipping usage event due to DB/logging error: %s", e)
    try:
        await db.rollback()
    except Exception:
        pass  # ❌ Swallowed silently
```

**Issue:** The nested `except Exception: pass` swallows all errors during rollback, including `CancelledError`, `KeyboardInterrupt` (via BaseException), and critical DB failures. This makes debugging impossible when rollback fails.

**Impact:** Critical DB state corruption could be masked. If rollback fails due to connection issues, the system will continue with potentially inconsistent state.

**Fix Required:**
```python
except Exception as e:
    logger.warning("[USAGE] Skipping usage event due to DB/logging error: %s", e)
    try:
        await db.rollback()
    except Exception as rollback_err:
        logger.error("[USAGE] Rollback failed during error recovery: %s", rollback_err, exc_info=True)
        # Re-raise if it's a critical error type
        if isinstance(rollback_err, (asyncio.CancelledError, KeyboardInterrupt)):
            raise
```

**Priority:** 🔴 **High** — Can mask critical DB failures

---

### 2. Missing error context in DB rollback patterns — hard to diagnose

**Locations:**
- `worker.py:1232-1233`
- `worker.py:1339-1340`
- `monitor_enhanced.py:196-197`
- `monitor_enhanced.py:300-301`

**Example:** `worker.py:1232-1233`

```python
except Exception as e:
    logger.error(f"Failed to update worker status: {e}", exc_info=True)
    await self.db.rollback()  # ❌ No error handling
```

**Issue:** DB rollback operations are called without error handling. If rollback fails, the exception propagates up and crashes the calling code without proper cleanup. No context about what operation was being rolled back.

**Impact:** Unhandled rollback failures can crash the orchestrator engine. When reviewing logs, it's unclear which task/worker/project the rollback was for.

**Fix Required:**
```python
except Exception as e:
    logger.error(
        f"Failed to update worker status (worker={worker_id}, task={task_id}): {e}",
        exc_info=True
    )
    try:
        await self.db.rollback()
    except Exception as rollback_err:
        logger.critical(
            f"DB rollback failed after worker status update failure "
            f"(worker={worker_id}, task={task_id}): {rollback_err}",
            exc_info=True
        )
```

**Priority:** 🔴 **High** — Can crash engine loop, missing diagnostic context

---

## 🟡 Important Issues (3)

### 3. Inconsistent error handling in session status checks

**Location:** `worker.py:784-786`

```python
except Exception as e:
    logger.warning("[WORKER] Error checking session status: %s", e)
    return None  # ❌ Silently returns None, caller must handle
```

**Issue:** Error is logged as warning but returns `None`, forcing caller to check for both "session not completed" and "error checking status". This pattern is inconsistent with other methods that raise exceptions on critical failures.

**Comparison with similar pattern in `_get_session_history` (line 812-814):**
```python
except Exception as e:
    logger.debug("[WORKER] Error querying Gateway sessions_history: %s", e)
    return None  # ✅ OK for fallback, but inconsistent logging level
```

**Issue:** Both methods have the same error handling but use different log levels (`warning` vs `debug`). It's unclear which failures are expected vs. unexpected.

**Impact:** Inconsistent error semantics make it hard to distinguish between "operation not possible" vs. "operation failed". Callers must defensively handle `None` returns even for unexpected errors.

**Fix Required:**
1. Document which errors are expected (use `logger.debug`)
2. Document which errors are unexpected (use `logger.warning` or `logger.error`)
3. Consider raising exceptions for unexpected errors rather than returning `None`
4. Add docstring to clarify: "Returns None if status cannot be determined (expected for recently spawned sessions)"

**Example fix:**
```python
async def _check_session_status(...) -> Optional[dict[str, Any]]:
    """Check if a worker session has completed.
    
    Returns:
        Status dict if check succeeded, None if temporary check failure
        (e.g., transcript not yet written, network timeout).
    
    Raises:
        RuntimeError: If session is permanently lost or Gateway is unreachable.
    """
    try:
        # ... existing logic ...
    except aiohttp.ClientError as e:
        # Network errors are temporary, return None
        logger.debug("[WORKER] Gateway unreachable during status check: %s", e)
        return None
    except Exception as e:
        # Unexpected errors should be visible
        logger.error("[WORKER] Unexpected error checking session status: %s", e, exc_info=True)
        return None
```

**Priority:** 🟡 **Medium** — Inconsistent error semantics, hard to debug

---

### 4. Nested exception handlers without context propagation

**Location:** `worker.py:1615-1633` (in `_process_sweep_review_results`)

```python
for d in decisions:
    # ... initiative processing ...
    try:
        result = await engine.decide(...)
        # ... success handling ...
    except Exception as e:
        logger.error(
            "[SWEEP_REVIEW] Failed to process initiative %s: %s",
            initiative_id[:8], e, exc_info=True
        )
        # ❌ Loop continues, error swallowed

# ... after loop ...
try:
    await self.db.commit()
except Exception as e:
    logger.error("[SWEEP_REVIEW] Failed to process sweep review results: %s", e, exc_info=True)
    await self.db.rollback()
```

**Issue:** Individual initiative processing errors are logged but swallowed — loop continues. The final `commit()` might fail due to earlier errors, but the error message doesn't indicate which initiative caused the issue.

**Impact:** Partial batch processing can leave DB in inconsistent state. If 3 out of 10 initiatives fail, the final commit will fail, but it's unclear why. No way to track which initiatives succeeded vs. failed.

**Fix Required:**
```python
failed_initiatives = []
for d in decisions:
    try:
        result = await engine.decide(...)
        # ... success handling ...
    except Exception as e:
        failed_initiatives.append({
            "initiative_id": initiative_id,
            "error": str(e),
        })
        logger.error(
            "[SWEEP_REVIEW] Failed to process initiative %s: %s",
            initiative_id[:8], e, exc_info=True
        )

# After loop, report failures
if failed_initiatives:
    logger.warning(
        "[SWEEP_REVIEW] %d initiative(s) failed to process: %s",
        len(failed_initiatives),
        [f["initiative_id"][:8] for f in failed_initiatives]
    )

try:
    await self.db.commit()
except Exception as e:
    logger.error(
        "[SWEEP_REVIEW] Failed to commit batch (processed=%d, failed=%d): %s",
        processed, len(failed_initiatives), e, exc_info=True
    )
    await self.db.rollback()
```

**Priority:** 🟡 **Medium** — Partial failure handling, missing diagnostic context

---

### 5. Missing timezone awareness checks in monitor time calculations

**Location:** `monitor_enhanced.py:40-48`, `monitor.py:40-48`

```python
now = datetime.now(timezone.utc)
stuck_cutoff = now - timedelta(seconds=self.stuck_timeout)

# ... query tasks ...

for task in tasks:
    updated_at = task.updated_at.replace(tzinfo=timezone.utc) if task.updated_at.tzinfo is None else task.updated_at
    age_seconds = (now - updated_at).total_seconds()  # ⚠️ Can crash if timezone-naive
```

**Issue:** Code assumes `task.updated_at` might be timezone-naive and defensively adds UTC timezone. However, this is done **after** the query, not during comparison. If SQLAlchemy returns naive datetimes and the `.replace()` happens too late, the subtraction will crash.

**Impact:** Orchestrator engine will crash with `TypeError: can't subtract offset-naive and offset-aware datetimes` if the DB migration state is inconsistent (some tables have timezone-aware columns, some don't).

**Fix Required:**

1. **Immediate fix** — move timezone normalization before calculation:
```python
for task in tasks:
    # Normalize timezone BEFORE any operations
    if task.updated_at is None:
        continue
    updated_at = (
        task.updated_at.replace(tzinfo=timezone.utc)
        if task.updated_at.tzinfo is None
        else task.updated_at
    )
    age_seconds = (now - updated_at).total_seconds()
```

2. **Long-term fix** — add DB-level timezone validation:
```python
# In models.py, ensure all datetime columns use timezone-aware types
updated_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True),  # Enforce timezone-aware
    default=lambda: datetime.now(timezone.utc),
    nullable=False,
)
```

**Priority:** 🟡 **Medium** — Can crash monitor, but rare (depends on DB state)

---

## 🔵 Suggestions (minor improvements)

### Additional observations:

1. **Good:** Comprehensive logging with structured log keys (`[WORKER]`, `[MONITOR]`, `[SWEEP]`)
2. **Good:** Most error handlers include `exc_info=True` for stack traces
3. **Good:** DB rollback is called in most error paths (but see issue #2)
4. **Suggestion:** Consider using custom exception types for different failure modes:
   - `WorkerSpawnError` — failed to spawn worker
   - `SessionStatusError` — failed to check session status
   - `DBRollbackError` — rollback failed during recovery

5. **Suggestion:** Add error counters/metrics for monitoring:
   ```python
   # Track error rates for alerting
   self.error_counter = {"spawn_failures": 0, "session_check_failures": 0}
   ```

6. **Suggestion:** In `worker.py:_spawn_session`, the error type classification is good (`classify_error_type`), but results are only used for provider health tracking. Consider exposing error types in API responses for better client-side error handling.

---

## Test Coverage Gaps

**Missing tests for error scenarios:**
1. ❌ No tests for DB rollback failures
2. ❌ No tests for timezone-naive datetime handling in monitor
3. ❌ No tests for partial batch processing failures in sweep review
4. ❌ No tests for worker kill scenarios
5. ❌ No tests for Gateway API failures (rate limits, auth errors)

**Recommendation:** Add integration tests for these failure modes to catch regressions.

---

## Recommended Actions

**Priority order:**

1. **Fix issue #1** (bare except pass) — 30 minutes
2. **Fix issue #2** (missing rollback error handling) — 1 hour (affects 4+ locations)
3. **Fix issue #5** (timezone handling) — 30 minutes
4. **Fix issue #3** (inconsistent error handling) — 1 hour (requires API design decision)
5. **Fix issue #4** (nested exception handling) — 45 minutes

**Total estimated effort:** ~4 hours

---

## Files Requiring Changes

| File | Lines to Change | Estimated Time |
|------|-----------------|----------------|
| `worker.py` | 110-111, 1232-1233, 1339-1340, 784-786, 1615-1633 | 2.5 hours |
| `monitor_enhanced.py` | 40-48, 196-197, 300-301 | 1 hour |
| `monitor.py` | 40-48 | 15 minutes |
| `sweep_arbitrator.py` | (minor, no changes required) | — |

---

## Conclusion

The refactored orchestrator modules have **solid error handling foundations** with comprehensive logging and rollback logic. However, there are **5 high-priority issues** that should be addressed before production deployment:

- 2 critical issues (swallowed errors, missing rollback handling)
- 3 important issues (inconsistent patterns, missing context, timezone bugs)

All issues are **fixable in ~4 hours** with targeted changes. No architectural changes required.

**Overall assessment:** ✅ **Good** — Production-ready after fixing the 5 issues identified above.

---

**Next Steps:**

1. Create programmer handoffs for the top 5 issues
2. Add integration tests for error scenarios
3. Review similar patterns in other orchestrator modules (router, engine, scanner)
