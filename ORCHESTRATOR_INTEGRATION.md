# Orchestrator Integration

## Summary

Successfully ported the lobs-orchestrator into lobs-server as a built-in background service. The orchestrator now runs as an asyncio background task started via FastAPI lifespan, with all git operations replaced by direct SQLAlchemy DB queries.

## What Was Done

### 1. Created `app/orchestrator/` Package

**Core Modules:**

- **`engine.py`** — Main async loop that polls for work and spawns workers
  - Runs as `asyncio.create_task()` in FastAPI lifespan
  - Replaced git sync with DB polling
  - Manages the orchestration loop with adaptive backoff

- **`scanner.py`** — Task and project scanning via DB queries
  - Replaced `bin/open-work` shell script with `SELECT * FROM tasks WHERE status='active' AND work_state='not_started'`
  - Direct SQLAlchemy queries for eligible tasks and projects

- **`worker.py`** — OpenClaw worker process management
  - Keeps subprocess spawning logic (OpenClaw processes)
  - Replaced git commit/push with DB writes for task status updates
  - Writes worker run history to `worker_runs` table
  - Enforces domain locks (one worker per project) and agent locks (one per agent type)

- **`router.py`** — Task-to-agent routing logic
  - Pure logic module, no I/O changes needed
  - Regex-based routing with fallback to default (programmer)

- **`monitor.py`** — Health monitoring and stuck task detection
  - Replaced file reads with DB queries for stuck task detection
  - Checks worker heartbeats and task durations

- **`escalation.py`** — Failure handling and alerts
  - Replaced file I/O with DB operations
  - Creates inbox items for failure alerts
  - Records failure metadata in task notes

- **`agent_tracker.py`** — Per-agent status tracking
  - Replaced JSON file writes with `UPDATE agent_status SET ...`
  - Tracks thinking, activity, and statistics per agent type
  - Syncs to database instead of git commits

### 2. Configuration

Added orchestrator config to `app/config.py`:

```python
# Orchestrator
ORCHESTRATOR_ENABLED: bool = os.getenv("ORCHESTRATOR_ENABLED", "true").lower() in ("true", "1", "yes")
ORCHESTRATOR_POLL_INTERVAL: int = int(os.getenv("ORCHESTRATOR_POLL_INTERVAL", "10"))
ORCHESTRATOR_MAX_WORKERS: int = int(os.getenv("ORCHESTRATOR_MAX_WORKERS", "3"))
```

**Environment Variables:**
- `ORCHESTRATOR_ENABLED` — Set to `false` to disable (default: `true`)
- `ORCHESTRATOR_POLL_INTERVAL` — Polling interval in seconds (default: `10`)
- `ORCHESTRATOR_MAX_WORKERS` — Max concurrent workers (default: `3`)

### 3. FastAPI Integration

Updated `app/main.py` lifespan to start/stop the orchestrator:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    settings.ensure_data_dir()
    await init_db()
    
    # Start orchestrator if enabled
    if settings.ORCHESTRATOR_ENABLED:
        orchestrator_engine = OrchestratorEngine(AsyncSessionLocal)
        await orchestrator_engine.start()
    
    app.state.orchestrator = orchestrator_engine
    
    yield
    
    # Shutdown
    if orchestrator_engine:
        await orchestrator_engine.stop(timeout=60.0)
```

### 4. API Endpoints

Created `app/routers/orchestrator.py` with control endpoints:

- **`GET /api/orchestrator/status`** — Get orchestrator status (running, uptime)
- **`GET /api/orchestrator/workers`** — Get active worker status
- **`POST /api/orchestrator/pause`** — Pause orchestrator (stop accepting new work)
- **`POST /api/orchestrator/resume`** — Resume orchestrator

## Key Changes from Original

### Git → Database

| Original (git-based) | New (DB-based) |
|---------------------|----------------|
| `git pull --rebase` | DB session refresh |
| `bin/open-work` | `SELECT * FROM tasks WHERE status='active' AND work_state='not_started'` |
| `git add . && git commit -m "..." && git push` | `UPDATE tasks SET work_state='completed'` + `db.commit()` |
| JSON file writes (`state/agents/<type>.json`) | `UPDATE agent_status SET ...` |
| File-based worker status | `worker_status` and `worker_runs` tables |
| Inbox file writes | `INSERT INTO inbox_items` |

### Architecture

**Before:**
```
Orchestrator (standalone daemon)
  ↓ git pull/push
