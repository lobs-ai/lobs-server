# How to Read Observability Metrics

**Purpose:** Understand and interpret logs, traces, and metrics to monitor system health

**Related:** [ADR 0005: Observability Architecture](../decisions/0005-observability-architecture.md)

---

## Quick Reference

```bash
# View all metrics
curl http://localhost:8000/metrics

# Specific metric families
curl http://localhost:8000/metrics | grep lobs_tasks
curl http://localhost:8000/metrics | grep lobs_worker
curl http://localhost:8000/metrics | grep lobs_circuit_breaker

# Query logs by trace ID
jq 'select(.trace_id == "abc123")' logs/server.log

# Query logs by event type
jq 'select(.event_type == "worker.spawned")' logs/server.log

# Recent errors
jq 'select(.level == "ERROR")' logs/server.log | tail -20
```

---

## Metrics Reference

### Task Metrics

#### `lobs_tasks_total`
**Type:** Counter  
**Labels:** `status`, `agent_type`, `initiative`  
**Meaning:** Total number of tasks processed

```promql
# Example values
lobs_tasks_total{status="completed",agent_type="programmer",initiative="none"} 45
lobs_tasks_total{status="failed",agent_type="programmer",initiative="none"} 3
lobs_tasks_total{status="completed",agent_type="researcher",initiative="init-123"} 12
```

**What to watch:**
- **High failure rate:** `failed / (completed + failed) > 0.1` → investigate common failures
- **Imbalanced agents:** One agent type has most failures → review prompts/capabilities
- **Initiative stalling:** Tasks for initiative not increasing → check if blocked

**Query examples:**
```promql
# Failure rate by agent type
rate(lobs_tasks_total{status="failed"}[1h]) 
  / rate(lobs_tasks_total[1h])

# Tasks per hour
rate(lobs_tasks_total[1h])
```

#### `lobs_tasks_in_state`
**Type:** Gauge  
**Labels:** `work_state`  
**Meaning:** Current count of tasks in each state

```promql
lobs_tasks_in_state{work_state="queued"} 5
lobs_tasks_in_state{work_state="running"} 3
lobs_tasks_in_state{work_state="completed"} 142
```

**What to watch:**
- **Queued growing:** More tasks queued than workers can handle → scale workers
- **Running constant:** Tasks stuck in running state → check for timeouts
- **Blocked increasing:** Tasks hitting blockers → review and unblock

#### `lobs_task_duration_seconds`
**Type:** Histogram  
**Labels:** `agent_type`, `status`  
**Meaning:** Time from task start to completion

```promql
# Buckets
lobs_task_duration_seconds_bucket{agent_type="programmer",status="completed",le="60"} 10
lobs_task_duration_seconds_bucket{agent_type="programmer",status="completed",le="300"} 25
lobs_task_duration_seconds_bucket{agent_type="programmer",status="completed",le="1800"} 40

# Quantiles
lobs_task_duration_seconds_sum{agent_type="programmer",status="completed"} 12450
lobs_task_duration_seconds_count{agent_type="programmer",status="completed"} 42
```

**What to watch:**
- **P95 duration increasing:** Tasks getting slower → investigate bottleneck
- **Failed tasks duration:** If failed tasks take full timeout → tasks are stuck, not fast-failing

**Query examples:**
```promql
# Average task duration
lobs_task_duration_seconds_sum / lobs_task_duration_seconds_count

# P95 duration (approximate)
histogram_quantile(0.95, lobs_task_duration_seconds_bucket)

# Tasks completing under 5 minutes
lobs_task_duration_seconds_bucket{le="300"} / lobs_task_duration_seconds_count
```

---

### Worker Metrics

#### `lobs_worker_spawns_total`
**Type:** Counter  
**Labels:** `agent_type`, `model_tier`  
**Meaning:** Total worker spawn attempts

```promql
lobs_worker_spawns_total{agent_type="programmer",model_tier="standard"} 38
lobs_worker_spawns_total{agent_type="researcher",model_tier="strong"} 15
```

**What to watch:**
- **Spawn rate matches task rate:** Workers are being created for tasks
- **Model tier distribution:** Are we using expensive models too often?

#### `lobs_active_workers`
**Type:** Gauge  
**Labels:** `agent_type`  
**Meaning:** Currently running workers

```promql
lobs_active_workers{agent_type="programmer"} 2
lobs_active_workers{agent_type="researcher"} 1
```

