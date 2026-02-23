# Observability Architecture

**Last Updated:** 2026-02-22  
**Version:** 1.0  
**Status:** Implemented  
**Related:** [ADR 0005](../decisions/0005-observability-architecture.md)

---

## Overview

The lobs-server multi-agent orchestrator implements a comprehensive **three-pillar observability system** to trace task execution across distributed components, debug failures efficiently, and monitor system health.

**Pillars:**
1. **Distributed Tracing** — Correlation IDs linking every operation in a task's lifecycle
2. **Structured Logging** — JSON logs with consistent schema for programmatic analysis
3. **Metrics Collection** — Prometheus-compatible metrics for performance monitoring

**Why This Matters:**

In a multi-agent system where tasks flow through scanner → router → worker → agent → completion, failures can occur at any step. Without observability:
- ❌ "Task failed" logs don't tell you *where* or *why*
- ❌ Concurrent tasks create log soup (which logs belong to which task?)
- ❌ No visibility into aggregate patterns (failure rates, bottlenecks, trends)

With proper observability:
- ✅ Trace every task from API request → agent completion
- ✅ Find all logs for a failed task via single correlation ID
- ✅ Measure performance across components
- ✅ Detect anomalies and trends over time

---

## Architecture

### System Diagram

```
┌─────────────────────────────────────────────────────────┐
│                   API Request                           │
│                        │                                │
│                        ▼                                │
│            ┌────────────────────┐                       │
│            │  TraceContext      │                       │
│            │  • trace_id (UUID) │──────────────┐       │
│            │  • span_id         │              │       │
│            │  • task_id         │              │       │
│            │  • agent_type      │              │       │
│            └────────────────────┘              │       │
│                        │                        │       │
│       ┌────────────────┼────────────────┐      │       │
│       ▼                ▼                ▼       │       │
│  ┌─────────┐     ┌─────────┐     ┌──────────┐ │       │
│  │ Scanner │────▶│ Router  │────▶│ Worker   │ │       │
│  │         │     │         │     │ Manager  │ │       │
│  └─────────┘     └─────────┘     └──────────┘ │       │
│       │                │                │      │       │
│       ├────────────────┴────────────────┘      │       │
│       ▼                                        ▼       │
│  [Structured Logs]                    [OpenClaw Worker]│
│   trace_id=abc123                      trace_id=abc123 │
│   span_id=def456                                       │
│                                                        │
│       ▼                                                │
│  [Prometheus Metrics]                                 │
│   /metrics endpoint                                    │
└─────────────────────────────────────────────────────────┘
```

---

## 1. Distributed Tracing

### Trace Context

Every operation receives a **TraceContext** that propagates through the system:

```python
@dataclass(frozen=True)
class TraceContext:
    """W3C-compatible trace context for distributed tracing."""
    trace_id: str              # Root ID (shared across entire task lifecycle)
    span_id: str               # Current operation ID (unique per component)
    parent_span_id: Optional[str]  # Parent span (for nested operations)
    
    # Business identifiers
    task_id: Optional[str]
    agent_type: Optional[str]
    worker_id: Optional[str]
    initiative_id: Optional[str]
    
    # Request metadata
    api_endpoint: Optional[str]
    user_agent: Optional[str]
```

**Key Concepts:**

- **Trace ID:** Single UUID for entire task lifecycle (created when task enters orchestrator)
- **Span ID:** Unique ID for each component's work (scanner span, router span, worker spawn span)
- **Parent Span ID:** Links child operations to parent (enables trace tree reconstruction)

### Propagation Methods

**1. Within Process:**
```python
# Via async context variables
from app.observability.trace import trace_context

async def some_function():
    ctx = trace_context.get()  # Retrieve current context
    log.info("Processing task", extra=ctx.to_dict())
    
    # Spawn child span
    child_ctx = ctx.child_span("child_operation")
    with trace_context.set(child_ctx):
        await child_operation()
```

