# Spawn Depth Guard — Design

**Problem:** Tasks can loop through escalation tiers. When `retry_count >= 3`, hard-stop: block the task, create an inbox item, before a new worker is spawned.

**Last Updated:** 2026-03-01

---

## Problem Statement

The current escalation system routes failures through up to 4 tiers. There is no **pre-spawn guard** preventing a worker from being dispatched to a task that has already hit its retry ceiling. `monitor_enhanced.detect_failure_patterns()` detects high-retry tasks after the fact but doesn't block them.

---

## Proposed Solution

Add a **spawn depth guard** at the top of `WorkerManager.spawn_worker()` — the single choke point all workers pass through before dispatch.

### Insertion Point

`app/orchestrator/worker.py` → `spawn_worker()`, after project-lock checks (~line 213), before any session spawning.

### Guard Logic

```
MAX_SPAWN_DEPTH = 3  (default; configurable via OrchestratorSetting key: orchestrator.spawn.max_depth)

if task.retry_count >= MAX_SPAWN_DEPTH:
    if task.work_state != "blocked":
        task.work_state = "blocked"
        task.failure_reason = f"Blocked by spawn depth guard after {task.retry_count} failed attempts"
        create InboxItem with task details
        await db.commit()
    log warning
    return False  # never spawn
```

**Idempotent:** If already blocked, skip inbox creation, just return False.

### Inbox Item Fields

- Title: `🛑 Task blocked after {retry_count} failed attempts — {task.title[:50]}`
- Content: task ID, project, retry_count, last failure_reason, recommended actions
- `is_read = False`

---

## Tradeoffs

Guard in `spawn_worker()` is the right call — it's the only place guaranteed to execute before a worker fires, regardless of how the task enters the queue. Monitor-based enforcement is reactive (one extra spawn gets through). Escalation-only enforcement misses direct spawns.

---

## Implementation Plan

### Task 1: Add spawn depth guard in `spawn_worker()`

**File:** `app/orchestrator/worker.py`

1. After project-lock/persisted-run checks, load `retry_count` from task dict (already passed in) or from DB Task row.
2. Read threshold from `OrchestratorSetting` (key `orchestrator.spawn.max_depth`, default `3`) — follow same pattern as other setting reads in the file.
3. If `retry_count >= threshold`:
   - If `work_state != "blocked"`: set blocked, set failure_reason, create `InboxItem`, commit.
   - Log warning.
   - Return `False`.
4. Otherwise: fall through to existing spawn logic unchanged.

**Acceptance criteria:**
- `retry_count >= 3` → no spawn, task blocked, inbox item created (first time)
- Already blocked → no spawn, no duplicate inbox item
- `retry_count < 3` → existing behavior unchanged
- Tests: unit-test `spawn_worker()` with mocked DB for retry_count = 0, 2, 3

---

## Testing Strategy

- Unit: mock Task with retry_count=3 → assert returns False, work_state=blocked, InboxItem created
- Unit: retry_count=2 → assert falls through to spawn
- Unit: already blocked + retry_count=3 → assert returns False, no new InboxItem
- Integration: drive a task to failure 3 times, assert blocked state + inbox item appear
