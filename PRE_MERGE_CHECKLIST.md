# Pre-Merge Review Checklist

**Purpose:** Use this checklist before creating handoffs to catch common issues early.  
**For:** Programmer, Architect, and other agents before submitting work  
**When:** Before marking task complete, before creating handoff  

This checklist codifies recurring code review findings to reduce review burden and improve code quality.

---

## 🔴 Critical Checks (Must Pass)

### 1. Tests Exist and Pass
- [ ] **Tests written for all new/changed code**
- [ ] **Tests actually test the right things** (not just coverage theater)
- [ ] All tests pass locally (`python -m pytest -v`)
- [ ] Test coverage ≥80% for new modules
- [ ] Integration tests cover critical paths
- [ ] Edge cases tested (empty input, None values, invalid data)

**Common mistake:** Writing code without tests, or tests that only check happy path.

---

### 2. Error Handling Present
- [ ] **All external calls wrapped in try/except** (DB, API, file I/O)
- [ ] **Errors logged with context** (what failed, why, relevant IDs)
- [ ] **Errors don't silently swallow failures**
- [ ] Graceful degradation where appropriate
- [ ] HTTP errors return proper status codes (400, 404, 500)
- [ ] Database transaction rollback on error

**Common mistake:** Missing try/except around API calls, or catching exceptions without logging.

**Example:**
```python
# ❌ BAD
async def get_task(task_id: int, db: AsyncSession):
    task = await db.execute(select(Task).where(Task.id == task_id))
    return task.scalar_one()  # Will crash if not found

# ✅ GOOD
async def get_task(task_id: int, db: AsyncSession):
    try:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            logger.warning("Task not found: %s", task_id)
            raise HTTPException(status_code=404, detail="Task not found")
        return task
    except SQLAlchemyError as e:
        logger.error("Database error fetching task %s: %s", task_id, e)
        raise HTTPException(status_code=500, detail="Database error")
```

---

### 3. Input Validation
- [ ] **All user inputs validated** (type, format, length, range)
- [ ] Pydantic models define constraints
- [ ] SQL injection prevented (use parameterized queries, not string concat)
- [ ] XSS prevented (escape user input in HTML responses)
- [ ] File uploads validated (size, type, content)
- [ ] Rate limiting on expensive operations

**Common mistake:** Trusting user input without validation.

**Example:**
```python
# ❌ BAD
class TaskCreate(BaseModel):
    title: str
    description: str

# ✅ GOOD
from pydantic import Field, field_validator

class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    
    @field_validator('title')
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError('Title cannot be empty')
        return v.strip()
```

---

### 4. No Hardcoded Secrets
- [ ] **No API keys, tokens, passwords in code**
- [ ] Secrets loaded from environment variables or config
- [ ] No sensitive data in logs (redact tokens, passwords)
- [ ] No credentials in test fixtures (use mocks)

**Common mistake:** Hardcoding tokens for testing, forgetting to remove.

---

### 5. Database Transactions Correct
- [ ] **Single DB session per request** (no independent sessions unless documented)
- [ ] Commits and rollbacks handled properly
- [ ] No race conditions from concurrent access
- [ ] Locks used where needed (row-level, pessimistic)
- [ ] No N+1 queries (use `joinedload()` or `selectinload()`)

**Common mistake:** Creating independent DB sessions for "cleanup" operations, introducing race conditions.

**Example:**
```python
# ❌ BAD - Independent session creates race condition
async def _cleanup_task(task_id: int):
    db = AsyncSessionLocal()  # NEW session!
    task = await db.execute(select(Task).where(Task.id == task_id))
    # ... main request might commit conflicting changes

# ✅ GOOD - Use passed session
async def _cleanup_task(task_id: int, db: AsyncSession):
    task = await db.execute(select(Task).where(Task.id == task_id))
    # ... uses same session as main request
```

---

### 6. Integration Complete
- [ ] **All old code removed or deprecated** (no duplicate functions)
- [ ] **Imports updated** (no imports from deleted modules)
- [ ] Old files deleted after migration
- [ ] CHANGELOG updated with migration notes
- [ ] All tests updated for new code paths