**What to watch:**
- **At max capacity:** All worker slots full → may need to scale
- **Stuck at same number:** Workers not completing → check for hung tasks

**Alert:**
```promql
# Too many concurrent workers
lobs_active_workers > 10
```

#### `lobs_worker_spawn_duration_seconds`
**Type:** Histogram  
**Labels:** `agent_type`  
**Meaning:** Time to spawn a worker

```promql
lobs_worker_spawn_duration_seconds_bucket{agent_type="programmer",le="1.0"} 35
lobs_worker_spawn_duration_seconds_bucket{agent_type="programmer",le="5.0"} 42
```

**What to watch:**
- **P95 > 10s:** Gateway slow or overloaded
- **Increasing over time:** Resource exhaustion, need to scale

**Query:**
```promql
# P95 spawn time
histogram_quantile(0.95, lobs_worker_spawn_duration_seconds_bucket)
```

#### `lobs_worker_failures_total`
**Type:** Counter  
**Labels:** `agent_type`, `failure_type`  
**Meaning:** Worker failures by type

```promql
lobs_worker_failures_total{agent_type="programmer",failure_type="timeout"} 5
lobs_worker_failures_total{agent_type="programmer",failure_type="spawn_error"} 2
lobs_worker_failures_total{agent_type="researcher",failure_type="result_parse_error"} 1
```

**What to watch:**
- **Timeout failures increasing:** Tasks too complex or timeout too short
- **Spawn errors:** Gateway or model provider issues
- **Parse errors:** Agent output format changed → update parser

---

### Circuit Breaker Metrics

#### `lobs_circuit_breaker_trips_total`
**Type:** Counter  
**Labels:** `provider`, `error_type`  
**Meaning:** How many times circuit breaker opened

```promql
lobs_circuit_breaker_trips_total{provider="ollama",error_type="timeout"} 3
lobs_circuit_breaker_trips_total{provider="anthropic",error_type="rate_limit"} 1
```

**What to watch:**
- **Frequent trips:** Provider unstable → investigate provider health
- **Specific error type dominant:** Targeted issue (e.g., all rate limits)

#### `lobs_circuit_breaker_state`
**Type:** Gauge  
**Labels:** `provider`  
**Values:** `0` = closed (healthy), `1` = open (unhealthy), `2` = half-open (testing)

```promql
lobs_circuit_breaker_state{provider="ollama"} 0
lobs_circuit_breaker_state{provider="anthropic"} 1
```

**What to watch:**
- **State = 1 (open):** Provider is down or degraded, tasks will fail
- **Stuck in half-open:** Provider unstable, keeps failing health checks

**Alert:**
```promql
lobs_circuit_breaker_state == 1
```

---

### Escalation Metrics

#### `lobs_escalations_total`
**Type:** Counter  
**Labels:** `tier`, `reason`  
**Meaning:** Task escalations by tier and reason

```promql
lobs_escalations_total{tier="reflection",reason="task_failed"} 8
lobs_escalations_total{tier="code_review",reason="quality_check"} 12
lobs_escalations_total{tier="human",reason="blocked"} 2
```

**What to watch:**
- **High escalation rate:** Tasks failing frequently → improve prompts or capabilities
- **Human escalations:** Tasks system can't handle → may need new features
- **Reflection not helping:** If reflection tier high but failures not decreasing

---

### API Metrics

#### `lobs_api_requests_total`
**Type:** Counter  
**Labels:** `method`, `endpoint`, `status_code`

```promql
lobs_api_requests_total{method="GET",endpoint="/api/tasks",status_code="200"} 1523
lobs_api_requests_total{method="POST",endpoint="/api/tasks",status_code="201"} 87
lobs_api_requests_total{method="GET",endpoint="/api/tasks",status_code="500"} 3
```

**What to watch:**
- **5xx errors:** Server errors → investigate logs
- **High request rate:** Load increasing → may need caching or rate limiting

#### `lobs_api_request_duration_seconds`
**Type:** Histogram  
**Labels:** `method`, `endpoint`

**What to watch:**
- **P95 > 1s:** Endpoint slow → optimize query or add index
- **Increasing over time:** Database growth or N+1 queries

**Query:**
```promql
# Slow endpoints
histogram_quantile(0.95, lobs_api_request_duration_seconds_bucket) > 1.0
```

---

### Model Usage Metrics

#### `lobs_model_usage_total`
**Type:** Counter  
**Labels:** `provider`, `model`, `tier`

