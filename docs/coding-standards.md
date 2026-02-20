# Coding Standards

**Last Updated:** 2026-02-20

Guidelines for code quality, testing, and review standards across the lobs-server codebase.

---

## Code Quality Principles

### 1. Clarity Over Cleverness
- Write code that's easy to understand and maintain
- Prefer explicit over implicit
- Use descriptive variable and function names
- Add comments for complex logic, not obvious code

### 2. Consistency
- Follow existing patterns in the codebase
- Match the style of the file you're editing
- Use project conventions (async/await patterns, error handling, etc.)

### 3. Error Handling
- Always handle errors explicitly
- Use try/except blocks for external calls (API, DB, file I/O)
- Log errors with context (what failed, why, relevant IDs)
- Return meaningful error responses to clients

### 4. Type Safety
- Use type hints for all function signatures
- Validate input data with Pydantic models
- Use Literal types for known string constants
- Let mypy catch type errors early

---

## Python Style

### General
- Follow PEP 8 with a few exceptions:
  - Line length: 100 characters (not 79)
  - Use f-strings for string formatting
  - Use trailing commas in multi-line structures

### Imports
- Standard library first
- Third-party packages second
- Local imports third
- Alphabetize within each group
- Use absolute imports, not relative

```python
# Good
import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.database import get_db
from app.models import Task
```

### Async/Await
- Use `async def` for all route handlers
- Use `await` for all async operations (DB, API calls)
- Don't block the event loop (no `time.sleep`, use `asyncio.sleep`)
- Use `AsyncSession` for database operations

```python
# Good
async def get_tasks(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Task))
    return result.scalars().all()

# Bad - blocking
def get_tasks(db: Session = Depends(get_db)):
    return db.query(Task).all()  # blocks event loop
```

---

## Database Patterns

### 1. Avoid N+1 Queries
Use eager loading with `selectinload()` or `joinedload()` when accessing relationships.

