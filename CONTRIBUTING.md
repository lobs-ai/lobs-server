# Contributing to lobs-server

**Last Updated:** 2026-02-14  
**For:** Developers, AI agents, contributors

Guide for working on the lobs-server backend (FastAPI + Python).

---

## Quick Start

### Prerequisites

- Python 3.11+
- Git with SSH configured
- Virtual environment tool (venv or similar)

### First Setup

```bash
# Clone repository
git clone <repository-url>
cd lobs-server

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the server
uvicorn app.main:app --reload --port 8000
```

### Verify Setup

```bash
# Server should be running at http://localhost:8000
curl http://localhost:8000/api/health

# Check API documentation
open http://localhost:8000/docs  # FastAPI auto-generated docs
```

---

## Project Structure

```
lobs-server/
├── app/
│   ├── main.py              # FastAPI app initialization
│   ├── database.py          # SQLAlchemy setup, database connection
│   ├── models.py            # Database models (SQLAlchemy ORM)
│   ├── routers/             # API route handlers
│   │   ├── tasks.py         # Task management endpoints
│   │   ├── projects.py      # Project CRUD
│   │   ├── inbox.py         # Inbox processing
│   │   ├── topics.py        # Topics/Knowledge system
│   │   ├── calendar.py      # Events and scheduling
│   │   ├── chat.py          # Chat + WebSocket
│   │   ├── status.py        # System status
│   │   ├── tracker.py       # Work tracker
│   │   └── ...              # (19 total routers)
│   ├── orchestrator/        # Agent orchestration
│   │   ├── scheduler.py     # Task scheduling
│   │   ├── scanner.py       # Task scanning and assignment
│   │   ├── project_manager.py # PM agent coordination
│   │   └── ...
│   └── agents/              # Agent-specific logic
├── tests/                   # Test suite (pytest)
├── docs/                    # Documentation
│   ├── README.md            # Documentation index
│   ├── KNOWN_ISSUES.md      # Technical debt tracking
│   ├── TESTING.md           # Testing guide
│   └── *.md                 # Feature designs and docs
├── requirements.txt         # Python dependencies
├── pyproject.toml           # Project metadata
└── README.md                # Project overview
```

---

## Development Workflow

### 1. Starting the Server

```bash
# Development mode (auto-reload on file changes)
uvicorn app.main:app --reload --port 8000

# Production mode
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 2. Database Migrations

**Current setup:** SQLite with SQLAlchemy ORM

```python
# Database lives at: lobs.db (created automatically on first run)

# Schema changes require manual migration:
# 1. Update models in app/models.py
# 2. Add migration logic in app/database.py or a dedicated migration script
# 3. Test migration locally
# 4. Document in commit message
```

**Note:** No Alembic currently configured. Consider adding for production.

### 3. Adding a New API Endpoint

**Example:** Adding `/api/example`

1. **Create router file** (or add to existing router):
   ```python
   # app/routers/example.py
   from fastapi import APIRouter, Depends
   from sqlalchemy.ext.asyncio import AsyncSession
   from app.database import get_db
   
   router = APIRouter(prefix="/api/example", tags=["example"])
   
   @router.get("/")
   async def list_examples(db: AsyncSession = Depends(get_db)):
       # Implementation
       return {"examples": []}
   ```

2. **Register router in main.py**:
   ```python
   # app/main.py
   from app.routers import example
   
   app.include_router(example.router)
   ```

3. **Add tests**:
   ```python
   # tests/test_example.py
   import pytest
   from httpx import AsyncClient
   
   @pytest.mark.asyncio
   async def test_list_examples(client: AsyncClient):
       response = await client.get("/api/example/")
       assert response.status_code == 200
   ```

4. **Update AGENTS.md**:
   ```markdown
   | example.py | /api/example | Example resource management |
   ```

5. **Document in docs/** if it's a major feature

### 4. Adding a Database Model

```python
# app/models.py
from sqlalchemy import Column, Integer, String, DateTime
from app.database import Base

class Example(Base):
    __tablename__ = "examples"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)
