# Runbook: stuck_no_progress

**Code:** `stuck_no_progress` (failure_reason: `Stuck - no progress for N minutes`)  
**Severity:** Medium  
**Category:** Monitor-detected timeout

---

## What This Means

The orchestrator monitor detected that a task has been in `in_progress` state for
longer than the configured stall threshold (default: 30 minutes) without any
heartbeat or worker run update. The task is moved to `blocked` with this reason.

Common underlying causes:
- Worker session silently hung (no output, no crash)
- OpenClaw session was evicted or died without signalling
- Task spawned but the worker never picked it up (scheduling gap)
- Network partition between server and OpenClaw gateway
- The task itself triggered an infinite loop or very long operation

---

## Diagnosis Steps

1. **Confirm the stuck task:**
   ```sql
   SELECT id, title, work_state, failure_reason, updated_at, retry_count
   FROM tasks WHERE failure_reason LIKE 'Stuck%'
   ORDER BY updated_at DESC LIMIT 10;
   ```

2. **Check for an active worker session:**
   ```bash
   curl -s http://localhost:8000/api/agents -H "Authorization: Bearer $TOKEN" | jq '.[] | select(.status == "running")'
   ```

3. **Check OpenClaw session status:**
   ```bash
   openclaw gateway status
   ```

4. **Look for monitor log entries:**
   ```bash
   grep "MONITOR\|stuck\|Stuck" logs/server.log | tail -30
   ```

5. **Verify the task's last heartbeat time** vs the current time. If the gap is
   less than the stall threshold, the monitor may have triggered prematurely.

---

## Fix Paths

| Symptom | Fix |
|---------|-----|
| Worker session still running (orphaned) | Kill the session; reset task to `not_started` |
| OpenClaw gateway down | Restart gateway: `openclaw gateway restart`; resume orchestrator |
| Task has been blocked for hours | Reduce task scope; re-queue with a shorter deadline or simpler spec |
| Monitor threshold too aggressive | Tune `STALL_THRESHOLD_MINUTES` in `monitor_enhanced.py` |
| Infinite loop in task code | Add a timeout constraint in the task notes; use a background time limit |

---

## Resolution

1. Kill any orphaned sessions for this task.
2. Clear the `failure_reason` by resetting the task:
   ```bash
   # Via API
   curl -X PATCH http://localhost:8000/api/tasks/<task-id> \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"work_state": "not_started", "failure_reason": null}'
   ```
3. Resume orchestrator if it was paused:
   ```bash
   curl -X POST http://localhost:8000/api/orchestrator/resume \
     -H "Authorization: Bearer $TOKEN"
   ```

---

## Prevention

- Scope tasks to complete in under 20 minutes of agent wall-clock time.
- Add time estimates to task notes so agents can self-manage their effort.
- Monitor gateway health with `openclaw gateway status` after server restarts.
