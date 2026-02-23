# Observability Implementation Design

**Version:** 1.0  
**Date:** 2026-02-22  
**Status:** Ready for Implementation  
**Related ADR:** [ADR 0005: Observability Architecture](../decisions/0005-observability-architecture.md)

---

## Overview

This document provides implementation-level details for the observability system described in ADR 0005.

**Goals:**
1. Trace every task from API request → worker spawn → agent execution → completion
2. Structured logs with correlation IDs for debuggable failures
3. Metrics per component for performance insights and alerting

**Non-Goals:**
- Full-stack APM solution (DataDog/New Relic equivalent)
- Real-time dashboards (can add later with Grafana)
- Log aggregation infrastructure (external: Loki, CloudWatch, etc.)

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      API Request                            │
│                          │                                  │
│                          ▼                                  │
│              ┌────────────────────┐                         │
│              │  TraceContext      │                         │
│              │  • trace_id (UUID) │                         │
│              │  • span_id         │                         │
│              │  • task_id         │                         │
│              └────────────────────┘                         │
│                          │                                  │
│         ┌────────────────┼────────────────┐                │
│         ▼                ▼                ▼                 │
│    ┌─────────┐     ┌─────────┐     ┌──────────┐           │
│    │ Scanner │     │ Router  │     │ Worker   │           │
│    │         │────▶│         │────▶│ Manager  │           │
│    └─────────┘     └─────────┘     └──────────┘           │
│         │                │                │                 │
│         ▼                ▼                ▼                 │
│    [Structured Logs with trace_id]                         │
│    [Prometheus Metrics]                                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Component 1: Trace Context

### TraceContext Dataclass

**File:** `app/observability/trace.py`

```python
from dataclasses import dataclass, field
from typing import Optional
import uuid
import contextvars

@dataclass(frozen=True)
class TraceContext:
    """Distributed trace context for request/task tracking.
    
    Follows W3C Trace Context spec (compatible with OpenTelemetry).
    """
    trace_id: str          # Root trace ID (UUID, shared across entire task)
    span_id: str           # Current span ID (UUID, unique per component)
    parent_span_id: Optional[str] = None  # Parent span (for nested operations)
    
    # Business identifiers
    task_id: Optional[str] = None
    agent_type: Optional[str] = None
    worker_id: Optional[str] = None
    initiative_id: Optional[str] = None
    
    # Request metadata
    api_endpoint: Optional[str] = None
    user_agent: Optional[str] = None
    
    @classmethod
    def create(
        cls,
        task_id: Optional[str] = None,
        agent_type: Optional[str] = None,
        parent_trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        **kwargs
    ) -> "TraceContext":
        """Create new trace context."""
        return cls(
            trace_id=parent_trace_id or str(uuid.uuid4()),
            span_id=str(uuid.uuid4()),
            parent_span_id=parent_span_id,
            task_id=task_id,
            agent_type=agent_type,
            **kwargs
        )
    
    def new_span(self, **updates) -> "TraceContext":
        """Create child span with same trace_id."""
        return TraceContext(
            trace_id=self.trace_id,
            span_id=str(uuid.uuid4()),
            parent_span_id=self.span_id,
            task_id=self.task_id or updates.get("task_id"),
            agent_type=updates.get("agent_type") or self.agent_type,
            worker_id=updates.get("worker_id") or self.worker_id,
            initiative_id=updates.get("initiative_id") or self.initiative_id,
            api_endpoint=updates.get("api_endpoint") or self.api_endpoint,
            user_agent=updates.get("user_agent") or self.user_agent,
        )
    
    def to_dict(self) -> dict:
        """Serialize to dict for logging."""
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "task_id": self.task_id,
            "agent_type": self.agent_type,
            "worker_id": self.worker_id,
            "initiative_id": self.initiative_id,
            "api_endpoint": self.api_endpoint,
        }
    
    def to_w3c_traceparent(self) -> str:
        """W3C Trace Context header format.
        
        Format: version-trace_id-parent_id-flags
        Example: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
        """
        # Simplified: no sampling flags (always trace)
        trace_id_hex = self.trace_id.replace("-", "")[:32].ljust(32, "0")
        span_id_hex = self.span_id.replace("-", "")[:16].ljust(16, "0")
        return f"00-{trace_id_hex}-{span_id_hex}-01"


# Context variable for async propagation
_trace_context_var: contextvars.ContextVar[Optional[TraceContext]] = contextvars.ContextVar(
    "trace_context", default=None
)


def get_trace_context() -> Optional[TraceContext]:
    """Get current trace context from async context."""
    return _trace_context_var.get()


def set_trace_context(ctx: TraceContext) -> None:
    """Set trace context for current async context."""
    _trace_context_var.set(ctx)


def with_trace_context(ctx: Optional[TraceContext] = None):
    """Decorator to run function with trace context."""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # If no context provided, create new one
            context = ctx or TraceContext.create()
            token = _trace_context_var.set(context)
            try:
                return await func(*args, **kwargs)
            finally:
                _trace_context_var.reset(token)
        return wrapper
    return decorator
```

