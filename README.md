# lobs-server

Central backend for [Lobs Mission Control](https://github.com/RafeSymonds/lobs-mission-control). FastAPI + SQLite REST API with built-in task orchestrator.

## Features
- **Task & Project Management** — Full CRUD with kanban workflow
- **Memory System** — Second brain: daily notes, long-term memory, search, quick capture
- **Chat** — Real-time WebSocket messaging with OpenClaw agent bridge
- **Orchestrator** — Automatic task routing, worker spawning, failure escalation
- **System Health** — Activity timeline, cost tracking, monitoring
- **Auth** — Bearer token authentication on all endpoints

## Setup
```bash
git clone git@github.com:RafeSymonds/lobs-server.git
cd lobs-server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Generate an API token
python scripts/generate_token.py my-token

# Run
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API
All endpoints at `/api/*` require Bearer token (except `/api/health`).

See [AGENTS.md](AGENTS.md) for full endpoint reference.

## Testing
```bash
source .venv/bin/activate
python -m pytest -v
```

## License
Private
