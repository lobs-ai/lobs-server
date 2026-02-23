# Orchestrator Flow

**Last Updated:** 2026-02-22

Complete reference for how the lobs-server orchestrator executes tasks autonomously.

---

## Overview

The orchestrator is a background async loop that:
1. **Scans** for eligible tasks
2. **Routes** tasks to appropriate agents
3. **Spawns** workers via OpenClaw Gateway
4. **Monitors** task execution and health
5. **Handles** failures with multi-tier escalation

```
┌─────────────────────────────────────────────────────────┐
│                  Orchestrator Engine                    │
│                  (app/orchestrator/engine.py)           │
└─────────────────────────────────────────────────────────┘
                           │
                           │ Main Loop (every 10s, adaptive)
                           ▼
        ┌──────────────────┬──────────────────┬──────────────────┐
        │                  │                  │                  │
    ┌───▼────┐      ┌──────▼──────┐   ┌──────▼──────┐   ┌──────▼──────┐
    │Scanner │      │  Monitor    │   │  Scheduler  │   │   Inbox     │
    │        │      │  Enhanced   │   │             │   │  Processor  │
    └───┬────┘      └──────┬──────┘   └──────┬──────┘   └──────┬──────┘
        │                  │                  │                  │
        │ eligible_tasks   │ stuck_tasks      │ due_events       │ threads
        ▼                  ▼                  ▼                  ▼
    ┌─────────────────────────────────────────────────────────┐
    │              WorkerManager                              │
    │  - Checks capacity (max workers)                        │
    │  - Checks domain locks (one worker per project)         │
    │  - Consults CircuitBreaker                              │
    │  - Chooses model tier                                   │
    │  - Spawns via Gateway API                               │
    └─────────────────────────────────────────────────────────┘
                           │
                           ▼
            ┌──────────────────────────┐
            │  OpenClaw Gateway API    │
            │  POST /tools/invoke      │
            │  → sessions_spawn        │
            └──────────────────────────┘
                           │
                           ▼
            ┌──────────────────────────┐
            │  Sub-agent runs task     │
            │  (programmer/researcher/ │
            │   writer/architect/etc)  │
            └──────────────────────────┘
                           │
                           │ Webhook callback
                           ▼
            ┌──────────────────────────┐
            │  POST /api/openclaw/     │
            │  webhook                 │
            │  → WorkerManager.        │
            │     handle_completion    │
            └──────────────────────────┘
                           │
                           ▼
        ┌──────────────────┴──────────────────┐
        │                                     │
    Success                              Failure
        │                                     │
        ▼                                     ▼
    Update task:                    ┌──────────────────┐
    - work_state=completed          │  Escalation      │
    - review_state=pending          │  Manager         │
    - Mark for review               │  Enhanced        │
                                    └──────┬───────────┘
                                           │
                        ┌──────────────────┼──────────────────┐
                        │                  │                  │
                    Tier 1             Tier 2             Tier 3+
                  Auto-retry       Agent Switch        Diagnostic/Human
                (same agent)      (try different)      (reviewer analysis
                  max 2x              agent)            or inbox alert)
```

---

## Component Responsibilities

### Scanner (`app/orchestrator/scanner.py`)

**Purpose:** Find tasks ready to be worked on.

**Eligibility criteria:**
- `status='active'`
- `work_state` in `['not_started', 'ready']`
- Sync state compatible: `None`, `'synced'`, or `'local_changed'`
- For GitHub tasks: `eligible_for_claim` or `claimed_by_lobs` must be true

**Key methods:**
- `get_eligible_tasks()` — Returns list of task dicts ready for pickup
- `get_unrouted_tasks()` — Returns tasks with no agent assigned (for diagnostics)
- `get_projects()` — Returns active projects

**Output:** List of task dictionaries with full metadata.

---

### Router (`app/orchestrator/router.py`)

**Purpose:** Determine which agent type should handle a task.

**Routing priority:**
1. **Explicit assignment** — If `task.agent` is set, use it
2. **Keyword matching** — Regex patterns for specialized agents:
   - `researcher` — research, investigate, explore, analyze, proof of concept
   - `writer` — write doc/summary/report, draft documentation
   - `architect` — design system, architect, restructure
   - `reviewer` — code review, audit, failure analysis