### Integration Points

**1. API Endpoints (FastAPI Middleware)**

```python
# app/middleware.py

class TraceContextMiddleware:
    """Inject trace context into every API request."""
    
    async def __call__(self, request: Request, call_next):
        # Extract from headers if present (for distributed tracing)
        traceparent = request.headers.get("traceparent")
        if traceparent:
            # Parse W3C Trace Context header
            ctx = parse_w3c_traceparent(traceparent)
        else:
            # Create new trace
            ctx = TraceContext.create(
                api_endpoint=f"{request.method} {request.url.path}",
                user_agent=request.headers.get("user-agent"),
            )
        
        set_trace_context(ctx)
        
        # Add to response headers
        response = await call_next(request)
        response.headers["X-Trace-Id"] = ctx.trace_id
        return response
```

**2. Task Model (Database)**

```python
# app/models.py (add to Task model)

class Task(Base):
    # ... existing fields ...
    
    # Observability fields
    trace_id = Column(String)  # Root trace ID for this task
    parent_trace_id = Column(String)  # If spawned by another task
```

**3. Worker Spawn (OpenClaw Integration)**

```python
# app/orchestrator/worker.py

async def spawn_worker(self, task: dict, agent_type: str):
    # Get or create trace context
    ctx = get_trace_context()
    if not ctx:
        ctx = TraceContext.create(task_id=task["id"], agent_type=agent_type)
    else:
        # Create child span for worker
        ctx = ctx.new_span(agent_type=agent_type, worker_id=worker_id)
    
    # Pass trace context to OpenClaw worker via environment
    env_vars = {
        "LOBS_TRACE_ID": ctx.trace_id,
        "LOBS_SPAN_ID": ctx.span_id,
        "LOBS_TASK_ID": task["id"],
    }
    
    # Spawn with trace context
    result = await gateway_client.spawn_session(
        agent=agent_type,
        label=f"task-{task_id_short}",
        env=env_vars,  # ← Trace context propagates to worker
    )
```

---

## Component 2: Structured Logging

### Enhanced JSONFormatter

**File:** `app/logging_config.py` (update existing)

```python
class ObservableJSONFormatter(logging.Formatter):
    """JSON formatter with trace context integration."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON with trace context."""
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Inject trace context if available
        ctx = get_trace_context()
        if ctx:
            log_data.update(ctx.to_dict())
        
        # Extract event_type and event_data from extra
        if hasattr(record, "event_type"):
            log_data["event_type"] = record.event_type
        if hasattr(record, "event_data"):
            log_data["event_data"] = record.event_data
        
        # Add exception info
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_data)
```

### Logging Helpers

**File:** `app/observability/logging.py`

```python
import logging
from typing import Any, Optional

def log_event(
    logger: logging.Logger,
    level: int,
    event_type: str,
    message: str,
    event_data: Optional[dict[str, Any]] = None,
    **kwargs
):
    """Log structured event with consistent schema.
    
    Args:
        logger: Python logger instance
        level: logging.INFO, logging.ERROR, etc.
        event_type: Dot-separated event type (e.g., 'worker.spawned')
        message: Human-readable message
        event_data: Event-specific data dict
        **kwargs: Additional log fields
    
    Example:
        log_event(
            logger,
            logging.INFO,
            "task.started",
            "Task execution started",
            event_data={"agent_type": "programmer", "timeout": 1800}
        )
    """
    extra = {
        "event_type": event_type,
        "event_data": event_data or {},
        **kwargs
    }
    logger.log(level, message, extra=extra)


# Convenience functions
def log_task_event(logger, level, action: str, task_id: str, **data):
    """Log task lifecycle event."""
    log_event(
        logger, level,
        f"task.{action}",
        f"Task {task_id[:8]}: {action}",
        event_data={"task_id": task_id, **data}
    )

def log_worker_event(logger, level, action: str, worker_id: str, **data):
    """Log worker lifecycle event."""
    log_event(
        logger, level,
        f"worker.{action}",
        f"Worker {worker_id}: {action}",
        event_data={"worker_id": worker_id, **data}
    )

def log_agent_event(logger, level, action: str, agent_type: str, **data):
    """Log agent event."""
    log_event(
        logger, level,
        f"agent.{action}",
        f"Agent {agent_type}: {action}",
        event_data={"agent_type": agent_type, **data}
    )
```

