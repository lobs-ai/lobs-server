# Architecture — lobs-server

High-level overview of the backend system design, data flow, and key components.

**Last Updated:** 2026-02-25

**Recent Architectural Changes (Feb 25):**
- **Failure Explainer API** — Deterministic failure triage endpoint for Mission Control. `FailureExplainerService` (`app/services/failure_explainer.py`) evaluates 10 prioritized rules over Task, WorkerRun, and ModelUsageEvent records and returns a structured `FailureExplanation` with `primary_failure_code`, `all_failure_codes`, human-readable explanation, and `next_actions`. No LLM — pure rule engine. Served under new `app/routers/intelligence.py` at `GET /api/intelligence/tasks/{task_id}/failure-explanation`. Design: [docs/failure-explainer-design.md](docs/failure-explainer-design.md). Handoffs: [docs/handoffs/failure-explainer-handoffs.json](docs/handoffs/failure-explainer-handoffs.json).
- **Model Spend Guardrails + Auto-Downgrade** — Budget-aware routing policy with 3-lane daily spend caps (`critical`/`standard`/`background`). Core: `BudgetGuard` class in `app/orchestrator/budget_guard.py` applies caps and tier downgrade at model-selection time; `ModelChooser.choose()` in `model_chooser.py` calls it as layer 2 of a 3-layer guard (per-provider monthly cap → per-lane daily cap → global daily hard cap). Lane is classified from task criticality + agent type; cap breach triggers tier restriction (e.g. standard lane downgrades to ≤medium). Override log persisted to `OrchestratorSetting`. Endpoints: `GET/PATCH /api/usage/budget-lanes`, `GET /api/usage/daily-report`. `ModelUsageEvent.budget_lane` column enables accurate per-lane spend tracking.

**Recent Architectural Changes (Feb 24):**
- **Daily Ops Brief** — 8am auto-posted summary of calendar, email, GitHub blockers, and top agent tasks (design: [docs/daily-ops-brief-design.md](docs/daily-ops-brief-design.md)); `BriefService` + `/api/brief/today` + direct engine timer pattern (same as memory maintenance — `_brief_hour_et=8` ET, `_last_brief_date_et` persisted to `OrchestratorSetting`). Handoffs: [docs/handoffs/daily-ops-brief-handoffs.json](docs/handoffs/daily-ops-brief-handoffs.json)
- **Inbox Remediation Tracking** — Closes approved→queued→cancelled silent decay loop: `Task.source_inbox_item_id` (FK to inbox item at spawn), `Task.cancel_reason` (required on rejection), `GET /api/inbox/stuck-remediations` triage list, and `StuckRemediationsAdapter` in BriefService (design: [docs/inbox-remediation-tracking-design.md](docs/inbox-remediation-tracking-design.md))

**Recent Architectural Changes (Feb 21-24):**
- **Agent learning system (MVP in progress)** — Hook 1 (prompt enhancement via `PromptEnhancer`) is live in `worker.py` with 80/20 A/B split. Hook 2 (outcome tracking), `/api/agent-learning` endpoints, and daily batch job are still to build. MVP spec: [docs/learning-loop-mvp-design.md](docs/learning-loop-mvp-design.md); current state: [docs/handoffs/learning-loop-mvp-status.md](docs/handoffs/learning-loop-mvp-status.md)
- **5-tier model routing** — Upgraded to micro/small/medium/standard/strong tier system with Ollama auto-discovery
- **Reflection system improvements** — Domain-specific prompts, isolated sessions, manual trigger endpoint
- **Token usage tracking** — Extract and track token usage from session transcripts

See [~/lobs-shared-memory/docs/server/](../lobs-shared-memory/docs/server/) for detailed documentation.

---

## System Overview

lobs-server is the central backend for the Lobs multi-agent system. It provides:

1. **REST API** — Task management, memory, knowledge, chat, calendar, system status
2. **WebSocket Server** — Real-time chat and updates
3. **Task Orchestrator** — Autonomous task execution via AI agent workers
4. **Project Manager** — Intelligent task delegation and approval workflows
5. **Agent Learning System** — Closed-loop feedback from outcomes to behavior improvement
6. **Data Storage** — SQLite database for all system state

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
│   │   ├── learning.py        # Personal learning plans (separate from agent learning)
│   │   ├── agent_learning.py  # ❌ Not yet built — /api/agent-learning/* endpoints
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
│   │   ├── prompt_enhancer.py       # ✅ Inject learnings into prompts (A/B split live)
│   │   ├── outcome_tracker.py       # ❌ Not yet built — log outcomes at task completion
│   │   ├── learning_batch.py        # ❌ Not yet built — daily 2am pattern aggregation
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
- Captures agent output and result summaries (via `sessions_history` API)
- Updates database with worker status
- **Concurrency:** Up to `MAX_WORKERS=5` can run in parallel
  - **Agent locks removed** (2026-02-14) — Multiple instances of same agent type can run concurrently
  - **Project locks enforced** — Prevents concurrent workers modifying same repository
  - **Example:** 3 programmers + 2 writers can work simultaneously on different projects

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

### 8. Agent Learning System

**Purpose:** Closed-loop learning where agents improve from task outcomes and human feedback

**Status:** Partially live — prompt enhancement active, outcome tracking pending  
**Design:** [docs/learning-loop-mvp-design.md](docs/learning-loop-mvp-design.md)  
**Current state:** [docs/handoffs/learning-loop-mvp-status.md](docs/handoffs/learning-loop-mvp-status.md)

**How it works:**

```
Task Execution
      ↓
[Hook 1 ✅ LIVE] PromptEnhancer injects relevant learnings
      ↓
Worker runs with enhanced prompt
      ↓
[Hook 2 ❌ PENDING] OutcomeTracker records success/failure
      ↓
Human Feedback → POST /api/agent-learning/outcomes
      ↓
Daily Batch Job → Ranks failure patterns, queues suggestions
      ↓
Human Approves → PATCH /api/agent-learning/learnings/{id}
      ↓
Learning becomes active → injected on next matching task
```

**Key components:**

#### PromptEnhancer (`app/orchestrator/prompt_enhancer.py`) ✅ Live
- Queries active `OutcomeLearning` rows matching agent type + task category + complexity
- Selects top N learnings by confidence (configurable via `MAX_LEARNINGS_PER_PROMPT`)
- Prepends a "Lessons from Past Tasks" section to the prompt
- 20% A/B control group (configurable via `LEARNING_CONTROL_GROUP_PCT`) receives no learnings
- Fail-safe: any error returns the base prompt unchanged

#### OutcomeTracker (`app/orchestrator/outcome_tracker.py`) ❌ Not yet built
- Will write a `TaskOutcome` row after every worker completion
- Captures: success/failure, agent type, context hash, applied learning IDs, A/B flag
- Updates confidence on active `OutcomeLearning` rows used in this run
- Must be fail-safe — errors are logged, never raised

#### Agent Learning API (`app/routers/agent_learning.py`) ❌ Not yet built
- `POST /api/agent-learning/outcomes` — submit or correct an outcome
- `GET /api/agent-learning/summary` — top failure patterns, A/B lift, active learnings
- `PATCH /api/agent-learning/learnings/{id}` — approve/reject a suggested learning
- **Note:** prefix is `/api/agent-learning`, not `/api/learning` (that belongs to personal learning plans)

#### Learning Batch Job (`app/orchestrator/learning_batch.py`) ❌ Not yet built
- Runs daily at 2am ET via engine timer
- Aggregates failure patterns from last 14 days
- Creates inactive `OutcomeLearning` candidates for patterns with ≥3 failures
- Posts summary to ops brief; queues inbox suggestions for human approval
- Auto-deactivates learnings where `confidence < 0.3` AND `failure_count >= 3`

**Database tables:**
- `task_outcomes` — Every agent run: success flag, reason tags, A/B group, applied learnings
- `outcome_learnings` — Extracted lessons with confidence scores and injection text

**Configuration:**

| Variable | Default | Effect |
|----------|---------|--------|
| `LEARNING_ENABLED` | `true` | Master on/off switch |
| `LEARNING_INJECTION_ENABLED` | `true` | Disable prompt injection only |
| `LEARNING_CONTROL_GROUP_PCT` | `0.20` | Fraction of tasks with no injection |
| `MAX_LEARNINGS_PER_PROMPT` | `3` | Max lessons injected per prompt |
| `MIN_CONFIDENCE_THRESHOLD` | `0.3` | Inject only learnings above this score |