```

**Remember:**
- Add indexes for frequently queried columns
- Use appropriate data types
- Add relationships using `relationship()` and `ForeignKey`
- Update AGENTS.md with model description

---

## Architecture Decision Records (ADRs)

**When making significant architectural decisions, document them as ADRs.**

ADRs live in the shared memory repository at `~/lobs-shared-memory/docs/decisions/` so they're accessible to all agents and indexed by OpenClaw's memory search.

### When to Write an ADR

Create an ADR when you make a decision that:
- **Affects system architecture or infrastructure** (database choice, deployment model, API design)
- **Has significant tradeoffs** with multiple viable options
- **Would be hard to reverse** once implemented
- **Needs context for future developers/agents** to understand why we chose this path
- **Resolves a long-standing debate** or makes an important technical commitment

**Examples of ADR-worthy decisions:**
- ✅ "Use SQLite vs Postgres" → [ADR-0002](~/lobs-shared-memory/docs/decisions/0002-sqlite-for-primary-database.md)
- ✅ "Embed orchestrator in FastAPI vs external worker pool" → [ADR-0001](~/lobs-shared-memory/docs/decisions/0001-embedded-orchestrator.md)
- ✅ "Five-tier model routing with fallback chains" → [ADR-0004](~/lobs-shared-memory/docs/decisions/0004-five-tier-model-routing.md)
- ❌ "Use pytest instead of unittest" → Too minor, document in comments or docs
- ❌ "Fix bug in task status update" → Document in commit message, not ADR

### How to Create an ADR

1. **Copy the template:**
   ```bash
   cd ~/lobs-shared-memory/docs/decisions/
   cp 0000-template.md NNNN-short-kebab-title.md
   # Use next number in sequence (check README index)
   ```

2. **Fill in all sections:**
   - **Context** — What problem are you solving? What's the background?
   - **Decision** — What did you decide to do? Be specific.
   - **Consequences** — List positive, negative, and neutral outcomes
   - **Alternatives Considered** — What else did you evaluate? Why did you reject it?
   - **References** — Link to related code, docs, discussions

3. **Update the index:**
   ```bash
   # Edit ~/lobs-shared-memory/docs/decisions/README.md
   # Add your ADR to the index table
   ```

4. **Commit:**
   ```bash
   git add .
   git commit -m "docs: ADR-NNNN <short title>"
   git push
   ```

### ADR Format (Quick Reference)

See [~/lobs-shared-memory/docs/decisions/0000-template.md](~/lobs-shared-memory/docs/decisions/0000-template.md) for the full template.

```markdown
# N. Title

**Date:** YYYY-MM-DD
**Status:** Accepted
**Deciders:** [Who was involved]

## Context
What problem are we solving? What's the background?

## Decision
What are we doing? Be specific and concrete.

## Consequences

### Positive
- What gets better?

### Negative
- What tradeoffs are we accepting?

### Neutral
- What side effects should we be aware of?

## Alternatives Considered

### Option 1: [Name]
- Pros: ...
- Cons: ...
- Why rejected: ...

## References
- Links to code, docs, related ADRs
```

### Existing ADRs

Browse existing decisions at `~/lobs-shared-memory/docs/decisions/` or see the [index](~/lobs-shared-memory/docs/decisions/README.md).

**Recent examples:**
- [ADR-0001: Embedded Task Orchestrator](~/lobs-shared-memory/docs/decisions/0001-embedded-orchestrator.md) — Shows detailed alternatives analysis
- [ADR-0011: Agent Learning System](~/lobs-shared-memory/docs/decisions/0011-agent-learning-system.md) — Example of complex multi-component decision
- [ADR-0014: Risk-Based Initiative Approval](~/lobs-shared-memory/docs/decisions/0014-risk-based-initiative-approval.md) — Shows process design decision

### ADRs Are Immutable

Once accepted, **ADRs should not be edited** (except for typos). If a decision changes:
1. Create a new ADR that supersedes the old one
2. Update the old ADR's status to "Superseded by ADR-NNNN"
3. Link between them

This preserves the **decision history** and prevents losing context about why we made changes.

---

## Testing

See **[docs/TESTING.md](docs/TESTING.md)** for complete testing guide.

### Quick Reference

```bash
# Run all tests
python3 -m pytest -v

# Run specific test file
python3 -m pytest tests/test_tasks.py -v

# Run specific test
python3 -m pytest tests/test_tasks.py::test_create_task -v

# Run with coverage
python3 -m pytest --cov=app tests/

# Skip slow tests
python3 -m pytest -m "not slow"
```

**Current test health:** 97.8% pass rate (269/275 passing)

**Known issues:** WebSocket tests broken (see [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md))

### Security Scanning

Security scanning runs automatically in CI but can be run locally:

```bash
# Install security tools
pip install 'bandit[toml]' safety

# Run Bandit security linter (checks for common Python security issues)
bandit -r app/ -c .bandit --severity-level high --confidence-level medium -f screen

