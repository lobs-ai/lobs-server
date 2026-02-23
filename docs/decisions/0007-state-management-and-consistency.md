# 7. State Management and Consistency Model

**Date:** 2026-02-22  
**Status:** Accepted  
**Deciders:** System architect

## Context

The lobs-server is a **multi-agent autonomous system** where multiple AI workers access shared state concurrently:

- **Multiple agents** (programmer, researcher, writer, architect, etc.) run simultaneously
- **Shared resources**: database, project git repositories, workspaces, worker results
- **Concurrent access patterns**: read-heavy (orchestrator polling), write-occasional (agent updates)
- **Risk**: Race conditions, lost updates, repository conflicts, inconsistent state

Current mitigation is **ad-hoc**:
- Database: SQLite WAL mode provides some concurrency
- Repositories: Domain locks (one worker per project) in `WorkerManager`
- Workspaces: Isolated per agent type (workspace-programmer, workspace-architect)
- No formal consistency model or documented patterns

**Problem:** As we scale to more agents and concurrent work, we need a clear architectural model for:
1. What shared state exists
2. How we prevent conflicts
3. What consistency guarantees we provide
4. How we detect and resolve conflicts

This ADR defines that model.

---

## Decision

We adopt a **hybrid consistency model** with **resource-specific guarantees** based on access patterns and risk tolerance:

### Consistency Model by Resource Type

| Resource | Consistency Model | Coordination Mechanism | Why |
|----------|-------------------|------------------------|-----|
| **Database** | Strong | SQLite WAL + transactions | Source of truth, must be accurate |
| **Git Repositories** | Eventually consistent | Domain locks (project-level) | Prevent simultaneous edits |
| **Agent Workspaces** | Isolated (no sharing) | Per-agent directories | No conflict possible |
| **Worker Results** | Write-once, read-many | Unique filenames (task-id based) | No overwrites |
| **In-Memory State** | Eventually consistent | Refresh from DB on each tick | Short-lived, recoverable |

### State Inventory

#### 1. **Database (`data/lobs.db`)**

**What:** SQLite database with 30+ tables (tasks, projects, workers, reflections, etc.)

**Access Pattern:**
- Read: High frequency (orchestrator polls every 10s, API endpoints on demand)
- Write: Moderate (task state changes, agent completions, scheduled events)

**Coordination:** 
- **SQLite WAL mode** — Allows concurrent reads during writes
- **Async transactions** — All writes wrapped in `async with db.begin()`
- **Row-level locking** — SQLite handles via WAL journal
- **busy_timeout=10000ms** — Retry on `SQLITE_BUSY`

**Consistency Guarantee:** **Strong consistency**
- All reads see committed writes
- ACID transactions guarantee atomicity
- No dirty reads, no lost updates

**Conflict Resolution:**
- SQLite blocks concurrent writes (one writer at a time in WAL mode)
- Application retries on busy timeout
- No manual conflict resolution needed (database handles it)

#### 2. **Git Repositories (`~/project-name/`)**

**What:** Project source code repositories where agents make changes.

**Access Pattern:**
- Read: Agent startup (fetch latest)
- Write: Agent completion (commit + push)

**Coordination:** **Domain locks** (project-level)
- `WorkerManager.project_locks: dict[project_id, task_id]`
- **Invariant:** At most one active worker per project at any time
- Checked before spawn in `worker_manager.py:can_spawn_worker()`
- Released on worker completion or timeout

**Consistency Guarantee:** **Sequential consistency**
- One agent works on a project at a time
- Each agent sees changes from previous agent (git pull before start)
- No concurrent edits, no merge conflicts

**Conflict Resolution:**
- **Prevention:** Domain locks prevent conflicts from occurring
- **Detection:** If spawn attempted on locked project → queued for later
- **Recovery:** If worker crashes, monitor cleans up stale locks after timeout

**Why not finer-grained locks (file-level)?**
- Git operates at repo level (branch, commit, push)
- Merge conflicts are expensive to resolve autonomously
- Project-level locks are simple, safe, and sufficient for current workload

#### 3. **Agent Workspaces (`~/.openclaw/workspace-{agent-type}/`)**

**What:** Isolated working directories for each agent type.

**Access Pattern:**
- Read/Write: Only by single agent instance
- Persistent across tasks (agent memory, tools, templates)

**Coordination:** **None needed** (isolated by design)
- `workspace-programmer/` — Only programmer accesses
- `workspace-researcher/` — Only researcher accesses
- No overlap, no conflicts

**Consistency Guarantee:** **Isolated (no sharing)**

**Conflict Resolution:** N/A (cannot conflict)

#### 4. **Worker Results (`worker-results/{task-id}/`)**

**What:** Artifacts produced by completed workers (transcripts, handoffs, outputs).

**Access Pattern:**
- Write: Once, when worker completes
- Read: Multiple (orchestrator, API endpoints, debugging)