### Migration Strategy

**Existing logs:**
```python
logger.info(f"[WORKER] Spawned worker {worker_id} for task {task_id_short}")
```

**New structured logs:**
```python
log_worker_event(
    logger, logging.INFO, "spawned",
    worker_id=worker_id,
    task_id=task_id,
    agent_type=agent_type,
    model=model,
    timeout_sec=timeout,
)
```

**Backward compatibility:** Keep console logs simple, structured logging only in JSON files.

---

## Component 3: Metrics Collection

### Metrics Registry

**File:** `app/observability/metrics.py`

```python
from prometheus_client import Counter, Gauge, Histogram, Info, REGISTRY
from typing import Optional

# ============================================================================
# Task Metrics
# ============================================================================

tasks_total = Counter(
    "lobs_tasks_total",
    "Total tasks processed",
    labelnames=["status", "agent_type", "initiative"],
)

tasks_in_state = Gauge(
    "lobs_tasks_in_state",
    "Current tasks by work state",
    labelnames=["work_state"],
)

task_duration_seconds = Histogram(
    "lobs_task_duration_seconds",
    "Task execution duration",
    labelnames=["agent_type", "status"],
    buckets=[10, 30, 60, 120, 300, 600, 1800, 3600, 7200],  # 10s to 2h
)

# ============================================================================
# Worker Metrics
# ============================================================================

worker_spawns_total = Counter(
    "lobs_worker_spawns_total",
    "Total worker spawns",
    labelnames=["agent_type", "model_tier"],
)

active_workers = Gauge(
    "lobs_active_workers",
    "Currently active workers",
    labelnames=["agent_type"],
)

worker_spawn_duration_seconds = Histogram(
    "lobs_worker_spawn_duration_seconds",
    "Time to spawn worker",
    labelnames=["agent_type"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0],  # 100ms to 10s
)

worker_failures_total = Counter(
    "lobs_worker_failures_total",
    "Worker failures by type",
    labelnames=["agent_type", "failure_type"],
)

# ============================================================================
# Circuit Breaker Metrics
# ============================================================================

circuit_breaker_trips_total = Counter(
    "lobs_circuit_breaker_trips_total",
    "Circuit breaker trips",
    labelnames=["provider", "error_type"],
)

circuit_breaker_state = Gauge(
    "lobs_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half_open)",
    labelnames=["provider"],
)

# ============================================================================
# Escalation Metrics
# ============================================================================

escalations_total = Counter(
    "lobs_escalations_total",
    "Task escalations",
    labelnames=["tier", "reason"],
)

# ============================================================================
# API Metrics
# ============================================================================

api_requests_total = Counter(
    "lobs_api_requests_total",
    "Total API requests",
    labelnames=["method", "endpoint", "status_code"],
)

api_request_duration_seconds = Histogram(
    "lobs_api_request_duration_seconds",
    "API request duration",
    labelnames=["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0],  # 10ms to 5s
)

# ============================================================================
# Model Usage Metrics
# ============================================================================

model_usage_total = Counter(
    "lobs_model_usage_total",
    "Model invocations",
    labelnames=["provider", "model", "tier"],
)

model_token_usage = Counter(
    "lobs_model_tokens_total",
    "Token usage by model",
    labelnames=["provider", "model", "token_type"],  # input/output
)


# ============================================================================
# Helper Functions
# ============================================================================

def observe_task_started(agent_type: str, initiative: Optional[str] = None):
    """Record task start."""
    active_workers.labels(agent_type=agent_type).inc()

def observe_task_completed(
    agent_type: str,
    status: str,
    duration_seconds: float,
    initiative: Optional[str] = None
):
    """Record task completion."""
    tasks_total.labels(
        status=status,
        agent_type=agent_type,
        initiative=initiative or "none"
    ).inc()
    task_duration_seconds.labels(
        agent_type=agent_type,
        status=status
    ).observe(duration_seconds)
    active_workers.labels(agent_type=agent_type).dec()

def observe_worker_spawned(agent_type: str, model_tier: str, spawn_duration: float):
    """Record worker spawn."""
    worker_spawns_total.labels(agent_type=agent_type, model_tier=model_tier).inc()
    worker_spawn_duration_seconds.labels(agent_type=agent_type).observe(spawn_duration)

def observe_circuit_breaker_opened(provider: str, error_type: str):
    """Record circuit breaker trip."""
    circuit_breaker_trips_total.labels(provider=provider, error_type=error_type).inc()
    circuit_breaker_state.labels(provider=provider).set(1)

def observe_circuit_breaker_closed(provider: str):
    """Record circuit breaker close."""
    circuit_breaker_state.labels(provider=provider).set(0)
```