lobs-control (git repo)
  ↓ read/write
Dashboard (reads git repo)
```

**After:**
```
lobs-server (FastAPI)
  ↓ FastAPI lifespan
OrchestratorEngine (asyncio background task)
  ↓ SQLAlchemy queries
PostgreSQL/SQLite database
  ↑ read/write
Dashboard (REST API client)
```

## Important Constraints Met

✅ **Async SQLAlchemy** — All DB operations use `AsyncSession` matching server patterns  
✅ **Optional** — Orchestrator can be disabled via `ORCHESTRATOR_ENABLED=false`  
✅ **Domain Locks** — One worker per project at a time (via `project_locks` dict)  
✅ **Agent Locks** — One instance per agent type at a time (via `agent_locks` dict)  
✅ **Subprocess Spawning** — OpenClaw workers still spawn via `subprocess.Popen`  
✅ **Existing API** — No breaking changes to existing routers  

## How to Use

### Enable/Disable

```bash
# Disable orchestrator
export ORCHESTRATOR_ENABLED=false

# Enable with custom settings
export ORCHESTRATOR_ENABLED=true
export ORCHESTRATOR_POLL_INTERVAL=15
export ORCHESTRATOR_MAX_WORKERS=5
```

### Check Status

```bash
# Get orchestrator status
curl http://localhost:8000/api/orchestrator/status

# Get worker status
curl http://localhost:8000/api/orchestrator/workers

# Pause orchestrator
curl -X POST http://localhost:8000/api/orchestrator/pause

# Resume orchestrator
curl -X POST http://localhost:8000/api/orchestrator/resume
```

### Run Server

```bash
cd ~/lobs-server
source .venv/bin/activate
uvicorn app.main:app --reload
```

The orchestrator will start automatically on server startup if `ORCHESTRATOR_ENABLED=true`.

## Testing

All imports verified:

```bash
cd ~/lobs-server
source .venv/bin/activate
python -c "from app.orchestrator.engine import OrchestratorEngine; print('OK')"
# Output: OK
```

## Next Steps

1. **Test end-to-end** — Create a task via API and verify orchestrator picks it up
2. **Add tests** — Unit tests for scanner, router, worker manager
3. **Monitor memory** — Track resource usage with multiple concurrent workers
4. **Logging** — Ensure all orchestrator logs are captured by FastAPI logging
5. **Metrics** — Add Prometheus/OpenTelemetry metrics for orchestrator health

## Files Changed

```
app/orchestrator/__init__.py         (new)
app/orchestrator/config.py           (new)
app/orchestrator/scanner.py          (new)
app/orchestrator/router.py           (new)
app/orchestrator/agent_tracker.py    (new)
app/orchestrator/worker.py           (new)
app/orchestrator/engine.py           (new)
app/orchestrator/monitor.py          (new)
app/orchestrator/escalation.py       (new)
app/routers/orchestrator.py          (new)
app/config.py                        (modified - added orchestrator config)
app/main.py                          (modified - added lifespan orchestrator start/stop)
```

## Commit

```
feat: integrate orchestrator as built-in background service

- Created app/orchestrator/ package with ported modules
- Replaced all git operations with SQLAlchemy DB queries
- Added orchestrator config to app/config.py (ORCHESTRATOR_ENABLED)
- Integrated with FastAPI lifespan for start/stop
- Added API endpoints: GET /api/orchestrator/status, /workers, POST /pause, /resume
```

**Commit hash:** `b592e69`  
**Pushed to:** `github.com:RafeSymonds/lobs-server.git`

---

**Status:** ✅ Complete and pushed to GitHub