3. **Default** — Falls back to `programmer`

**Important:** The engine now **requires explicit agent assignment**. If no agent is set, it creates an inbox item requesting Lobs (project-manager) to assign one. This prevents guessing and ensures proper delegation.

**Key methods:**
- `route(task: dict) -> str` — Returns agent type string

---

### WorkerManager (`app/orchestrator/worker.py`)

**Purpose:** Spawn and track OpenClaw workers, enforce concurrency limits.

**Responsibilities:**
1. **Capacity management** — Enforces `MAX_WORKERS` limit (default 3)
2. **Domain locking** — One worker per project (prevents git conflicts)
3. **Circuit breaker consultation** — Blocks spawning if infrastructure is failing
4. **Model tier selection** — Chooses appropriate model tier for task
5. **Worker spawning** — HTTP POST to Gateway API (`/tools/invoke` → `sessions_spawn`)
6. **Lifecycle tracking** — Monitors active workers via Gateway API
7. **Completion handling** — Processes webhook callbacks when workers finish

**Concurrency model:**
- Workers run in **parallel** (up to `MAX_WORKERS` simultaneously)
- **Project locks** prevent multiple workers on same project
- Same agent type can run on **different projects** concurrently

**State tracking (in-memory):**
```python
active_workers: dict[str, WorkerInfo]  # worker_id -> WorkerInfo
project_locks: dict[str, str]          # project_id -> task_id
```

**Key methods:**
- `spawn_worker(task, project_id, agent_type)` — Spawn a new worker
- `check_workers()` — Poll worker status via Gateway API
- `handle_completion(run_id, result)` — Process worker completion webhook
- `get_worker_status()` — Return current worker state for monitoring

**Worker spawning flow:**
```
1. Check capacity (len(active_workers) < MAX_WORKERS)
2. Check project lock (project not already locked)
3. Check circuit breaker (infrastructure healthy)
4. Choose model tier (via ModelChooser)
5. Build task prompt (via Prompter)
6. POST to Gateway API:
   {
     "tool": "sessions_spawn",
     "args": {
       "task": "<prompt>",
       "agentId": "<agent_type>",
       "model": "<model_tier>",
       "label": "task-<short_id>",
       ...
     }
   }
7. Track worker in active_workers dict
8. Set project lock
9. Update task: work_state='in_progress', worker_id=<run_id>
```

---

### CircuitBreaker (`app/orchestrator/circuit_breaker.py`)

**Purpose:** Detect infrastructure failures and prevent cascading retries.

**States:**
- **CLOSED** — Normal operation, spawning allowed
- **OPEN** — Infrastructure failure detected, spawning paused
- **HALF_OPEN** — Cooldown elapsed, allowing probe spawn

**Failure classification:**
Infrastructure failures (not task-specific):
- Gateway auth errors
- Session file locks
- Missing API keys
- Service unavailable (ECONNREFUSED, ETIMEDOUT)
- Rate limiting (429, "too many requests")
- All models failed / failover exhausted

**Tracked per:**
- Global (all spawning)
- Per-project
- Per-agent type

**Parameters:**
- `threshold` — Consecutive failures before opening (default: 3)
- `cooldown_seconds` — Wait before allowing probe spawn (default: 300s / 5min)

**Key methods:**
- `should_allow_spawn(project_id, agent_type)` — Returns `(allowed: bool, reason: str)`
- `record_failure(task_id, project_id, agent_type, error_log)` — Track failure, update state
- `record_success(project_id, agent_type)` — Reset circuit on success

**Integration:**
WorkerManager consults circuit breaker **before spawning** each worker. If circuit is open, spawn is blocked and task remains queued.

---

### Monitor Enhanced (`app/orchestrator/monitor_enhanced.py`)

**Purpose:** Detect and remediate stuck/failing tasks.

**Features:**