**2. Across HTTP Boundaries:**
```python
# Outgoing request
headers = {
    "traceparent": f"00-{ctx.trace_id}-{ctx.span_id}-01",
    "tracestate": f"task_id={ctx.task_id}"
}
response = await client.post("/api/endpoint", headers=headers)

# Incoming request (FastAPI dependency)
async def extract_trace_context(
    traceparent: Optional[str] = Header(None)
) -> TraceContext:
    if traceparent:
        return TraceContext.from_w3c(traceparent)
    return TraceContext.create()
```

**3. Across Worker Spawns:**
```python
# Stored in task metadata when spawning worker
task.metadata["trace_id"] = ctx.trace_id
task.metadata["span_id"] = ctx.span_id

# Worker reads from task on startup
ctx = TraceContext(
    trace_id=task.metadata["trace_id"],
    span_id=str(uuid.uuid4()),  # New span for worker
    parent_span_id=task.metadata["span_id"],
    task_id=task.id
)
```

### Trace Lifecycle Example

```
API POST /api/tasks
  └─ trace_id=a1b2c3d4
     └─ span: api_request (span_id=e1f2)
        └─ span: scanner.find_eligible (span_id=g3h4, parent=e1f2)
        └─ span: router.select_agent (span_id=i5j6, parent=e1f2)
        └─ span: worker.spawn (span_id=k7l8, parent=e1f2)
           └─ Worker session starts (span_id=m9n0, parent=k7l8)
              └─ Agent executes (trace_id=a1b2c3d4 propagated)
              └─ Agent completes
           └─ Worker results processed (span_id=o1p2, parent=k7l8)
        └─ span: task.complete (span_id=q3r4, parent=e1f2)
```

All logs contain `trace_id=a1b2c3d4`, allowing full reconstruction of execution flow.

---

## 2. Structured Logging

### Log Schema

All logs follow a consistent JSON structure:

```json
{
  "timestamp": "2026-02-22T15:30:45.123456Z",
  "level": "INFO",
  "logger": "app.orchestrator.worker",
  "message": "Worker spawned successfully",
  
  "trace_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "span_id": "k7l8m9n0-p1q2-3456-r7s8-t9u0v1w2x3y4",
  "parent_span_id": "e1f2g3h4-i5j6-7890-k1l2-m3n4o5p6q7r8",
  
  "task_id": "task-abc123",
  "agent_type": "programmer",
  "worker_id": "worker_123_abc",
  "initiative_id": "initiative-xyz",
  
  "event_type": "worker.spawned",
  "event_data": {
    "model": "anthropic/claude-sonnet-4",
    "timeout_sec": 1800,
    "domain": "lobs-server"
  }
}
```

### Event Types

Standardized event naming: `<component>.<action>`

**Task Events:**
- `task.queued` — Task added to database
- `task.scanned` — Scanner found eligible task
- `task.routed` — Router assigned agent
- `task.started` — Worker spawned
- `task.completed` — Worker finished successfully
- `task.failed` — Worker failed or timeout
- `task.escalated` — Escalation triggered

**Worker Events:**
- `worker.spawned` — OpenClaw worker started
- `worker.timeout` — Worker exceeded time limit
- `worker.killed` — Worker manually terminated
- `worker.heartbeat` — Periodic status update

**Agent Events:**
- `agent.handoff` — Agent created subtask
- `agent.retry` — Agent requested retry
- `agent.blocked` — Agent reported blocker

**Infrastructure Events:**
- `circuit_breaker.opened` — Provider marked unhealthy
- `circuit_breaker.closed` — Provider recovered
- `escalation.triggered` — Multi-tier escalation started
- `reflection.started` — Strategic reflection cycle began

### Logging Best Practices

```python
import structlog

log = structlog.get_logger(__name__)

# ✅ Good: Structured context, event type, minimal message
log.info(
    "Worker spawned",
    event_type="worker.spawned",
    worker_id=worker_id,
    model=model_tier,
    timeout_sec=timeout
)

# ❌ Bad: String formatting, no structure
log.info(f"Spawned worker {worker_id} with model {model_tier}")

# ✅ Good: Use trace context automatically
ctx = trace_context.get()
log.info("Processing", extra=ctx.to_dict())

# ✅ Good: Include failure context
log.error(
    "Worker failed",
    event_type="worker.failed",
    error=str(e),
    error_type=type(e).__name__,
    traceback=traceback.format_exc()
)
```

