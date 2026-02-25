# Runbook: infrastructure_failure

**Code:** `infrastructure_failure` (failure_reason: `Infrastructure failure detected`)  
**Severity:** High  
**Category:** Session / system-level failure

---

## What This Means

The circuit breaker detected repeated infrastructure-pattern failures and set the
task to `blocked`. This is triggered by `CircuitBreaker.classify_failure()` when
the error log matches patterns like:

- `Connection refused` / `connection error`
- `Gateway timeout` / `gateway unreachable`
- `Session terminated unexpectedly`
- `SIGKILL` / `OOM Killed`
- `No route to host`
- `SSL handshake failed`

Unlike `worker_failed`, infrastructure failures are systemic — the problem is with
the execution environment, not the task itself.

---

## Diagnosis Steps

1. **Check the circuit breaker state** — if it's open, all tasks for the project are blocked:
   ```bash
   grep "CIRCUIT\|circuit_breaker\|OPEN\|infrastructure" logs/server.log | tail -30
   ```

2. **Check the OpenClaw gateway:**
   ```bash
   openclaw gateway status
   curl -s http://localhost:8000/api/health | jq .
   ```

3. **Check system resources:**
   ```bash
   # Memory
   vm_stat | grep 'Pages free'
   # Disk
   df -h /
   # Load
   uptime
   ```

4. **Review the raw failure message** in the task:
   ```sql
   SELECT failure_reason, retry_count, escalation_tier, updated_at
   FROM tasks WHERE id = '<task-id>';
   ```

5. **Check model provider status** — if this is an API-level error, check:
   - https://status.anthropic.com
   - https://status.openai.com
   - Ollama: `curl http://localhost:11434/api/tags`

---

## Fix Paths

| Symptom | Fix |
|---------|-----|
| OpenClaw gateway down | `openclaw gateway restart`; verify it's listening |
| OOM / memory pressure | Restart the server; reduce concurrent worker count |
| SSL / TLS certificate error | Renew or trust the certificate; check system clock |
| Model provider outage | Wait for provider recovery; switch to alternate model tier |
| Disk full | Free disk space; check `logs/` and `data/` directories |
| Network partition | Restore network; restart gateway |

---

## Resolution

1. Fix the underlying infrastructure issue (see Fix Paths above).
2. Reset the task and clear the failure reason:
   ```bash
   curl -X PATCH http://localhost:8000/api/tasks/<task-id> \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"work_state": "not_started", "failure_reason": null, "escalation_tier": 0}'
   ```
3. Resume the orchestrator:
   ```bash
   curl -X POST http://localhost:8000/api/orchestrator/resume \
     -H "Authorization: Bearer $TOKEN"
   ```
4. Monitor the next run — if it fails again with the same pattern, escalate.

---

## Prevention

- Configure gateway health checks and auto-restart via launchd.
- Set memory limits on worker processes to prevent OOM cascades.
- Run `openclaw gateway status` as part of the daily ops brief health check.
- Use the circuit breaker's half-open state to test recovery before full reset.