```promql
lobs_model_usage_total{provider="anthropic",model="claude-sonnet-4",tier="standard"} 45
lobs_model_usage_total{provider="ollama",model="qwen2.5-coder:32b",tier="medium"} 23
```

**What to watch:**
- **Expensive model overuse:** Too many `strong` tier calls → review routing
- **Cheap model failures:** `micro` tier failing → may need better models

#### `lobs_model_tokens_total`
**Type:** Counter  
**Labels:** `provider`, `model`, `token_type` (input/output)

```promql
lobs_model_tokens_total{provider="anthropic",model="claude-sonnet-4",token_type="input"} 125420
lobs_model_tokens_total{provider="anthropic",model="claude-sonnet-4",token_type="output"} 45230
```

**What to watch:**
- **Token costs:** Calculate cost = tokens × price per token
- **Output/input ratio:** High ratio = agent is verbose (good or bad depending on task)

**Cost calculation:**
```python
# Anthropic pricing (example)
INPUT_PRICE = 3.00 / 1_000_000   # $3 per million tokens
OUTPUT_PRICE = 15.00 / 1_000_000  # $15 per million tokens

input_tokens = metrics["lobs_model_tokens_total"]["input"]
output_tokens = metrics["lobs_model_tokens_total"]["output"]

cost = (input_tokens * INPUT_PRICE) + (output_tokens * OUTPUT_PRICE)
```

---

## Log Event Types

### Task Events

| Event Type | Meaning | When It Fires |
|------------|---------|---------------|
| `task.queued` | Task created and queued | Task creation |
| `task.started` | Task picked up by orchestrator | Scanner finds task |
| `task.completed` | Task finished successfully | Worker reports success |
| `task.failed` | Task failed | Worker reports failure or timeout |
| `task.blocked` | Task marked as blocked | Manual or automatic blocking |
| `task.handoff_created` | Handoff task created | Worker result includes handoff |

### Worker Events

| Event Type | Meaning | When It Fires |
|------------|---------|---------------|
| `worker.spawn_started` | Starting to spawn worker | Before OpenClaw call |
| `worker.spawned` | Worker created successfully | After OpenClaw returns |
| `worker.spawn_failed` | Worker spawn failed | OpenClaw error |
| `worker.timeout` | Worker exceeded timeout | Monitor detects timeout |
| `worker.completed` | Worker finished | Worker returns result |
| `worker.killed` | Worker forcibly terminated | Timeout or manual kill |

### Scanner Events

| Event Type | Meaning | When It Fires |
|------------|---------|---------------|
| `scanner.scan_started` | Starting task scan | Start of scan cycle |
| `scanner.scan_completed` | Scan finished | End of scan cycle |
| `scanner.task_found` | Eligible task found | Task matches criteria |

### Router Events

| Event Type | Meaning | When It Fires |
|------------|---------|---------------|
| `router.route_decision` | Agent selected for task | After routing logic |
| `router.delegation` | Delegating to project-manager | No explicit agent |

### Circuit Breaker Events

| Event Type | Meaning | When It Fires |
|------------|---------|---------------|
| `circuit_breaker.opened` | Circuit opened (degraded) | Failure threshold reached |
| `circuit_breaker.closed` | Circuit closed (healthy) | Health check passed |
| `circuit_breaker.half_open` | Testing provider | After cool-down period |

### Escalation Events

| Event Type | Meaning | When It Fires |
|------------|---------|---------------|
| `escalation.triggered` | Escalation created | Task failure triggers escalation |
| `escalation.reflection_started` | Reflection agent started | Reflection tier escalation |
| `escalation.human_notified` | Human escalation created | Automated fixes exhausted |

---

## Reading Structured Logs

### Log Structure

Every JSON log entry follows this schema:

```json
{
  "timestamp": "2026-02-22T15:30:45.123456Z",
  "level": "INFO",
  "logger": "app.orchestrator.worker",
  "message": "Worker spawned successfully",
  
  "trace_id": "a1b2c3d4-...",
  "span_id": "e5f6g7h8-...",
  "task_id": "task-abc123",
  "agent_type": "programmer",
  
  "event_type": "worker.spawned",
  "event_data": {
    "worker_id": "worker_123",
    "model": "claude-sonnet-4",
    "timeout_sec": 1800
  }
}
```

### Common Queries

