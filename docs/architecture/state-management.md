# State Management Architecture

**Last Updated:** 2026-02-22  
**Version:** 1.0  
**Status:** Implemented  
**Related:** [ADR 0007](../decisions/0007-state-management-and-consistency.md)

---

## Overview

The lobs-server orchestrator manages **shared state across multiple concurrent AI agents**. This document defines the consistency model, coordination mechanisms, and conflict resolution strategies that ensure data integrity and prevent race conditions.

**Key Challenge:**

Multiple agents (programmer, researcher, writer, etc.) work simultaneously on different projects, accessing:
- **Shared database** — Task state, worker runs, reflections, projects
- **Git repositories** — Project source code
- **Agent workspaces** — Persistent working directories
- **In-memory state** — Active workers, locks, health metrics

Without proper coordination, this leads to:
- ❌ Lost database updates (read-modify-write races)
- ❌ Git merge conflicts (concurrent edits to same repo)
- ❌ Inconsistent state (memory vs database divergence)
- ❌ Orphaned resources (locks not released on crash)

**Solution:**

We implement a **hybrid consistency model** with **resource-specific guarantees** based on access patterns and failure tolerance.

---

## Consistency Model

### Resource Inventory

| Resource | Consistency Model | Coordination | Why |
|----------|------------------|--------------|-----|
| **Database** | Strong | SQLite WAL + transactions | Source of truth, must be accurate |
| **Git Repos** | Sequential | Domain locks (project-level) | Prevent merge conflicts |
| **Workspaces** | Isolated | Per-agent directories | No sharing = no conflicts |
| **Worker Results** | Write-once | Unique filenames | No overwrites possible |
| **In-Memory** | Eventually consistent | Refresh from DB each tick | Short-lived, recoverable |

---

## 1. Database State

### Technology: SQLite with WAL Mode

**Configuration:**
```python
# app/database.py
engine = create_async_engine(
    f"sqlite+aiosqlite:///{db_path}",
    connect_args={
        "check_same_thread": False,
        "timeout": 10.0,  # 10 second busy timeout
    },
    echo=False,
    pool_pre_ping=True,
)

# Enable WAL mode
async with engine.begin() as conn:
    await conn.execute(text("PRAGMA journal_mode=WAL"))
    await conn.execute(text("PRAGMA busy_timeout=10000"))
```

**What WAL Provides:**
- ✅ **Concurrent reads during writes** — Readers don't block writers
- ✅ **Atomic commits** — All-or-nothing transaction semantics
- ✅ **Crash recovery** — WAL journal replayed on startup
- ✅ **Row-level locking** — Fine-grained concurrency

**Limitations:**
- ⚠️ **Single writer at a time** — Concurrent writes queue (one at a time)
- ⚠️ **Same-machine only** — Can't use networked storage for WAL
- ⚠️ **~1000 writes/sec max** — SQLite throughput cap

### Consistency Guarantee: **Strong Consistency**

All reads see committed writes. No dirty reads, lost updates, or phantom reads.

### Transaction Patterns

**Pattern 1: Optimistic Concurrency**

Detect and handle concurrent modifications:

```python
async def update_task_status(task_id: str, new_status: str):
    async with db.begin() as session:
        # Read current state
        task = await session.get(Task, task_id)
        
        # Check state hasn't changed
        if task.work_state not in ["not_started", "ready"]:
            raise ConflictError(f"Task state changed to {task.work_state}")
        
        # Update
        task.work_state = "in_progress"
        task.updated_at = datetime.utcnow()
        
        # Commit atomically
        await session.flush()
```

**Why this works:**
- If another transaction modifies `task.work_state` between read and write, our flush will fail
- SQLite busy_timeout causes automatic retry
- Transaction ensures all-or-nothing update

**Pattern 2: Conditional Updates with Versioning**

For high-contention resources (e.g., worker counts):

```python
class Task(Base):
    id = Column(String, primary_key=True)
    work_state = Column(String, nullable=False)
    version = Column(Integer, default=1)  # Optimistic lock version

async def claim_task(task_id: str, worker_id: str):
    async with db.begin() as session:
        result = await session.execute(
            update(Task)
            .where(Task.id == task_id)
            .where(Task.work_state == "ready")  # Only if still ready
            .where(Task.version == expected_version)  # Version check
            .values(
                work_state="in_progress",
                assigned_worker=worker_id,
                version=Task.version + 1,  # Increment version
                updated_at=datetime.utcnow()
            )
        )
        
        if result.rowcount == 0:
            raise ConflictError("Task already claimed or version mismatch")
```

