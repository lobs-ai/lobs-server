# Quickstart Guide

Get lobs-server running in 5 minutes.

---

## Prerequisites

- **Python 3.11+**
- **Git**
- **OpenClaw Gateway** running (for orchestrator features)

---

## Installation

### 1. Clone and Install

```bash
git clone git@github.com:RafeSymonds/lobs-server.git
cd lobs-server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Initialize Database

The database is created automatically on first run. To customize location:

```bash
# Optional: Copy environment template
cp .env.example .env

# Edit DATABASE_PATH if desired (default: ./data/lobs.db)
```

### 3. Generate API Token

```bash
python bin/generate_token.py mission-control
```

**Save the token** — you'll need it for Mission Control and Mobile apps.

Example output:
```
Token created for 'mission-control':
z5mr-WWjPxAAHvRd2ZULm7HLNW1oRubXmcMiBJoEmsU
```

### 4. Start Server

```bash
./bin/run
```

Or manually:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Server starts at **http://localhost:8000**

---

## Verify It Works

### Health Check
```bash
curl http://localhost:8000/api/health
```

Expected: `{"status":"ok","uptime":"..."}`

### Authenticated Request
```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:8000/api/projects
```

Expected: `[]` (empty project list on fresh install)

---

## What's Running

With default config, the server starts:

- ✅ **REST API** on port 8000
- ✅ **WebSocket** endpoint at `/api/chat/ws`
- ✅ **Task Orchestrator** polling every 10 seconds
- ✅ **Backup Manager** saving DB every 6 hours
- ✅ **Memory Sync** on startup

---

## Next Steps

### Connect Mission Control

1. Launch lobs-mission-control
2. Enter server URL: `http://localhost:8000` (or Tailscale IP)
3. Paste API token from step 3
4. Click "Connect"

### Explore the API

Interactive docs at:
- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

### Create Your First Task

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test task",
    "project_id": null,
    "notes": "My first task",
    "status": "todo"
  }'
```

### Read the Docs

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — System design overview
- **[AGENTS.md](AGENTS.md)** — Complete API reference
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — Development workflow
- **[docs/TESTING.md](docs/TESTING.md)** — How to run tests

---

## Configuration

All config is optional. Edit `.env` or set environment variables:

**Most useful settings:**

```bash
# Disable orchestrator (if you don't have OpenClaw)
ORCHESTRATOR_ENABLED=false

# Change database location
DATABASE_PATH=/custom/path/lobs.db

# Increase log verbosity
LOG_LEVEL=DEBUG

# Disable backups (dev only)
BACKUP_ENABLED=false
```

See [`.env.example`](.env.example) for all options.

---

## Troubleshooting

### "Database is locked"
Already fixed! Server uses WAL mode by default.

If you still see this, check that no other process is using the database.

### "Orchestrator not starting"
Common causes:
- OpenClaw Gateway not running
- Wrong `OPENCLAW_GATEWAY_URL` or `OPENCLAW_GATEWAY_TOKEN`

**Fix:**
1. Check Gateway is running: `curl http://localhost:18789/status`
2. Verify token in `.env` matches Gateway config
3. Set `ORCHESTRATOR_ENABLED=false` if you don't need it

### "Port 8000 already in use"
Another process is using port 8000.

**Fix:**
```bash
# Find the process
lsof -i :8000

# Kill it or choose a different port
uvicorn app.main:app --port 8001
```

### "Import errors" or "Module not found"
Virtual environment not activated.

**Fix:**
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Development Workflow

### Run Tests
```bash
source .venv/bin/activate
pytest -v
```

### Watch Mode (auto-reload)
```bash
./bin/run  # Already includes --reload
```

### Check Code Quality
```bash
# Install ruff (optional)
pip install ruff

# Lint
ruff check app/

# Format
ruff format app/
```

### Database Migrations

No migration tool (yet). Schema changes are manual:
1. Edit models in `app/models/`
2. Drop/recreate tables in SQLite (dev only)
3. For production: write manual SQL migration script

---

## Getting Help

- **Known issues:** See [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md)
- **Architecture questions:** See [ARCHITECTURE.md](ARCHITECTURE.md)
- **API questions:** See [AGENTS.md](AGENTS.md)
- **Testing:** See [docs/TESTING.md](docs/TESTING.md)

---

**You're all set!** 🎉

Now go build something with agents.