**See:** [docs/learning-loop-mvp-design.md](docs/learning-loop-mvp-design.md) for full spec

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

## Agent Learning System (MVP In Progress)

**Status:** Hook 1 live; Hook 2 + API + batch job still to build  
**MVP Design:** [docs/learning-loop-mvp-design.md](docs/learning-loop-mvp-design.md)  
**Current state:** [docs/handoffs/learning-loop-mvp-status.md](docs/handoffs/learning-loop-mvp-status.md)

### Overview

Closed-loop system where agents learn from task outcomes: each run is logged, failure patterns are ranked, and lessons are injected into future prompts. The 80/20 A/B split measures whether the learning actually helps.

### Implementation State (as of 2026-02-24)

| Component | File | Status |
|-----------|------|--------|
| `TaskOutcome` model | `app/models.py:846` | ✅ Done |
| `OutcomeLearning` model | `app/models.py:866` | ✅ Done |
| Database tables | migrations | ✅ Migrated |
| `PromptEnhancer` | `app/orchestrator/prompt_enhancer.py` | ✅ Fully implemented and live |
| Worker A/B split (20% control) | `app/orchestrator/worker.py:~234` | ✅ Live |
| Worker Hook 1 (pre-spawn injection) | `app/orchestrator/worker.py:~241` | ✅ Live |
| `OutcomeTracker` | `app/orchestrator/outcome_tracker.py` | ❌ Not built |
| Worker Hook 2 (post-completion tracking) | `app/orchestrator/worker.py` | ❌ Not built |
| Agent learning API | `app/routers/agent_learning.py` | ❌ Not built |
| Daily batch job | `app/orchestrator/learning_batch.py` | ❌ Not built |
| Engine timer (2am ET) | `app/orchestrator/engine.py` | ❌ Not built |

### Architecture

```
Task Execution
      ↓
[✅ LIVE] PromptEnhancer injects active OutcomeLearnings into prompt
      ↓
Worker runs
      ↓
[❌ PENDING] OutcomeTracker.track_completion() writes TaskOutcome row
      ↓
Human or system submits feedback → POST /api/agent-learning/outcomes
      ↓
Daily batch at 2am ET → aggregates patterns, queues inbox suggestions
      ↓
Human approves → PATCH /api/agent-learning/learnings/{id} → is_active=True
      ↓
Next matching task receives the learning
```

### API Endpoints (pending)

- `POST /api/agent-learning/outcomes` — submit or correct an outcome
- `GET /api/agent-learning/summary` — failure patterns, A/B lift, active learnings
- `PATCH /api/agent-learning/learnings/{id}` — approve or reject a pending learning

**Important:** The API prefix is `/api/agent-learning`, not `/api/learning`. The `/api/learning` prefix belongs to personal learning plans (`app/routers/learning.py`), a separate system.

### Design Principles

- **Simplicity first** — Rule-based pattern extraction, not ML
- **Fail-safe everywhere** — Any learning error logs and continues; no worker crashes
- **Human in the loop** — Learnings require human approval before activation
- **Measurable** — A/B control group provides a baseline for lift calculation

### Success Metrics

- **Primary:** Code review acceptance rate improvement >10%
- **Secondary:** Learning coverage, confidence distribution, application frequency
- **A/B validation:** Control group (no learnings) vs treatment group

---

## Related Documentation

- **[AGENTS.md](AGENTS.md)** — Complete API reference and development guide
- **[docs/TESTING.md](docs/TESTING.md)** — Testing guide
- **[docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md)** — Known issues and technical debt
- **[docs/project-manager-agent.md](docs/project-manager-agent.md)** — Project manager design
- **[docs/tiered-approval-system.md](docs/tiered-approval-system.md)** — Approval workflow design
- **[docs/README.md](docs/README.md)** — Documentation index
- **[docs/agent-learning-system.md](docs/agent-learning-system.md)** — Agent learning system design

---

*Last updated: 2026-02-23*