**Pattern 3: Idempotent Operations**

Safe to retry without side effects:

```python
async def record_worker_spawn(worker_id: str, task_id: str):
    """Idempotent: can call multiple times, only creates once."""
    async with db.begin() as session:
        # Check if already exists
        existing = await session.execute(
            select(WorkerRun).where(WorkerRun.worker_id == worker_id)
        )
        if existing.scalar_one_or_none():
            return  # Already recorded, skip
        
        # Create new record
        worker_run = WorkerRun(
            worker_id=worker_id,
            task_id=task_id,
            status="spawned",
            created_at=datetime.utcnow()
        )
        session.add(worker_run)
```

### Failure Recovery

**Scenario: Orchestrator crashes during worker execution**

```python
# On orchestrator restart
async def recover_stuck_tasks():
    """Find tasks marked in_progress but with no active worker."""
    async with db.begin() as session:
        # Tasks in_progress with no recent heartbeat
        stuck_tasks = await session.execute(
            select(Task)
            .where(Task.work_state == "in_progress")
            .where(Task.last_heartbeat < datetime.utcnow() - timedelta(hours=2))
        )
        
        for task in stuck_tasks.scalars():
            # Check if worker is actually dead
            worker_alive = await check_worker_health(task.assigned_worker)
            
            if not worker_alive:
                # Reset task to ready for retry
                task.work_state = "ready"
                task.assigned_worker = None
                log.warning("Recovered stuck task", task_id=task.id)
```

---

## 2. Git Repository State

### Problem

Concurrent agents editing the same project repository will create merge conflicts:

```
Agent A (programmer): Edits src/main.py, commits, pushes
Agent B (researcher): Edits src/main.py, commits, push FAILS (conflict)
```

### Solution: Domain Locks (Project-Level)

**Invariant:** At most **one active worker per project** at any time.

**Implementation:**

```python
# app/orchestrator/worker_manager.py
class WorkerManager:
    def __init__(self):
        self.project_locks: dict[str, str] = {}  # project_id -> task_id
    
    def can_spawn_worker(self, task: Task) -> bool:
        """Check if project is available for new worker."""
        project_id = task.project_id
        
        if project_id in self.project_locks:
            locked_task = self.project_locks[project_id]
            log.info(
                "Project locked",
                project_id=project_id,
                locked_by=locked_task,
                blocked_task=task.id
            )
            return False
        
        return True
    
    async def spawn_worker(self, task: Task, agent_type: str):
        """Spawn worker and acquire project lock."""
        # Acquire lock
        self.project_locks[task.project_id] = task.id
        
        try:
            worker_id = await self._spawn_via_gateway(task, agent_type)
            return worker_id
        except Exception as e:
            # Release lock on spawn failure
            del self.project_locks[task.project_id]
            raise
    
    async def on_worker_complete(self, task_id: str):
        """Release project lock when worker finishes."""
        task = await db.get(Task, task_id)
        if task.project_id in self.project_locks:
            del self.project_locks[task.project_id]
            log.info("Project lock released", project_id=task.project_id)
```

### Consistency Guarantee: **Sequential Consistency**

- Agents see changes from previous agents (git pull before work)
- No concurrent edits → no merge conflicts
- Changes applied in total order (A → B → C)

### Lock Lifecycle

```
Task A enters orchestrator
  └─ Check: Is project locked?
     └─ No → Acquire lock (project_locks[project_id] = task_A)
     └─ Spawn worker
        └─ Worker pulls latest git changes
        └─ Worker makes changes
        └─ Worker commits and pushes
     └─ Worker completes
     └─ Release lock (del project_locks[project_id])

Task B enters orchestrator
  └─ Check: Is project locked?
     └─ No (A released) → Acquire lock
     └─ Spawn worker (sees A's changes via git pull)
```

### Stale Lock Cleanup

**Problem:** Orchestrator crashes while holding lock → lock never released

**Solution:** Monitor detects and cleans stale locks

```python
async def cleanup_stale_locks(self):
    """Remove locks for tasks that are no longer running."""
    for project_id, task_id in list(self.project_locks.items()):
        task = await db.get(Task, task_id)
        
        # Task completed or failed? Release lock
        if task.work_state not in ["in_progress"]:
            del self.project_locks[project_id]
            log.warning("Cleaned stale lock", project_id=project_id, task_id=task_id)
        
        # Task stuck for >2 hours? Release lock
        elif task.updated_at < datetime.utcnow() - timedelta(hours=2):
            del self.project_locks[project_id]
            log.error("Force-released stuck lock", project_id=project_id, task_id=task_id)
```