**Common mistake:** Creating new modules but leaving old code in place, engine still imports old module.

---

## 🟡 Important Checks (Should Pass)

### 7. Type Safety
- [ ] **Type hints on all function parameters and returns**
- [ ] Use `Optional[T]` for nullable types
- [ ] Use `list[T]`, `dict[K, V]` instead of `List`, `Dict` (Python 3.9+)
- [ ] Enums for fixed string values (not magic strings)
- [ ] Pydantic models for structured data

**Example:**
```python
# ❌ BAD
def get_task(task_id):
    return None

# ✅ GOOD
from typing import Optional

async def get_task(task_id: int, db: AsyncSession) -> Optional[Task]:
    result = await db.execute(select(Task).where(Task.id == task_id))
    return result.scalar_one_or_none()
```

---

### 8. Documentation Present
- [ ] **Docstrings on all public functions/classes**
- [ ] Complex logic explained with comments
- [ ] API endpoints documented (summary, description)
- [ ] Module docstrings explain purpose and context
- [ ] README/AGENTS.md updated if behavior changed

**Common mistake:** No docstrings, or docstrings that just repeat the function name.

**Example:**
```python
# ❌ BAD
async def process_task(task_id: int):
    """Process a task."""
    pass

# ✅ GOOD
async def process_task(task_id: int, db: AsyncSession) -> bool:
    """
    Process a task by executing its assigned agent workflow.
    
    Updates task status to 'in_progress', spawns agent worker,
    and records start time. Returns True if spawned successfully.
    
    Args:
        task_id: Database ID of task to process
        db: Database session
        
    Returns:
        True if worker spawned, False if task not eligible
        
    Raises:
        HTTPException: If task not found or agent spawn fails
    """
    pass
```

---

### 9. No Code Duplication (DRY)
- [ ] **No copy-pasted functions** (extract to shared module)
- [ ] Similar logic consolidated
- [ ] Magic numbers/strings extracted to constants
- [ ] Repeated patterns turned into utilities

**Common mistake:** Copy-pasting helper functions across modules instead of extracting to shared util.

**Example:**
```python
# ❌ BAD - Duplicated in 3 router files
def get_orchestrator(request: Request) -> OrchestratorEngine:
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(status_code=503, ...)
    return orchestrator

# ✅ GOOD - Extract to shared module
# app/orchestrator/utils.py
def get_orchestrator(request: Request) -> OrchestratorEngine:
    """Get orchestrator instance from app state."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(
            status_code=503, 
            detail="Orchestrator not running"
        )
    return orchestrator
```

---

### 10. Modern Patterns (No Deprecated Code)
- [ ] **Pydantic v2** (`ConfigDict`, not `class Config`)
- [ ] **Python 3.9+ types** (`list[T]`, not `List[T]`)
- [ ] **Async/await** for all I/O (no blocking calls)
- [ ] **SQLAlchemy 2.0 style** (if applicable)

**Common mistake:** Using Pydantic v1 `orm_mode` instead of v2 `from_attributes`.

**Example:**
```python
# ❌ BAD (Pydantic v1)
class TaskResponse(BaseModel):
    class Config:
        orm_mode = True

# ✅ GOOD (Pydantic v2)
from pydantic import ConfigDict

class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
```

---

### 11. Performance Considered
- [ ] **No N+1 queries** (eager load relationships)
- [ ] Expensive operations cached where appropriate
- [ ] Database indexes on foreign keys
- [ ] Pagination for large result sets
- [ ] Background tasks for slow operations

**Common mistake:** Looping through results and making a DB call for each item.

**Example:**
```python
# ❌ BAD - N+1 query
tasks = await db.execute(select(Task))
for task in tasks.scalars():
    project = await db.execute(select(Project).where(Project.id == task.project_id))
    # ... uses project

# ✅ GOOD - Eager load
from sqlalchemy.orm import joinedload

tasks = await db.execute(
    select(Task).options(joinedload(Task.project))
)
for task in tasks.scalars():
    project = task.project  # Already loaded
```

