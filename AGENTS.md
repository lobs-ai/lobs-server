# AGENTS.md — lobs-server

## What This Is
Central backend for Lobs Mission Control. FastAPI + SQLite REST API with built-in orchestrator. All state lives here — tasks, projects, memories, documents, chat, agent management.

## Quick Start
```bash
cd ~/lobs-server
source .venv/bin/activate
./run.sh  # or: uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Architecture
```
FastAPI App (app/main.py)
├── Routers (app/routers/)     — REST API endpoints
├── Services (app/services/)   — Chat manager, OpenClaw bridge
├── Orchestrator (app/orchestrator/) — Task execution engine
├── Models (app/models.py)     — SQLAlchemy DB models
├── Schemas (app/schemas.py)   — Pydantic request/response schemas
├── Auth (app/auth.py)         — Bearer token authentication
├── Database (app/database.py) — Async SQLAlchemy + aiosqlite
└── Config (app/config.py)     — Settings
```

## API Routers (app/routers/)
| Router | Prefix | Purpose |
|--------|--------|---------|
| health.py | /api/health | Health check (PUBLIC, no auth) |
| projects.py | /api/projects | Project CRUD |
| tasks.py | /api/tasks | Task CRUD, status/state updates |
| memories.py | /api/memories | Memory CRUD, search, quick capture |
| documents.py | /api/docs | Reports, research documents |
| inbox.py | /api/inbox | Inbox items (proposals, suggestions) |
| research.py | /api/research | Research requests and findings |
| topics.py | /api/topics | Topics CRUD, document organization |
| chat.py | /api/chat | Chat sessions, messages, WebSocket |
| calendar.py | /api/calendar | Scheduled events, calendar views, deadlines |
| status.py | /api/status | System health, activity, costs |
| agents.py | /api/agents | Agent statuses and personality files |
| learning.py | /api/learning | Learning system stats and health checks |
| worker.py | /api/worker | Worker status, history, and activity feed |
| orchestrator.py | /api/orchestrator | Orchestrator control (pause/resume) |
| tracker.py | /api/tracker | Project tracker items |
| templates.py | /api/templates | Task templates |
| backup.py | /api/backup | DB backup management |
| text_dumps.py | /api/text-dumps | Text dump storage |

## Authentication
- **All endpoints require Bearer token** (except `/api/health`)
- **WebSocket**: Token passed as query param (`/api/chat/ws?token=...`)
- **No token creation API** — tokens generated server-side only
- Auth dependency: `app/auth.py` → `require_auth`
- Token management scripts:
  ```bash
  python scripts/generate_token.py <name>    # Create token
  python scripts/list_tokens.py              # List all tokens
  python scripts/revoke_token.py <name>      # Revoke token
  ```

## Orchestrator (app/orchestrator/)
Built into the server — direct DB access, no HTTP overhead.

| File | Purpose |
|------|---------|
| engine.py | Main polling loop, dispatches work |
| worker.py | Spawns workers via OpenClaw Gateway `/tools/invoke` API |
| scanner.py | Finds eligible tasks |
| router.py | Routes tasks to agent types (explicit assignment -> capability match -> fallback) |
| monitor.py | Basic health monitoring |
| monitor_enhanced.py | Stuck task detection, auto-unblock, failure patterns |
| escalation.py | Basic failure handling |
| escalation_enhanced.py | Multi-tier escalation (retry → agent switch → diagnostic → human) |
| circuit_breaker.py | Infrastructure failure detection, per-project/agent isolation |
| agent_tracker.py | Agent status tracking |
| prompter.py | Builds task prompts with context |
| config.py | Orchestrator settings |
| registry.py | Agent template registry (loads agent configs from `agents/`) |

**Worker spawning**: Uses OpenClaw Gateway `/tools/invoke` with `sessions_spawn` (not direct `openclaw` CLI).  
**Agent delegation**: Routing is server-driven; `lobs-server` applies explicit task agent assignment first, then capability routing, then deterministic fallback.  
**Graceful degradation**: If Gateway is unreachable, orchestrator runs in monitoring-only mode.

## Services (app/services/)
| File | Purpose |
|------|---------|
| chat_manager.py | WebSocket connection tracking, message broadcasting |
| openclaw_bridge.py | Webhook interface for OpenClaw agent responses |

## Database
- **SQLite** via async SQLAlchemy + aiosqlite
- DB file: `data/lobs.db`
- Models in `app/models.py` — 10+ tables (projects, tasks, memories, inbox, documents, agents, worker_runs, chat_sessions, chat_messages, api_tokens, etc.)
- Auto-creates tables on startup via `init_db()`

## Task State Management

Tasks have two independent state fields for orchestration and review workflows:

### work_state
**Purpose:** Tracks orchestration/execution state  
**Default:** `not_started`  
**Values:** `not_started`, `ready`, `in_progress`, `completed`, `failed`

**Usage:**
- Scanner finds tasks with `work_state IN ('not_started', 'ready')`
- Scheduler creates tasks with `work_state='not_started'`
- Worker sets to `in_progress` on start, `completed`/`failed` on finish
- Endpoint: `PUT /api/tasks/{task_id}/work_state` (body: `{"work_state": "ready"}`)

### review_state  
**Purpose:** Tracks approval/review workflow (tiered approval system)  
**Default:** `null` (unreviewed)  
**Values:** `null`, `auto_approved`, `needs_review`, `approved`, `rejected`

**Usage:**
- Project-manager sets after reviewing completed work
- `auto_approved` — Routine work approved autonomously
- `needs_review` — Sent to inbox for human review
- `approved`/`rejected` — Human decision recorded

**Note:** `status` field is separate (inbox/active/completed/rejected/waiting_on) and represents user-visible kanban state.

## Testing
```bash
source .venv/bin/activate
python -m pytest -v              # All tests
python -m pytest tests/test_memories.py -v  # Specific module
python -m pytest -x              # Stop on first failure
```
Tests auto-create auth tokens via fixtures.

## Key Files
- `app/main.py` — App setup, lifespan, router registration
- `app/models.py` — All SQLAlchemy models
- `app/schemas.py` — All Pydantic schemas
- `app/auth.py` — Token auth dependency
- `app/database.py` — DB engine, session factory
- `app/config.py` — Settings (API_PREFIX, DB path, etc.)
- `scripts/migrate_from_git.py` — One-time migration from lobs-control
- `scripts/seed_memories.py` — Seed memories from workspace files

## Agent Scripts (bin/agent-scripts/)
Shared utility scripts available to all agents during task execution. Included in agent workspace path.

| Script | Purpose |
|--------|---------|
| `lobs-tasks` | Task management (list, get, update, complete) |
| `lobs-status` | System status (overview, projects, agents) |
| `lobs-inbox` | Inbox operations (list, create) |
| `SKILLS-REFERENCE.md` | Quick reference for agent capabilities |

**Usage**: Agents can call these scripts directly (e.g., `./scripts/lobs-tasks list-mine`). Scripts use the lobs-server API internally.

## Agent Output Files
Agents write files in their workspace that the orchestrator processes on finalization:

| File | Purpose |
|------|---------|
| `.work-summary` | Short summary of work done (1-2 lines) or blocker message |
| `.new-topics.json` | (Researcher only) Topic creation requests (auto-created topics) |
| `.approval-request.json` | (Future) Structured approval requests |

**Example `.new-topics.json`:**
```json
[
  {
    "title": "WebSocket Performance Patterns",
    "description": "Research on WS connection pooling, message batching, backpressure"
  }
]
```

## Common Edits
- **Add endpoint**: Create router in `app/routers/`, add model/schema, register in `app/main.py`
- **Add model field**: Update `app/models.py` + `app/schemas.py`, run Alembic migration
- **Add orchestrator feature**: Edit files in `app/orchestrator/`
- **Add agent type**: Create template in `agents/<agent-name>/` with AGENTS.md + SOUL.md
- **Add agent script**: Create in `bin/agent-scripts/`, make executable, document in SKILLS-REFERENCE.md

## Worker API Endpoints

### GET /api/worker/status
Get current worker orchestrator status (active/paused, task counts, token usage).

**Response:**
```json
{
  "active": true,
  "tasks_completed": 42,
  "input_tokens": 125000,
  "output_tokens": 38000
}
```

### GET /api/worker/history
List recent worker runs (limit/offset pagination).

**Query params:** `?limit=20&offset=0`

**Response:** Array of worker run objects with task_id, started_at, ended_at, succeeded, summary.

### GET /api/worker/activity
List recent agent activity with task details and summaries. Joins worker runs with task information for display in activity feeds.

**Query params:** `?limit=20&offset=0`

**Response:**
```json
[
  {
    "id": 123,
    "worker_id": "abc123",
    "started_at": "2026-02-14T11:30:00Z",
    "ended_at": "2026-02-14T11:45:00Z",
    "succeeded": true,
    "summary": "Added authentication middleware to API router",
    "source": "task",
    "task_id": "A1B2C3D4",
    "task_title": "Implement auth middleware",
    "project_id": "lobs-server",
    "agent": "programmer"
  }
]
```

**Use case:** Display recent agent work in dashboard activity feeds with task context.

---

## Networking
- Binds to `0.0.0.0:8000`
- Dashboard connects via Tailscale (private network)
- Not exposed to LAN
