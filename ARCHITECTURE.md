# Architecture — lobs-server

High-level overview of the backend system design, data flow, and key components.

**Last Updated:** 2026-02-14

---

## System Overview

lobs-server is the central backend for the Lobs multi-agent system. It provides:

1. **REST API** — Task management, memory, knowledge, chat, calendar, system status
2. **WebSocket Server** — Real-time chat and updates
3. **Task Orchestrator** — Autonomous task execution via AI agent workers
4. **Project Manager** — Intelligent task delegation and approval workflows
5. **Data Storage** — SQLite database for all system state

```
┌─────────────────────────────────────────────────────────┐
│               lobs-server (FastAPI)                     │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │          REST API (app/routers/)                 │  │
│  │  • /api/projects, /api/tasks, /api/inbox        │  │
│  │  • /api/memories, /api/topics, /api/docs        │  │
│  │  • /api/chat, /api/calendar, /api/status        │  │
│  │  • /api/agents, /api/orchestrator               │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │      WebSocket Server (app/routers/chat.py)      │  │
│  │  • Real-time chat messaging                      │  │
│  │  • Live system updates                           │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │     Task Orchestrator (app/orchestrator/)        │  │
│  │  • Scanner → finds eligible tasks                │  │
│  │  • Router → delegates to project-manager         │  │
│  │  • Engine → spawns workers via OpenClaw          │  │
│  │  • Monitor → detects stuck/failed tasks          │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │         Database (SQLite + SQLAlchemy)           │  │
│  │  • Async aiosqlite                               │  │
│  │  • WAL mode for concurrency                      │  │
│  │  • 15+ tables (tasks, projects, memories, ...)   │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
           ┌───────────────────────────────┐
           │   OpenClaw Gateway (agents)   │
           │  • project-manager            │
           │  • programmer, researcher     │
           │  • writer, specialist         │
           └───────────────────────────────┘
```

---

## Directory Structure

```
lobs-server/
├── app/
│   ├── main.py                 # FastAPI app entry point
│   ├── routers/                # API endpoint routers
│   │   ├── projects.py
│   │   ├── tasks.py
│   │   ├── memories.py
│   │   ├── topics.py
│   │   ├── documents.py
│   │   ├── inbox.py
│   │   ├── chat.py            # WebSocket + HTTP chat endpoints
│   │   ├── calendar.py
│   │   ├── status.py
│   │   ├── agents.py
│   │   ├── orchestrator.py
│   │   └── ...
│   ├── orchestrator/           # Task execution engine
│   │   ├── engine.py          # Main polling loop
│   │   ├── scanner.py         # Finds eligible tasks
│   │   ├── router.py          # Delegates to project-manager
│   │   ├── worker.py          # Spawns OpenClaw workers
│   │   ├── monitor_enhanced.py      # Stuck task detection
│   │   ├── escalation_enhanced.py   # Multi-tier failure handling
│   │   ├── circuit_breaker.py       # Infrastructure failure isolation
│   │   ├── agent_tracker.py         # Agent status tracking
│   │   ├── prompter.py              # Task prompt builder
│   │   └── registry.py              # Agent config loader
│   ├── services/
│   │   ├── chat_manager.py    # WebSocket connection management
│   │   └── openclaw_bridge.py # Webhook handler for agent responses
│   ├── models.py              # SQLAlchemy database models
│   ├── schemas.py             # Pydantic request/response schemas
│   ├── database.py            # Database session management
│   ├── auth.py                # Bearer token authentication
│   └── config.py              # Settings (from environment)
├── bin/
│   ├── run                    # Start server script
│   ├── generate_token.py      # Create API tokens
│   ├── list_tokens.py         # List active tokens
│   ├── revoke_token.py        # Revoke tokens
│   └── agent-scripts/         # Shared agent helper scripts
│       ├── lobs-tasks         # Task management CLI
│       ├── lobs-inbox         # Inbox CLI
│       ├── lobs-status        # System status CLI
│       └── ...
├── data/
│   └── lobs.db               # SQLite database
├── tests/                    # Pytest test suite
├── docs/                     # Documentation
└── requirements.txt          # Python dependencies
```

---

## Core Components

### 1. REST API (app/routers/)

**Purpose:** Expose all system functionality via HTTP endpoints

**Authentication:** Bearer token required for all `/api/*` endpoints except `/api/health`

**Key routers:**