### Metrics Endpoint

**File:** `app/routers/metrics.py`

```python
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from prometheus_client import generate_latest, REGISTRY

router = APIRouter()

@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
async def metrics():
    """Prometheus metrics endpoint.
    
    Returns metrics in Prometheus text format.
    Configure Prometheus to scrape this endpoint:
    
    scrape_configs:
      - job_name: 'lobs-server'
        static_configs:
          - targets: ['localhost:8000']
    """
    return generate_latest(REGISTRY)
```

---

## Component 4: Integration Examples

### Scanner Integration

**File:** `app/orchestrator/scanner.py`

```python
from app.observability.trace import get_trace_context, set_trace_context, TraceContext
from app.observability.logging import log_event
from app.observability.metrics import observe_task_started

async def scan_for_eligible_tasks(self):
    """Find tasks ready for execution."""
    # Create span for scan operation
    ctx = get_trace_context() or TraceContext.create()
    ctx = ctx.new_span()
    set_trace_context(ctx)
    
    log_event(
        logger, logging.INFO,
        "scanner.scan_started",
        "Scanning for eligible tasks"
    )
    
    # ... existing scan logic ...
    
    log_event(
        logger, logging.INFO,
        "scanner.scan_completed",
        f"Found {len(eligible)} eligible tasks",
        event_data={"count": len(eligible), "task_ids": [t["id"] for t in eligible]}
    )
    
    return eligible
```

### WorkerManager Integration

**File:** `app/orchestrator/worker.py`

```python
from app.observability.metrics import (
    observe_worker_spawned,
    observe_task_started,
    observe_task_completed,
    worker_failures_total,
)

async def spawn_worker(self, task: dict, agent_type: str):
    spawn_start = time.time()
    
    # Create trace context for worker
    ctx = get_trace_context() or TraceContext.create(task_id=task["id"])
    worker_ctx = ctx.new_span(agent_type=agent_type, worker_id=worker_id)
    
    log_worker_event(
        logger, logging.INFO, "spawn_started",
        worker_id=worker_id,
        task_id=task["id"],
        agent_type=agent_type,
        model=model,
    )
    
    try:
        # Spawn worker with trace context
        result = await self._spawn_openclaw_session(...)
        
        spawn_duration = time.time() - spawn_start
        observe_worker_spawned(agent_type, model_tier, spawn_duration)
        observe_task_started(agent_type, task.get("initiative_id"))
        
        log_worker_event(
            logger, logging.INFO, "spawned",
            worker_id=worker_id,
            spawn_duration_ms=int(spawn_duration * 1000),
        )
        
        return result
        
    except Exception as e:
        worker_failures_total.labels(
            agent_type=agent_type,
            failure_type=classify_error(e)
        ).inc()
        
        log_worker_event(
            logger, logging.ERROR, "spawn_failed",
            worker_id=worker_id,
            error=str(e),
        )
        raise
```

---

## Testing Strategy

### Unit Tests

**Test trace context propagation:**
```python
# tests/test_trace_context.py

async def test_trace_context_async_propagation():
    """Trace context should propagate across async calls."""
    ctx = TraceContext.create(task_id="test-123")
    set_trace_context(ctx)
    
    async def nested():
        inner_ctx = get_trace_context()
        assert inner_ctx.trace_id == ctx.trace_id
    
    await nested()
    assert get_trace_context() == ctx
```

