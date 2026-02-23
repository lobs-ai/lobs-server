# State Management Patterns — Implementation Guide

**Last Updated:** 2026-02-22  
**Audience:** Developers and AI agents working on lobs-server

This guide provides practical code patterns for working with shared state in the lobs-server multi-agent system. See `docs/decisions/0007-state-management-and-consistency.md` for the architectural rationale.

---

## Quick Reference

| **Resource** | **Pattern** | **When to Use** |
|--------------|-------------|-----------------|
| Database | Transaction + retry | Any write to DB |
| Git repo | Check domain lock | Before spawning worker |
| Config file | Single writer only | Orchestrator-only updates |
| Worker results | Unique filenames | Writing task artifacts |

---

## Pattern 1: Database Writes (Transaction + Retry)

**Use for:** Any database modification (task updates, worker runs, settings).

### Basic Transaction

```python
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db

async def update_task_status(db: AsyncSession, task_id: str, new_status: str):
    """Update task status with transaction safety."""
    async with db.begin():
        result = await db.execute(
            select(Task).where(Task.id == task_id)
        )
        task = result.scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {task_id} not found")
        
        task.status = new_status
        task.updated_at = datetime.now(timezone.utc)
        await db.flush()
```

**Key points:**
- `async with db.begin()` — Auto-commit on success, rollback on exception
- `await db.flush()` — Write to database but keep transaction open
- SQLite handles locking and retry internally (busy_timeout=10s)

### Optimistic Concurrency (Prevent Lost Updates)

```python
async def complete_task_if_in_progress(db: AsyncSession, task_id: str) -> bool:
    """Complete task only if it's currently in progress (optimistic lock)."""
    async with db.begin():
        result = await db.execute(
            select(Task).where(Task.id == task_id).with_for_update()
        )
        task = result.scalar_one_or_none()
        if not task:
            return False
        
        # Check precondition
        if task.work_state != 'in_progress':
            logger.warning(f"Task {task_id} state changed, expected in_progress but got {task.work_state}")
            return False  # Another process changed it
        
        # Safe to update
        task.work_state = 'completed'
        task.finished_at = datetime.now(timezone.utc)
        await db.flush()
        return True
```

**When to use:** Operations that depend on current state (state machine transitions).

### Bulk Updates (Batch Transactions)

```python
async def mark_tasks_ready(db: AsyncSession, task_ids: list[str]):
    """Mark multiple tasks as ready in a single transaction."""
    async with db.begin():
        for task_id in task_ids:
            result = await db.execute(
                select(Task).where(Task.id == task_id)
            )
            task = result.scalar_one()
            task.work_state = 'ready'
        await db.flush()
```

**Key points:**
- One transaction for all updates (all-or-nothing)
- Faster than individual transactions
- Use for batch operations (cleanup, migrations)

---

## Pattern 2: Domain Locks (Repository Access)

**Use for:** Preventing concurrent work on the same git repository.

### Check Before Spawn

```python
class WorkerManager:
    def __init__(self, db: AsyncSession):
        self.project_locks: dict[str, str] = {}  # project_id -> task_id
    
    def can_spawn_worker(self, task_id: str, project_id: str) -> bool:
        """Check if we can spawn a worker for this task."""
        if project_id in self.project_locks:
            locked_by = self.project_locks[project_id]
            if locked_by != task_id:
                logger.info(
                    f"[LOCK] Project {project_id} locked by task {locked_by}, "
                    f"cannot spawn for task {task_id}"
                )
                return False
        return True
    
    async def spawn_worker(self, db: AsyncSession, task_id: str, project_id: str):
        """Spawn worker with lock acquisition."""
        if not self.can_spawn_worker(task_id, project_id):
            return None  # Queue for later
        
        # Acquire lock
        self.project_locks[project_id] = task_id
        logger.info(f"[LOCK] Acquired project={project_id} task={task_id}")
        
        try:
            # Spawn worker via OpenClaw Gateway
            worker = await self._spawn_via_gateway(task_id, project_id)
            return worker
        except Exception as e:
            # Release lock on failure
            del self.project_locks[project_id]
            logger.error(f"[LOCK] Released project={project_id} due to spawn failure: {e}")
            raise
    
    async def handle_worker_completion(self, task_id: str, project_id: str):
        """Release lock when worker completes."""
        if project_id in self.project_locks:
            del self.project_locks[project_id]
            logger.info(f"[LOCK] Released project={project_id} task={task_id}")
```

**Key points:**
- Check lock before spawn, not after (fail fast)
- Release lock in finally block or on error
- Log all lock operations (observability)

### Cleanup Stale Locks