**See:** [BEST_PRACTICES.md](BEST_PRACTICES.md#avoiding-n1-queries) for detailed examples.

```python
# Good - eager load
from sqlalchemy.orm import selectinload

result = await db.execute(
    select(Project).options(selectinload(Project.tasks))
)
projects = result.scalars().all()

# Bad - N+1 query
projects = await db.execute(select(Project))
for project in projects.scalars():
    tasks = await db.execute(select(Task).where(Task.project_id == project.id))
```

### 2. Use WAL Mode for SQLite
- Set `PRAGMA journal_mode=WAL` for better concurrency
- Set `PRAGMA busy_timeout=5000` to handle lock contention
- See [BEST_PRACTICES.md](BEST_PRACTICES.md#sqlite-wal-mode-and-concurrency) for setup

### 3. Session Management
- Use dependency injection for database sessions
- Always use `AsyncSession` from `get_db()` dependency
- Don't create sessions manually in route handlers
- Let FastAPI handle session lifecycle (auto-commit on success, rollback on error)

---

## API Design

### Request/Response Models
- Use Pydantic models for all request/response bodies
- Separate models for create, update, and response
- Use Pydantic v2 patterns (not v1 deprecated APIs)

```python
# Good - Pydantic v2
from pydantic import BaseModel, ConfigDict

class TaskCreate(BaseModel):
    title: str
    description: str | None = None
    
    model_config = ConfigDict(from_attributes=True)

# Bad - Pydantic v1 (deprecated)
class TaskCreate(BaseModel):
    title: str
    
    class Config:
        orm_mode = True  # deprecated
```

### Endpoint Naming
- Use plural nouns for collections: `/api/tasks`
- Use singular for single resource: `/api/tasks/{id}`
- Use verbs for actions: `/api/tasks/{id}/complete`
- Group by resource: `/api/projects/{id}/tasks`

### Status Codes
- `200 OK` - Successful GET, PUT, PATCH
- `201 Created` - Successful POST
- `204 No Content` - Successful DELETE
- `400 Bad Request` - Validation error
- `404 Not Found` - Resource doesn't exist
- `500 Internal Server Error` - Unexpected error

---

## Testing Standards

### Coverage Requirements
- **Critical paths:** 100% coverage (auth, data mutations, money/cost tracking)
- **API endpoints:** Test happy path + major error cases
- **New features:** Add tests before merging
- **Bug fixes:** Add regression test

### Test Structure
- Use pytest fixtures for common setup
- One test function per scenario
- Use descriptive test names: `test_endpoint_scenario_expected_result`
- Group related tests in classes

```python
# Good
class TestTaskAPI:
    async def test_create_task_success(self, client, db):
        # Arrange
        payload = {"title": "Test task"}
        
        # Act
        response = await client.post("/api/tasks", json=payload)
        
        # Assert
        assert response.status_code == 201
        assert response.json()["title"] == "Test task"
    
    async def test_create_task_missing_title_returns_400(self, client):
        response = await client.post("/api/tasks", json={})
        assert response.status_code == 400
```

### Running Tests
```bash
# All tests
pytest

# Specific file
pytest tests/test_tasks.py

# Specific test
pytest tests/test_tasks.py::test_create_task_success

# With coverage
pytest --cov=app --cov-report=html
```

**See:** [TESTING.md](TESTING.md) for complete testing guide.

---

## Git Workflow

### Commits
- Write clear, descriptive commit messages
- Use conventional commits format:
  - `feat:` - New feature
  - `fix:` - Bug fix
  - `docs:` - Documentation only
  - `refactor:` - Code restructuring (no behavior change)
  - `test:` - Adding or updating tests
  - `chore:` - Build, config, or tooling changes

```bash
# Good
git commit -m "feat: add provider health tracking"
git commit -m "fix: prevent N+1 query in activity endpoint"
git commit -m "docs: update ARCHITECTURE.md with orchestrator flow"

# Bad
git commit -m "stuff"
git commit -m "wip"
```

### Branches
- Work directly on `main` for most changes (small team, high trust)
- Use feature branches for large/risky changes
- Delete branches after merging

### Pull Requests
- Optional for this project (small team)
- Use for complex changes that benefit from review
- Include context: what changed, why, how to test

---

## Logging

### Log Levels
- `DEBUG` - Detailed diagnostic info (not in production)
- `INFO` - Important events (task started, agent spawned, etc.)
- `WARNING` - Something unexpected but handled
- `ERROR` - Error that prevented operation from completing
- `CRITICAL` - System-level failure

### Best Practices
- Include context in log messages (task_id, user_id, etc.)
- Log at appropriate level
- Don't log sensitive data (tokens, passwords, full error traces in INFO)
- Use structured logging for easy parsing

```python
# Good
logger.info(f"Task {task.id} assigned to agent {agent_type}")
logger.error(f"Failed to spawn worker for task {task.id}: {error}")

# Bad
logger.info("Task assigned")  # no context
logger.debug(f"Token: {secret_token}")  # sensitive data
```

---

## Code Review Checklist

When reviewing code (AI or human):

- [ ] **Tests** - Does it include tests? Do they pass?
- [ ] **Type safety** - Are type hints present and correct?
- [ ] **Error handling** - Are errors caught and logged?
- [ ] **N+1 queries** - Are relationships eager-loaded?
- [ ] **Pydantic v2** - Using v2 patterns (not deprecated v1 APIs)?
- [ ] **Async/await** - All IO operations async?
- [ ] **Logging** - Appropriate log level and context?
- [ ] **Documentation** - Is AGENTS.md updated for API changes?
- [ ] **Backwards compatibility** - Will this break existing clients?

---

## Anti-Patterns to Avoid

### 1. God Functions
Don't write 200+ line functions. Break them down into smaller, focused functions.

### 2. Magic Strings
Use constants or enums for repeated string values.

```python
# Good
from enum import Enum

class TaskStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"

# Bad
status = "pending"  # typo risk
```

### 3. Silent Failures
Don't catch exceptions without logging or handling them.

```python
# Good
try:
    result = await risky_operation()
except Exception as e:
    logger.error(f"Operation failed: {e}")
    raise

# Bad
try:
    result = await risky_operation()
except:
    pass  # what happened?
```

### 4. Blocking the Event Loop
Don't use synchronous IO in async functions.

```python
# Good
await asyncio.sleep(1)
async with httpx.AsyncClient() as client:
    response = await client.get(url)

# Bad
time.sleep(1)  # blocks event loop
requests.get(url)  # blocks event loop
```

---

## Resources

- **Pydantic v2:** [https://docs.pydantic.dev/latest/](https://docs.pydantic.dev/latest/)
- **FastAPI:** [https://fastapi.tiangolo.com/](https://fastapi.tiangolo.com/)
- **SQLAlchemy async:** [https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- **Pytest:** [https://docs.pytest.org/](https://docs.pytest.org/)

---

## See Also

- **[BEST_PRACTICES.md](BEST_PRACTICES.md)** - N+1 prevention, SQLite optimization, Pydantic v2 patterns
- **[TESTING.md](TESTING.md)** - Complete testing guide
- **[ARCHITECTURE.md](../ARCHITECTURE.md)** - System architecture overview
- **[CONTRIBUTING.md](../CONTRIBUTING.md)** - Contribution guide for external contributors