| Router | Endpoints | Purpose |
|--------|-----------|---------|
| `projects.py` | `/api/projects/*` | Project CRUD (list, create, update, archive) |
| `tasks.py` | `/api/tasks/*` | Task CRUD, status/state updates, assignment |
| `inbox.py` | `/api/inbox/*` | Inbox items (proposals, suggestions) from agents |
| `memories.py` | `/api/memories/*` | Personal memory CRUD, search, quick capture |
| `topics.py` | `/api/topics/*` | Research workspace organization |
| `documents.py` | `/api/docs/*` | Document CRUD and delivery |
| `chat.py` | `/api/chat/*` | Chat sessions, messages, WebSocket endpoint |
| `calendar.py` | `/api/calendar/*` | Event CRUD, recurring schedules, deadline sync |
| `status.py` | `/api/status/*` | System health, activity timeline, cost tracking |
| `agents.py` | `/api/agents/*` | Agent status and configuration |
| `orchestrator.py` | `/api/orchestrator/*` | Orchestrator control (pause, resume, status) |

**Response format:**
- Success: JSON with data
- Error: `{"detail": "error message"}` with appropriate HTTP status
- Snake_case keys (converted to camelCase by Mission Control clients)

---

### 2. Task Orchestrator (app/orchestrator/)

**Purpose:** Autonomous task execution system that delegates work to AI agents

**How it works:**

```
┌─────────────┐
│   Scanner   │  Finds tasks with work_state='not_started'
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Router    │  Routes ALL tasks to project-manager for delegation
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Engine    │  Spawns workers via OpenClaw Gateway API
└──────┬──────┘
       │
       ▼
┌─────────────────────┐
│  project-manager    │  Analyzes task, delegates to specialist agent
│  (OpenClaw agent)   │  or handles directly; applies approval tiers
└─────────┬───────────┘
          │
    ┌─────┴──────┐
    ▼            ▼
programmer   researcher   (or other specialist)
```

**Key components:**

#### Scanner (`scanner.py`)
- Queries database for tasks with `work_state='not_started'` or `'ready'`
- Respects orchestrator pause state
- Returns eligible tasks sorted by priority

#### Router (`router.py`)
- **Simple:** Routes all tasks to `project-manager` agent
- Project-manager makes intelligent delegation decisions
- Constructs routing prompt with task context

#### Engine (`engine.py`)
- Main polling loop (runs every 10s by default)
- Spawns workers via OpenClaw Gateway `/tools/invoke` API
- Tracks active workers in database (`worker_runs` table)
- Updates task `work_state` as workers progress
- Handles worker completion/failure

#### Worker (`worker.py`)
- Spawns OpenClaw sessions via Gateway API
- Passes task context to agents via prompt
- Captures agent output and result summaries
- Updates database with worker status

#### Monitor Enhanced (`monitor_enhanced.py`)
- Detects stuck tasks (running too long)
- Auto-unblocks tasks after timeout
- Tracks failure patterns

#### Escalation Enhanced (`escalation_enhanced.py`)
- Multi-tier failure handling:
  1. **Retry** — Same agent, fresh attempt
  2. **Agent Switch** — Try different agent type
  3. **Diagnostic** — Gather failure context
  4. **Human Escalation** — Send to inbox for review
- Exponential backoff between retries

#### Circuit Breaker (`circuit_breaker.py`)
- Detects infrastructure failures (Gateway down, OpenClaw unreachable)
- Isolates failures per project/agent
- Prevents cascade failures

---

### 3. Project Manager Agent

**Purpose:** Central coordinator for task routing, delegation, and approval workflows

**Capabilities:**
- Analyzes incoming tasks and determines appropriate agent
- Delegates to specialist agents (programmer, researcher, writer)
- Handles simple tasks directly
- Applies tiered approval system (auto-approve, review, escalate)
- Reviews completed work for quality
- Routes inbox items requiring human decision

**Workflow:**

```
Task created → Scanner finds it → Router sends to project-manager
                                              ↓
                       project-manager analyzes task context
                                              ↓
                    ┌──────────────────────────┼────────────────┐
                    ▼                          ▼                ▼
            Simple task?              Specialist needed?    Needs approval?
         (handle directly)          (delegate to agent)    (send to inbox)
                    ↓                          ↓                ↓
            Mark complete              Spawn worker      Create inbox item
                                              ↓
                                      Worker completes
                                              ↓
                                  project-manager reviews
                                              ↓
                                    Apply approval tier:
                                    • Auto-approve (low-risk)
                                    • Human review (medium-risk)
                                    • Escalate (high-risk/failed)
```

**See:** `docs/project-manager-agent.md` for full design

---

### 4. Database Layer

**Technology:** SQLite + async SQLAlchemy + aiosqlite

**Configuration:**
- **WAL mode** — Enables concurrent reads/writes
- **Busy timeout:** 30s to handle contention
- **Connection pooling** — Async session management

**Key tables:**