**Find all events for a task:**
```bash
jq 'select(.task_id == "task-abc123")' logs/server.log
```

**Find all errors:**
```bash
jq 'select(.level == "ERROR")' logs/server.log
```

**Find events by type:**
```bash
jq 'select(.event_type == "worker.spawned")' logs/server.log
```

**Timeline for trace:**
```bash
jq 'select(.trace_id == "abc...") | [.timestamp, .event_type, .message]' logs/server.log | jq -s 'sort_by(.[0])'
```

**Count events by type:**
```bash
jq -r '.event_type' logs/server.log | sort | uniq -c
```

**Extract event data:**
```bash
jq 'select(.event_type == "worker.spawned") | .event_data' logs/server.log
```

---

## Health Dashboards

### System Health Checklist

Check these daily:

- [ ] **Task throughput:** Are tasks being processed?
  ```promql
  rate(lobs_tasks_total[1h]) > 0
  ```

- [ ] **Failure rate:** Acceptable failure rate (<10%)?
  ```promql
  rate(lobs_tasks_total{status="failed"}[1h]) 
    / rate(lobs_tasks_total[1h]) < 0.1
  ```

- [ ] **Active workers:** Workers active and completing?
  ```promql
  lobs_active_workers > 0
  ```

- [ ] **Circuit breakers:** All providers healthy?
  ```promql
  lobs_circuit_breaker_state == 0
  ```

- [ ] **Queue size:** Tasks not backing up?
  ```promql
  lobs_tasks_in_state{work_state="queued"} < 20
  ```

- [ ] **API latency:** Endpoints responding quickly?
  ```promql
  histogram_quantile(0.95, lobs_api_request_duration_seconds_bucket) < 1.0
  ```

### Alert Thresholds

**Critical (page immediately):**
- Circuit breaker open: `lobs_circuit_breaker_state == 1`
- No tasks completing: `rate(lobs_tasks_total[5m]) == 0`
- High error rate: `rate(lobs_api_requests_total{status_code=~"5.."}[5m]) > 5`

**Warning (investigate during business hours):**
- High failure rate: `rate(lobs_tasks_total{status="failed"}[1h]) / rate(lobs_tasks_total[1h]) > 0.1`
- Queue growing: `lobs_tasks_in_state{work_state="queued"} > 50`
- Slow API: `histogram_quantile(0.95, lobs_api_request_duration_seconds_bucket) > 2.0`

---

## Example Scenarios

### Scenario 1: Slow Task Processing

**Observation:**
```promql
histogram_quantile(0.95, lobs_task_duration_seconds_bucket{agent_type="programmer"}) 
# → 2400s (40 minutes, up from 15 minutes last week)
```

**Investigation:**
1. Check if tasks are more complex:
   ```bash
   jq 'select(.event_type == "task.completed") | .event_data.description' logs/server.log | tail -20
   ```

2. Check worker spawn time:
   ```promql
   histogram_quantile(0.95, lobs_worker_spawn_duration_seconds_bucket)
   # → 0.5s (normal)
   ```

3. Check model tier usage:
   ```promql
   lobs_worker_spawns_total{model_tier="micro"} / lobs_worker_spawns_total
   # → 0.8 (80% using micro tier — too weak for current tasks)
   ```

**Conclusion:** Tasks need better models.

**Fix:** Adjust model routing to use higher tiers for complex tasks.

### Scenario 2: High Failure Rate

**Observation:**
```promql
rate(lobs_tasks_total{status="failed"}[1h]) / rate(lobs_tasks_total[1h])
# → 0.25 (25% failure rate)
```

**Investigation:**
1. Check failure types:
   ```bash
   jq 'select(.level == "ERROR") | .event_type' logs/server.log | sort | uniq -c
   # → 15 worker.timeout
   # → 3 worker.spawn_failed
   # → 2 circuit_breaker.opened
   ```

2. Most failures are timeouts → check timeout distribution:
   ```bash
   jq 'select(.event_type == "worker.timeout") | .event_data.timeout_minutes' logs/server.log
   # → All are 30 minute timeout
   ```

**Conclusion:** 30 minute timeout too short for current tasks.

**Fix:** Increase default timeout to 60 minutes.

---

## Related Runbooks

- **[Debugging Failing Agents](02-debugging-failing-agents.md)** — Use metrics to debug specific failures
- **[Handoff Failure Debugging](04-handoff-failure-debugging.md)** — Handoff-specific metrics
