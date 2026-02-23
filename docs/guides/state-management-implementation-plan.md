# State Management Implementation Plan

**Created:** 2026-02-22  
**Status:** Ready for implementation  
**Related ADR:** [0007-state-management-and-consistency.md](../decisions/0007-state-management-and-consistency.md)

This document outlines concrete tasks to implement and enforce the state management patterns defined in ADR-0007.

---

## Phase 1: Observability (Week 1)

**Goal:** Make state conflicts and contention visible before enforcing stricter patterns.

### Task 1.1: Add Lock Operation Logging

**What:** Comprehensive logging for all domain lock operations.

**Where:** `app/orchestrator/worker.py`

**Changes:**
```python
# At lock acquisition
logger.info(
    "[LOCK] Acquired",
    extra={"project_id": project_id, "task_id": task_id, "timestamp": time.time()}
)

# At lock release
logger.info(
    "[LOCK] Released",
    extra={"project_id": project_id, "task_id": task_id, "hold_duration_sec": duration}
)

# When spawn blocked
logger.warning(
    "[LOCK] Spawn blocked",
    extra={"project_id": project_id, "task_id": task_id, "locked_by": locked_task_id}
)
```

**Acceptance:**
- All lock lifecycle events logged
- Structured logging (JSON format for parsing)
- Lock hold duration tracked

---

### Task 1.2: Add Database Contention Metrics

**What:** Track SQLite busy events, transaction retries, and query latency.

**Where:** `app/database.py`

**Changes:**
```python
from prometheus_client import Counter, Histogram

db_busy_count = Counter('sqlite_busy_total', 'SQLITE_BUSY errors')
db_transaction_retries = Counter('db_transaction_retries_total', 'Transaction retry count')
db_query_duration = Histogram('db_query_duration_seconds', 'Query execution time')

# In get_db() or transaction wrapper
@contextmanager
def track_db_operation(operation: str):
    start = time.time()
    try:
        yield
    except sqlite3.OperationalError as e:
        if 'database is locked' in str(e):
            db_busy_count.inc()
        raise
    finally:
        db_query_duration.observe(time.time() - start)
```

**Acceptance:**
- Prometheus metrics exposed at `/metrics`
- Grafana dashboard showing contention patterns
- Alerts on high busy rate (>10/min)

---

### Task 1.3: Create State Consistency Health Check

**What:** Endpoint to detect divergence between in-memory and DB state.

**Where:** New endpoint in `app/routers/orchestrator.py`

**Changes:**
```python
@router.get("/orchestrator/health/state")
async def check_state_consistency(db: AsyncSession = Depends(get_db)):
    """Verify in-memory state matches database state."""
    issues = []
    
    # Check 1: In-progress tasks without active workers
    in_progress_tasks = await db.execute(
        select(Task).where(Task.work_state == 'in_progress')
    )
    for task in in_progress_tasks.scalars():
        if task.id not in active_workers:
            issues.append({
                "type": "orphan_task",
                "task_id": task.id,
                "started_at": task.started_at
            })
    
    # Check 2: Active workers without in-progress tasks
    for worker_id, worker_info in active_workers.items():
        task = await db.get(Task, worker_info.task_id)
        if not task or task.work_state != 'in_progress':
            issues.append({
                "type": "phantom_worker",
                "worker_id": worker_id,
                "task_id": worker_info.task_id
            })
    
    return {
        "healthy": len(issues) == 0,
        "issues": issues,
        "checked_at": datetime.now(timezone.utc)
    }
```

**Acceptance:**
- Health check runs on demand via API
- Scheduled check every 5 minutes (orchestrator)
- Slack alert on consistency issues

---

## Phase 2: Pattern Enforcement (Week 2-3)

**Goal:** Add guardrails to prevent common state management mistakes.

### Task 2.1: Add Transaction Context Manager

**What:** Reusable transaction wrapper with automatic retry and error handling.

**Where:** `app/database.py`

**Changes:**
```python
from contextlib import asynccontextmanager
from tenacity import retry, stop_after_attempt, wait_exponential

@asynccontextmanager
async def transactional(db: AsyncSession, max_retries: int = 3):
    """Safe transaction with automatic retry on busy errors."""
    retries = 0
    while retries < max_retries:
        try:
            async with db.begin():
                yield db
                return  # Success
        except sqlite3.OperationalError as e:
            if 'database is locked' in str(e) and retries < max_retries - 1:
                retries += 1
                db_transaction_retries.inc()
                await asyncio.sleep(0.1 * (2 ** retries))  # Exponential backoff
                continue
            raise
```

