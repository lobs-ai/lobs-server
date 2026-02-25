# Runbook: sessions_spawn_failed / sessions_spawn_not_accepted

**Codes:** `sessions_spawn_failed`, `sessions_spawn_not_accepted`  
**Severity:** High  
**Category:** Worker spawn / gateway failure

---

## What This Means

The orchestrator tried to create a new OpenClaw worker session but the gateway
rejected or failed the request.

- **`sessions_spawn_failed`** — The gateway API call returned an error or the
  session object was not returned in the expected format.
- **`sessions_spawn_not_accepted`** — The gateway responded but the session status
  was not `accepted` / `active` — e.g., `rejected`, `quota_exceeded`, or
  `capacity_full`.

Both codes are set in `worker_gateway.py` and `worker.py` before the task is
escalated.

---

## Diagnosis Steps

1. **Check gateway reachability:**
   ```bash
   openclaw gateway status
   curl -s http://localhost:8000/api/health | jq '.gateway_status // .status'
   ```

2. **Review server logs for the spawn attempt:**
   ```bash
   grep "spawn_failed\|spawn_not_accepted\|sessions_spawn\|GATEWAY" logs/server.log | tail -20
   ```

3. **Check active session count** — gateway may be at capacity:
   ```bash
   curl -s http://localhost:8000/api/agents \
     -H "Authorization: Bearer $TOKEN" | jq 'length'
   ```

4. **Inspect the full error payload** from the task's failure_reason:
   ```sql
   SELECT id, title, failure_reason FROM tasks
   WHERE failure_reason IS NOT NULL ORDER BY updated_at DESC LIMIT 10;
   ```

5. **Check model quota / API keys** — spawn failures sometimes originate from
   the model provider, not the gateway itself.

---

## Fix Paths

| Symptom | Fix |
|---------|-----|
| Gateway not running | `openclaw gateway start`; verify on port default |
| Gateway at session capacity | Kill idle sessions; increase `max_sessions` in gateway config |
| Invalid API key / token | Regenerate: `python bin/generate_token.py <name>`; update `.env` |
| Model quota exceeded | Switch model tier in orchestrator settings or wait for quota reset |
| Gateway version mismatch | Update OpenClaw; restart gateway |
| Spawn timeout | Check network latency to gateway; increase spawn timeout in config |

---

## Resolution

1. Diagnose and fix the root cause from the table above.
2. Verify the gateway is healthy:
   ```bash
   openclaw gateway status
   ```
3. Reset the affected task:
   ```bash
   curl -X PATCH http://localhost:8000/api/tasks/<task-id> \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"work_state": "not_started", "failure_reason": null}'
   ```
4. Resume orchestrator if paused and watch for next spawn attempt.

---

## Prevention

- Set up a gateway health monitor that alerts on repeated spawn failures.
- Configure `MAX_CONCURRENT_WORKERS` to stay below the gateway session limit.
- Rotate API keys on a schedule and update `.env` proactively.
- Include gateway reachability in the daily ops brief health check.