**Test log formatting:**
```python
# tests/test_logging.py

def test_observable_json_formatter():
    """JSON formatter should include trace context."""
    ctx = TraceContext.create(task_id="test-456")
    set_trace_context(ctx)
    
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="test message",
        args=(),
        exc_info=None,
    )
    
    formatter = ObservableJSONFormatter()
    output = json.loads(formatter.format(record))
    
    assert output["trace_id"] == ctx.trace_id
    assert output["task_id"] == "test-456"
```

### Integration Tests

**Test worker spawn with trace context:**
```python
# tests/test_worker_observability.py

async def test_worker_spawn_propagates_trace_context():
    """Worker spawn should pass trace context to OpenClaw."""
    ctx = TraceContext.create(task_id="task-789")
    set_trace_context(ctx)
    
    worker = WorkerManager(db)
    task = {"id": "task-789", "title": "Test"}
    
    # Mock OpenClaw spawn
    with patch("app.orchestrator.worker.gateway_spawn") as mock_spawn:
        await worker.spawn_worker(task, "programmer")
        
        # Verify trace context passed to worker
        call_args = mock_spawn.call_args
        env = call_args.kwargs["env"]
        assert env["LOBS_TRACE_ID"] == ctx.trace_id
        assert env["LOBS_TASK_ID"] == "task-789"
```

### Performance Tests

**Measure overhead:**
```python
# tests/test_observability_overhead.py

async def test_logging_overhead():
    """Structured logging should add <5ms overhead."""
    # Baseline: simple log
    start = time.perf_counter()
    for _ in range(1000):
        logger.info("simple message")
    baseline = time.perf_counter() - start
    
    # With trace context
    ctx = TraceContext.create()
    set_trace_context(ctx)
    start = time.perf_counter()
    for _ in range(1000):
        log_event(logger, logging.INFO, "test.event", "test")
    with_context = time.perf_counter() - start
    
    overhead = (with_context - baseline) / 1000 * 1000  # ms per log
    assert overhead < 5.0, f"Overhead {overhead:.2f}ms exceeds 5ms target"
```

---

## Rollout Plan

### Phase 1: Foundation (Week 1) — CRITICAL PATH

**Goal:** Trace context infrastructure in place, no component integration yet

1. **Create observability package** (`app/observability/`)
   - `trace.py` — TraceContext + contextvars
   - `logging.py` — Structured logging helpers
   - `metrics.py` — Prometheus metrics registry
   
2. **Update logging config**
   - Add `ObservableJSONFormatter`
   - Keep console logging unchanged
   
3. **Add trace middleware**
   - Create `TraceContextMiddleware`
   - Register in `app/main.py`
   
4. **Add Task.trace_id field**
   - Database migration
   - Update Task model

**Acceptance:**
- ✅ Trace context can be created, propagated across async calls
- ✅ API requests get trace_id in response headers
- ✅ JSON logs include trace_id when context is set
- ✅ Tests pass with <5ms overhead

### Phase 2: Core Orchestrator (Week 2)

**Goal:** Scanner, Router, WorkerManager fully instrumented

5. **Instrument Scanner**
   - Add trace context creation for scan cycles
   - Log `scanner.scan_started`, `scanner.scan_completed`
   - Metric: scanner scan duration

6. **Instrument Router**
   - Propagate trace context through routing
   - Log `router.route_decision` with agent_type
   - Metric: routing decisions by agent_type

7. **Instrument WorkerManager**
   - Pass trace context to OpenClaw workers
   - Log `worker.spawned`, `worker.completed`, `worker.failed`
   - Metrics: worker spawns, active workers, spawn duration

**Acceptance:**
- ✅ Can trace task from scan → route → worker spawn via trace_id
- ✅ Worker failures include trace_id for debugging
- ✅ Metrics show active workers, spawn rates

### Phase 3: Monitoring & Failure Handling (Week 3)

**Goal:** Monitor, circuit breaker, escalation instrumented

8. **Instrument Monitor**
   - Log `monitor.stuck_task_detected`, `monitor.timeout_triggered`
   - Metric: stuck task count

9. **Instrument CircuitBreaker**
   - Log `circuit_breaker.opened`, `circuit_breaker.closed`
   - Metrics: trips, state per provider