**Usage:**
```python
async with transactional(db) as txn:
    task = await txn.get(Task, task_id)
    task.status = 'active'
```

**Acceptance:**
- All database writes use `transactional()`
- Tests verify retry behavior
- Migration guide for existing code

---

### Task 2.2: Add @require_lock Decorator

**What:** Decorator to enforce domain lock checks before repo operations.

**Where:** `app/orchestrator/worker.py`

**Changes:**
```python
def require_lock(project_id_arg: str = 'project_id'):
    """Decorator to enforce domain lock before executing function."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            project_id = kwargs.get(project_id_arg)
            if not project_id:
                raise ValueError(f"Missing required argument: {project_id_arg}")
            
            # Check lock
            worker_manager = kwargs.get('worker_manager')
            task_id = kwargs.get('task_id')
            
            if not worker_manager.can_spawn_worker(task_id, project_id):
                raise LockError(f"Project {project_id} is locked")
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator

# Usage
@require_lock(project_id_arg='project_id')
async def spawn_worker(task_id: str, project_id: str, worker_manager: WorkerManager):
    # Safe to proceed — lock is held
    ...
```

**Acceptance:**
- All repo write operations use `@require_lock`
- Tests verify lock check before execution
- Clear error message on lock violation

---

### Task 2.3: Add Pre-Commit Lock Validation

**What:** Git pre-commit hook to prevent commits without proper lock.

**Where:** `.git/hooks/pre-commit` (or pre-commit config)

**Changes:**
```bash
#!/bin/bash
# Verify that git operations only happen when orchestrator holds lock

if [ -f ".lock-state" ]; then
    PROJECT_ID=$(git rev-parse --show-toplevel | xargs basename)
    LOCK_HOLDER=$(cat .lock-state)
    
    if [ -z "$LOCK_HOLDER" ]; then
        echo "ERROR: No lock held for project $PROJECT_ID"
        exit 1
    fi
fi
```

**Acceptance:**
- Blocks commits without lock (dev environment)
- Disabled in CI/production (agents manage locks)
- Documentation on how to bypass (emergency)

---

## Phase 3: Edge Case Testing (Week 4)

**Goal:** Verify patterns work under adversarial conditions.

### Task 3.1: Concurrent Spawn Test

**What:** Integration test for concurrent spawn attempts on same project.

**Where:** `tests/integration/test_domain_locks.py`

**Test:**
```python
@pytest.mark.asyncio
async def test_concurrent_spawn_same_project(db_factory, worker_manager):
    """Verify only one worker spawns when multiple tasks ready."""
    project_id = "test-project"
    task_ids = ["task-1", "task-2", "task-3"]
    
    # Attempt to spawn all concurrently
    results = await asyncio.gather(*[
        worker_manager.spawn_worker(db, tid, project_id)
        for tid in task_ids
    ], return_exceptions=True)
    
    # Exactly one should succeed
    successful = [r for r in results if r is not None and not isinstance(r, Exception)]
    assert len(successful) == 1, f"Expected 1 spawn, got {len(successful)}"
    
    # Others should be None (blocked by lock)
    blocked = [r for r in results if r is None]
    assert len(blocked) == 2
```

**Acceptance:**
- Test passes reliably (no flakes)
- Lock prevents >1 worker per project
- Blocked spawns logged appropriately

---

### Task 3.2: Orchestrator Crash Recovery Test

**What:** Verify state recovery after abrupt orchestrator shutdown.

**Where:** `tests/integration/test_crash_recovery.py`

**Test:**
```python
@pytest.mark.asyncio
async def test_orchestrator_crash_during_worker_execution():
    """Verify recovery when orchestrator dies mid-execution."""
    # 1. Spawn worker
    worker = await spawn_worker(db, task_id, project_id)
    assert worker is not None
    
    # 2. Simulate orchestrator crash (kill process, clear in-memory state)
    orchestrator_engine.stop()
    worker_manager.active_workers.clear()
    worker_manager.project_locks.clear()
    
    # 3. Restart orchestrator
    new_engine = OrchestratorEngine()
    await new_engine.start()
    
    # 4. Monitor should detect stuck task
    await asyncio.sleep(65)  # Wait for one monitor cycle
    
    # 5. Verify task either retried or escalated
    task = await db.get(Task, task_id)
    assert task.work_state in ['ready', 'completed', 'needs_review']
```

