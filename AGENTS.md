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
| agent_learning.py | /api/agent-learning | Agent learning outcome ledger, failure patterns, lesson approval (see Worker API Endpoints below) |
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

## Agent Learning API Endpoints

The agent learning system logs every agent run with outcome, failure reason, and which prompt learnings were applied. This data feeds a daily batch job that extracts patterns and queues lesson suggestions for approval.

**Note:** Prefix is `/api/agent-learning` — not `/api/learning`, which is the personal learning plans system (separate).

**Design spec:** [`docs/learning-loop-mvp-design.md`](docs/learning-loop-mvp-design.md)  
**Operator guide:** [`docs/agent-learning-operator-guide.md`](docs/agent-learning-operator-guide.md)

### POST /api/agent-learning/outcomes
Submit or correct a task outcome. Called automatically by the worker on completion; can also be used manually to tag a task as user-corrected.

**Request:**
```json
{
  "task_id": "A1B2C3D4",
  "outcome": "user-corrected",
  "human_feedback": "Tests were present but the approach was wrong.",
  "reason_tags": ["wrong_approach"]
}
```

**Fields:**
- `task_id` — required. Task UUID or short ID.
- `outcome` — required. One of: `success`, `failure`, `user-corrected`.
- `human_feedback` — optional. Free-text explanation.
- `reason_tags` — optional. Array of standard tags: `missing_tests`, `missing_error_handling`, `unclear_names`, `missing_docs`, `missing_validation`, `wrong_approach`, `incomplete`, `scope_creep`, `hallucinated_api`.

**Response (201):**
```json
{
  "outcome_id": "abc123",
  "task_id": "A1B2C3D4",
  "outcome": "user-corrected"
}
```

### GET /api/agent-learning/summary
Return aggregate stats: success rate, A/B lift, top failure patterns, active learnings, and pending suggestions.

**Query params:**
- `since_days` — default `30`. Lookback window.
- `agent_type` — optional. Filter to `programmer`, `researcher`, `writer`, etc.
- `task_category` — optional. Filter to `bug_fix`, `feature`, `test`, `refactor`, `docs`, `research`.

**Response:**
```json
{
  "generated_at": "2026-02-24T08:00:00Z",
  "period_days": 30,
  "totals": {
    "tasks_tracked": 142,
    "success_rate": 0.73,
    "treatment_success_rate": 0.78,
    "control_success_rate": 0.65,
    "lift": 0.20
  },
  "top_failure_patterns": [
    {
      "pattern": "missing_tests",
      "occurrences": 18,
      "pct_of_failures": 0.45,
      "active_learning": {
        "id": "learn_abc",
        "lesson_text": "Always include unit tests...",
        "confidence": 0.72
      }
    }
  ],
  "active_learnings": [
    {
      "id": "learn_abc",
      "agent_type": "programmer",
      "task_category": "feature",
      "lesson_text": "Always include unit tests...",
      "confidence": 0.72,
      "success_count": 14,
      "failure_count": 3
    }
  ],
  "pending_suggestions": [
    {
      "id": "learn_xyz",
      "agent_type": "programmer",
      "lesson_text": "Add try/except around I/O calls...",
      "confidence": 0.5,
      "is_active": false,
      "evidence_count": 5
    }
  ]
}
```

**Key fields:**
- `lift` — relative improvement in success rate for the treatment group (got learnings) vs. control group (did not). A positive lift confirms learnings help.
- `pending_suggestions` — lessons generated by the daily batch job, not yet approved. Use `PATCH /api/agent-learning/learnings/{id}` to approve.

### PATCH /api/agent-learning/learnings/{id}
Approve, reject, or edit a suggested lesson. The daily batch creates lessons with `is_active=false`; this endpoint activates them.

**Request:**
```json
{
  "action": "approve"
}
```

Or with an edit:
```json
{
  "action": "edit",
  "lesson_text": "Always write tests in a separate file named test_<module>.py."
}
```

**Actions:**
- `approve` — Sets `is_active=true`, `confidence=0.5`. Lesson will be injected into matching future prompts.
- `reject` — Sets `is_active=false`, `confidence=0.0`. Lesson will not be used.
- `edit` — Updates `lesson_text`. Keep `is_active` state unchanged.

**Response (200):**
```json
{
  "id": "learn_xyz",
  "action": "approve",
  "is_active": true,
  "confidence": 0.5
}
```

---

## Networking
- Binds to `0.0.0.0:8000`
- Dashboard connects via Tailscale (private network)
- Not exposed to LAN