1. **Stuck task detection**
   - Finds tasks in `in_progress` with no recent updates
   - Checks worker heartbeat freshness
   - Severity levels: medium (15m), high (30m), critical (60m)
   - Auto-creates inbox alerts for stuck tasks

2. **Auto-unblock**
   - Detects tasks blocked by completed dependencies
   - Automatically moves them back to `ready` state

3. **Failure pattern detection**
   - Identifies tasks failing repeatedly
   - Triggers escalation or human review

4. **Worker health monitoring**
   - Tracks worker process health
   - Detects zombie workers (process running but no progress)

**Timeouts:**
- `stuck_timeout` — 15 minutes (initial detection)
- `warning_timeout` — 30 minutes (escalate severity)
- `kill_timeout` — 60 minutes (critical, force termination)

**Key methods:**
- `run_full_check()` — Execute all monitoring checks
- `check_stuck_tasks()` — Find and mark stuck tasks
- `auto_unblock_tasks()` — Unblock tasks with resolved dependencies

**Called by:** Engine main loop (every iteration)

---

### Escalation Manager Enhanced (`app/orchestrator/escalation_enhanced.py`)

**Purpose:** Multi-tier failure handling with progressive escalation.

**Escalation tiers:**

| Tier | Action | When | Details |
|------|--------|------|---------|
| 0 | None | Initial state | First attempt, no escalation |
| 1 | Auto-retry | After first failure | Same agent, max 2 retries |
| 2 | Agent switch | After 2 failed retries | Try alternative agent type |
| 3 | Diagnostic | After agent switch fails | Spawn `reviewer` to analyze |
| 4 | Human escalation | After all auto-remediation | Create inbox alert, wait for manual fix |

**Agent alternatives (Tier 2):**
```python
{
  "programmer": ["architect", "reviewer"],
  "architect": ["programmer", "researcher"],
  "researcher": ["writer", "programmer"],
  "writer": ["researcher", "programmer"],
  "reviewer": ["architect", "programmer"],
}
```

**Task state updates:**
- `escalation_tier` — Current tier (0-4)
- `retry_count` — Number of retries attempted
- `failure_reason` — Last error message (truncated to 1000 chars)
- `last_retry_reason` — Why retry was triggered
- `notes` — Appends escalation history

**Key methods:**
- `handle_failure(task_id, project_id, agent_type, error_log, exit_code)` — Main entry point
- `_tier_1_auto_retry(task, agent_type, error_log)` — Simple retry logic
- `_tier_2_agent_switch(task, agent_type, error_log)` — Pick alternative agent
- `_tier_3_diagnostic(task, agent_type, error_log)` — Spawn reviewer analysis
- `_tier_4_human_escalation(task, agent_type, error_log)` — Create inbox alert

**Integration:**
WorkerManager calls `handle_failure()` when a worker returns non-zero exit code or fails to complete.

---

## Execution Flow (Happy Path)

Step-by-step walkthrough of a successful task execution:

### 1. Engine Loop Iteration

```python
# app/orchestrator/engine.py: _run_once()
scanner = Scanner(db)
eligible_tasks = await scanner.get_eligible_tasks()
```

**Result:** List of tasks with `status='active'`, `work_state='not_started'`

### 2. Task Routing

```python
for task_dict in eligible_tasks:
    agent_type = task_dict.get("agent")
    
    if not agent_type:
        # Create inbox item requesting Lobs to assign agent
        await self._request_lobs_assignment(db, task_dict)
        continue
```

**Result:** Either agent is explicitly set, or Lobs is asked to assign one.

### 3. Circuit Breaker Check

```python
circuit_breaker = CircuitBreaker(db)
allowed, reason = await circuit_breaker.should_allow_spawn(
    project_id=project_id,
    agent_type=agent_type
)

if not allowed:
    logger.warning(f"Circuit breaker blocked spawn: {reason}")
    continue
```

**Result:** Spawn allowed (circuit CLOSED) or blocked (circuit OPEN).

### 4. Worker Spawn

```python
worker_manager = WorkerManager(db)
spawned = await worker_manager.spawn_worker(
    task=task_dict,
    project_id=project_id,
    agent_type=agent_type
)
```