---

### 12. Logging Informative
- [ ] **Structured logging** with context (use `extra={}`)
- [ ] Log levels used correctly (DEBUG/INFO/WARNING/ERROR)
- [ ] No sensitive data in logs
- [ ] Critical operations logged (start/success/failure)
- [ ] Errors logged with full context

**Example:**
```python
# ❌ BAD
logger.info("Task completed")

# ✅ GOOD
logger.info(
    "Task completed successfully",
    extra={
        "task_id": task.id,
        "project_id": task.project_id,
        "duration_seconds": duration,
        "agent": agent_type
    }
)
```

---

## 🔵 Nice-to-Have Checks

### 13. Schema/Model Consistency
- [ ] Pydantic schemas match database models
- [ ] Response models expose all relevant fields
- [ ] No schema/reality mismatches

---

### 14. Security Headers
- [ ] CORS configured correctly
- [ ] Rate limiting on auth endpoints
- [ ] No CSRF vulnerabilities in state-changing endpoints

---

### 15. Graceful Degradation
- [ ] Fallback behavior when external services fail
- [ ] Informative error messages for users
- [ ] Partial results when possible

---

## How to Use This Checklist

### Before Creating a Handoff
1. **Self-review against this checklist**
2. Fix obvious issues before handing off
3. Include "pre-merge checklist completed" in handoff context

### Before Marking Task Complete
1. Run through all 🔴 Critical checks
2. Address any failures
3. Run through 🟡 Important checks, fix what's reasonable

### As a Reviewer
1. Use this as a starting point for reviews
2. If an issue appears repeatedly, add it to this checklist
3. Reference specific checklist items in review feedback

---

## Common Anti-Patterns

### 1. "Will fix in follow-up"
❌ Don't defer critical checks (tests, error handling, security)  
✅ Fix critical issues before merge, defer only nice-to-haves

### 2. "It works on my machine"
❌ Don't assume environment is correct  
✅ Test with fresh venv, clean database, standard config

### 3. "Tests are too hard to write"
❌ Don't skip tests because mocking is complex  
✅ Refactor code to be testable (dependency injection, smaller functions)

### 4. "No time for documentation"
❌ Don't ship undocumented code  
✅ Write docstring while coding (when context is fresh)

### 5. "Performance optimization can wait"
❌ Don't introduce obvious N+1 queries  
✅ Use eager loading from the start

---

## Project-Specific Patterns

### FastAPI Patterns
- Use `Depends()` for dependency injection (DB sessions, auth)
- Raise `HTTPException` for error responses
- Use response models for serialization

### SQLAlchemy Async
- Always `await` queries
- Use `scalar_one_or_none()` instead of `scalar_one()` (safer)
- Wrap in `try/except` for `SQLAlchemyError`

### OpenClaw Integration
- Worker spawning uses Gateway `/tools/invoke` API
- Session keys use format `task:<task_id>` or `reflection:<id>`
- Always check Gateway availability before spawning

### Orchestrator
- Tasks have two state fields: `work_state` (execution), `approval_state` (review)
- Use `agent_tracker` to mark agent working/completed/failed
- Record failures in `circuit_breaker` for health tracking

---

## Quick Reference: Review Priorities

| Priority | What | Why |
|----------|------|-----|
| 🔴 Critical | Tests, error handling, security, transactions | Bugs, data loss, security issues |
| 🟡 Important | Types, docs, performance, DRY | Maintainability, future bugs |
| 🔵 Nice-to-have | Consistency, optimization, style | Code quality, polish |

---

## Updating This Checklist

This is a living document. When you find recurring issues in reviews:

1. Add item to appropriate section
2. Include example of bad/good pattern
3. Note which project patterns it relates to
4. Commit with message: `docs: add <issue> to pre-merge checklist`

---

**Last Updated:** 2026-02-22  
**Source:** Code review findings from initiatives #35, #62, KNOWN_ISSUES.md  
**Maintainers:** Reviewer agents, Programmer agents