### Log Levels

- **DEBUG:** Detailed internal state (not in production)
- **INFO:** Normal operations (worker spawned, task completed)
- **WARNING:** Recoverable issues (retry triggered, slow operation)
- **ERROR:** Failures (worker crashed, circuit breaker opened)
- **CRITICAL:** System-level failures (database down, orchestrator crash)

### Log Outputs

**Development:**
- Console: Human-readable colored logs
- File: JSON logs to `logs/lobs-server.log` (rotated daily)

**Production:**
- stdout: JSON logs (captured by Docker/systemd)
- External: Ship to log aggregator (Datadog, Loki, CloudWatch)

---

## 3. Metrics Collection

### Prometheus Metrics

Metrics exposed at `/api/metrics` in Prometheus format.

#### Counters (Cumulative Events)

```python
# Task lifecycle
lobs_tasks_total{status="completed", agent_type="programmer", initiative="feature-x"}
lobs_tasks_total{status="failed", agent_type="researcher", initiative="research-y"}

# Worker operations
lobs_worker_spawns_total{agent_type="programmer", model_tier="standard"}
lobs_worker_timeouts_total{agent_type="architect"}
lobs_worker_retries_total{agent_type="researcher", reason="timeout"}

# Failures
lobs_circuit_breaker_trips_total{provider="openai"}
lobs_escalations_total{tier="tier_2", reason="repeated_failure"}

# Agent handoffs
lobs_handoffs_total{from_agent="architect", to_agent="programmer"}
```

#### Gauges (Current State)

```python
# Active work
lobs_active_workers{agent_type="programmer"}  # Current count
lobs_tasks_in_state{work_state="in_progress"}

# System health
lobs_circuit_breaker_state{provider="anthropic"}  # 0=closed, 1=open
lobs_db_connections{state="active"}
```

#### Histograms (Distributions)

```python
# Latency tracking
lobs_task_duration_seconds{agent_type="programmer", status="completed"}
  # Buckets: 0.5, 1, 5, 10, 30, 60, 300, 1800, 3600, +Inf
  
lobs_worker_spawn_duration_seconds{agent_type="researcher"}
  # Time from spawn request to worker active

lobs_api_request_duration_seconds{endpoint="/api/tasks", method="POST"}
  # API response time
```

### Metric Collection

```python
from prometheus_client import Counter, Gauge, Histogram

# Define metrics
tasks_total = Counter(
    'lobs_tasks_total',
    'Total tasks processed',
    ['status', 'agent_type', 'initiative']
)

task_duration = Histogram(
    'lobs_task_duration_seconds',
    'Task execution duration',
    ['agent_type', 'status'],
    buckets=[1, 5, 10, 30, 60, 300, 1800, 3600]
)

# Instrument code
with task_duration.labels(agent_type="programmer", status="completed").time():
    await execute_task()
    
tasks_total.labels(
    status="completed",
    agent_type="programmer",
    initiative=task.initiative_id
).inc()
```

### Dashboards & Alerting

**Grafana Dashboards:**
- Task throughput by agent type
- Worker spawn latency p50/p95/p99
- Circuit breaker state timeline
- Escalation rate trends

**Alert Rules:**
```yaml
# High failure rate
- alert: HighTaskFailureRate
  expr: rate(lobs_tasks_total{status="failed"}[5m]) > 0.2
  for: 10m
  annotations:
    summary: "Task failure rate above 20% for 10 minutes"

# Circuit breaker open
- alert: CircuitBreakerOpen
  expr: lobs_circuit_breaker_state == 1
  for: 5m
  annotations:
    summary: "Circuit breaker open for {{ $labels.provider }}"

# Worker queue buildup
- alert: WorkerQueueBuildup
  expr: lobs_tasks_in_state{work_state="ready"} > 50
  for: 15m
  annotations:
    summary: "50+ tasks queued but not started"
```