**Inside `spawn_worker()`:**

```python
# 1. Check capacity
if len(self.active_workers) >= self.max_workers:
    return False

# 2. Check project lock
if project_id in self.project_locks:
    return False

# 3. Choose model tier
model_chooser = ModelChooser(db)
model_decision = await model_chooser.choose(agent_type, task)
model = model_decision.models[0]  # Primary model

# 4. Build prompt
prompter = Prompter()
task_prompt = await prompter.build_prompt(task, project)

# 5. Spawn via Gateway API
async with aiohttp.ClientSession() as session:
    resp = await session.post(
        f"{GATEWAY_URL}/tools/invoke",
        headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
        json={
            "tool": "sessions_spawn",
            "args": {
                "task": task_prompt,
                "agentId": agent_type,
                "model": model,
                "label": f"task-{task_id[:8]}",
                "runTimeoutSeconds": 1800,  # 30 min default
                ...
            }
        }
    )
    data = await resp.json()

# 6. Track worker
run_id = data["result"]["runId"]
self.active_workers[run_id] = WorkerInfo(
    run_id=run_id,
    task_id=task_id,
    project_id=project_id,
    agent_type=agent_type,
    model=model,
    start_time=time.time(),
    ...
)

# 7. Lock project
self.project_locks[project_id] = task_id

# 8. Update task state
task_obj = await db.get(Task, task_id)
task_obj.work_state = 'in_progress'
task_obj.worker_id = run_id
await db.commit()
```

**Result:** Worker spawned, running in OpenClaw Gateway, task marked `in_progress`.

### 5. Worker Execution

The sub-agent (programmer/researcher/writer/etc) executes in its own workspace:
- Reads project context files
- Performs the task (writes code, researches, writes docs, etc)
- Writes output to appropriate location
- Exits with code 0 (success) or 1 (failure)

### 6. Completion Webhook

When worker finishes, Gateway calls back:

```http
POST /api/openclaw/webhook
Content-Type: application/json

{
  "event": "run.completed",
  "runId": "abc123",
  "success": true,
  "transcript": "...",
  "outputs": {...}
}
```

**Handled by:**
```python
# app/services/openclaw_bridge.py
async def handle_completion(run_id: str, result: dict):
    worker_manager.handle_completion(run_id, result)
```

### 7. Post-Completion Processing

```python
# app/orchestrator/worker.py: handle_completion()

if result["success"]:
    # Success path
    task.work_state = 'completed'
    task.review_state = 'pending'
    task.completed_at = datetime.now(timezone.utc)
    
    # Log usage
    await log_usage_event(
        db,
        agent_type=agent_type,
        model=model,
        task_id=task_id,
        success=True,
        ...
    )
    
    # Release locks
    del self.active_workers[run_id]
    del self.project_locks[project_id]
    
else:
    # Failure path
    escalation_mgr = EscalationManagerEnhanced(db)
    result = await escalation_mgr.handle_failure(
        task_id=task_id,
        project_id=project_id,
        agent_type=agent_type,
        error_log=error_log,
        exit_code=exit_code
    )
    
    # Circuit breaker tracking
    is_infra = await circuit_breaker.record_failure(...)
```

**Result:** Task either completed successfully or escalated for retry/remediation.

---

## Failure Paths

### Infrastructure Failure (Circuit Breaker Opens)

```
Worker fails with infrastructure error
   (e.g., "Gateway auth failed", "All models failed")
          │
          ▼
CircuitBreaker.record_failure()
   classifies as infrastructure failure
          │
          ▼
Increment consecutive_failures counter
          │
          ▼
   consecutive_failures >= threshold?
          │
      YES │
          ▼
Circuit opens (is_open = True)
Set opened_at timestamp
          │
          ▼
Subsequent spawn attempts:
  should_allow_spawn() returns (False, reason)
          │
          ▼
Tasks queue up, no new spawns
          │
          ▼
Wait cooldown_seconds (default 5min)
          │
          ▼
Circuit enters HALF_OPEN state
          │
          ▼
Allow one probe spawn
          │
      ┌───┴───┐
  Success   Failure
      │       │
      ▼       ▼
  Circuit   Circuit
  closes    reopens
  (reset)   (extend cooldown)
```

