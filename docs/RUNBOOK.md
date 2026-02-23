# Operational Runbook — lobs-server

Practical guide for common operational tasks and troubleshooting.

**Last Updated:** 2026-02-23

---

## Quick Reference

| Task | Command |
|------|---------|
| Start server | `./bin/run` or `./bin/server start` |
| Stop server | `./bin/server stop` |
| Restart server | `./bin/server restart` |
| Check status | `./bin/server status` |
| View logs | `./bin/server logs` or `tail -f logs/server.log` |
| Pause orchestrator | `curl -X POST http://localhost:8000/api/orchestrator/pause -H "Authorization: Bearer TOKEN"` |
| Resume orchestrator | `curl -X POST http://localhost:8000/api/orchestrator/resume -H "Authorization: Bearer TOKEN"` |
| Generate API token | `python bin/generate_token.py <name>` |
| Trigger backup | `curl -X POST http://localhost:8000/api/backup/trigger -H "Authorization: Bearer TOKEN"` |

---

## Table of Contents

1. [Server Management](#server-management)
2. [Orchestrator Control](#orchestrator-control)
3. [Task Management](#task-management)
4. [Agent & Worker Status](#agent--worker-status)
5. [Database Operations](#database-operations)
6. [API Token Management](#api-token-management)
7. [Logs & Monitoring](#logs--monitoring)
8. [Health Checks](#health-checks)
9. [Troubleshooting](#troubleshooting)

---

## Server Management

### Start Server

**Development (foreground):**
```bash
cd /Users/lobs/lobs-server
source .venv/bin/activate
./bin/run
```

**Production (as launchd service):**
```bash
./bin/server start
```

The server starts on `0.0.0.0:8000` by default.

### Stop Server

```bash
./bin/server stop
```

Or kill the process manually:
```bash
pkill -f "uvicorn app.main:app"
```

### Restart Server

```bash
./bin/server restart
```

Or manually:
```bash
./bin/server stop
sleep 2
./bin/server start
```

### Check Server Status

```bash
./bin/server status
```

**Output examples:**
- `✅ Running (PID 12345, 0.0.0.0:8000)` — Server is up
- `🛑 Not running` — Server is down

**Via API:**
```bash
curl http://localhost:8000/api/health
```

Expected response:
```json
{
  "status": "ok",
  "uptime": "2h 15m 30s",
  "database": "connected"
}
```

---

## Orchestrator Control

The orchestrator automatically spawns agents to work on tasks. You can pause/resume it without restarting the server.

### Check Orchestrator Status

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/status
```

**Response includes:**
- `running`: true/false
- `paused`: true/false
- `workers`: List of active workers
- `queued_tasks`: Number of tasks waiting
- `stuck_tasks`: Tasks that haven't progressed

**Example:**
```json
{
  "running": true,
  "paused": false,
  "workers": 2,
  "queued_tasks": 5,
  "stuck_tasks": 0,
  "uptime": "3h 45m"
}
```

### Pause Orchestrator

Stops spawning new workers but allows active workers to complete:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/pause
```

**Use cases:**
- System maintenance
- Debugging a runaway task
- Preventing new work during high load

### Resume Orchestrator

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/resume
```

### View Active Workers

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/workers
```

**Response:**
```json
{
  "count": 2,
  "workers": [
    {
      "session_id": "agent:programmer:abc123",
      "task_id": "task-456",
      "agent_type": "programmer",
      "started_at": "2026-02-23T14:30:00Z",
      "duration": "15m 30s"
    }
  ]
}
```

### Orchestrator Health Summary

Includes stuck task detection:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/health
```

---

## Task Management

### List Tasks

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/tasks?status=in_progress&limit=20"
```

**Filter parameters:**
- `status` — `todo`, `in_progress`, `review`, `blocked`, `completed`
- `project_id` — Filter by project
- `owner` — Filter by assigned agent
- `limit` — Max results (default 50, max 1000)
- `offset` — Pagination offset

### Get Task Details

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/{task_id}
```

### Clear Stuck Tasks

If tasks are stuck in `in_progress` but no worker is active:

**1. Check for stuck tasks:**
```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/health
```

Look for `stuck_tasks` count.

**2. Manually reset task status:**
```bash
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status": "todo"}' \
  http://localhost:8000/api/tasks/{task_id}/status
```

**3. Alternatively, mark as blocked and add notes:**
```bash
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status": "blocked", "notes": "Reset from stuck state"}' \
  http://localhost:8000/api/tasks/{task_id}
```

### Cancel a Running Task

**Option 1: Update task status (graceful)**
```bash
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status": "blocked"}' \
  http://localhost:8000/api/tasks/{task_id}/status
```

The worker will notice the status change and stop.

**Option 2: Kill the worker process (forceful)**

First, find the worker session:
```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/workers
```

Then kill via OpenClaw (if accessible) or find the process:
```bash
# Find uvicorn workers spawned by orchestrator
ps aux | grep openclaw

# Kill specific session (if you have openclaw CLI)
openclaw sessions kill <session_id>
```

### Retry a Failed Task

```bash
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status": "todo", "work_state": null, "owner": null}' \
  http://localhost:8000/api/tasks/{task_id}
```

This resets the task to `todo`, clearing the owner and work state so the orchestrator picks it up again.

### Archive Old Completed Tasks

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/tasks/auto-archive?older_than_days=30"
```

Archives tasks completed more than 30 days ago.

---

## Agent & Worker Status

### List All Agent Statuses

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/agents
```

**Response:**
```json
[
  {
    "agent_type": "programmer",
    "status": "active",
    "current_task_id": "task-123",
    "tasks_completed_today": 5,
    "last_active_at": "2026-02-23T15:00:00Z"
  }
]
```

### Get Specific Agent Status

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/agents/programmer
```

### Check Active Workers

See [Orchestrator Control → View Active Workers](#view-active-workers).

### Agent Identity Versions

View agent prompt/identity history:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/agents/programmer/identity-versions
```

---

## Database Operations

### Location

Default database location:
```
/Users/lobs/lobs-server/data/lobs.db
/Users/lobs/lobs-server/data/lobs.db-wal
/Users/lobs/lobs-server/data/lobs.db-shm
```

### Backup Status

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/backup/status
```

**Response:**
```json
{
  "enabled": true,
  "last_backup": "2026-02-23T12:00:00Z",
  "next_backup": "2026-02-23T18:00:00Z",
  "backup_count": 15,
  "retention": 30
}
```

### Trigger Manual Backup

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/backup/trigger
```

**Backup location:**
```
/Users/lobs/lobs-server/data/backups/
```

### List Available Backups

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/backup/list
```

### Restore from Backup

**⚠️ DANGER: This overwrites the current database!**

```bash
# 1. Stop the server
./bin/server stop

# 2. List backups
ls -lh data/backups/

# 3. Copy backup over current DB
cp data/backups/lobs-2026-02-23-120000.db data/lobs.db

# 4. Start server
./bin/server start
```

**Via API (with confirmation):**
```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/backup/restore?filename=lobs-2026-02-23-120000.db&confirm=true"
```

### Manual Database Backup (Without API)

```bash
# Stop server first (recommended)
./bin/server stop

# Copy database files
cp data/lobs.db data/backups/manual-$(date +%Y%m%d-%H%M%S).db

# Restart server
./bin/server start
```

### Vacuum Database (Reclaim Space)

```bash
sqlite3 data/lobs.db "VACUUM;"
```

**When to vacuum:**
- After deleting many tasks/projects
- Database file is much larger than expected
- Performance degradation

---

## API Token Management

### Generate a New Token

```bash
cd /Users/lobs/lobs-server
source .venv/bin/activate
python bin/generate_token.py <token-name>
```

**Example:**
```bash
python bin/generate_token.py mission-control-prod
```

**Output:**
```
Token created for 'mission-control-prod':
z5mr-WWjPxAAHvRd2ZULm7HLNW1oRubXmcMiBJoEmsU
```

⚠️ **Save this token** — it's only shown once!

### List All Tokens

```bash
python bin/list_tokens.py
```

**Output:**
```
API Tokens:
1. mission-control (created 2026-02-20)
2. mobile-app (created 2026-02-15)
3. test-token (created 2026-02-10)
```

### Revoke a Token

```bash
python bin/revoke_token.py <token-name>
```

**Example:**
```bash
python bin/revoke_token.py old-test-token
```

### Test a Token

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:8000/api/projects
```

- **200 OK** — Token is valid
- **401 Unauthorized** — Token is invalid or revoked

---

## Logs & Monitoring

### Log Files

**Main server log:**
```bash
tail -f logs/server.log
```

**Error log:**
```bash
tail -f logs/error.log
```

**View recent logs via script:**
```bash
./bin/server logs    # Last 50 lines
./bin/server tail    # Follow in real-time
```

### Log Locations

```
/Users/lobs/lobs-server/logs/
├── server.log       # All server activity
├── error.log        # Error-level only
└── error.log.1      # Rotated logs
```

### What to Look For

**Normal operation:**
```
INFO: Started server process [12345]
INFO: Uvicorn running on http://0.0.0.0:8000
INFO: [ENGINE] Orchestrator started
INFO: [SCANNER] Found 3 eligible tasks
INFO: [WORKER] Spawned programmer for task-123
```

**Problems:**
```
ERROR: Database connection failed
ERROR: [WORKER] Task task-456 failed: timeout
WARNING: [MONITOR] Stuck task detected: task-789
ERROR: OpenClaw Gateway not reachable
```

### Filter Logs

**Show only errors:**
```bash
grep ERROR logs/server.log | tail -20
```

**Show orchestrator activity:**
```bash
grep "\[ENGINE\]\|\[SCANNER\]\|\[WORKER\]" logs/server.log | tail -50
```

**Show task-specific logs:**
```bash
grep "task-abc123" logs/server.log
```

### System Activity Timeline

View recent system activity via API:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/status/timeline?limit=50"
```

Returns events like task starts, completions, agent spawns, errors.

---

## Health Checks

### Basic Health Check

```bash
curl http://localhost:8000/api/health
```

**Success:**
```json
{
  "status": "ok",
  "uptime": "5h 30m",
  "database": "connected"
}
```

**Failure scenarios:**
- Server not responding → Check if process is running
- `"database": "error"` → Database file locked or corrupted
- Timeout → Server overloaded or stuck

### Orchestrator Health

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/health
```

**Includes:**
- Active worker count
- Stuck task detection
- Recent failures
- Circuit breaker status (infrastructure health)

### Database Health

Check database is accessible:

```bash
sqlite3 data/lobs.db "SELECT COUNT(*) FROM tasks;"
```

Should return a number. If it hangs or errors, database may be locked.

### Check OpenClaw Gateway

The orchestrator requires OpenClaw Gateway to spawn agents:

```bash
curl http://localhost:18789/status
```

**Expected:**
```json
{
  "status": "ok",
  "version": "..."
}
```

If unreachable, orchestrator will log errors but API will still work.

---

## Troubleshooting

### Server Won't Start

**Symptoms:**
- `./bin/server start` fails
- Port 8000 already in use

**Solutions:**

1. **Check if already running:**
   ```bash
   ./bin/server status
   lsof -i :8000
   ```

2. **Kill existing process:**
   ```bash
   pkill -f "uvicorn app.main:app"
   ```

3. **Check logs for startup errors:**
   ```bash
   tail -50 logs/error.log
   ```

4. **Common startup errors:**
   - Database locked → Stop all server instances
   - Missing dependencies → `pip install -r requirements.txt`
   - Port in use → Change port in `bin/run` or stop conflicting process

### Orchestrator Not Starting Tasks

**Symptoms:**
- Tasks stay in `todo` status
- No workers appear in `/api/orchestrator/workers`
- Logs show orchestrator polling but no spawns

**Solutions:**

1. **Check orchestrator status:**
   ```bash
   curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/orchestrator/status
   ```

2. **Is it paused?**
   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/orchestrator/resume
   ```

3. **Check OpenClaw Gateway:**
   ```bash
   curl http://localhost:18789/status
   ```
   
   If unreachable:
   - Start OpenClaw: `openclaw gateway start`
   - Check `OPENCLAW_GATEWAY_URL` in `.env`
   - Check `OPENCLAW_GATEWAY_TOKEN` matches Gateway config

4. **Check for task eligibility issues:**
   - Tasks must have `status = 'todo'`
   - Tasks must have `work_state = null` or `'not_started'`
   - Tasks must not have `owner` set
   - Project must not be archived

5. **Check logs:**
   ```bash
   grep "\[SCANNER\]\|\[ROUTER\]" logs/server.log | tail -20
   ```

### Tasks Stuck in `in_progress`

**Symptoms:**
- Task shows `in_progress` but no worker is active
- Task has been in progress for hours/days

**Solutions:**

1. **Check if worker is actually running:**
   ```bash
   curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/orchestrator/workers
   ```

2. **If no worker found, reset task:**
   ```bash
   curl -X PATCH -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"status": "todo", "owner": null, "work_state": null}' \
     http://localhost:8000/api/tasks/{task_id}
   ```

3. **If worker exists but stuck, check OpenClaw:**
   ```bash
   # If you have openclaw CLI
   openclaw sessions list
   ```

4. **Force-kill worker (last resort):**
   Find the process and kill it, then reset task status.

### Database Locked Errors

**Symptoms:**
- API requests return "database is locked"
- Server logs show `OperationalError: database is locked`

**Solutions:**

1. **Check for multiple server instances:**
   ```bash
   pgrep -af "uvicorn app.main:app"
   ```
   
   Should show only one process. If multiple, kill extras.

2. **Close any open SQLite connections:**
   ```bash
   # Find processes with DB open
   lsof data/lobs.db
   
   # Kill them if they're not the server
   kill <PID>
   ```

3. **WAL mode issues (rare):**
   ```bash
   ./bin/server stop
   sqlite3 data/lobs.db "PRAGMA journal_mode=WAL;"
   ./bin/server start
   ```

4. **Last resort — restart server:**
   ```bash
   ./bin/server restart
   ```

### High Memory Usage

**Symptoms:**
- Server process using >2GB RAM
- System slowdown
- Out of memory errors

**Solutions:**

1. **Check active workers:**
   ```bash
   curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/orchestrator/workers
   ```
   
   Too many workers can cause high memory. Reduce `ORCHESTRATOR_MAX_WORKERS` in `.env`.

2. **Pause orchestrator temporarily:**
   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/orchestrator/pause
   ```

3. **Check for memory leaks in logs:**
   ```bash
   grep -i "memory\|malloc\|oom" logs/error.log
   ```

4. **Restart server:**
   ```bash
   ./bin/server restart
   ```

### Backup Failures

**Symptoms:**
- Backups not appearing in `data/backups/`
- Backup status shows errors

**Solutions:**

1. **Check backup status:**
   ```bash
   curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/backup/status
   ```

2. **Check disk space:**
   ```bash
   df -h
   ```

3. **Check backup directory permissions:**
   ```bash
   ls -la data/backups/
   mkdir -p data/backups/
   chmod 755 data/backups/
   ```

4. **Manually trigger backup:**
   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/backup/trigger
   ```

5. **Check logs:**
   ```bash
   grep -i backup logs/server.log | tail -20
   ```

### Can't Connect from Mission Control

**Symptoms:**
- Mission Control shows "Connection failed"
- API requests timeout or refuse connection

**Solutions:**

1. **Check server is running:**
   ```bash
   ./bin/server status
   curl http://localhost:8000/api/health
   ```

2. **Check firewall (if remote):**
   ```bash
   # Test from Mission Control machine
   curl http://<server-ip>:8000/api/health
   ```

3. **Check server is bound to correct interface:**
   - `127.0.0.1` → localhost only (not accessible remotely)
   - `0.0.0.0` → all interfaces (accessible remotely)
   
   Server uses `0.0.0.0` by default. Check `bin/run` if unsure.

4. **Verify API token:**
   ```bash
   curl -H "Authorization: Bearer YOUR_TOKEN" \
     http://localhost:8000/api/projects
   ```
   
   Should return `200 OK`, not `401 Unauthorized`.

5. **Check Mission Control logs:**
   Look for connection errors, certificate issues, or wrong URL.

---

## Emergency Procedures

### Complete System Reset (Nuclear Option)

⚠️ **This deletes all data!**

```bash
# 1. Stop server
./bin/server stop

# 2. Backup database (just in case)
cp data/lobs.db /tmp/lobs-backup-$(date +%Y%m%d).db

# 3. Remove database
rm data/lobs.db data/lobs.db-wal data/lobs.db-shm

# 4. Start server (recreates fresh DB)
./bin/server start

# 5. Generate new API token
python bin/generate_token.py fresh-start
```

### Restore from Backup

See [Database Operations → Restore from Backup](#restore-from-backup).

### Server Completely Unresponsive

```bash
# 1. Kill the process
pkill -9 -f "uvicorn app.main:app"

# 2. Check for hung database connections
lsof data/lobs.db

# 3. Kill any lingering processes
kill -9 <PIDs from lsof>

# 4. Start server fresh
./bin/server start
```

---

## Configuration Reference

Key environment variables (set in `.env`):

```bash
# Database
DATABASE_PATH=./data/lobs.db

# Orchestrator
ORCHESTRATOR_ENABLED=true
ORCHESTRATOR_POLL_INTERVAL=10
ORCHESTRATOR_MAX_WORKERS=3

# OpenClaw Gateway
OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789
OPENCLAW_GATEWAY_TOKEN=your-token-here

# Backups
BACKUP_ENABLED=true
BACKUP_INTERVAL_HOURS=6
BACKUP_RETENTION_COUNT=30

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=console
```

See [`.env.example`](../.env.example) for full reference.

---

## See Also

- **[QUICKSTART.md](../QUICKSTART.md)** — Initial setup guide
- **[ARCHITECTURE.md](../ARCHITECTURE.md)** — System design overview
- **[AGENTS.md](../AGENTS.md)** — Complete API reference
- **[TESTING.md](TESTING.md)** — Testing guide
- **[docs/](.)** — Additional technical documentation

---

**Questions or issues not covered here?**

Check the logs first:
```bash
tail -100 logs/error.log
```

Then check existing docs or create an issue in the repo.
