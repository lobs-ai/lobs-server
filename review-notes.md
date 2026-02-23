# Diagnostic Review Notes — Task `diag_186a9af5-9385-4135-996a-e176fe0e6746_1771884271`

## Scope
Analyze repeated failure of original task `186a9af5-9385-4135-996a-e176fe0e6746` ("Document database migration strategy and create migration template").

## Findings

### 1) Root cause (primary)
**This is an orchestration/session-lifecycle failure, not a content/task-definition failure.**

Evidence:
- Task row records infra-style failure reason, not domain error:
  - `tasks.failure_reason = "Session not found"`
  - `tasks.retry_count = 3`, `tasks.escalation_tier = 3`
- All worker attempts failed with **no model output**:
  - `worker_runs` for this task all have `succeeded=0`, `timeout_reason=exit_code_-1`
  - Token usage was `0 in, 0 out` for all runs (logs around lines `28755`, `29161`, `29497`, `29827`)
- Failure happens after ~5 minutes per attempt, matching fallback logic in session checker:
  - `worker_1771883059_186a9af5`: started `21:44:20`, ended `21:49:21`
  - Similar ~5 min for all retries
  - In `app/orchestrator/worker.py` (`_check_session_status`), when transcript/history cannot be found, after age >=5 min it returns `{"error": "Session not found"}` (around lines `767-782`).

Interpretation:
- Spawn succeeded (runId/childSessionKey created), but status polling later cannot find transcript or session history for that key.
- The same pattern reproduces across agents/models (writer -> researcher; OpenAI + Anthropic), so this is unlikely to be prompt/content-specific.

### 2) Contributing issue
**Observability/persistence is degraded by intermittent SQLite lock errors**, which reduces diagnostic fidelity and slows recovery.

Evidence:
- `logs/server.log` shows repeated `sqlite3.OperationalError: database is locked` when persisting reflection output (`app/orchestrator/worker.py:_persist_reflection_output`).

## Recommended fix / workaround

### Immediate operational workaround
1. **Retry as a constrained run**: single agent (`writer` or `programmer`), single model, single attempt, with reduced queue contention.
2. **Reduce dependence on session-history lookup** by requiring direct artifact output in repo files for this task (migration strategy doc + template script) so completion can be validated from filesystem.
3. If possible, **restart/health-check Gateway session services** before retrying this task to clear stale session-state.

### Code-level fixes (recommended)
1. **Harden session completion detection in `worker.py` / `worker_gateway.py`**:
   - Distinguish `session lookup miss` vs `spawn/runtime crash` vs `transcript missing`.
   - Persist structured failure metadata (childSessionKey, runId, gateway response excerpt, poll attempts, age).
2. **Add pre-fail verification before returning `Session not found`**:
   - Re-query `sessions_list`/history with short retry/backoff window before classifying as terminal.
3. **Add DB write retry/backoff** for reflection/task-result persistence to avoid losing diagnostics under lock contention.

## Retry / modify / escalate decision
**Decision: MODIFY then RETRY.**

- The task itself is valid and straightforward documentation work.
- Current failures indicate infra/session tracking instability.
- Escalate only if a constrained retry still returns `Session not found` after session-lifecycle fixes/workaround.

## Suggested handoff
```json
{
  "to": "programmer",
  "initiative": "code-review-fixes",
  "title": "Fix orchestrator session-not-found false terminal failures",
  "context": "Task 186a9af5 failed 3 times with failure_reason='Session not found' and zero token usage across attempts. See review-notes.md and app/orchestrator/worker.py _check_session_status (~lines 767-782).",
  "acceptance": "Worker status checker no longer prematurely terminal-fails as 'Session not found' without structured diagnostics; fallback recheck/backoff implemented; tests cover missing transcript/history + delayed session visibility paths.",
  "files": ["app/orchestrator/worker.py", "app/orchestrator/worker_gateway.py", "tests/"]
}
```