```python
async def cleanup_stale_locks(self, db: AsyncSession, max_age_hours: int = 3):
    """Remove locks for workers that crashed or timed out."""
    stale_count = 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    
    for project_id, task_id in list(self.project_locks.items()):
        # Check if worker is still active
        result = await db.execute(
            select(WorkerRun)
            .where(WorkerRun.task_id == task_id)
            .where(WorkerRun.ended_at.is_(None))
        )
        active_worker = result.scalar_one_or_none()
        
        if not active_worker or active_worker.started_at < cutoff:
            del self.project_locks[project_id]
            logger.warning(f"[LOCK] Cleaned stale lock project={project_id} task={task_id}")
            stale_count += 1
    
    return stale_count
```

**When to run:** Orchestrator startup, periodic health checks (every 30 min).

---

## Pattern 3: Idempotent Operations

**Use for:** Operations that might retry or execute multiple times.

### Idempotent Database Insert

```python
async def log_worker_completion(
    db: AsyncSession,
    worker_id: str,
    task_id: str,
    succeeded: bool
):
    """Log worker completion (safe to call multiple times)."""
    async with db.begin():
        # Check if already logged
        result = await db.execute(
            select(WorkerRun).where(WorkerRun.worker_id == worker_id)
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            logger.debug(f"Worker {worker_id} already logged, skipping")
            return existing
        
        # First time, insert
        run = WorkerRun(
            worker_id=worker_id,
            task_id=task_id,
            ended_at=datetime.now(timezone.utc),
            succeeded=succeeded,
        )
        db.add(run)
        await db.flush()
        return run
```

**Why:** Webhook callbacks might arrive multiple times (network retries).

### Idempotent State Transition

```python
async def mark_task_completed(db: AsyncSession, task_id: str) -> bool:
    """Mark task as completed (safe to call multiple times)."""
    async with db.begin():
        task = await db.get(Task, task_id)
        if not task:
            return False
        
        if task.work_state == 'completed':
            logger.debug(f"Task {task_id} already completed")
            return True  # Already done, no-op
        
        task.work_state = 'completed'
        task.finished_at = datetime.now(timezone.utc)
        await db.flush()
        return True
```

**Key points:**
- Check current state before applying transition
- Return success even if already in target state
- Log when operation is no-op (helps debugging)

---

## Pattern 4: Single Writer Principle

**Use for:** Configuration files, agent templates, shared workspace files.

### Orchestrator-Only Writes

```python
async def update_agent_config(config_path: Path, updates: dict):
    """Update agent config (orchestrator-only operation)."""
    # This should ONLY be called from orchestrator
    # Agents must read-only
    
    async with asyncio.Lock():  # Single writer even within orchestrator
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        config.update(updates)
        
        # Atomic write (write to temp, then rename)
        tmp_path = config_path.with_suffix('.tmp')
        with open(tmp_path, 'w') as f:
            yaml.dump(config, f)
        tmp_path.replace(config_path)
```

**Key points:**
- Only orchestrator writes config
- Agents are read-only consumers
- Use atomic write (temp + rename) to prevent partial reads

### Agent Read Pattern

```python
def load_agent_config(config_path: Path) -> dict:
    """Load agent config (read-only, safe to call anytime)."""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)
```

**Why single writer:** Avoids write conflicts, simple mental model, easy to reason about.

---

## Pattern 5: Write-Once, Read-Many

**Use for:** Worker artifacts (transcripts, outputs, handoffs).

### Writing Worker Results

```python
async def write_worker_transcript(task_id: str, transcript: list[dict]):
    """Write worker transcript to disk (write-once)."""
    results_dir = Path("worker-results") / task_id
    results_dir.mkdir(parents=True, exist_ok=True)
    
    transcript_path = results_dir / "transcript.jsonl"
    
    # Write-once: overwrite is OK (re-running same task)
    with open(transcript_path, 'w') as f:
        for entry in transcript:
            f.write(json.dumps(entry) + '\n')
    
    logger.info(f"[RESULTS] Wrote transcript to {transcript_path}")
    return transcript_path
```

**Key points:**
- Unique directory per task (task ID is UUID)
- Overwrites are safe (same task re-run)
- No locking needed (no concurrent writes to same task)

### Reading Results (Concurrent Safe)

```python
def read_worker_transcript(task_id: str) -> list[dict]:
    """Read worker transcript (safe to call from multiple threads)."""
    transcript_path = Path("worker-results") / task_id / "transcript.jsonl"
    
    if not transcript_path.exists():
        return []
    
    with open(transcript_path, 'r') as f:
        return [json.loads(line) for line in f]
```

**Why safe:** File is written atomically (once), then becomes read-only.

---

## Common Pitfalls

### ❌ Don't: Forget to release locks