### Task-Level Failure (Escalation)

```
Worker fails with task-specific error
   (e.g., "Test failed", "Build error", "Missing dependency")
          │
          ▼
CircuitBreaker.record_failure()
   classifies as task failure (not infrastructure)
          │
          ▼
CircuitBreaker resets consecutive_failures = 0
   (this is a task problem, not infra)
          │
          ▼
EscalationManager.handle_failure()
          │
          ▼
Check current escalation_tier and retry_count
          │
    ┌─────┼─────┬─────┬─────┐
    │     │     │     │     │
  Tier0 Tier1 Tier2 Tier3 Tier4
    │     │     │     │     │
    ▼     ▼     ▼     ▼     ▼
  Retry Switch Diag Human Block
  same  agent  review alert task
  agent  type  task   wait
  (max2x)
```

**Tier 1: Auto-Retry**
```python
task.escalation_tier = 1
task.retry_count += 1
task.work_state = 'not_started'  # Re-queue
task.status = 'active'
# Task picked up in next scan
```

**Tier 2: Agent Switch**
```python
task.escalation_tier = 2
task.retry_count = 0  # Reset for new agent
new_agent = alternatives[current_agent][0]
task.agent = new_agent
task.work_state = 'not_started'
# Task picked up with new agent
```

**Tier 3: Diagnostic**
```python
task.escalation_tier = 3
task.work_state = 'blocked'

# Spawn reviewer to analyze
diagnostic_task = create_task(
    title=f"Diagnostic: {task.title}",
    agent='reviewer',
    notes=f"Analyze failure:\n{error_log}",
    parent_task_id=task.id
)
```

**Tier 4: Human Escalation**
```python
task.escalation_tier = 4
task.work_state = 'blocked'
task.status = 'blocked'

# Create inbox alert
inbox_item = InboxItem(
    title=f"⚠️ Task Failed (all auto-remediation exhausted): {task.title}",
    content=f"Manual intervention required.\n\nError:\n{error_log}",
    ...
)
# Human must manually fix and reset task
```

---

## Monitoring & Health Checks

The monitor runs on every engine loop iteration:

### Stuck Task Detection

```python
# Find tasks in_progress with stale updated_at
now = datetime.now(timezone.utc)
stuck_cutoff = now - timedelta(seconds=900)  # 15 min

tasks = db.query(Task).filter(
    Task.work_state == 'in_progress',
    Task.updated_at < stuck_cutoff
)

for task in tasks:
    # Check worker heartbeat
    worker = db.query(WorkerStatus).filter(
        WorkerStatus.current_task == task.id,
        WorkerStatus.active == True
    ).first()
    
    if not worker or (now - worker.last_heartbeat).seconds > 900:
        # Task is stuck
        task.work_state = 'blocked'
        task.failure_reason = f"Stuck - no progress for {age_minutes}m"
        
        # Create inbox alert
        create_stuck_task_alert(task)
```

### Auto-Unblock

```python
# Find blocked tasks with completed dependencies
blocked_tasks = db.query(Task).filter(
    Task.work_state == 'blocked',
    Task.blocked_by != None
)

for task in blocked_tasks:
    blocking_task = db.get(Task, task.blocked_by)
    
    if blocking_task and blocking_task.work_state == 'completed':
        # Dependency resolved
        task.work_state = 'ready'
        task.blocked_by = None
        logger.info(f"Auto-unblocked task {task.id}")
```

---

## Control Loop Phases

Beyond task execution, the engine runs several control loop phases:

### 1. Scheduler Check (every 60s)
```python
scheduler = EventScheduler(db)
result = await scheduler.check_due_events()
# Fires calendar events, creates tasks from recurring schedules
```

### 2. Routine Registry (every 60s)
```python
runner = RoutineRunner(db)
result = await runner.process_due_routines(limit=10)
# Runs scheduled routines (memory maintenance, health checks, etc)
```