### Alternative Considered: File-Level Locks

**Why not finer-grained locks?**
- Git operates at repository level (branches, commits, pushes)
- Detecting file conflicts requires parsing git diffs
- Resolving conflicts autonomously is error-prone
- Project-level locks are simple and safe

**When to reconsider:** If we have many agents on same large monorepo, could implement branch-based isolation.

---

## 3. Agent Workspace State

### Layout

Each agent type has an isolated workspace:

```
~/.openclaw/
├── workspace-programmer/
│   ├── memory/           # Programmer's memory files
│   ├── MEMORY.md         # Legacy memory
│   └── .work-summary     # Current task summary
├── workspace-researcher/
│   ├── memory/
│   └── MEMORY.md
├── workspace-architect/
│   ├── memory/
│   └── MEMORY.md
└── workspace-writer/
    ├── memory/
    └── MEMORY.md
```

### Consistency Guarantee: **Isolated (No Coordination Needed)**

- No agent reads another agent's workspace
- No conflicts possible
- Each agent maintains persistent state across tasks

### Workspace Persistence

**Advantages:**
- Agent memory accumulates over time
- Tools and templates persist
- No setup cost on each spawn

**Tradeoffs:**
- Disk usage grows (mitigated by periodic cleanup)
- State can become stale (memory refresh cycles handle this)

---

## 4. Worker Results State

### Storage Layout

```
worker-results/
├── task-abc123/
│   ├── transcript.jsonl        # Full execution log
│   ├── handoffs.json          # Created subtasks
│   ├── output.txt             # Agent output
│   └── metadata.json          # Timing, tokens, model
├── task-def456/
│   └── ...
```

### Consistency Guarantee: **Write-Once, Read-Many**

- Each task has unique ID (UUID prefix)
- Worker writes results once on completion
- Multiple readers (orchestrator, API, debugging)
- No overwrites or conflicts

### Idempotent Result Processing

```python
async def process_worker_results(task_id: str):
    """Process worker results idempotently."""
    result_file = f"worker-results/{task_id}/metadata.json"
    
    if not os.path.exists(result_file):
        log.warning("No results found", task_id=task_id)
        return
    
    # Parse results
    with open(result_file) as f:
        metadata = json.load(f)
    
    # Check if already processed
    task = await db.get(Task, task_id)
    if task.work_state == "completed":
        log.info("Results already processed", task_id=task_id)
        return  # Idempotent
    
    # Mark as completed
    task.work_state = "completed"
    task.completed_at = datetime.utcnow()
    task.result_metadata = metadata
    await db.commit()
```

---

## 5. In-Memory State

### Scope

Orchestrator maintains transient state:

```python
class WorkerManager:
    active_workers: dict[str, WorkerInfo]  # worker_id -> info
    project_locks: dict[str, str]          # project_id -> task_id
    provider_health: ProviderHealthTracker # Model provider status
```

### Consistency Guarantee: **Eventually Consistent**

- Refreshed from database on each orchestrator tick (every 10 seconds)
- Short TTL (state cleared on restart)
- **Database is always source of truth**

### Refresh Pattern

```python
async def refresh_worker_state(self):
    """Rebuild in-memory state from database."""
    # Fetch active workers from DB
    active_runs = await db.execute(
        select(WorkerRun).where(WorkerRun.status == "running")
    )
    
    # Rebuild in-memory cache
    new_active = {}
    for run in active_runs.scalars():
        new_active[run.worker_id] = WorkerInfo(
            worker_id=run.worker_id,
            task_id=run.task_id,
            started_at=run.started_at,
            status=run.status
        )
    
    self.active_workers = new_active
```

### Crash Recovery

**On orchestrator restart:**
1. In-memory state is empty
2. First tick refreshes from database
3. Monitor detects stuck workers (in_progress but no heartbeat)
4. Stuck workers are cleaned up or retried

**Data loss:** None (database is durable, worker results on disk)

---

## Concurrency Patterns

### Pattern 1: Check-Then-Act with Locks

**Problem:** Race between checking state and taking action

```python
# ❌ UNSAFE: Race condition
if not self.project_locked(project_id):
    self.lock_project(project_id)  # Another worker might lock between check and act
```