| Table | Purpose |
|-------|---------|
| `projects` | Projects (active/archived) |
| `tasks` | Tasks with work_state + status fields |
| `inbox` | Agent proposals/suggestions requiring human decision |
| `memories` | Personal memory entries |
| `topics` | Research workspace metadata |
| `documents` | Reports, research deliverables |
| `chat_sessions` | Chat conversation sessions |
| `chat_messages` | Individual chat messages |
| `scheduled_events` | Calendar events and recurring schedules |
| `agents` | Agent status tracking |
| `worker_runs` | Orchestrator worker execution history |
| `activity_log` | System activity timeline |
| `api_tokens` | Authentication tokens |
| `agent_memory_sync` | Agent workspace memory synchronization |

**Models:** `app/models.py`  
**Schemas:** `app/schemas.py` (Pydantic request/response validation)

---

### 5. Authentication

**Method:** Bearer token authentication

**How it works:**
1. Tokens generated server-side via `bin/generate_token.py`
2. Stored in `api_tokens` table
3. Clients include `Authorization: Bearer <token>` header
4. `require_auth` dependency validates on each request

**Token management:**
```bash
# Create token
python bin/generate_token.py mission-control

# List tokens
python bin/list_tokens.py

# Revoke token
python bin/revoke_token.py mission-control
```

**WebSocket auth:** Token passed as query parameter:
```
wss://server:8000/api/chat/ws?token=<token>
```

---

### 6. WebSocket Real-Time Updates

**Endpoint:** `/api/chat/ws`

**Purpose:** Real-time bidirectional communication for chat and live updates

**How it works:**

```
Client connects → Authenticate via token → Register in ChatManager
                                                    ↓
User sends message → POST /api/chat/sessions/:key/messages
                                ↓
                    Saved to database
                                ↓
            Broadcast via WebSocket to all connected clients
                                ↓
            Clients receive and display message instantly
```

**Connection management:**
- Tracked in `ChatManager` (`app/services/chat_manager.py`)
- Automatic reconnection handling (client-side responsibility)
- Heartbeat/keepalive (future enhancement)

**See:** `research-findings.md` for WebSocket best practices

---

### 7. Tiered Approval System

**Purpose:** Automatically approve low-risk changes, flag high-risk changes for review

**Three tiers:**

| Tier | Risk Level | Action | Examples |
|------|-----------|--------|----------|
| **1** | Low | Auto-approve | Documentation, tests, bug fixes |
| **2** | Medium | Human review | New features, refactoring, schema changes |
| **3** | High | Escalate | Critical bugs, security, breaking changes |

**Workflow:**
1. Worker completes task
2. Project-manager reviews output
3. Applies tier based on change scope
4. Tier 1 → mark complete, Tier 2/3 → create inbox item

**See:** `docs/tiered-approval-system.md` for full design

---

## Data Flow Patterns

### Task Lifecycle

```
1. Task created (via UI or agent)
   ↓
2. work_state = 'not_started'
   ↓
3. Scanner finds task
   ↓
4. Router sends to project-manager
   ↓
5. project-manager analyzes and delegates
   ↓
6. Worker spawned (work_state = 'in_progress')
   ↓
7. Worker completes
   ↓
8. project-manager reviews output
   ↓
9. Apply approval tier:
   • Tier 1: work_state = 'completed'
   • Tier 2/3: work_state = 'pending_review', create inbox item
   ↓
10. Human approves (if needed)
    ↓
11. work_state = 'completed', status = 'completed'
```

### Inbox Workflow

```
Agent proposes change → Create inbox item (type: 'proposal')
                                    ↓
                    Human reviews in Mission Control
                                    ↓
                        ┌───────────┴──────────┐
                        ▼                      ▼
                    Approve                 Reject
                        ↓                      ↓
              Execute proposal          Mark rejected
                        ↓                      ↓
              Mark inbox read           Notify agent
```

### Memory System

```
Quick capture → POST /api/memories/capture
                        ↓
                Store in database
                        ↓
        Sync to agent workspaces (background job)
                        ↓
            Agents have access via memory search
```

### Document Delivery

```
Agent creates document → Write to agent workspace
                                    ↓
                    POST /api/docs (with file content)
                                    ↓
                        Store in database
                                    ↓
                Mission Control displays in Documents view
                                    ↓
                    Human reviews and approves
```

---

## Background Jobs

lobs-server includes background tasks via FastAPI lifespan:

| Job | Frequency | Purpose |
|-----|-----------|---------|
| Orchestrator main loop | 10s | Find and execute tasks |
| Recurring schedule expansion | 1 hour | Generate calendar events from schedules |
| Agent memory sync | On-demand | Sync memories to agent workspaces |

---

## Configuration

**Environment variables** (`.env` file):

```bash
# Database
DATABASE_URL=sqlite+aiosqlite:///./data/lobs.db

# OpenClaw Gateway
OPENCLAW_GATEWAY_URL=http://localhost:8080
OPENCLAW_GATEWAY_TOKEN=<token>

# Server
HOST=0.0.0.0
PORT=8000

# Orchestrator
ORCHESTRATOR_ENABLED=true
ORCHESTRATOR_POLL_INTERVAL=10
```