10. **Instrument EscalationManager**
    - Log `escalation.triggered` with tier + reason
    - Metric: escalations by tier

**Acceptance:**
- ✅ Circuit breaker failures include trace_id
- ✅ Escalation events show full task history via trace_id

### Phase 4: API & Tooling (Week 4)

**Goal:** API metrics, Grafana dashboards, query tools

11. **API Request Metrics**
    - Middleware to track request duration
    - Metrics: requests total, duration histogram

12. **Metrics Endpoint**
    - Add `/metrics` endpoint for Prometheus

13. **Log Query Tools**
    - CLI tool: `python bin/query_logs.py --trace-id <id>`
    - Find all logs for a trace_id

14. **Grafana Dashboards** (optional)
    - Task throughput dashboard
    - Worker performance dashboard
    - Circuit breaker status

**Acceptance:**
- ✅ `/metrics` endpoint returns Prometheus format
- ✅ Can query all logs for a failed task by trace_id
- ✅ Dashboards visualize system health

---

## Migration Guide

### For Developers: Updating Existing Code

**Before:**
```python
logger.info(f"[ENGINE] Starting task {task_id}")
```

**After:**
```python
from app.observability.logging import log_task_event

log_task_event(
    logger, logging.INFO, "started",
    task_id=task_id,
    agent_type=agent_type,
)
```

**Key changes:**
1. Import `log_*_event` helpers
2. Replace string prefixes with event_type
3. Pass structured data as kwargs
4. Keep console logs readable (formatter handles it)

### For Operations: Querying Logs

**Find all logs for a failed task:**
```bash
# JSON logs in logs/server.log
jq 'select(.trace_id == "a1b2c3d4-...")' logs/server.log
```

**Find all worker spawns in last hour:**
```bash
jq 'select(.event_type == "worker.spawned" and .timestamp > "2026-02-22T14:00:00Z")' logs/server.log
```

**Count errors by component:**
```bash
jq -r 'select(.level == "ERROR") | .logger' logs/error.log | sort | uniq -c
```

---

## Performance Considerations

### Overhead Budget

| Operation | Target | Measured |
|-----------|--------|----------|
| Trace context creation | <1ms | TBD |
| JSON log formatting | <2ms | TBD |
| Metric update (counter) | <0.1ms | TBD |
| Metric update (histogram) | <0.5ms | TBD |
| **Total per operation** | **<5ms** | **TBD** |

### Mitigation Strategies

**1. Async Metric Updates (if needed):**
```python
async def observe_metric_async(metric, value):
    """Async metric update to avoid blocking."""
    await asyncio.get_event_loop().run_in_executor(
        None, metric.observe, value
    )
```

**2. Sampling for High-Frequency Events:**
```python
# Sample 10% of API requests for detailed tracing
if random.random() < 0.1:
    log_event(logger, logging.DEBUG, "api.request_detail", ...)
```

**3. Log Level Filtering:**
```python
# DEBUG logs only in development
if settings.LOG_LEVEL == "DEBUG":
    log_event(logger, logging.DEBUG, "scanner.task_detail", ...)
```

---

## Open Questions

1. **Log retention:** How long to keep JSON logs? (Proposal: 30 days, archive to S3)
2. **Metrics storage:** Run Prometheus locally or use managed service? (Proposal: local for now)
3. **Trace sampling:** Sample 100% or implement sampling for high volume? (Proposal: 100% for now, revisit if >1000 tasks/day)
4. **External integrations:** Send traces to external system (Honeycomb, Jaeger)? (Proposal: no, keep local-first)

---

## Success Metrics

**After Phase 2 completion:**
- ✅ 100% of task failures can be debugged via trace_id
- ✅ Dashboard shows worker utilization, task throughput
- ✅ Circuit breaker metrics track provider health
- ✅ <5ms overhead on p99 API requests

**After Phase 4 completion:**
- ✅ Grafana dashboards used daily for system monitoring
- ✅ Alerts fire before system degradation (proactive, not reactive)
- ✅ Mean time to debug (MTTD) reduced by 50%

---

## References

- **ADR 0005:** [Observability Architecture](../decisions/0005-observability-architecture.md)
- **Prometheus Best Practices:** https://prometheus.io/docs/practices/naming/
- **W3C Trace Context:** https://www.w3.org/TR/trace-context/
- **Python Logging Cookbook:** https://docs.python.org/3/howto/logging-cookbook.html