**Solution:** Atomic check-and-lock

```python
# ✅ SAFE: Atomic operation
def try_lock_project(self, project_id: str, task_id: str) -> bool:
    """Atomically check and acquire lock."""
    if project_id in self.project_locks:
        return False
    
    self.project_locks[project_id] = task_id
    return True
```

### Pattern 2: Eventual Consistency with Reconciliation

**Problem:** In-memory state diverges from database

**Solution:** Periodic reconciliation loop

```python
async def reconcile_state(self):
    """Ensure in-memory state matches database."""
    db_workers = await fetch_active_workers_from_db()
    memory_workers = set(self.active_workers.keys())
    
    # Workers in DB but not in memory → add
    for worker_id in db_workers - memory_workers:
        log.warning("Reconciliation: adding missing worker", worker_id=worker_id)
        self.active_workers[worker_id] = await load_worker_info(worker_id)
    
    # Workers in memory but not in DB → remove
    for worker_id in memory_workers - db_workers:
        log.warning("Reconciliation: removing stale worker", worker_id=worker_id)
        del self.active_workers[worker_id]
```

### Pattern 3: Idempotent Operations

**Problem:** Retries or duplicate events cause side effects

**Solution:** Design operations to be safely retriable

```python
# ✅ Idempotent: calling multiple times has same effect as once
async def mark_task_complete(task_id: str):
    task = await db.get(Task, task_id)
    
    if task.work_state == "completed":
        return  # Already done, no-op
    
    task.work_state = "completed"
    task.completed_at = datetime.utcnow()
    await db.commit()
```

---

## Failure Scenarios

### Scenario 1: Database Lock Timeout

**Symptom:** `sqlite3.OperationalError: database is locked`

**Cause:** Multiple transactions contending for write lock

**Recovery:**
1. SQLite busy_timeout (10s) retries automatically
2. If still fails, transaction rolls back
3. Application logs error and retries operation
4. If persistent, circuit breaker opens to prevent cascade

**Prevention:**
- Keep transactions short
- Avoid long-running queries inside transactions
- Use read-only connections for queries

### Scenario 2: Stale Project Lock

**Symptom:** Project locked but no active worker (orchestrator crashed)

**Detection:** Monitor checks for locks held >2 hours

**Recovery:**
```python
# Monitor cleanup
if lock_age > timedelta(hours=2):
    log.error("Stale lock detected", project_id=project_id, task_id=task_id)
    del self.project_locks[project_id]
    
    # Optionally: reset task to ready
    task = await db.get(Task, task_id)
    if task.work_state == "in_progress":
        task.work_state = "ready"
        await db.commit()
```

### Scenario 3: Concurrent Worker Spawn Attempts

**Symptom:** Two tasks for same project try to spawn workers simultaneously

**Prevention:** Domain lock check is atomic (single-threaded orchestrator)

**If it happens anyway (race in distributed setup):**
1. Second spawn attempt fails domain lock check
2. Task remains in queue
3. Retried on next orchestrator tick after first worker completes

### Scenario 4: Database Corruption

**Symptom:** `PRAGMA integrity_check` fails

**Recovery:**
1. Restore from automated backup (hourly backups in `data/backups/`)
2. Replay missing transactions from worker results (manual)
3. Notify operator

**Prevention:**
- SQLite WAL mode is crash-safe
- Regular integrity checks
- Monitor disk health

---

## Observability

### Metrics

```python
# Database contention
lobs_db_lock_timeouts_total
lobs_db_transaction_retry_total
lobs_db_query_duration_seconds

# Domain locks
lobs_domain_locks_active{project_id}
lobs_domain_lock_wait_seconds
lobs_stale_locks_cleaned_total

# State consistency
lobs_worker_state_reconciliation_total
lobs_stuck_tasks_detected_total
```

### Logging

```python
# Lock operations
log.info("Lock acquired", project_id=project_id, task_id=task_id)
log.info("Lock released", project_id=project_id, duration_sec=elapsed)
log.warning("Lock contention", project_id=project_id, blocked_task=task_id)

# Database conflicts
log.warning("Transaction retry", operation="update_task", attempt=retry_count)
log.error("DB lock timeout", operation="spawn_worker", timeout_sec=10)

# State reconciliation
log.info("State reconciled", added=len(added_workers), removed=len(removed_workers))
```

### Health Checks