**Settings:** `app/config.py`

---

## Testing

**Framework:** pytest + pytest-asyncio + httpx

**Test organization:**
- `tests/test_*.py` — API endpoint tests
- Fixtures in `tests/conftest.py`
- Async tests via `@pytest.mark.asyncio`

**Run tests:**
```bash
source .venv/bin/activate
python -m pytest -v
```

**See:** `docs/TESTING.md` for comprehensive testing guide

---

## API Conventions

### Request/Response Format
- **Request:** JSON body, snake_case keys
- **Response:** JSON, snake_case keys
- **Dates:** ISO 8601 format with timezone
- **IDs:** UUID v4 (short form: first 8 chars for display)

### HTTP Methods
- **GET** — Retrieve resource(s)
- **POST** — Create resource
- **PUT** — Full update (replace entire resource)
- **PATCH** — Partial update (modify specific fields)
- **DELETE** — Remove resource

### Status Codes
- **200** — Success (GET, PUT, PATCH, DELETE)
- **201** — Created (POST)
- **400** — Bad request (validation error)
- **401** — Unauthorized (missing/invalid token)
- **404** — Not found
- **500** — Internal server error

### Pagination
- Query params: `?limit=50&offset=0`
- Response includes total count when applicable

---

## Agent Integration

Agents interact with lobs-server in two ways:

### 1. HTTP API (from agent workspaces)
Agents use helper scripts in `bin/agent-scripts/`:

```bash
# Task management
./bin/agent-scripts/lobs-tasks list-mine
./bin/agent-scripts/lobs-tasks get <task-id>
./bin/agent-scripts/lobs-tasks complete <task-id>

# Inbox
./bin/agent-scripts/lobs-inbox list
./bin/agent-scripts/lobs-inbox respond <item-id> --approve

# System status
./bin/agent-scripts/lobs-status overview
```

These scripts make authenticated API calls to lobs-server.

### 2. Orchestrator (passive)
Agents are spawned by orchestrator when tasks are assigned:

```
Orchestrator → OpenClaw Gateway /tools/invoke
                      ↓
            Spawn agent session with task prompt
                      ↓
            Agent executes task in workspace
                      ↓
            Agent delivers result (commit, report, etc.)
                      ↓
            Orchestrator captures output
                      ↓
            Update task work_state in database
```

---

## Deployment

**Current:** Development mode (single instance)

**Production considerations:**
- Use PostgreSQL instead of SQLite for better concurrency
- Deploy behind nginx reverse proxy
- Use Gunicorn/Uvicorn workers
- Set up HTTPS/TLS certificates
- Configure CORS for web clients
- Add rate limiting
- Set up monitoring (Prometheus, Grafana)

---

## Key Design Decisions

### Why SQLite?
- **Pro:** Zero configuration, file-based, easy backups
- **Pro:** Sufficient for single-user/small team
- **Con:** Limited concurrency (mitigated with WAL mode)
- **Future:** Migrate to PostgreSQL if multi-user needed

### Why FastAPI?
- **Pro:** Modern async Python framework
- **Pro:** Automatic OpenAPI docs
- **Pro:** Excellent Pydantic integration
- **Pro:** WebSocket support built-in

### Why Embedded Orchestrator?
- **Pro:** Direct database access (no HTTP overhead)
- **Pro:** Simpler deployment (single service)
- **Pro:** Easier debugging
- **Con:** Couples orchestration to API server
- **Future:** Could split into separate service

### Why project-manager delegation?
- **Pro:** Intelligent routing based on task context
- **Pro:** Centralized approval logic
- **Pro:** Easier to add new agent types
- **Con:** Extra hop in task execution
- **Benefit:** Better quality control, reduced human review burden

---

## Performance Characteristics

**API response times:**
- Simple GETs: <50ms
- Complex queries (search, aggregations): 100-200ms
- Task creation: <100ms
- WebSocket latency: <10ms

**Database:**
- SQLite with WAL mode handles 100+ concurrent reads
- Write contention minimal (busy timeout handles spikes)

**Orchestrator:**
- Polls every 10s (configurable)
- Can handle 10+ concurrent workers
- Worker spawn time: 2-5s (OpenClaw startup)

---

## Related Documentation

- **[AGENTS.md](AGENTS.md)** — Complete API reference and development guide
- **[docs/TESTING.md](docs/TESTING.md)** — Testing guide
- **[docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md)** — Known issues and technical debt
- **[docs/project-manager-agent.md](docs/project-manager-agent.md)** — Project manager design
- **[docs/tiered-approval-system.md](docs/tiered-approval-system.md)** — Approval workflow design
- **[docs/README.md](docs/README.md)** — Documentation index

---

*Last updated: 2026-02-14*
