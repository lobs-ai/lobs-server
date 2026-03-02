# Design: Stuck Task Auto-Escalation

**Task ID:** 6538EC78-BF72-4E83-A11F-770BC51AA88B  
**Date:** 2026-03-01  
**Status:** Ready for implementation

---

## Problem Statement

Tasks can enter `work_state == "in_progress"` and stay there indefinitely with no signal if a worker crashes or gets stuck. The orchestrator today (`MonitorEnhanced.check_stuck_tasks`) detects stuck tasks but has several problems:

1. **Threshold too aggressive** — fires at 15 minutes, creating noise for legitimately long tasks
2. **Immediately moves to `blocked`** — hides the task from the active queue with no human-visible reason
3. **No deduplication** — fires a new inbox alert every engine tick (every 10s) once a task crosses the threshold
4. **No exclusion of internal tasks** — reflection/sweep/diagnostic/inbox-processing tasks are internal and noisy if escalated

The incident with gs-13-polish stuck at spawn_programmer for 30+ min was detected but not escalated to human review.

---

## Proposed Solution

Enhance `MonitorEnhanced.check_stuck_tasks()` to implement a two-tier response:

| Tier | Threshold | Action |
|------|-----------|--------|
| Flag | 2h in_progress with no update | Create inbox item (once, deduplicated) |
| Auto-reset | 4h (configurable, disabled by default) | Reset to `not_started` + inbox note |

### Key design decisions

**Deduplication via in-memory set**: `_alerted_task_ids: set[str]` on `MonitorEnhanced`. Resets on server restart — acceptable, since a stuck task re-alerting after restart is fine.

**Threshold change**: 15m → 2h. Explicit requirement. Old threshold was too noisy.

**Auto-reset is configurable via OrchestratorSetting**: Key `stuck_task_policy`, field `auto_reset_hours` (default 0 = disabled). Operator can enable via API without code changes.

**Exclusion filter**: Skip tasks where title starts with `reflection`/`sweep`/`diagnostic`/`inbox-processing`/`daily ops brief`, or `task.agent` is in `("reflection", "sweep", "diagnostic")`.

**Do NOT move to `blocked` on flag**: The current code sets `work_state = "blocked"` in `_mark_task_stuck`. This hides the task from scheduling and confuses state machines. Change: only create the inbox item, don't touch `work_state`. The human decides.

---

## Implementation Plan

### Task 1 (Medium): Update MonitorEnhanced stuck task logic

**File:** `app/orchestrator/monitor_enhanced.py`

1. In `__init__`, add:
   - `self._alerted_task_ids: set[str] = set()`
   - `self._flag_hours: float = 2.0`
   - `self._auto_reset_hours: float = 0.0`  (0 = disabled)
   
2. Change `self.stuck_timeout` from 900 to 7200 (2h)

3. In `check_stuck_tasks()`, add exclusion filter:
   ```python
   INTERNAL_PREFIXES = ("reflection", "sweep", "diagnostic", "inbox-processing", "daily ops brief")
   INTERNAL_AGENTS = {"reflection", "sweep", "diagnostic"}
   
   for task in tasks:
       # Skip internal/system tasks
       if any(task.title.lower().startswith(p) for p in INTERNAL_PREFIXES):
           continue
       if task.agent in INTERNAL_AGENTS:
           continue
       
       age_seconds = (now - task.updated_at).total_seconds()
       
       if task.id not in self._alerted_task_ids:
           self._alerted_task_ids.add(task.id)
           await self._mark_task_stuck(task, age_seconds)  # inbox item only
       elif self._auto_reset_hours > 0 and age_seconds >= self._auto_reset_hours * 3600:
           await self._auto_reset_task(task, age_seconds)
           # Remove from alerted so it can re-alert if stuck again
           self._alerted_task_ids.discard(task.id)
   ```

4. Update `_mark_task_stuck()`:
   - Remove `task.work_state = "blocked"` — do NOT change work_state
   - Remove `task.failure_reason = ...` — do NOT modify task
   - Keep inbox item creation, update title/content to match spec:
     - Title: `"⏰ Task appears stuck: {task.title[:60]}"`
     - Content: `"Task {task.id} appears stuck (in_progress for {hours:.1f}h with no update). Project: {task.project_id}. Manual review required."`

5. Add `_auto_reset_task()` method:
   ```python
   async def _auto_reset_task(self, task: Task, age_seconds: float) -> None:
       task.work_state = "not_started"
       task.updated_at = datetime.now(timezone.utc)
       hours = age_seconds / 3600
       alert_id = f"stuck_reset_{task.id}_{int(datetime.now(timezone.utc).timestamp())}"
       alert = InboxItem(
           id=alert_id,
           title=f"🔄 Task auto-reset: {task.title[:60]}",
           content=f"Task {task.id} was automatically reset to not_started after {hours:.1f}h with no update.",
           modified_at=datetime.now(timezone.utc),
           is_read=False,
           summary=f"Task {task.id[:8]} auto-reset after {int(hours)}h stuck"
       )
       self.db.add(alert)
       await self.db.commit()
       logger.warning("[MONITOR] Auto-reset stuck task %s after %.1fh", task.id[:8], hours)
   ```

6. In `run_full_check()`, before calling `check_stuck_tasks()`, load policy from DB:
   ```python
   policy_setting = await self.db.get(OrchestratorSetting, "stuck_task_policy")
   policy = policy_setting.value if (policy_setting and isinstance(policy_setting.value, dict)) else {}
   self._flag_hours = float(policy.get("flag_hours", 2.0))
   self._auto_reset_hours = float(policy.get("auto_reset_hours", 0))
   self.stuck_timeout = int(self._flag_hours * 3600)
   ```

### Task 2 (Small): Tests

**File:** `tests/test_monitor_enhanced.py` (create or extend)

Test cases:
- Task updated 1h ago → NOT flagged
- Task updated 3h ago → flagged, inbox item created, work_state unchanged
- Same task checked again → NOT re-flagged (deduplication)
- Task with title "reflection cycle" → NOT flagged (exclusion)
- Task with agent="sweep" → NOT flagged (exclusion)
- Task updated 5h ago with auto_reset_hours=4 → auto-reset to not_started + inbox item
- After auto-reset, task can be re-alerted if it gets stuck again

---

## Tradeoffs

**In-memory dedup vs DB-persisted**: In-memory is simpler, no schema change. Acceptable since re-alerting after restart is fine behavior. A task that was stuck before restart is still stuck — human should see it again.

**Not changing work_state**: Previous code set `blocked` which removes tasks from active scheduling. That's too aggressive for what's essentially a monitoring signal. Human should decide what to do.

**OrchestratorSetting for config**: Consistent with how budget-lanes and other policies are configured. No code change needed to tune behavior.

---

## Files Changed

- `app/orchestrator/monitor_enhanced.py`
- `tests/test_monitor_enhanced.py` (create or extend)

No schema changes required.
