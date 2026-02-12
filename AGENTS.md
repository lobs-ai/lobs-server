# AGENTS.md — lobs-server

## What This Is

lobs-server is a FastAPI + SQLite REST API that serves as the central backend for the Lobs task management system. It replaces the old git-based lobs-control state management with a proper database-backed server.

## Architecture

```
lobs-server/
├── app/
│   ├── main.py              # FastAPI app, lifespan, middleware
│   ├── config.py             # Settings (env vars)
│   ├── database.py           # Async SQLAlchemy engine + session
│   ├── models.py             # All SQLAlchemy models (16 tables)
│   ├── schemas.py            # Pydantic request/response schemas
│   ├── backup.py             # SQLite backup manager (scheduled + manual)
│   ├── logging_config.py     # Structured logging (console/JSON, rotating files)
│   ├── middleware.py          # Request logging middleware
│   ├── routers/              # REST API endpoints
│   │   ├── projects.py       # /api/projects
│   │   ├── tasks.py          # /api/tasks
│   │   ├── inbox.py          # /api/inbox
│   │   ├── documents.py      # /api/documents
│   │   ├── research.py       # /api/research/{project_id}/...
│   │   ├── tracker.py        # /api/tracker/{project_id}/...
│   │   ├── worker.py         # /api/worker/status, /api/worker/history
│   │   ├── agents.py         # /api/agents
│   │   ├── templates.py      # /api/templates
│   │   ├── reminders.py      # /api/reminders
│   │   ├── text_dumps.py     # /api/text-dumps
│   │   ├── backup.py         # /api/backup/...
│   │   ├── orchestrator.py   # /api/orchestrator/...
│   │   └── health.py         # /api/health
│   └── orchestrator/         # Built-in task orchestrator
│       ├── engine.py          # Main async polling loop
│       ├── scanner.py         # Finds eligible tasks (DB queries)
│       ├── worker.py          # Spawns OpenClaw worker processes
│       ├── router.py          # Routes tasks to agent types
│       ├── prompter.py        # Builds rich prompts for workers
│       ├── registry.py        # Agent config registry
│       ├── monitor.py         # Health checks, stuck detection
│       ├── escalation.py      # Tiered failure handling
│       ├── circuit_breaker.py # Prevents cascading failures
│       └── agent_tracker.py   # Per-agent status tracking
├── agents/                    # Agent templates (AGENTS.md, SOUL.md per type)
├── tests/                     # Pytest test suite (129+ tests)
├── scripts/
│   └── migrate_from_git.py   # One-time migration from lobs-control
├── data/
│   ├── lobs.db               # SQLite database
│   └── backups/              # Automatic backups
├── logs/                      # Rotating log files
├── requirements.txt
└── run.sh                     # Start server (uvicorn)
```

## Running

```bash
cd ~/lobs-server
source .venv/bin/activate
./run.sh                    # Starts on 0.0.0.0:8000
```

## Key Design Decisions

- **Async everything**: SQLAlchemy async with aiosqlite, FastAPI async handlers
- **Orchestrator is built-in**: Runs as asyncio background task in the same process — direct DB access, no HTTP overhead
- **SQLite**: Single-file database, no separate DB server needed. Backed up automatically every 6 hours
- **No git for state**: All state lives in the database. The old git-based approach (lobs-control) is fully replaced
- **Workers are external**: The orchestrator spawns OpenClaw worker processes via subprocess — they're separate processes, not threads

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_PATH` | `./data/lobs.db` | SQLite database path |
| `ORCHESTRATOR_ENABLED` | `true` | Enable/disable orchestrator |
| `ORCHESTRATOR_POLL_INTERVAL` | `10` | Seconds between task scans |
| `ORCHESTRATOR_MAX_WORKERS` | `3` | Max concurrent workers |
| `BACKUP_ENABLED` | `true` | Enable automatic backups |
| `BACKUP_INTERVAL_HOURS` | `6` | Hours between backups |
| `BACKUP_RETENTION_COUNT` | `30` | Max backups to keep |
| `BACKUP_GIT_ENABLED` | `false` | Commit backups to git |
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOG_FORMAT` | `console` | `console` or `json` |

## Testing

```bash
cd ~/lobs-server && source .venv/bin/activate
python -m pytest tests/ -v
```

## API Docs

When the server is running: http://localhost:8000/docs (auto-generated OpenAPI/Swagger)

## Database

SQLite at `data/lobs.db`. Tables:
- `projects`, `tasks`, `inbox_items`, `inbox_threads`, `inbox_messages`
- `agent_documents`, `research_requests`, `research_docs`, `research_sources`
- `tracker_items`, `worker_status`, `worker_runs`, `agent_status`
- `task_templates`, `reminders`, `text_dumps`

## Conventions

- All timestamps are UTC ISO 8601
- IDs are UUID strings (uppercase)
- API uses snake_case for JSON keys
- Pagination: `?limit=N&offset=M` on all list endpoints
- Filtering: query params (e.g., `?status=active&project_id=flock`)

## Working on This Repo

- **Don't deploy** without explicit approval
- **Run tests** before pushing: `python -m pytest tests/ -x -q`
- **Server must boot**: `python -c "from app.main import app; print('OK')"`
- **Keep models.py and schemas.py in sync** — if you add a DB column, add the Pydantic field too
- **Routers follow a pattern** — look at an existing router before creating a new one
