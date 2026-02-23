# ADR 0005: Observability Architecture for Multi-Agent System

**Status:** Proposed  
**Date:** 2026-02-22  
**Author:** Architect Agent

---

## Context

The multi-agent orchestrator system has grown complex:
- Tasks spawn OpenClaw workers which may spawn sub-agents
- Multiple concurrent workers across different domains (server, mobile, app)
- Async handoffs between scanner → router → worker → agent
- Circuit breakers, escalation tiers, retry logic, provider health tracking

**Current state:**
- ❌ No correlation IDs — impossible to follow a task across components
- ❌ Ad-hoc logging — string prefixes like `[ENGINE]`, `[WORKER]` with inconsistent structure
- ❌ No distributed tracing — can't see the full lifecycle of a task
- ❌ No metrics collection — no Prometheus/StatsD, just database queries
- ❌ Opaque debugging — when agents fail, we have scattered logs with no connection

**The problem:** System complexity demands systematic observability, not ad-hoc logging.

---

## Decision

**We will implement a three-pillar observability system:**

1. **Structured Logging** — JSON logs with consistent schema and correlation IDs
2. **Distributed Tracing** — Trace context propagation across async handoffs
3. **Metrics Collection** — Component-level metrics via Prometheus client library

**Key architectural choices:**

### 1. Trace Context as First-Class Citizen

Every request/task gets a **trace context** that flows through all operations:

```python
@dataclass
class TraceContext:
    trace_id: str          # UUID for entire task lifecycle
    span_id: str           # Current component/operation
    parent_span_id: str    # Parent operation (for nested calls)
    task_id: str           # Business identifier
    agent_type: str        # programmer/researcher/writer/etc
    worker_id: str         # OpenClaw worker identifier
```

Context propagates:
- **Within process:** Via function parameters and async context vars
- **Across workers:** Via task metadata and OpenClaw session labels
- **HTTP calls:** Via W3C Trace Context headers (traceparent/tracestate)

### 2. Structured Logging Schema