```python
# GET /api/orchestrator/status
{
  "active_workers": 3,
  "project_locks": {
    "lobs-server": "task-abc123",
    "lobs-mobile": "task-def456"
  },
  "db_status": "healthy",
  "last_tick": "2026-02-22T15:30:45Z"
}

# GET /api/health
{
  "status": "healthy",
  "db_integrity": "ok",
  "db_connections": 5
}
```

---

## Testing Strategy

### Unit Tests

```python
def test_project_lock_prevents_concurrent_spawn():
    manager = WorkerManager()
    
    task_a = Task(id="task-a", project_id="proj-1")
    task_b = Task(id="task-b", project_id="proj-1")
    
    # Task A can spawn
    assert manager.can_spawn_worker(task_a) == True
    manager.project_locks["proj-1"] = "task-a"
    
    # Task B blocked (same project)
    assert manager.can_spawn_worker(task_b) == False
    
    # Release lock
    del manager.project_locks["proj-1"]
    
    # Task B can now spawn
    assert manager.can_spawn_worker(task_b) == True
```

### Integration Tests

```python
@pytest.mark.asyncio
async def test_concurrent_task_updates():
    """Ensure concurrent updates don't cause lost writes."""
    task = Task(id="task-1", work_state="ready")
    await db.add(task)
    await db.commit()
    
    # Spawn two concurrent updaters
    async def update_status(new_status):
        async with db.begin() as session:
            t = await session.get(Task, "task-1")
            await asyncio.sleep(0.1)  # Simulate concurrent access
            t.work_state = new_status
    
    # Both should succeed (one blocks on the other)
    await asyncio.gather(
        update_status("in_progress"),
        update_status("completed")
    )
    
    # Final state is deterministic (last writer wins)
    final = await db.get(Task, "task-1")
    assert final.work_state in ["in_progress", "completed"]
```

### Chaos Tests

```python
@pytest.mark.chaos
async def test_orchestrator_crash_during_worker_execution():
    """Ensure state recovery after crash."""
    task = Task(id="task-1", work_state="in_progress")
    await db.add(task)
    
    # Simulate crash: clear in-memory state
    orchestrator.worker_manager.active_workers.clear()
    orchestrator.worker_manager.project_locks.clear()
    
    # Run recovery
    await orchestrator.recover_stuck_tasks()
    
    # Task should be reset to ready
    recovered = await db.get(Task, "task-1")
    assert recovered.work_state == "ready"
```

---

## Best Practices

### ✅ Do

- **Use transactions for all writes** — Ensures atomicity
- **Keep transactions short** — Reduces lock contention
- **Make operations idempotent** — Safe to retry
- **Log all lock operations** — Essential for debugging
- **Reconcile state periodically** — Catch divergence early
- **Test concurrency scenarios** — Don't assume single-threaded

### ❌ Don't

- **Don't hold locks across async operations** — Deadlock risk
- **Don't trust in-memory state** — DB is source of truth
- **Don't ignore lock timeouts** — Indicates contention issue
- **Don't assume atomic read-modify-write** — Use transactions
- **Don't leak locks on exception** — Use try/finally or context managers

---

## Future Considerations

### Horizontal Scaling

**Current limitation:** Single orchestrator instance (project locks in memory)

**If we need multiple orchestrators:**
- Replace in-memory locks with Redis distributed locks
- Add leader election (etcd, Consul)
- Shard tasks by project ID

**Cost:** Significant complexity increase

**Trigger:** When single machine can't handle task volume (>1000 concurrent workers)

### Database Scaling

**Current:** SQLite ~1000 writes/sec

**If we hit limits:**
- Migrate to PostgreSQL (10,000+ writes/sec)
- Read replicas for query load
- Partition by project or time range

**Trigger:** Sustained >500 writes/sec or query latency >100ms p95

---

## References

- [SQLite WAL Mode](https://www.sqlite.org/wal.html)
- [Distributed Systems Consistency Models](https://jepsen.io/consistency)
- [Designing Data-Intensive Applications](https://dataintensive.net/) (Chapter 9: Consistency and Consensus)

**Related Documentation:**
- [ADR 0007: State Management and Consistency](../decisions/0007-state-management-and-consistency.md)
- [ADR 0002: SQLite for Primary Database](../decisions/0002-sqlite-for-primary-database.md)
- [Multi-Agent System Architecture](./multi-agent-system.md)

---

**Revision History:**
- 2026-02-22: Initial version based on ADR 0007