# Run Safety dependency scanner (checks for known vulnerabilities)
safety check --bare 2>/dev/null
```

**Configuration:**
- Bandit config: `.bandit` (YAML format)
- Configured to fail on high-severity issues only
- Excludes: tests, .venv, migrations, cache directories

**CI Integration:**
- Runs automatically on all PRs and pushes to main
- See `.github/workflows/validation.yml`

---

## Common Patterns

### Async Database Access

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db

async def get_task(task_id: int, db: AsyncSession):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    return task
```

### Error Handling

```python
from fastapi import HTTPException

@router.get("/tasks/{task_id}")
async def get_task(task_id: int, db: AsyncSession = Depends(get_db)):
    task = await fetch_task(task_id, db)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task
```

### Pydantic Models for Request/Response

```python
from pydantic import BaseModel, ConfigDict

class TaskCreate(BaseModel):
    title: str
    description: str | None = None

class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # Pydantic v2
    
    id: int
    title: str
    status: str
```

**Note:** Use `ConfigDict(from_attributes=True)` not deprecated `orm_mode=True`

### WebSocket Handling

```python
from fastapi import WebSocket

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            await websocket.send_json({"response": "acknowledged"})
    except WebSocketDisconnect:
        # Clean up
        pass
```

---

## Code Style

### Python Conventions

- **PEP 8** compliance (use `black` formatter recommended)
- **Type hints** for function parameters and returns
- **Async/await** for all database operations
- **Snake case** for variables/functions, **PascalCase** for classes
- **Docstrings** for complex functions

### API Design

- **RESTful** conventions (GET, POST, PUT/PATCH, DELETE)
- **Consistent naming**: plural nouns for collections (`/tasks`, `/projects`)
- **Query parameters** for filtering/pagination
- **Request bodies** use Pydantic models
- **Response models** for serialization

### Database

- **Async SQLAlchemy** for all queries
- **Transactions** for multi-step operations
- **Indexes** on foreign keys and frequently queried columns
- **Migrations** documented in commit messages

---

## Debugging

### Check Server Logs

```bash
# Server logs show requests, errors, and debug info
# Look for:
# - HTTP status codes
# - SQLAlchemy query logs (if enabled)
# - Exception tracebacks
```

### Test Database State

```bash
# SQLite database: lobs.db
sqlite3 lobs.db

# Inspect tables
.tables

# Query data
SELECT * FROM tasks LIMIT 10;

# Exit
.quit
```

### Interactive FastAPI Docs

```bash
# OpenAPI docs with interactive API testing
open http://localhost:8000/docs

# Alternative docs format
open http://localhost:8000/redoc
```

### Enable Debug Mode

```python
# app/main.py
app = FastAPI(debug=True)  # More verbose error messages
```

---

## Common Pitfalls

### ❌ Forgetting async/await

```python
# WRONG
def get_task(task_id, db):
    return db.execute(select(Task).where(Task.id == task_id))

# RIGHT
async def get_task(task_id, db):
    result = await db.execute(select(Task).where(Task.id == task_id))
    return result.scalar_one_or_none()
```

### ❌ Not using dependency injection

```python
# WRONG
@router.get("/tasks")
async def list_tasks():
    db = get_db()  # Creates new session
    ...

# RIGHT
@router.get("/tasks")
async def list_tasks(db: AsyncSession = Depends(get_db)):
    ...  # FastAPI manages session lifecycle
```

### ❌ Using deprecated Pydantic patterns

```python
# WRONG (Pydantic v1)
class TaskResponse(BaseModel):
    class Config:
        orm_mode = True

# RIGHT (Pydantic v2)
from pydantic import ConfigDict

class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
```

### ❌ Blocking operations in async functions

```python
# WRONG
async def process_task(task):
    result = slow_sync_function()  # Blocks event loop!
    
# RIGHT
import asyncio

async def process_task(task):
    result = await asyncio.to_thread(slow_sync_function)  # Run in thread
```

---

## Related Documentation

- **[README.md](README.md)** — Project overview and setup
- **[AGENTS.md](AGENTS.md)** — Complete API reference for AI agents
- **[docs/README.md](docs/README.md)** — Documentation index
- **[docs/TESTING.md](docs/TESTING.md)** — Testing guide
- **[docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md)** — Technical debt and known issues

---

## Getting Help

- Check **[docs/](docs/)** for feature documentation
- Review **[docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md)** for known problems
- Search existing issues and PRs
- Review FastAPI documentation: https://fastapi.tiangolo.com
- Check SQLAlchemy docs: https://docs.sqlalchemy.org

---

*Last reviewed: 2026-02-14*
