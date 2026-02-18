# lobs-server

Central backend for [Lobs Mission Control](https://github.com/RafeSymonds/lobs-mission-control). FastAPI + SQLite REST API with built-in task orchestrator.

## Features
- **Task & Project Management** — Full CRUD with kanban workflow, tiered approvals
- **Memory System** — Second brain: daily notes, long-term memory, search, quick capture
- **Topics/Knowledge** — Research workspaces with documents and auto-created topics
- **Chat** — Real-time WebSocket messaging with OpenClaw agent bridge
- **Orchestrator** — Automatic server-side task routing (explicit agent -> capability registry -> fallback), worker spawning, model routing with fallback chains, failure escalation
- **Calendar Integration** — Events, recurring schedules, tracker deadline sync
- **System Health** — Activity timeline, cost tracking, monitoring
- **Auth** — Bearer token authentication on all endpoints

## Setup

**Quick start:** See [QUICKSTART.md](QUICKSTART.md) for detailed setup instructions.

```bash
git clone git@github.com:RafeSymonds/lobs-server.git
cd lobs-server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Generate an API token
python bin/generate_token.py my-token

# Run
./bin/run  # or: uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Documentation

- **[QUICKSTART.md](QUICKSTART.md)** — Get up and running in 5 minutes
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — System architecture, data flow, key components
- **[AGENTS.md](AGENTS.md)** — Complete API reference and development guide
- **[CHANGELOG.md](CHANGELOG.md)** — API changes and version history
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — Development guide for contributors and AI agents
- **[docs/](docs/)** — Implementation guides, design documents, investigations
  - [Testing Guide](docs/TESTING.md) — How to run and write tests
  - [Known Issues](docs/KNOWN_ISSUES.md) — Technical debt and known problems
  - [Topics Implementation](docs/TOPICS_IMPLEMENTATION.md) — Knowledge organization system
  - [Document Lifecycle](docs/document-lifecycle-design.md) — Document state management
  - See [docs/README.md](docs/README.md) for full index

## API
All endpoints at `/api/*` require Bearer token (except `/api/health`).

See [AGENTS.md](AGENTS.md) for complete endpoint reference.

## Testing
```bash
source .venv/bin/activate
python -m pytest -v
```

## See Also

**Lobs Ecosystem Documentation** (in `~/self-improvement/docs/`):
- [LOBS_ECOSYSTEM.md](../self-improvement/docs/LOBS_ECOSYSTEM.md) — Cross-project architecture and feature matrix
- [GETTING_STARTED.md](../self-improvement/docs/GETTING_STARTED.md) — 20-30 min ecosystem onboarding
- [TECH_STACK_REFERENCE.md](../self-improvement/docs/TECH_STACK_REFERENCE.md) — Technology choices and patterns
- [Code Quality System](../self-improvement/README.md) — Handoffs, reviews, technical debt tracking

## License
Private
