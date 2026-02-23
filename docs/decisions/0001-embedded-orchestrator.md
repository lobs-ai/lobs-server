# 1. Embedded Task Orchestrator

**Date:** 2026-02-22  
**Status:** Accepted  
**Deciders:** System architect, product owner

## Context

lobs-server needed a way to autonomously execute tasks without manual intervention. The system had to:
- Find eligible tasks automatically
- Route them to appropriate agents
- Spawn worker processes via OpenClaw
- Monitor task progress and handle failures
- Operate continuously without external job schedulers

Two architectural approaches were considered: embedded vs. external orchestrator.

## Decision

We embed the task orchestrator directly into the FastAPI application as an async background task that runs in the same process.

The orchestrator consists of:
- **Scanner** — Polls database for eligible tasks
- **Router** — Delegates routing decisions to project-manager agent
- **Engine** — Main polling loop coordinating scanner, router, worker
- **Worker** — Spawns OpenClaw subagent processes
- **Monitor** — Detects stuck/failed tasks and triggers escalation
- **Circuit Breaker** — Isolates infrastructure failures

All components live in `app/orchestrator/` and run within the FastAPI process lifecycle.

## Consequences

### Positive

- **Zero infrastructure complexity** — No separate job queue, worker pool, or message broker
- **Simple deployment** — Single process to run, monitor, and scale
- **Fast feedback loops** — Direct database access, no serialization overhead
- **Easy debugging** — All logs in one place, direct access to application context
- **Immediate consistency** — Orchestrator sees database changes instantly
- **Simpler testing** — No distributed system coordination to mock

### Negative

- **Single point of failure** — If the FastAPI process crashes, orchestration stops
- **Resource contention** — Orchestrator shares CPU/memory with API requests
- **Scaling constraints** — Can't scale orchestrator independently from API
- **Long-running tasks block shutdown** — Graceful shutdown must wait for in-flight tasks
- **No work distribution** — Can't easily run multiple orchestrator instances

### Neutral

- Orchestrator state lives in database, not in-memory queues
- Polling-based rather than event-driven (acceptable for current scale)

## Alternatives Considered

### Option 1: External Worker Pool (Celery/RQ)

- **Pros:**
  - Industry standard pattern
  - Built-in retry, scheduling, monitoring
  - Independent scaling of workers and API
  - Fault isolation
  - Multiple workers for parallelism

- **Cons:**
  - Requires Redis/RabbitMQ infrastructure
  - Additional deployment complexity
  - Serialization overhead for task payloads
  - Debugging across multiple processes
  - Overkill for current task volume (~10-50 tasks/day)

- **Why rejected:** Infrastructure complexity outweighs benefits at current scale. We can migrate later if needed.

### Option 2: Cron + CLI Scripts

- **Pros:**
  - Simple, well-understood
  - Easy to test individually
  - No long-running processes

- **Cons:**
  - Fixed polling intervals (poor responsiveness)
  - No live monitoring or adaptive behavior
  - Difficult to coordinate multi-step workflows
  - State management becomes complex
  - No graceful degradation

- **Why rejected:** Too rigid for dynamic task routing and agent-driven workflows.

### Option 3: Cloud Functions (Lambda/Cloud Run)

- **Pros:**
  - Auto-scaling
  - No server management
  - Pay-per-execution

- **Cons:**
  - Cold start latency
  - Vendor lock-in
  - Complex local development
  - Database connection limits
  - Requires external trigger mechanism (EventBridge, Cloud Scheduler)

- **Why rejected:** Adds cloud dependency and complexity without clear benefit for our workload.

## References

- `app/orchestrator/engine.py` — Main orchestrator loop
- `app/main.py` — Orchestrator lifecycle integration
- ARCHITECTURE.md — System overview
- [Task #38] — Original orchestrator implementation
- [Initiative #52] — Monitoring and escalation improvements

## Notes

This decision is **reversible**. If task volume or complexity grows significantly, we can:
1. Extract orchestrator to separate process (same codebase, different entry point)
2. Add message queue for task distribution
3. Migrate to Celery/Temporal/similar

The embedded design allows us to move fast now while keeping migration paths open.

---

*Based on Michael Nygard's ADR format*
