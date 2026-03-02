# Stuck Task Auto-Escalation Design

**Status:** Ready for implementation  
**Date:** 2026-03-01  
**Task ID:** 6538EC78-BF72-4E83-A11F-770BC51AA88B

---

## Problem

Tasks can get stuck `in_progress` indefinitely with no signal. The monitor detects stuck tasks but:

1. **Skips tasks with active workflow runs** — the incident case (gs-13-polish stuck at `spawn_programmer`) had a running WorkflowRun, so it was invisible to the existing check.
2. **No label/prefix filtering** — reflection/sweep/internal tasks would create noisy false alerts.
3. **Current thresholds are 15min/1h** — too aggressive for the new 2h human-review threshold.
4. **No configurable auto-reset** — after 4h with no human action, should revert to `not_started`.

---

## Solution

New service: `app/services/stuck_task_escalator.py` — `StuckTaskEscalator`.

Called once per engine monitor cycle (same cadence as `monitor_enhanced.run_full_check()`).

### Detection Logic

Two stuck conditions (both require `work_state == 'in_progress'`):

**Case A — No workflow run:**  
`updated_at < now - flag_hours` AND no active WorkflowRun for the task.  
This is the existing path (workers crash silently).

**Case B — Stuck workflow run:**  
A WorkflowRun exists in `status=running` AND `workflow_run.updated_at < now - flag_hours`.  
This catches spawn nodes waiting forever (the gs-13-polish incident pattern).

### Filtering (skip these tasks)

Skip if any of:
- `task.agent` is `"reflection"` or `"sweep"`
- `task.title` starts with `[reflection]`, `[sweep]`, `[internal]`, or `[diagnostic]` (case-insensitive)
- `task.project_id` in a configurable skip-list (OrchestratorSetting `stuck_escalation_skip_projects`)

### Deduplication

No new DB columns. Check InboxItems: if an InboxItem with `id` starting with `stuck_{task.id}_` was created within the last `flag_hours * 2` hours, skip. Query: `InboxItem.id.like(f"stuck_{task.id}_%")` with `modified_at` filter.

### State Transitions

**At flag_hours (default 2h):** Create InboxItem alert. Do NOT change `work_state` — leave it `in_progress`. Cancelling the workflow is too destructive without human review.

**At reset_hours (default 4h):** If task is still `in_progress` AND alert was previously created → reset `work_state` to `not_started`, set `failure_reason = "Auto-reset: stuck in_progress for {N}h"`. Also cancel any active WorkflowRun (set status=`cancelled`).

### Configuration

Settings stored in `OrchestratorSetting` table:

| Key | Default | Meaning |
|-----|---------|---------|
| `stuck_escalation_flag_hours` | `2` | Hours before creating inbox alert |
| `stuck_escalation_reset_hours` | `4` | Hours before auto-reset to not_started (0 = disabled) |
| `stuck_escalation_skip_projects` | `[]` | Project IDs to exclude |

### Inbox Alert Format

```
Title: "⏰ Task stuck in_progress for {Xh}: {task.title[:60]}"

Body:
Task **{task.id[:8]}** ({task.title}) has been `in_progress` for {Xh Ym} with no update.

- **Project:** {project_id}
- **Last updated:** {updated_at ISO}
- **Stuck type:** no_worker | stuck_workflow_run
- **Workflow run:** {run.id[:8] if applicable}

If no action is taken, the task will be auto-reset to `not_started` at {reset_time}.
```

---

## Architecture

```
engine._monitor_loop()
  ├─ monitor_enhanced.run_full_check()      ← existing
  └─ stuck_task_escalator.check(db)         ← NEW (called after monitor)

StuckTaskEscalator.check(db):
  1. Load config from OrchestratorSetting (flag_hours, reset_hours, skip_projects)
  2. Query: in_progress tasks updated > flag_hours ago
  3. Filter: skip reflection/sweep/internal by agent field + title prefix
  4. For each task: determine stuck_type (Case A: no run, Case B: stuck run)
  5. Dedup: skip if InboxItem with stuck_{task.id}_ exists and is recent
  6. Create InboxItem alert
  7. Auto-reset: if > reset_hours AND alert exists, reset + cancel workflow run
  8. Return {flagged: int, reset: int}
```

---

## Tradeoffs

**Why not modify existing `check_stuck_tasks` in monitor_enhanced.py?**  
The existing check (15min threshold, kill-worker behavior) serves fast worker crash recovery. The 2h human-review escalation is a different concern. Mixing them complicates the monitor. Separate service = separate concerns, easier to test.

**Why no new DB column for `stuck_flagged_at`?**  
InboxItem dedup via `id` prefix is sufficient and avoids a migration. If we need richer state later, add the column then.

**Why not change `work_state` to `blocked` at flag time?**  
`blocked` means dependency blocking. Stuck is different — the task hasn't been explicitly blocked, just silently stalled. Leaving it `in_progress` with an alert keeps the human in control of the transition.

**Why cancel WorkflowRun on auto-reset?**  
A running WorkflowRun prevents re-spawn. Cancelling it enables clean re-queue. Only done at `reset_hours` after the human had `flag_hours` window to intervene.

---

## Testing Strategy

1. **Unit: filter logic** — tasks with agent=reflection/sweep, or title prefix [internal]/[sweep]/[reflection]/[diagnostic] are skipped
2. **Unit: dedup** — second call within window doesn't create second inbox item
3. **Unit: flag threshold** — tasks < 2h don't trigger; tasks ≥ 2h do
4. **Unit: reset threshold** — tasks ≥ 4h get reset to not_started + WorkflowRun cancelled
5. **Integration: Case A** — in_progress task with no WorkflowRun triggers after 2h
6. **Integration: Case B** — in_progress task with stuck WorkflowRun (running but stale) triggers after 2h
7. **Integration: config** — OrchestratorSetting overrides respected

---

## Implementation Plan

### Task 1 — Core service (medium)
Create `app/services/stuck_task_escalator.py`:
- `StuckTaskEscalator` class with `async def check(db) -> dict`
- Config loading from OrchestratorSetting with hardcoded defaults
- Filter, detect, dedup, alert, reset logic as specified
- Tests in `tests/test_stuck_task_escalator.py`

### Task 2 — Engine wiring (small)
In `app/orchestrator/engine.py`:
- Import `StuckTaskEscalator`, instantiate once
- After `monitor_enhanced.run_full_check()`, call `await stuck_task_escalator.check(db)`
- Log: `[ENGINE] Stuck escalation: flagged={n}, reset={n}`
- Wrap in try/except (escalator errors must not kill engine loop)

---

## Files

- **CREATE** `app/services/stuck_task_escalator.py`
- **CREATE** `tests/test_stuck_task_escalator.py`
- **MODIFY** `app/orchestrator/engine.py` — wire in escalator