All log events follow a consistent schema:

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
    "worker_id": "worker_123_abc",
    "model": "anthropic/claude-sonnet-4",
    "timeout_sec": 1800
  }
}
```

**Event types** follow component.action naming:
- `task.queued`, `task.started`, `task.completed`, `task.failed`
- `worker.spawned`, `worker.timeout`, `worker.killed`
- `agent.handoff`, `agent.retry`, `agent.escalated`
- `circuit_breaker.opened`, `circuit_breaker.closed`

### 3. Metrics Per Component

Prometheus metrics for each architectural component:

**Counters:**
- `lobs_tasks_total{status, agent_type, initiative}`
- `lobs_worker_spawns_total{agent_type, model_tier}`
- `lobs_circuit_breaker_trips_total{provider}`
- `lobs_escalations_total{tier, reason}`

**Gauges:**
- `lobs_active_workers{agent_type}`
- `lobs_tasks_in_state{work_state}`
- `lobs_circuit_breaker_state{provider}` (0=closed, 1=open)

**Histograms:**
- `lobs_task_duration_seconds{agent_type, status}`
- `lobs_worker_spawn_duration_seconds{agent_type}`
- `lobs_api_request_duration_seconds{endpoint, method}`

---

## Rationale

### Why Not Just Better Logging?

Logs are necessary but insufficient:
- **Logs:** Tell you *what happened* on one component
- **Traces:** Show you *the path* through the entire system
- **Metrics:** Give you *aggregate view* for patterns and trends

All three are needed for complex distributed systems.

### Why Not OpenTelemetry?

**Considered:** Full OpenTelemetry (OTel) stack with OTLP exporters

**Why not:**
- ⚠️ **Heavy dependency** — OTel SDK is large, adds startup time
- ⚠️ **Requires infrastructure** — Needs OTLP collector/endpoint (Jaeger, Honeycomb, etc.)
- ⚠️ **Overkill for current scale** — This is a single-process FastAPI app, not Kubernetes

**Chosen:** Lightweight custom implementation with OTel-compatible trace IDs

**Escape hatch:** If we grow to multi-service architecture, our trace context is W3C compatible and can migrate to OTel.

### Why Prometheus Over StatsD/DataDog?

**Prometheus pros:**
- ✅ Pull-based (server scrapes metrics) — no agent setup
- ✅ Built-in Python client — minimal dependencies
- ✅ Local-first — works without external service
- ✅ Compatible with Grafana for visualization

**Tradeoff:** Requires Prometheus server for production monitoring, but metrics are exposed via `/metrics` endpoint and work standalone.

### Why Async Context Vars?

Python's `contextvars` provides context propagation across async boundaries:
- ✅ No explicit parameter threading through every function
- ✅ Works with FastAPI dependency injection
- ✅ Automatically cloned on asyncio task creation

**Alternative considered:** Thread-locals — doesn't work with asyncio.

---

## Consequences

### Positive

✅ **Full task visibility** — Trace every task from API request → worker spawn → completion  
✅ **Debuggable failures** — Correlation IDs let you find all logs for a failed task  
✅ **Performance insights** — Metrics show slow components, bottlenecks, failure patterns  
✅ **Production-ready** — Structured logs ship to log aggregators (Datadog, Loki, CloudWatch)  
✅ **Incremental adoption** — Can roll out component by component  
✅ **Standard practices** — W3C Trace Context, Prometheus metrics are industry standard

### Negative

⚠️ **Increased code verbosity** — Every function needs trace context parameter or contextvar access  
⚠️ **Performance overhead** — JSON serialization + metric updates add ~1-5ms per operation  
⚠️ **Storage costs** — Structured logs are larger than simple text logs  
⚠️ **Learning curve** — Team needs to understand trace context propagation

### Mitigation Strategies

- **Verbosity:** Helper decorators and context managers reduce boilerplate
- **Performance:** Async metric updates, sampling for high-frequency events
- **Storage:** Log rotation + retention policies, separate debug vs production levels
- **Learning:** Clear documentation + examples, helper utilities

---

## Implementation Notes

### Rollout Phases

**Phase 1: Trace Context Foundation (Critical Path)**
1. Create `TraceContext` dataclass
2. Add `trace_context` contextvar
3. Update `JSONFormatter` to include trace fields
4. Add trace context to Task model

**Phase 2: Component Instrumentation (By Priority)**
1. Scanner (task discovery)
2. Router (agent selection)
3. WorkerManager (worker lifecycle)
4. API endpoints (incoming requests)
5. Monitor, circuit breaker, escalation

**Phase 3: Metrics Collection**
1. Add Prometheus client dependency
2. Create metrics registry
3. Instrument components with counters/gauges/histograms
4. Add `/metrics` endpoint

**Phase 4: Tooling & Visualization**
1. Log query helpers (find all logs for trace_id)
2. Grafana dashboards
3. Alert rules

### Backward Compatibility

- ✅ Existing console logs unchanged (ConsoleFormatter stays simple)
- ✅ JSON logs additive (new fields don't break parsers)
- ✅ `/metrics` endpoint is new (no breaking changes)

### Testing Strategy

- **Unit tests:** Trace context propagation across function calls
- **Integration tests:** Trace context flows through worker spawn
- **Load tests:** Measure overhead (target: <5ms p99)
- **Log validation:** Ensure all event_types have consistent schema

---

## Alternatives Considered

### Alt 1: Just Add More Console Logs

**Why not:** Doesn't solve correlation problem. More logs without structure = harder debugging.

### Alt 2: Full OpenTelemetry Stack

**Why not:** Too heavy for current scale. Can migrate later if needed.

### Alt 3: Vendor Solution (DataDog APM, New Relic)

**Why not:**
- 💰 Expensive for side project
- 🔒 Vendor lock-in
- ⚡ Requires agent installation

**When to reconsider:** If this becomes a production SaaS with revenue.

### Alt 4: Logging Only (No Metrics)

**Why not:** Logs are event-based, metrics are aggregates. Both needed:
- Logs → "Why did this task fail?"
- Metrics → "What's our task success rate over 7 days?"

---

## References

- [W3C Trace Context Specification](https://www.w3.org/TR/trace-context/)
- [Prometheus Python Client](https://github.com/prometheus/client_python)
- [Python contextvars](https://docs.python.org/3/library/contextvars.html)
- [Google SRE Book - Monitoring](https://sre.google/sre-book/monitoring-distributed-systems/)
- [Distributed Tracing Best Practices](https://peter.bourgon.org/blog/2017/02/21/metrics-tracing-and-logging.html)

---

## Related Decisions

- **ADR 0001:** Embedded Orchestrator — why observability is critical for async task system
- **ADR 0003:** Project Manager Delegation — handoffs need trace context
- **ADR 0004:** Five-Tier Model Routing — metrics needed to track model tier usage

---

## Status Updates

- **2026-02-22:** Initial design (Proposed)