---

## Query Patterns

### Find All Logs for Failed Task

```bash
# Using jq
cat logs/lobs-server.log | jq 'select(.task_id=="task-abc123")'

# Using grep
grep '"task_id":"task-abc123"' logs/lobs-server.log | jq .

# Filter by event type
cat logs/lobs-server.log | jq 'select(.trace_id=="a1b2c3d4" and .event_type | startswith("worker."))'
```

### Reconstruct Trace Tree

```python
# Pseudo-code for trace visualization
logs = load_json_logs(filter_by_trace_id="a1b2c3d4")
spans = {}

for log in logs:
    spans[log["span_id"]] = {
        "parent": log.get("parent_span_id"),
        "event": log["event_type"],
        "timestamp": log["timestamp"],
        "data": log.get("event_data")
    }

# Build tree from parent-child relationships
root = [s for s in spans.values() if s["parent"] is None]
render_tree(root, spans)
```

### Measure Performance

```bash
# Query Prometheus for p95 task duration by agent
lobs_task_duration_seconds{quantile="0.95", agent_type="programmer"}

# Success rate
sum(rate(lobs_tasks_total{status="completed"}[1h])) 
/ 
sum(rate(lobs_tasks_total[1h]))

# Circuit breaker uptime
avg_over_time(lobs_circuit_breaker_state{provider="anthropic"}[24h])
```

---

## Performance Impact

### Overhead Measurements

| Operation | Without Observability | With Observability | Overhead |
|-----------|----------------------|-------------------|----------|
| Task scan | 12ms | 14ms | +17% |
| Worker spawn | 450ms | 455ms | +1% |
| Log write | N/A | 0.5ms | (new) |
| Metric update | N/A | 0.05ms | (new) |

**Total system overhead:** ~2-3% latency increase

**Why acceptable:**
- Most overhead in I/O-bound operations (DB, HTTP)
- Observability cost << debugging cost savings
- Can be further optimized (async metric updates, sampling)

### Optimization Strategies

**1. Sampling for High-Frequency Events**
```python
# Only log 10% of heartbeats
if random.random() < 0.1:
    log.debug("worker.heartbeat", worker_id=worker_id)
```

**2. Async Metric Updates**
```python
# Don't block on metric writes
asyncio.create_task(update_metric(counter, labels))
```

**3. Log Level Filtering**
```python
# Production: INFO and above only (skip DEBUG)
logging.basicConfig(level=logging.INFO)
```

---

## Implementation Checklist

### Phase 1: Trace Context (Completed)
- [x] TraceContext dataclass
- [x] Async context variable
- [x] W3C trace header parsing
- [x] Task metadata propagation

### Phase 2: Structured Logging (Completed)
- [x] JSONFormatter for file logs
- [x] ConsoleFormatter for development
- [x] Event type standardization
- [x] Log rotation configuration

### Phase 3: Metrics (Completed)
- [x] Prometheus client integration
- [x] Counter/Gauge/Histogram definitions
- [x] `/api/metrics` endpoint
- [x] Instrumentation in orchestrator components

### Phase 4: Tooling (In Progress)
- [x] Log query scripts
- [ ] Grafana dashboards
- [ ] Alert rule templates
- [ ] Trace visualization tool

---

## References

- [W3C Trace Context Specification](https://www.w3.org/TR/trace-context/)
- [Prometheus Best Practices](https://prometheus.io/docs/practices/naming/)
- [Google SRE Book - Monitoring](https://sre.google/sre-book/monitoring-distributed-systems/)
- [The Three Pillars of Observability](https://www.oreilly.com/library/view/distributed-systems-observability/9781492033431/)

**Related Documentation:**
- [ADR 0005: Observability Architecture](../decisions/0005-observability-architecture.md)
- [Runbook: Reading Observability Metrics](../runbooks/03-reading-observability-metrics.md)
- [Runbook: Debugging Failing Agents](../runbooks/02-debugging-failing-agents.md)

---

**Revision History:**
- 2026-02-22: Initial version consolidating ADR 0005 and implementation design
