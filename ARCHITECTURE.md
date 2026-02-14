# lobs-server Architecture

**Last Updated:** February 14, 2026

This document describes the design and architecture of lobs-server, the central backend for Lobs Mission Control.

---

## Table of Contents

- [System Overview](#system-overview)
- [Technology Stack](#technology-stack)
- [Architecture Diagram](#architecture-diagram)
- [Core Components](#core-components)
- [Data Flow](#data-flow)
- [API Design](#api-design)
- [Database Schema](#database-schema)
- [Orchestration](#orchestration)
- [Authentication](#authentication)
- [Real-time Communication](#real-time-communication)
- [Testing Strategy](#testing-strategy)
- [Performance Considerations](#performance-considerations)
- [Future Improvements](#future-improvements)

---

## System Overview

lobs-server is a FastAPI-based backend providing:
- **Task & Project Management** with kanban workflow
- **Memory System** for long-term knowledge storage
- **Topics/Knowledge** organization for research workspaces
- **Chat** via WebSocket for real-time agent communication
- **Orchestrator** for autonomous task routing and execution
- **Calendar** for events, deadlines, and scheduling
- **System Health** monitoring and cost tracking

**Key Design Principles:**
- **REST-first API** — All operations accessible via HTTP
- **SQLite simplicity** — Single-file database with WAL mode for concurrency
- **Async everywhere** — FastAPI + SQLAlchemy async for I/O efficiency
- **Bearer token auth** — Simple, stateless authentication
- **Agent-friendly** — Designed for both human and AI agent clients

---

## Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Web Framework** | FastAPI 0.109+ | REST API, WebSocket, validation |
| **ASGI Server** | Uvicorn | Production server with auto-reload |
| **Database** | SQLite + SQLAlchemy 2.0 | Async ORM, migrations |
| **Validation** | Pydantic v2 | Request/response models, config |
| **Testing** | pytest + httpx | Async test suite |
| **Task Queue** | In-process scheduler | Simple cron-like scheduling |

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        Clients                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ Mission      │  │ Mobile       │  │ OpenClaw     │      │
│  │ Control      │  │ App          │  │ Agents       │      │
│  │ (macOS)      │  │ (iOS)        │  │              │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│         │                  │                  │              │
│         └──────────────────┼──────────────────┘              │
│                            │                                 │
│                   Bearer Token Auth                          │
└────────────────────────────┼─────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    lobs-server (FastAPI)                     │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │                    API Layer                            │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │ │
│  │  │ /tasks   │ │ /memory  │ │ /chat    │ │ /calendar│  │ │
│  │  │ /projects│ │ /topics  │ │ /inbox   │ │ /status  │  │ │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘  │ │
│  └───────┼────────────┼─────────────┼────────────┼────────┘ │
│          │            │             │            │          │
│  ┌───────▼────────────▼─────────────▼────────────▼────────┐ │
│  │              Business Logic Layer                       │ │
│  │  ┌─────────────────────────────────────────────────┐   │ │
│  │  │          Orchestrator (app/orchestrator/)       │   │ │
│  │  │  ┌──────────┐ ┌──────────┐ ┌──────────┐        │   │ │
│  │  │  │Scheduler │ │ Scanner  │ │  Worker  │        │   │ │
│  │  │  │(Cron)    │ │(Poller)  │ │ Manager  │        │   │ │
│  │  │  └────┬─────┘ └────┬─────┘ └────┬─────┘        │   │ │
│  │  └───────┼────────────┼─────────────┼──────────────┘   │ │
│  └──────────┼────────────┼─────────────┼──────────────────┘ │
│             │            │             │                    │
│  ┌──────────▼────────────▼─────────────▼──────────────────┐ │
│  │              Data Access Layer (SQLAlchemy)            │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │ │
│  │  │ Task     │ │ Memory   │ │ Chat     │ │ Event    │  │ │
│  │  │ Model    │ │ Model    │ │ Model    │ │ Model    │  │ │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘  │ │
│  └───────┼────────────┼─────────────┼────────────┼────────┘ │
└──────────┼────────────┼─────────────┼────────────┼──────────┘
           │            │             │            │
           ▼            ▼             ▼            ▼
    ┌─────────────────────────────────────────────────┐
    │           SQLite Database (lobs.db)             │
    │           (WAL mode, 30s busy timeout)          │
    └─────────────────────────────────────────────────┘
```

---

## Core Components

### 1. API Routers (`app/routers/`)

Organize endpoints by domain:

- **`tasks.py`** — Task CRUD, status updates, assignment
- **`projects.py`** — Project management, task grouping
- **`chat.py`** — WebSocket chat, sessions, messaging
- **`memory.py`** — Memory capture, search, retrieval
- **`topics.py`** — Knowledge organization, document management
- **`inbox.py`** — Triage queue for proposals and documents
- **`calendar.py`** — Events, deadlines, recurring schedules
- **`status.py`** — System health, activity timeline
- **`orchestrator_api.py`** — Orchestrator control (pause/resume)

### 2. Orchestrator (`app/orchestrator/`)

Autonomous task execution system:

**`scheduler.py`**
- Cron-like job scheduler
- Creates recurring tasks (daily summaries, weekly reviews)
- Uses `work_state='not_started'` for scanner integration

**`scanner.py`**
- Polls for ready tasks (`work_state='ready'` or `'not_started'`)
- Routes tasks via project-manager agent
- Spawns OpenClaw worker processes

**`worker.py`**
- Manages worker lifecycle
- Monitors process health
- Handles task completion and failures

**Key Pattern:** Single-writer (only orchestrator commits task state changes)

### 3. Database Models (`app/models.py`)

SQLAlchemy async models:

```python
class Task(Base):
    __tablename__ = 'tasks'
    id, title, description, status, work_state
    project_id, owner, created_at, updated_at

class Project(Base):
    __tablename__ = 'projects'
    id, name, slug, description, status

class Memory(Base):
    __tablename__ = 'memories'
    id, content, tags, created_at

class Topic(Base):
    __tablename__ = 'topics'
    id, name, description, created_at
    
class Document(Base):
    __tablename__ = 'documents'
    id, topic_id, title, content, created_at

class ChatSession(Base):
    __tablename__ = 'chat_sessions'
    session_key, name, created_at

class ChatMessage(Base):
    __tablename__ = 'chat_messages'
    id, session_key, role, content, created_at

class Event(Base):
    __tablename__ = 'events'
    id, title, start_time, end_time, event_type
```

### 4. Authentication (`app/dependencies.py`)

```python
async def verify_token(credentials: HTTPAuthorizationCredentials):
    # Bearer token validation
    # All /api/* endpoints except /api/health require auth
```

**Token Management:**
- Tokens stored in `~/.lobs/tokens.json`
- Generate with `bin/generate_token.py`
- No expiration (stateless design)

### 5. Database Session (`app/database.py`)

```python
# WAL mode for concurrent access
engine = create_async_engine(
    "sqlite+aiosqlite:///lobs.db",
    connect_args={"timeout": 30}
)

# Enable WAL mode
await conn.execute(text("PRAGMA journal_mode=WAL"))
await conn.execute(text("PRAGMA busy_timeout=30000"))
```

---

## Data Flow

### Task Creation Flow

```
1. Client → POST /api/tasks
2. Router validates request (Pydantic)
3. Check auth token
4. Create Task model instance
5. Save to database (SQLAlchemy)
6. Return TaskResponse (Pydantic)
```

### Orchestrator Task Processing Flow

```
1. Scheduler creates recurring task
   └─ Sets work_state='not_started'

2. Scanner polls for ready tasks
   └─ Finds tasks with work_state='ready' or 'not_started'
   
3. Scanner routes via project-manager
   └─ Determines best agent for task
   
4. Scanner spawns OpenClaw worker
   └─ Launches subprocess with task context
   
5. Worker executes task
   └─ Updates status via API
   
6. Scanner monitors completion
   └─ Handles success/failure/escalation
```

### WebSocket Chat Flow

```
1. Client → WS /api/chat/ws?session_key=xyz
2. Connection established
3. Client sends message → Server receives
4. Server broadcasts to all session clients
5. Server stores in ChatMessage table
6. All clients receive message
```

---

## API Design

### RESTful Conventions

- **GET** — Retrieve resources
- **POST** — Create resources
- **PATCH** — Partial update
- **DELETE** — Remove resources

### Response Format

Success:
```json
{
  "id": "abc123",
  "title": "Task title",
  "status": "active"
}
```

Error:
```json
{
  "detail": "Task not found"
}
```

### Snake Case Convention

- **API:** Returns `snake_case` JSON
- **Python:** Uses `snake_case` internally
- **Swift clients:** Auto-convert to `camelCase` via `JSONDecoder.convertFromSnakeCase`

---

## Database Schema

### Core Tables

**`tasks`** — Central task management table

| Field | Type | Purpose |
|-------|------|---------|
| `id` | String | Unique identifier (8-char hex) |
| `title` | String | Task title |
| `status` | String | User-facing status (`inbox`, `active`, `waiting_on`, `completed`, `rejected`) |
| `work_state` | String | Orchestrator execution state (see below) |
| `review_state` | String | Approval workflow state |
| `owner` | String | Task owner (`lobs`, `rafe`, agent name) |
| `agent` | String | Assigned agent type (`programmer`, `writer`, etc.) |
| `project_id` | String | Foreign key to `projects.id` |
| `notes` | Text | Task description and details |
| `created_at` | DateTime | Creation timestamp |
| `updated_at` | DateTime | Last modification timestamp |
| `started_at` | DateTime | When work began |
| `finished_at` | DateTime | When work completed |

**Other tables:** `projects`, `memories`, `topics`, `documents`, `inbox_items`, `chat_sessions`, `chat_messages`, `events`, `api_tokens`, `worker_runs`, `agent_states`

### Task State Fields

Tasks have **two separate state dimensions**:

#### 1. `status` — User-Facing Kanban State

| Value | Meaning | UI Column |
|-------|---------|-----------|
| `inbox` | Needs triage | Inbox |
| `active` | Ready to work on | Active |
| `waiting_on` | Blocked by dependency | Waiting On |
| `completed` | Work finished | Completed |
| `rejected` | Declined or cancelled | (archived) |

Users move tasks between these columns in Mission Control UI.

#### 2. `work_state` — Orchestrator Execution State

| Value | Meaning | Orchestrator Action |
|-------|---------|---------------------|
| `not_started` | Ready for pickup (default) | Scanner includes in eligible tasks |
| `ready` | Explicitly marked ready | Scanner includes in eligible tasks |
| `in_progress` | Worker actively working | Monitor tracks for timeouts |
| `blocked` | Cannot proceed | Skipped by scanner until unblocked |
| `completed` | Work finished | Finalize task, run approvals |

**Key insight:** A task can be `status='active'` (user moved it to "Active" column) but `work_state='blocked'` (orchestrator can't work on it yet). The orchestrator uses `work_state`, not `status`, to determine eligibility.

**Scanner query:**
```sql
SELECT * FROM tasks 
WHERE status = 'active' 
  AND work_state IN ('not_started', 'ready')
  AND agent IS NOT NULL
```

### WAL Mode Benefits

- **Concurrent reads:** Multiple readers don't block
- **Single writer:** Writes don't block readers (mostly)
- **Crash recovery:** Better durability than rollback journal

### Indexing Strategy

```sql
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_project ON tasks(project_id);
CREATE INDEX idx_tasks_work_state ON tasks(work_state);
CREATE INDEX idx_chat_messages_session ON chat_messages(session_key);
```

---

## Orchestration

### Scheduler (`app/orchestrator/scheduler.py`)

**Cron Jobs:**
- Daily summary (9 AM)
- Weekly review (Monday 9 AM)
- Monthly planning (1st of month)

**Implementation:**
```python
@scheduler.scheduled_job('cron', hour=9, minute=0)
async def daily_summary():
    await create_task(
        title="Daily Summary",
        owner="writer",
        work_state="not_started"
    )
```

### Scanner (`app/orchestrator/scanner.py`)

**Polling Cycle:**
1. Query for ready tasks
2. Route via project-manager
3. Spawn workers
4. Monitor health
5. Repeat every 30s

**Worker Spawning:**
```python
process = await asyncio.create_subprocess_exec(
    "openclaw", "run",
    "--agent", agent_type,
    "--task-id", task_id,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE
)
```

### Tiered Approval System

Tasks flow through approval gates:

1. **Auto-approve** — Trusted patterns (docs, summaries)
2. **Human review** — Default for most work
3. **Escalate** — High-risk or blocked tasks

See [docs/tiered-approval-system.md](docs/tiered-approval-system.md)

---

## Authentication

### Token Generation

```bash
python bin/generate_token.py my-token-name
```

Generates:
```json
{
  "name": "my-token-name",
  "token": "z5mr-WWjPxAAHvRd2ZULm7HLNW1oRubXmcMiBJoEmsU",
  "created_at": "2026-02-14T10:00:00Z"
}
```

### Token Validation

```python
# All routes use dependency injection
@router.get("/api/tasks")
async def list_tasks(token: str = Depends(verify_token)):
    # token is validated before this runs
```

---

## Real-time Communication

### WebSocket Chat

**Endpoint:** `ws://localhost:8000/api/chat/ws?session_key=xyz`

**Message Format:**
```json
{
  "role": "user",
  "content": "Hello, agent!"
}
```

**Broadcast Pattern:**
- Server maintains `active_connections: Dict[str, List[WebSocket]]`
- Messages sent to all clients in same session
- Persisted to database for history

**Reconnection Strategy:**
- Client responsibility (exponential backoff)
- Server doesn't enforce reconnection logic
- See [docs/research-findings.md](docs/../research-findings.md) for best practices

---

## Testing Strategy

### Test Structure

```
tests/
├── conftest.py              # Shared fixtures
├── test_tasks.py            # Task CRUD
├── test_projects.py         # Project management
├── test_chat.py             # Chat & WebSocket (⚠️ WS tests broken)
├── test_orchestrator.py     # Orchestration logic
└── ...
```

### Test Database

- **In-memory SQLite:** `:memory:` for test isolation
- **Fresh schema:** Created per test session
- **Async fixtures:** Support async tests

### Running Tests

```bash
# All tests
python -m pytest -v

# Specific file
python -m pytest tests/test_tasks.py -v

# Skip integration tests
python -m pytest -m "not integration" -v
```

### Test Coverage

- **Current:** 97.8% (269/275 tests passing)
- **Broken:** 6 WebSocket tests (httpx doesn't support WebSocket)
- **Gap:** Scheduler ↔ scanner integration

See [docs/TESTING.md](docs/TESTING.md) for complete guide.

---

## Performance Considerations

### Bottlenecks

1. **SQLite concurrency** — WAL mode mitigates; consider PostgreSQL for heavy loads
2. **WebSocket scaling** — In-memory connections don't scale across processes
3. **Synchronous cron** — Blocking scheduler; consider async scheduler library

### Optimization Strategies

- **Database indexes** — On frequently queried columns
- **Connection pooling** — SQLAlchemy handles this
- **Async I/O** — All routes and DB calls are async
- **Response caching** — Not implemented (stateless API design)

### Scalability Limits

- **SQLite:** Good for <100 concurrent writers, unlimited readers
- **WebSocket:** Single-process limit (~10k connections)
- **Orchestrator:** 3 concurrent workers (configurable)

**When to migrate:**
- PostgreSQL for >1000 tasks/day
- Redis for distributed WebSocket
- Celery for distributed task queue

---

## Future Improvements

### Short-term

1. **Fix WebSocket tests** — Migrate to Starlette TestClient
2. **Add ARCHITECTURE.md** ✅ (this document)
3. **Pydantic v2 migration** — Replace deprecated `Config` class
4. **Pytest marker registration** — Fix unknown marker warnings

### Medium-term

1. **GraphQL API** — For flexible client queries
2. **File attachments** — Document/image uploads
3. **Audit log** — Track all state changes
4. **API versioning** — `/api/v2/` for breaking changes

### Long-term

1. **PostgreSQL support** — For production scale
2. **Distributed orchestrator** — Multi-worker coordination
3. **Plugin system** — Custom agent types
4. **OAuth integration** — Google Calendar, GitHub, etc.

---

## Related Documentation

- **[AGENTS.md](AGENTS.md)** — Complete API reference and development guide
- **[docs/README.md](docs/README.md)** — All design documents and guides
- **[docs/TESTING.md](docs/TESTING.md)** — Testing guide
- **[docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md)** — Current issues and limitations
- **[docs/project-manager-agent.md](docs/project-manager-agent.md)** — Task routing design
- **[docs/tiered-approval-system.md](docs/tiered-approval-system.md)** — Approval workflow

---

**Document Status:** ✅ Current (February 14, 2026)  
**Next Review:** When major architectural changes land