**Acceptance:**
- Stuck tasks detected within 2 minutes
- Locks cleaned up automatically
- No data loss (worker results preserved)

---

### Task 3.3: Database Load Test

**What:** Stress test concurrent DB writes to find contention limits.

**Where:** `tests/load/test_db_contention.py`

**Test:**
```python
@pytest.mark.load
async def test_concurrent_task_updates(db_factory):
    """Simulate high write load (100 concurrent task updates)."""
    num_tasks = 100
    tasks = [create_task(db, f"task-{i}") for i in range(num_tasks)]
    
    async def update_task(task_id: str):
        async with db_factory() as db:
            async with transactional(db):
                task = await db.get(Task, task_id)
                task.retry_count += 1
    
    start = time.time()
    await asyncio.gather(*[update_task(t.id) for t in tasks])
    duration = time.time() - start
    
    # Verify no lost updates
    for task in tasks:
        await db.refresh(task)
        assert task.retry_count == 1
    
    # Performance assertion
    assert duration < 5.0, f"Took {duration}s, expected <5s"
    print(f"✅ {num_tasks} concurrent writes in {duration:.2f}s")
```

**Acceptance:**
- No lost updates under high concurrency
- Throughput: >20 writes/sec
- Latency: p99 < 500ms

---

## Phase 4: Documentation and Runbooks (Ongoing)

### Task 4.1: Recovery Runbooks

**What:** Step-by-step guides for common failure scenarios.

**Where:** `docs/runbooks/state-recovery.md`

**Runbooks:**
1. **Stale Lock Cleanup** — How to manually remove stuck locks
2. **Database Corruption Recovery** — Restore from backup, replay transactions
3. **Orphaned Task Recovery** — Find and retry tasks with no active worker
4. **Lock Debugging** — How to trace which task holds which lock

**Acceptance:**
- Runbooks tested in staging
- Linked from monitoring alerts
- Updated quarterly

---

### Task 4.2: Monitoring Dashboard

**What:** Grafana dashboard for state management metrics.

**Panels:**
- Active domain locks (gauge)
- Lock hold duration (histogram)
- Database busy rate (counter)
- State consistency issues (alert threshold)
- Worker state transitions (timeline)

**Acceptance:**
- Dashboard deployed to prod
- Alerts configured (Slack integration)
- Reviewed in weekly ops meeting

---

## Dependencies

| Task | Depends On | Est. Hours |
|------|------------|------------|
| 1.1 Lock Logging | None | 2h |
| 1.2 DB Metrics | None | 4h |
| 1.3 Health Check | 1.1 | 3h |
| 2.1 Transaction Manager | 1.2 | 4h |
| 2.2 Lock Decorator | 1.1 | 3h |
| 2.3 Pre-Commit Hook | 2.2 | 2h |
| 3.1 Concurrent Spawn Test | 2.2 | 3h |
| 3.2 Crash Recovery Test | 1.3 | 4h |
| 3.3 DB Load Test | 2.1 | 3h |
| 4.1 Runbooks | 3.1, 3.2, 3.3 | 6h |
| 4.2 Dashboard | 1.2, 1.3 | 4h |

**Total estimated effort:** ~38 hours (~1 week for experienced developer)

---

## Success Criteria

**Observability:**
- ✅ All lock operations logged
- ✅ DB contention metrics available
- ✅ State consistency health check running

**Enforcement:**
- ✅ No repo writes without lock
- ✅ All DB writes in transactions
- ✅ Retry logic on busy errors

**Resilience:**
- ✅ Orchestrator crash recovery automated
- ✅ No data loss under high load
- ✅ Runbooks for manual intervention

**Performance:**
- ✅ DB write throughput >20/sec
- ✅ Lock contention <5% of spawn attempts
- ✅ State divergence alerts trigger <1x/week

---

## Next Steps

1. **Create programmer handoffs** for Phase 1 tasks (observability)
2. **Set up metrics infrastructure** (Prometheus + Grafana)
3. **Run baseline load tests** (before enforcement changes)
4. **Implement in order** (observability → enforcement → testing → docs)
5. **Monitor for regressions** (alert on new state issues)

---

**Questions or feedback?** Update this plan as implementation progresses.
