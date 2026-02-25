# Runbook: no_file_changes

**Code:** `no_file_changes` (failure_reason: `No file changes produced`)  
**Severity:** Low–Medium  
**Category:** Empty output

---

## What This Means

The worker completed without error (exit 0) but the git diff check found zero
modified files in the workspace. The orchestrator interprets this as a failure
because the task was expected to produce code/documentation changes.

Common underlying causes:
- Agent misunderstood the task and only reported findings without writing code
- The task was already complete (idempotent re-run with nothing left to do)
- Agent wrote changes but to a file outside the tracked workspace
- Agent ran tests and reported them but didn't write any code changes
- The task spec was ambiguous about whether output files were expected

---

## Diagnosis Steps

1. **Read the worker summary** — it often explains what the agent did:
   ```sql
   SELECT summary, started_at, model FROM worker_runs
   WHERE task_id = '<task-id>'
   ORDER BY started_at DESC LIMIT 3;
   ```

2. **Check the `.work-summary` file** in the target workspace (if present):
   ```bash
   cat <workspace>/.work-summary
   ```

3. **Look at the task notes** — the agent may have appended an explanation:
   ```bash
   curl -s http://localhost:8000/api/tasks/<task-id> \
     -H "Authorization: Bearer $TOKEN" | jq '.notes'
   ```

4. **Check if this is truly an idempotent run:**
   - Review the task description — if it says "ensure X" and X was already true,
     no changes are correct behaviour.

---

## Fix Paths

| Symptom | Fix |
|---------|-----|
| Agent reported but didn't implement | Rephrase task to be explicit: "Write code that..." instead of "Ensure that..." |
| Task already complete | Close the task as done; no retry needed |
| Agent wrote to wrong location | Add workspace path constraint to task notes |
| Research-only task mistakenly assigned to programmer | Re-assign to `researcher` or `writer` agent |
| Agent confused about scope | Break into smaller, specific tasks with clear file targets |

---

## Resolution

1. Read the worker summary and task notes to understand what actually happened.
2. If the task is genuinely complete, mark it done:
   ```bash
   curl -X PATCH http://localhost:8000/api/tasks/<task-id> \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"work_state": "done", "status": "done"}'
   ```
3. If the task needs to be retried with better guidance, update `task.notes`
   with specific file paths and expected changes, then reset `work_state`.

---

## Prevention

- Use action verbs in task titles: "Implement", "Fix", "Add", "Write" — not "Review" or "Ensure".
- Specify target files in task notes when possible.
- Distinguish research tasks (→ researcher) from implementation tasks (→ programmer).