### 3. Inbox Processing (every 45s)
```python
inbox_processor = InboxProcessor(db)
result = await inbox_processor.process_threads()
# Routes inbox items to appropriate handlers
```

### 4. Reflection Cycle (every 3 hours, configurable)
```python
reflection_manager = ReflectionCycleManager(db, worker_manager)
result = await reflection_manager.run_strategic_reflection_cycle()
# Spawns reflection sessions for each agent to analyze inefficiencies,
# propose improvements, surface system issues
```

### 5. Daily Compression (once per day at configured hour)
```python
daily_result = await reflection_manager.run_daily_compression()
# Consolidates agent memories, prunes stale data, archives old sessions
```

### 6. Initiative Sweep (triggered after reflection batch completes)
```python
sweep_arbitrator = SweepArbitrator(db, worker_manager)
result = await sweep_arbitrator.run_once()
# Reviews proposed initiatives from reflections,
# applies policy rules (auto-approve low-risk, escalate high-risk to Lobs)
```

### 7. Diagnostic Triggers (every 10 minutes)
```python
diagnostic_engine = DiagnosticTriggerEngine(db, worker_manager)
result = await diagnostic_engine.run_once()
# Detects patterns requiring investigation:
#   - High failure rates
#   - Stuck initiative backlogs
#   - Performance regressions
```

---

## Key Configuration

All orchestrator config lives in `app/orchestrator/config.py`:

```python
# Polling
POLL_INTERVAL = 10  # seconds between engine loop iterations

# Capacity
MAX_WORKERS = 3  # max concurrent workers

# Timeouts
WORKER_WARNING_TIMEOUT = 1800  # 30 minutes
WORKER_KILL_TIMEOUT = 3600     # 60 minutes

# Gateway
GATEWAY_URL = "http://localhost:17777"  # OpenClaw Gateway
GATEWAY_TOKEN = os.getenv("OPENCLAW_TOKEN")
GATEWAY_SESSION_KEY = "lobs-orchestrator"

# Paths
WORKER_RESULTS_DIR = "/Users/lobs/lobs-orchestrator/results"
```

**Runtime-configurable settings** (stored in `orchestrator_settings` table):

| Key | Default | Description |
|-----|---------|-------------|
| `reflection_interval_seconds` | 10800 (3h) | How often to run strategic reflection |
| `diagnostic_interval_seconds` | 600 (10min) | How often to check diagnostic triggers |
| `github_sync_interval_seconds` | 120 (2min) | How often to sync GitHub-backed projects |
| `openclaw_model_sync_interval_seconds` | 900 (15min) | How often to fetch model catalog |
| `daily_compression_hour_et` | 3 | What hour (ET) to run daily compression |

---

## Debugging Tips

### Check orchestrator status
```bash
curl http://localhost:8000/api/orchestrator/status
```

### Check active workers
```bash
curl http://localhost:8000/api/orchestrator/workers
```

### Pause/resume orchestrator
```bash
curl -X POST http://localhost:8000/api/orchestrator/pause
curl -X POST http://localhost:8000/api/orchestrator/resume
```

### View worker logs
```bash
tail -f /Users/lobs/lobs-orchestrator/logs/worker-<run_id>.log
```

### Check circuit breaker state
```python
from app.orchestrator.circuit_breaker import CircuitBreaker
from app.database import AsyncSessionLocal

async with AsyncSessionLocal() as db:
    cb = CircuitBreaker(db)
    # Check global circuit
    print(cb.global_circuit)
    # Check project circuit
    print(cb.project_circuits.get("project-id"))
```

### Force reflection cycle
```bash
curl -X POST http://localhost:8000/api/orchestrator/trigger-reflection
```

### View escalation state
```sql
SELECT id, title, escalation_tier, retry_count, failure_reason
FROM tasks
WHERE escalation_tier > 0
ORDER BY updated_at DESC;
```

---

## See Also

- [Model Routing](./model-routing.md) — How model tiers are selected
- [Agent Capabilities](./agent-capabilities.md) — What each agent type can do
- [Patterns Guide](../guides/patterns.md) — Error handling, async patterns
- [Observability](../guides/observability.md) — Logging, metrics, debugging