**Coordination:** **Unique filenames**
- Each task has unique ID (UUID prefix)
- Worker writes to `worker-results/{task-id}/transcript.jsonl`
- No overwrites (tasks don't re-run in same directory)

**Consistency Guarantee:** **Write-once, read-many**

**Conflict Resolution:** N/A (no concurrent writes to same file)

#### 5. **In-Memory State (WorkerManager)**

**What:** Transient orchestrator state:
- `active_workers: dict[str, WorkerInfo]` — Currently running workers
- `project_locks: dict[str, str]` — Project ID → Task ID mappings
- `provider_health` — Model health metrics

**Access Pattern:**
- Read: Every orchestrator tick (10s)
- Write: On worker spawn/complete

**Coordination:** **Single-threaded orchestrator**
- All access happens in orchestrator main loop (async, single-threaded)
- No concurrent modification (Python GIL + asyncio guarantees)

**Consistency Guarantee:** **Eventually consistent**
- State refreshed from DB on each tick
- Short-lived (cleared on orchestrator restart)
- **Source of truth is always the database**

**Conflict Resolution:**
- On orchestrator restart: rebuild from DB (worker_runs table)
- Stale state is acceptable (corrected next tick)

---

## Concurrency Patterns

### Pattern 1: Optimistic Concurrency (Database Updates)

**When to use:** Updating task state that might change concurrently (e.g., task status).

**How:**
```python
async with db.begin():
    task = await db.get(Task, task_id)
    if task.work_state != 'in_progress':
        # State changed since we checked, abort or retry
        raise ConflictError("Task state changed")
    task.work_state = 'completed'
    await db.flush()
```

**Why:** Prevents lost updates when multiple processes read-modify-write.

### Pattern 2: Domain Locks (Repository Access)

**When to use:** Before spawning worker that modifies git repository.

**How:**
```python
if project_id in self.project_locks:
    # Another worker is active on this project
    return False  # Queue for later

self.project_locks[project_id] = task_id
# ... spawn worker ...
# On completion or timeout: del self.project_locks[project_id]
```

**Why:** Prevents merge conflicts and repository corruption.

### Pattern 3: Idempotent Operations (Worker Spawning)

**When to use:** Operations that might retry or execute multiple times.

**How:**
- Worker run IDs are unique (UUID)
- DB inserts check for existing run before creating
- Completion webhooks are idempotent (check task state first)

**Why:** Safe to retry without side effects.

### Pattern 4: Single Writer Principle (Configuration Files)

**When to use:** Updating shared config files (e.g., `agents/config.yaml`).

**How:**
- Only orchestrator writes config
- Agents read-only
- Updates are infrequent (startup, reflection cycles)

**Why:** Avoids write conflicts, simple mental model.

---

## Failure Scenarios and Recovery

### Scenario 1: Orchestrator Crash During Worker Execution

**What happens:**
- Workers continue running (spawned via Gateway API)
- In-memory state (active_workers, project_locks) lost
- Database still has task marked `work_state='in_progress'`

**Recovery:**
1. On restart, orchestrator polls DB for in-progress tasks
2. Monitor detects stuck tasks (>2 hours with no updates)
3. Monitor either:
   - Auto-retries if safe (task in known good state)
   - Escalates to human if ambiguous

**Data loss:** None (DB is durable, worker results written to disk)

### Scenario 2: Concurrent Task State Update

**What happens:**
- API endpoint updates task notes
- Worker completion webhook updates task work_state
- Both read-modify-write at same time

**Prevention:**
- SQLite transactions serialize writes
- Second writer blocks until first commits
- busy_timeout ensures retry

**Result:** Both updates succeed (one blocks, then applies)

### Scenario 3: Git Push Conflict

**What happens:**
- Worker A pushes to main branch
- Worker B (on different project) pushes to main branch
- Both modify same file

**Prevention:**
- Domain locks ensure only one worker per project
- Workers on different projects don't conflict (separate repos)

**Detection:**
- If domain locks fail, git push will fail with merge conflict
- Worker reports failure
- Escalation system triggers retry or human review

### Scenario 4: Database Corruption

**What happens:**
- Disk failure, power loss, or SQLite bug corrupts database

**Recovery:**
1. Automated backups in `data/backups/` (hourly)
2. Restore from most recent backup
3. Replay missing transactions from worker results (manual)

**Prevention:**
- SQLite WAL mode is crash-safe
- Regular integrity checks (`PRAGMA integrity_check`)
- Monitor disk space

---

## Observability

### Metrics to Track

1. **Database contention:**
   - `SQLITE_BUSY` errors per hour
   - Transaction retry count
   - Average query latency

2. **Domain lock conflicts:**
   - Spawn attempts blocked by locks
   - Average lock hold time
   - Stale locks detected and cleaned

3. **Worker state consistency:**
   - Divergence between DB and in-memory state
   - Tasks stuck in `in_progress` (monitor detections)

### Logging

- All lock acquisitions/releases logged: `[LOCK] Acquired project={project_id} task={task_id}`
- Database conflicts logged: `[DB] Transaction retry on BUSY`
- Worker state transitions logged: `[WORKER] {run_id} status={status}`

### Health Checks

- `GET /api/orchestrator/status` — Shows active workers, locks, health
- `GET /api/health` — Database reachable, no corruption
- `GET /api/status/activity` — Recent state changes, detect stalls

---

## Testing Strategy

### Unit Tests
- Lock acquisition/release logic
- Optimistic concurrency conflict handling
- Idempotent operations (multiple calls same result)

### Integration Tests
- Concurrent database writes (spawn multiple workers, verify no lost updates)
- Domain lock enforcement (attempt to spawn two workers on same project)
- Worker crash recovery (kill worker mid-execution, verify cleanup)

### Load Tests
- High concurrency (10+ workers, 100+ tasks)
- Database bottleneck detection (measure latency at scale)

---

## Consequences

### Positive

- **Clear guarantees** — Each resource has documented consistency model
- **Proven patterns** — Domain locks, optimistic concurrency, single writer (battle-tested)
- **Minimal coordination** — Most state is isolated or write-once (low overhead)
- **Fail-safe defaults** — Conflicts prevent rather than corrupt (locks, transactions)
- **Observable** — Metrics and logs for detecting issues
- **Incrementally enforceable** — Can add stricter guarantees if needed

### Negative

- **Serialization bottleneck** — Domain locks limit parallelism (one worker per project)
- **No distributed coordination** — Single orchestrator, single machine (can't scale horizontally)
- **Manual conflict resolution** — If domain locks fail, human intervention needed
- **SQLite limits** — Write throughput capped at ~1000 writes/sec (acceptable for now)

### Neutral

- State model is explicit but not enforced by types (rely on discipline + tests)
- Eventual consistency is acceptable for in-memory state (short TTL)

---

## Alternatives Considered

### Option 1: Distributed Locks (Redis, etcd)

**Pros:**
- Horizontal scaling (multiple orchestrator instances)
- Fine-grained locks (file-level, not project-level)
- Automatic expiration (TTL-based)

**Cons:**
- Requires external service (Redis/etcd daemon)
- Network failure modes (split-brain, stale locks)
- Overkill for single-machine workload
- Adds operational complexity

**Why rejected:** Current scale doesn't justify distributed coordination. If we need multiple orchestrators, revisit.

### Option 2: Pessimistic Locking (SELECT FOR UPDATE)

**Pros:**
- Stronger guarantees (no read-modify-write races)
- Explicit in code (clear which rows are locked)

**Cons:**
- Not well-supported in SQLite (no true row-level locks)
- Increases deadlock risk
- Longer transaction hold times

**Why rejected:** Optimistic concurrency + transactions is simpler and sufficient.

### Option 3: Event Sourcing

**Pros:**
- Complete audit trail (all state changes logged)
- Time travel (replay to any point)
- Conflict-free (append-only)

**Cons:**
- Complex (event store, projections, snapshots)
- Query complexity (reconstruct state from events)
- Overkill for current data model

**Why rejected:** CRUD model is simpler and fits current needs. Can add event log later if needed.

### Option 4: CRDTs (Conflict-free Replicated Data Types)

**Pros:**
- Automatic conflict resolution (merge without coordination)
- Works offline (sync when reconnected)

**Cons:**
- Limited data types (counters, sets, registers)
- Complex to reason about (eventual consistency semantics)
- Poor fit for task state machine (needs strong consistency)

**Why rejected:** Tasks have strict state machines (inbox→active→completed). CRDTs don't fit.

---

## Migration Plan

**Phase 1: Document current patterns** (this ADR) ✅

**Phase 2: Add observability (Week 1)**
- Log all lock operations
- Add Prometheus metrics for contention
- Dashboard for lock stats

**Phase 3: Enforce patterns in code (Week 2-3)**
- Add `@require_lock` decorator for repo operations
- Add transaction context managers with retry
- Add pre-spawn lock checker

**Phase 4: Test edge cases (Week 4)**
- Concurrent worker spawn on same project
- Orchestrator crash during worker execution
- Database busy timeout under load

**Phase 5: Document runbooks (Ongoing)**
- Recovery procedures for each failure scenario
- Lock cleanup scripts
- Database integrity check automation

---

## References

- `app/database.py` — SQLite WAL configuration
- `app/orchestrator/worker.py` — Domain lock implementation
- `app/orchestrator/monitor_enhanced.py` — Stuck task detection
- `docs/architecture/multi-agent-system.md` — Agent lifecycle
- `docs/architecture/orchestrator-flow.md` — Orchestrator internals
- ADR-0002: SQLite for Primary Database
- ADR-0003: Project Manager Delegation

---

## Open Questions

1. **Should we add row-level versioning for optimistic concurrency?**  
   → Not yet. Current transaction model is sufficient. Add if we see lost updates.

2. **Should we add distributed tracing (OpenTelemetry) for lock acquisition chains?**  
   → Nice to have. Defer until we have performance issues.

3. **Should we enforce lock acquisition order to prevent deadlocks?**  
   → Not needed yet. Current lock scope is small (project-level only).

---

*Based on Michael Nygard's ADR format*