```python
# BAD: Lock never released if spawn fails
self.project_locks[project_id] = task_id
worker = await self._spawn_worker(task_id)  # Might raise exception
```

**Fix:** Use try/finally or context manager.

### ❌ Don't: Read-modify-write without transaction

```python
# BAD: Race condition (task state might change between read and write)
task = await db.get(Task, task_id)
# ... some async operation ...
task.status = 'completed'
await db.flush()
```

**Fix:** Wrap in transaction and check state hasn't changed.

### ❌ Don't: Assume in-memory state is up-to-date

```python
# BAD: Cache might be stale
if task_id in self.completed_tasks_cache:
    return  # Might miss recent completion
```

**Fix:** Refresh from DB, or use short TTL on cache.

### ❌ Don't: Spawn multiple workers on same project

```python
# BAD: No lock check
for task in tasks:
    await self.spawn_worker(task)  # Might spawn two workers on same project
```

**Fix:** Check domain lock before spawn.

---

## Testing Your Changes

### Unit Test: Transaction Rollback

```python
async def test_transaction_rollback_on_error(db: AsyncSession):
    """Verify failed transaction doesn't leave partial state."""
    task = Task(id="test-1", title="Test", status="inbox")
    db.add(task)
    await db.commit()
    
    with pytest.raises(ValueError):
        async with db.begin():
            task.status = "active"
            await db.flush()
            raise ValueError("Simulated error")
    
    await db.refresh(task)
    assert task.status == "inbox"  # Rollback successful
```

### Integration Test: Domain Lock Enforcement

```python
async def test_domain_lock_prevents_concurrent_spawn(worker_manager, db):
    """Verify domain locks prevent concurrent work on same project."""
    project_id = "project-1"
    task1_id = "task-1"
    task2_id = "task-2"
    
    # Spawn first worker
    worker1 = await worker_manager.spawn_worker(db, task1_id, project_id)
    assert worker1 is not None
    
    # Attempt to spawn second worker (should fail)
    worker2 = await worker_manager.spawn_worker(db, task2_id, project_id)
    assert worker2 is None  # Blocked by lock
    
    # Complete first worker
    await worker_manager.handle_worker_completion(task1_id, project_id)
    
    # Now second worker can spawn
    worker2 = await worker_manager.spawn_worker(db, task2_id, project_id)
    assert worker2 is not None
```

### Load Test: Concurrent Database Writes

```python
async def test_concurrent_task_updates(db_session_factory):
    """Verify no lost updates under concurrent load."""
    task_id = "test-task"
    num_workers = 10
    
    async def update_task(worker_id: int):
        async with db_session_factory() as db:
            async with db.begin():
                task = await db.get(Task, task_id)
                task.retry_count += 1
                await db.flush()
    
    await asyncio.gather(*[update_task(i) for i in range(num_workers)])
    
    async with db_session_factory() as db:
        task = await db.get(Task, task_id)
        assert task.retry_count == num_workers  # No lost updates
```

---

## Monitoring and Observability

### Metrics to Add

```python
# In worker_manager.py
DOMAIN_LOCK_ACQUISITIONS = Counter('domain_lock_acquisitions_total', 'Lock acquisitions', ['project_id'])
DOMAIN_LOCK_BLOCKS = Counter('domain_lock_blocks_total', 'Spawn attempts blocked by lock', ['project_id'])
DOMAIN_LOCK_STALE_CLEANUPS = Counter('domain_lock_stale_cleanups_total', 'Stale locks cleaned')

# In database.py
DB_TRANSACTION_RETRIES = Counter('db_transaction_retries_total', 'SQLite busy retries')
DB_TRANSACTION_FAILURES = Counter('db_transaction_failures_total', 'Failed transactions')
```

### Log Patterns

```python
# Lock operations
logger.info(f"[LOCK] Acquired project={project_id} task={task_id}")
logger.info(f"[LOCK] Released project={project_id} task={task_id}")
logger.warning(f"[LOCK] Blocked spawn project={project_id} locked_by={locked_task_id}")

# Database operations
logger.debug(f"[DB] Transaction started task_id={task_id}")
logger.warning(f"[DB] Transaction retry due to SQLITE_BUSY")
logger.error(f"[DB] Transaction failed after retries: {error}")

# State transitions
logger.info(f"[STATE] Task {task_id}: {old_state} → {new_state}")
```

---

## Further Reading

- `docs/decisions/0007-state-management-and-consistency.md` — Architectural rationale
- `app/database.py` — Database session management
- `app/orchestrator/worker.py` — Domain lock implementation
- `docs/architecture/multi-agent-system.md` — Agent lifecycle and coordination

---

**Questions?** Add to this guide as patterns evolve. Update examples when code changes.
