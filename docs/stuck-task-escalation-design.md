# Stuck Task Auto-Escalation Design

**Task ID:** 6538EC78-BF72-4E83-A11F-770BC51AA88B  
**Status:** Design Complete  
**Last Updated:** 2026-03-01

---

## Problem Statement

Tasks can get stuck in `in_progress` indefinitely with no signal to humans. The orchestrator (`monitor_enhanced.py`) already detects stuck tasks at 15min but only logs them and sets `blocked` state without proper deduplication or filtering. During today's incident, `gs-13-polish` was stuck at `spawn_programmer` for 30+ min; the workflow re-spawned instead of escalating to human review.

**Current behavior gaps:**
- Thresholds are 15m/30m/1h — requirement wants 2h
- No filtering of internal/diagnostic tasks (e.g. `diag_` prefix)
- No deduplication — creates new inbox item every engine tick
- Sets state to `blocked` which interferes with auto-unblock logic
- Title format doesn't match spec

---

## Proposed Solution

Modify `monitor_enhanced.py` to:

1. **Detect** `in_progress` tasks with `updated_at > 2h ago` (configurable)
2. **Filter** internal tasks by ID prefix
3. **Deduplicate** inbox alerts with a stable ID
4. **Auto-reset** to `not_started` after 4h if no human action (configurable)

### Internal Task Filtering

Filter tasks whose `id` starts with:
- `diag_` — diagnostic tasks
- `sweep_` — sweep tasks  
- `reflect_` — reflection tasks
- `sys_` — system tasks

Also filter tasks where `hook IS NOT NULL` (these are internal workflow hooks).

### Deduplication

Use stable deterministic inbox item ID: `stuck_alert_{task_id}`.  
Before inserting, check if that ID already exists — skip if so.

### State Change Decision

**Do NOT change `work_state` to `blocked`.**  
Setting `blocked` triggers the auto-unblock logic (which checks `blocked_by` deps), creating confusing churn. Leave task in `in_progress` — the inbox item is the human signal. Auto-reset to `not_started` at 4h is the only state mutation.

### Config (OrchestratorSetting)

- `stuck_task_flag_hours` — default: `2`
- `stuck_task_reset_hours` — default: `4`
- `stuck_task_auto_reset` — default: `true`

---

## Implementation Plan

### Task 1 — Update stuck detection thresholds + add internal task filter

In `check_stuck_tasks()`:
- Change threshold to 2h (load from OrchestratorSetting with 2h default)
- Add `_is_internal_task(task)` helper checking ID prefix + hook field
- Skip internal tasks before any further processing

### Task 2 — Fix `_mark_task_stuck`: dedup + correct message format + no state mutation

- Remove `task.work_state = "blocked"` and `task.failure_reason` mutation
- Use stable alert ID: `stuck_alert_{task_id}`
- Check for existing alert ID before inserting (SELECT then INSERT)
- Title: `Task {task.title[:50]} appears stuck (in_progress for {hours}h with no update)`
- Content: task ID, project, duration, suggested actions

### Task 3 — Add auto-reset after 4h

After creating/skipping inbox item:
```
if auto_reset AND age_hours >= reset_hours AND inbox_item.is_read == False:
    task.work_state = "not_started"
    task.updated_at = now
    append to task.notes: "Auto-reset from stuck in_progress at <timestamp>"
```

Only reset if inbox item is unread — if human read it, they're handling it.

### Task 4 — Tests

File: `tests/orchestrator/test_stuck_escalation.py`

- `_is_internal_task` returns True for all prefix variants + hook tasks
- Task at 1h59m does NOT trigger; 2h1m does
- Running check twice creates only ONE inbox item (dedup)
- Auto-reset fires at 4h with unread inbox item
- Auto-reset does NOT fire if inbox item is read

---

## Files to Change

- `app/orchestrator/monitor_enhanced.py`
- `tests/orchestrator/test_stuck_escalation.py` (new)

---

## Tradeoffs

| Decision | Why |
|----------|-----|
| Don't mutate to `blocked` | Avoids confusing the auto-unblock machinery |
| Prefix filter, not project filter | Internal tasks span projects; prefixes are stable |
| Stable inbox ID for dedup | No schema migration; deterministic and idempotent |
| Auto-reset default on | Stuck = dead worker 90% of time; re-queue is safe and prevents permanent pileup |
