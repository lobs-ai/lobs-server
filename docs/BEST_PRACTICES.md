# Best Practices for lobs-server

**Last Updated:** 2026-02-14  
**Audience:** Developers, AI programmer agents  
**Purpose:** Patterns, anti-patterns, and guidelines for maintaining code quality

---

## Table of Contents

1. [Database Performance](#database-performance)
2. [SQLAlchemy Patterns](#sqlalchemy-patterns)
3. [Pydantic v2 Patterns](#pydantic-v2-patterns)
4. [API Design](#api-design)
5. [Testing](#testing)
6. [Code Quality](#code-quality)

---

## Database Performance

### SQLite WAL Mode (Current Setup)

✅ **We use WAL (Write-Ahead Logging) mode** — this is the right choice for a multi-agent system.

**Why WAL mode:**
- Writers don't block readers
- Readers don't block writers
- Significantly faster for concurrent workloads
- Perfect for orchestrator + multiple workers accessing DB simultaneously

**Current configuration:**
```python
# app/database.py
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=10000")  # 10 second timeout
    cursor.close()
```

**Recommended future optimizations:**
```python
# Additional PRAGMAs for performance (add when needed)
cursor.execute("PRAGMA synchronous=NORMAL")    # Safe in WAL mode, faster commits
cursor.execute("PRAGMA cache_size=-64000")     # 64MB cache (default is tiny)
cursor.execute("PRAGMA temp_store=MEMORY")     # Keep temp tables in memory
cursor.execute("PRAGMA mmap_size=268435456")   # 256MB memory-mapped I/O
```

**Impact:** 10-30% faster queries/writes with larger cache and mmap.

**Reference:** [SQLite WAL Documentation](https://www.sqlite.org/wal.html)

---

## SQLAlchemy Patterns

### N+1 Query Prevention (CRITICAL)

**The Problem:**

N+1 queries are the #1 performance killer. They happen when you load N parent objects, then access a relationship on each one, triggering N additional queries.

```python
# ❌ BAD - Causes 1 + N queries
async def get_activity_feed(limit: int = 100):
    result = await db.execute(
        select(WorkerRun).order_by(WorkerRun.created_at.desc()).limit(limit)
    )
    runs = result.scalars().all()
    
    for run in runs:
        task = run.task  # ⚠️ This triggers a separate SELECT query!
    
    # Result: 100 runs = 100 additional queries = 101 total queries
    # Impact: 10ms endpoint → 500ms+ endpoint
```

**The Solution: Eager Loading**

Use `selectinload()` to load relationships efficiently:

```python
# ✅ GOOD - Only 2 queries
from sqlalchemy.orm import selectinload

async def get_activity_feed(limit: int = 100):
    result = await db.execute(
        select(WorkerRun)
        .options(selectinload(WorkerRun.task))  # Eager load tasks
        .order_by(WorkerRun.created_at.desc())
        .limit(limit)
    )
    runs = result.scalars().all()
    
    for run in runs:
        task = run.task  # Already loaded! No additional query
    
    # Result: 2 queries total (1 for runs, 1 for all their tasks)
```

### Eager Loading Strategies

| Strategy | Queries | Best For | Example |
|----------|---------|----------|---------|
| **selectinload** | 2 | Most cases, collections | `selectinload(WorkerRun.task)` |
| **joinedload** | 1 | Many-to-one, simple queries | `joinedload(WorkerRun.task)` |
| **Relationship lazy="selectin"** | 2 | Always-accessed relationships | On the model definition |

#### When to use each:

**selectinload (Recommended for most cases):**
```python
# Best for: Lists of objects that access relationships
result = await db.execute(
    select(WorkerRun)
    .options(selectinload(WorkerRun.task))
    .limit(100)
)
```

**joinedload (For simple many-to-one):**
```python
# Best for: Single object lookup with relationship
result = await db.execute(
    select(WorkerRun)
    .options(joinedload(WorkerRun.task))
    .where(WorkerRun.id == run_id)
)
run = result.unique().scalars().first()  # Note: must call .unique()
```

**Relationship-level (For always-accessed relationships):**
```python
# On the model, if relationship is ALWAYS needed:
class WorkerRun(Base):
    task = relationship("Task", lazy="selectin")
    
    # Now all queries automatically eager-load task
    # Use sparingly - only for truly always-needed relationships
```

### How to Detect N+1 Queries

**Enable SQL logging in development:**
```python
# app/database.py (development only)
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=True,  # ← Prints all SQL queries
)
```

**Look for patterns like:**
```
SELECT * FROM worker_runs ...
SELECT * FROM tasks WHERE id = ?
SELECT * FROM tasks WHERE id = ?
SELECT * FROM tasks WHERE id = ?
...
```

If you see the same query repeating in a loop → N+1 problem.

### Code Review Checklist

When reviewing code that queries relationships, check:

- [ ] Does it load a list of objects?
- [ ] Does it access relationships in a loop?
- [ ] Does it use `selectinload()` or `joinedload()`?

---

## Pydantic v2 Patterns

### Current Status

✅ **We're on Pydantic v2** for most models.

⚠️ **2 models still use deprecated v1 config** (see [KNOWN_ISSUES.md](KNOWN_ISSUES.md))

### Modern Pydantic v2 Pattern

```python
# ✅ CORRECT - Pydantic v2
from pydantic import BaseModel, ConfigDict

class ProjectResponse(BaseModel):
    id: str
    name: str
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)  # ← v2 pattern
```

### Deprecated Pydantic v1 Pattern

```python
# ❌ DEPRECATED - Pydantic v1 (don't use)
class ProjectResponse(BaseModel):
    id: str
    name: str
    created_at: datetime
    
    class Config:  # ← Deprecated!
        orm_mode = True  # ← Should be from_attributes=True
```

### Config Key Changes

| v1 (Deprecated) | v2 (Current) |
|-----------------|--------------|
| `orm_mode = True` | `from_attributes=True` |
| `allow_population_by_field_name` | `populate_by_name` |
| `schema_extra` | `json_schema_extra` |

### Method Name Changes

| v1 (Deprecated) | v2 (Current) |
|-----------------|--------------|
| `.dict()` | `.model_dump()` |
| `.json()` | `.model_dump_json()` |
| `.parse_obj()` | `.model_validate()` |
| `.construct()` | `.model_construct()` |

**Note:** v1 methods still work but emit deprecation warnings.

### Field Validation Changes

```python
# ❌ v1 pattern (deprecated)
from typing import List
from pydantic import validator

class Model(BaseModel):
    items: List[int]
    
    @validator('items', each_item=True)
    def validate_positive(cls, v):
        if v < 0:
            raise ValueError('must be positive')
        return v

# ✅ v2 pattern (current)
from typing import Annotated, List
from pydantic import Field

class Model(BaseModel):
    items: List[Annotated[int, Field(ge=0)]]  # Built-in constraint
```

### Migration Tool

For bulk migration, use the official tool:
```bash
pip install bump-pydantic
bump-pydantic app/
```

**Review changes carefully before committing!**

---

## API Design

### Endpoint Patterns

**List endpoints:**
```python
@router.get("/workers/runs", response_model=List[WorkerRunResponse])
async def list_worker_runs(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(WorkerRun)
        .options(selectinload(WorkerRun.task))  # ← Prevent N+1
        .order_by(WorkerRun.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()
```

**Single object endpoints:**
```python
@router.get("/workers/runs/{run_id}", response_model=WorkerRunResponse)
async def get_worker_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(WorkerRun)
        .options(joinedload(WorkerRun.task))  # ← Single object, use joinedload
        .where(WorkerRun.id == run_id)
    )
    run = result.unique().scalars().first()
    
    if not run:
        raise HTTPException(status_code=404, detail="Worker run not found")
    
    return run
```

### Response Models

Always use response models to:
- Control what fields are exposed
- Add computed fields
- Handle relationships properly

```python
class WorkerRunResponse(BaseModel):
    id: str
    task_id: str | None
    status: str
    summary: str | None
    created_at: datetime
    
    # Related object
    task: TaskResponse | None = None
    
    model_config = ConfigDict(from_attributes=True)
```

### Error Handling

```python
from fastapi import HTTPException

# 404 for not found
if not obj:
    raise HTTPException(status_code=404, detail="Object not found")

# 400 for validation errors
if invalid:
    raise HTTPException(status_code=400, detail="Invalid input")

# 409 for conflicts
if exists:
    raise HTTPException(status_code=409, detail="Already exists")
```

---

## Testing

### Required Tests for New Endpoints

Every new endpoint MUST have tests for:

1. **Success case** — Happy path returns expected data
2. **Not found** — Returns 404 when object doesn't exist
3. **Validation** — Returns 400 for invalid input
4. **Pagination** — Limit/offset work correctly (for list endpoints)
5. **Schema** — Response matches Pydantic model

### Example Test Structure

```python
import pytest
from httpx import AsyncClient

class TestWorkerAPI:
    """Test /api/worker endpoints."""
    
    async def test_list_worker_runs_success(self, client: AsyncClient, db):
        """Should return list of worker runs."""
        # Arrange
        await create_test_worker_run(db)
        
        # Act
        response = await client.get("/api/worker/runs")
        
        # Assert
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0
        assert "id" in data[0]
        assert "status" in data[0]
    
    async def test_list_worker_runs_empty(self, client: AsyncClient):
        """Should return empty list when no runs exist."""
        response = await client.get("/api/worker/runs")
        assert response.status_code == 200
        assert response.json() == []
    
    async def test_list_worker_runs_pagination(self, client: AsyncClient, db):
        """Should respect limit and offset."""
        # Create 10 runs
        for _ in range(10):
            await create_test_worker_run(db)
        
        # Get first 5
        response = await client.get("/api/worker/runs?limit=5&offset=0")
        assert len(response.json()) == 5
        
        # Get next 5
        response = await client.get("/api/worker/runs?limit=5&offset=5")
        assert len(response.json()) == 5
    
    async def test_get_worker_run_not_found(self, client: AsyncClient):
        """Should return 404 for nonexistent run."""
        response = await client.get("/api/worker/runs/nonexistent")
        assert response.status_code == 404
```

### Test Database Setup

Use the existing fixture pattern:

```python
# conftest.py already provides:
# - client: AsyncClient
# - db: AsyncSession
# - Base cleanup between tests

@pytest.fixture
async def sample_worker_run(db):
    """Create a sample worker run for testing."""
    run = WorkerRun(
        id="test-run-1",
        task_id="test-task-1",
        status="completed",
        summary="Test summary",
    )
    db.add(run)
    await db.commit()
    return run
```

### Coverage Expectations

- **Critical endpoints:** 100% coverage
- **Important endpoints:** >90% coverage
- **Overall project:** >85% coverage

Run coverage locally:
```bash
pytest --cov=app --cov-report=html
open htmlcov/index.html
```

---

## Code Quality

### Import Organization

```python
# Standard library
import asyncio
from datetime import datetime
from typing import List, Optional

# Third-party
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

# Local
from app.database import get_db
from app.models import WorkerRun
from app.schemas import WorkerRunResponse
```

### Type Hints

Always use type hints for:
- Function parameters
- Return values
- Class attributes

```python
# ✅ Good
async def get_runs(limit: int, db: AsyncSession) -> List[WorkerRun]:
    ...

# ❌ Bad
async def get_runs(limit, db):
    ...
```

### Async/Await Consistency

```python
# ✅ Good - consistent async
async def process_task(task_id: str, db: AsyncSession) -> dict:
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    
    if task:
        data = await fetch_external_data(task)
        return data
    
    return {}

# ❌ Bad - mixing sync/async incorrectly
async def process_task(task_id, db):
    result = db.execute(...)  # Missing await
    task = result.scalar_one_or_none()
    ...
```

### Error Messages

Be specific and actionable:

```python
# ✅ Good
raise HTTPException(
    status_code=400,
    detail="Task ID must be a valid UUID, got: abc123"
)

# ❌ Bad
raise HTTPException(status_code=400, detail="Invalid input")
```

---

## Common Pitfalls

### 1. Forgetting Eager Loading

**Problem:** Accessing relationships in a loop without eager loading.

**Solution:** Always use `selectinload()` or `joinedload()` for relationships.

### 2. Missing .unique() with joinedload

**Problem:** `joinedload()` with one-to-many returns duplicate parent rows.

**Solution:** Call `.unique()` on results:
```python
result = await db.execute(
    select(Parent).options(joinedload(Parent.children))
)
parents = result.unique().scalars().all()  # ← Must call .unique()
```

### 3. Using class Config with Pydantic v2

**Problem:** Deprecation warnings about `class Config:`.

**Solution:** Use `model_config = ConfigDict(...)` instead.

### 4. Missing Response Models

**Problem:** Exposing internal fields or inconsistent responses.

**Solution:** Always define and use response models:
```python
@router.get("/items", response_model=List[ItemResponse])
```

### 5. No Test Coverage for New Endpoints

**Problem:** Production code without tests.

**Solution:** Write tests BEFORE deploying. See [Testing](#testing) section.

---

## Quick Reference

### Starting a New Endpoint

1. ✅ Define Pydantic request/response models with `ConfigDict`
2. ✅ Write the endpoint with proper type hints
3. ✅ Add eager loading if accessing relationships
4. ✅ Write tests (success, failure, edge cases)
5. ✅ Run tests and check coverage
6. ✅ Update API documentation if needed

### Before Committing

- [ ] All tests pass locally (`pytest`)
- [ ] Type hints added (`mypy app/` should pass)
- [ ] No N+1 queries (check with `echo=True` in dev)
- [ ] Pydantic models use v2 patterns
- [ ] Test coverage >85% for new code

---

## Resources

- [SQLAlchemy Relationship Loading](https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html)
- [SQLite WAL Mode](https://www.sqlite.org/wal.html)
- [Pydantic v2 Migration](https://docs.pydantic.dev/latest/migration/)
- [FastAPI Best Practices](https://fastapi.tiangolo.com/tutorial/)
- [Project KNOWN_ISSUES.md](KNOWN_ISSUES.md) — Current issues and tech debt

---

**Questions?** Check [docs/README.md](README.md) for full documentation index.

**Last Updated:** 2026-02-14
