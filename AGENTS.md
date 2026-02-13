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
| chat.py | /api/chat | Chat sessions, messages, WebSocket |
| status.py | /api/status | System health, activity, costs |
| agents.py | /api/agents | Agent statuses and personality files |
| worker.py | /api/worker | Worker status and history |
| orchestrator.py | /api/orchestrator | Orchestrator control (pause/resume) |
| tracker.py | /api/tracker | Project tracker items |
| templates.py | /api/templates | Task templates |
| reminders.py | /api/reminders | Reminders |
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
| worker.py | Spawns OpenClaw workers, manages lifecycle |
| scanner.py | Finds eligible tasks |
| router.py | Routes tasks to agent types |
| monitor.py | Basic health monitoring |
| monitor_enhanced.py | Stuck task detection, auto-unblock, failure patterns |
| escalation.py | Basic failure handling |
| escalation_enhanced.py | Multi-tier escalation (retry → agent switch → diagnostic → human) |
| circuit_breaker.py | Infrastructure failure detection, per-project/agent isolation |
| agent_tracker.py | Agent status tracking |
| prompter.py | Builds task prompts with context |
| config.py | Orchestrator settings |

**Graceful degradation**: If `openclaw` is not on PATH, orchestrator runs in monitoring-only mode (no worker spawning).

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

## Common Edits
- **Add endpoint**: Create router in `app/routers/`, add model/schema, register in `app/main.py`
- **Add model field**: Update `app/models.py` + `app/schemas.py`, ALTER TABLE in DB
- **Add orchestrator feature**: Edit files in `app/orchestrator/`

## Networking
- Binds to `0.0.0.0:8000`
- Dashboard connects via Tailscale (private network)
- Not exposed to LAN
