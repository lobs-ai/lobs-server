# Runbook: worker_failed

**Code:** `worker_failed`  
**Severity:** Medium  
**Category:** Worker execution failure

---

## What This Means

The worker process exited with a non-zero exit code and escalation was triggered via `handle_escalation()`. This is the catch-all failure code recorded when the agent process fails but no more-specific reason was set.

Common underlying causes:
- Agent crashed due to an unhandled exception in task logic
- Test suite failures caused the worker to exit 1
- Build/compile error in the target project
- Model returned an unexpected response that the agent couldn't recover from
- Missing tools or environment (e.g., command not found inside the worker)

---

## Diagnosis Steps

1. **Check the failure reason text** (set from the last 1000 chars of the error log):
   ```sql
   SELECT id, title, failure_reason, retry_count, escalation_tier
   FROM tasks WHERE failure_reason IS NOT NULL
   ORDER BY updated_at DESC LIMIT 20;
   ```

2. **Read the worker run summary** for more detail:
   ```sql
   SELECT task_id, summary, exit_code, model, started_at
   FROM worker_runs WHERE task_id = '<task-id>'
   ORDER BY started_at DESC LIMIT 5;
   ```

3. **Inspect logs** for the specific session:
   ```bash
   tail -n 200 logs/server.log | grep -A 5 "<task-id-prefix>"
   ```

4. **Look for patterns** — if the same task is failing repeatedly, the error log
   summary in `worker_runs.summary` often contains the stack trace root cause.

---

## Fix Paths

| Symptom | Fix |
|---------|-----|
| "command not found" / missing tool | Ensure the worker environment has required tools; update worker template |
| Test failures in target project | Fix broken tests, or mark them as known failures with a comment |
| Model response format error | Update prompt or parsing logic; consider switching model tier |
| Unhandled exception in task logic | Review the task notes and error log; update task with clearer spec |
| Build errors in target project | Fix compilation errors before retrying the task |

---

## Resolution

1. Read `task.failure_reason` and the latest `worker_run.summary`.
2. Update `task.notes` with the specific fix or clarification needed.
3. Reset `task.work_state = 'not_started'` to re-queue for the orchestrator.
4. If the task is structurally flawed, set `task.status = 'rejected'` and document why.

---

## Prevention

- Write clear, scoped task descriptions — vague tasks produce confused agents.
- Break large tasks into smaller, verifiable subtasks.
- Ensure the target project's test suite is green before assigning new tasks.
