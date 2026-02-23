# Orchestrator Flow — lobs-server

Complete documentation of orchestrator component interactions, data flows, and failure handling.

**Last Updated:** 2026-02-22

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Main Execution Flow](#main-execution-flow)
3. [Component Responsibilities](#component-responsibilities)
4. [Failure Escalation](#failure-escalation)
5. [Circuit Breaker](#circuit-breaker)
6. [Reflection Cycle](#reflection-cycle)
7. [Model Routing](#model-routing)
8. [Data Flow Diagrams](#data-flow-diagrams)

---

## System Overview

The orchestrator is a continuous-polling engine that autonomously executes tasks by spawning AI agent workers via the OpenClaw Gateway. It handles:

- **Task discovery** — Finding ready-to-work tasks from the database
- **Intelligent routing** — Selecting the right agent type for each task
- **Worker lifecycle** — Spawning, monitoring, and cleanup
- **Failure handling** — Multi-tier escalation and circuit breaking
- **Strategic reflection** — Periodic agent self-improvement cycles
- **Model selection** — Choosing appropriate AI models with fallback chains

**Key Design Principles:**
- Incremental resilience (failures don't cascade)
- Adaptive behavior (learns from patterns)
- Domain isolation (one worker per project prevents conflicts)
- Push-based completion (workers report back via webhook)

---

## Main Execution Flow

### High-Level Overview

```
┌──────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR ENGINE                        │
│                   (app/orchestrator/engine.py)                │
│                                                               │
│  Main Loop (every ~10s when active):                         │
│  1. Poll database for eligible work                          │
│  2. Route tasks to appropriate agents                        │
│  3. Spawn workers via OpenClaw Gateway                       │
│  4. Monitor worker health and completion                     │
│  5. Handle failures with escalation                          │
│  6. Periodic reflection and maintenance                      │
└──────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 1: SCANNER                                            │
│  (app/orchestrator/scanner.py)                              │
│                                                             │
│  • Query tasks: status='active', work_state='ready'        │
│  • Filter by sync_state compatibility                      │
│  • Check GitHub eligibility (if external source)           │
│  • Return list of eligible task dicts                      │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 2: ROUTER                                             │
│  (app/orchestrator/router.py)                               │
│                                                             │
│  Decision priority:                                         │
│  1. Explicit task.agent field → use specified agent        │
│  2. Keyword regex matching → route by intent               │
│  3. Default → "programmer"                                  │
│                                                             │
│  Regex patterns:                                            │
│  • "research", "investigate" → researcher                   │
│  • "write doc", "documentation" → writer                    │
│  • "design system", "architect" → architect                 │
│  • "code review", "audit" → reviewer                        │
│  • (no match) → programmer                                  │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 3: WORKER MANAGER                                     │
│  (app/orchestrator/worker_manager.py)                       │
│                                                             │
│  Pre-spawn checks:                                          │
│  • Max workers capacity (default: 5)                        │
│  • Project lock (one worker per project)                    │
│  • Circuit breaker status                                   │
│                                                             │
│  Spawn workflow:                                            │
│  1. Build task prompt (via Prompter)                        │
│  2. Choose model tier (via ModelChooser)                    │
│  3. Call OpenClaw Gateway sessions_spawn API                │
│  4. Register worker in active_workers map                   │
│  5. Acquire project lock                                    │
│  6. Update task.work_state = 'in_progress'                  │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 4: OPENCLAW GATEWAY                                   │
│  (External: OpenClaw agent spawner)                         │
│                                                             │
│  • Receives: task prompt, agent type, model preference     │
│  • Spawns: isolated agent session with workspace           │
│  • Returns: runId, childSessionKey for tracking            │
│  • Reports: completion/failure via webhook to /api/webhook │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 5: WORKER EXECUTION                                   │
│  (Runs in OpenClaw agent context)                           │
│                                                             │
│  Agent receives full context:                               │
│  • Project README, ARCHITECTURE, AGENTS.md                  │
│  • Task title and detailed notes                            │
│  • Engineering rules (if defined)                           │
│  • Acceptance criteria                                      │
│                                                             │
│  Agent autonomy:                                            │
│  • Reads relevant files                                     │
│  • Implements solution                                      │
│  • Runs tests                                               │
│  • Writes .work-summary                                     │
│                                                             │
│  Completion:                                                │
│  • Webhook POST to lobs-server with result                 │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 6: COMPLETION HANDLING                                │
│  (app/services/openclaw_bridge.py → worker_manager)         │
│                                                             │
│  Success path:                                              │
│  • Extract usage from transcript (token counts)             │
│  • Update task.work_state = 'ready_for_review'              │
│  • Release project lock                                     │
│  • Remove from active_workers                               │
│  • Record success in circuit breaker                        │
│  • Log usage metrics                                        │
│                                                             │
│  Failure path:                                              │
│  • Classify error type (task vs infrastructure)            │
│  • Record in circuit breaker                                │
│  • Trigger escalation manager                               │
│  • Release project lock                                     │
│  • Remove from active_workers                               │
└─────────────────────────────────────────────────────────────┘
```

---

## Component Responsibilities

### Engine (`engine.py`)

**Role:** Main orchestration loop coordinator

**Key Responsibilities:**
- Runs async polling loop (adaptive interval: 10-60s)
- Coordinates all subsystems (scanner, worker manager, monitor, scheduler)
- Maintains persistent worker manager across ticks
- Triggers periodic jobs:
  - Reflection cycles (every 3 hours)
  - Daily compression (3 AM ET)
  - Memory maintenance (daily)
  - GitHub sync (every 2 minutes)
  - Model catalog refresh (every 15 minutes)
  - Diagnostic triggers (every 10 minutes)

**State Management:**
- `_running`: Engine active flag
- `_paused`: Manual pause state
- `_worker_manager`: Persistent worker lifecycle manager
- `provider_health`: Tracks model provider success/failure rates

**Control Methods:**
- `start()` — Begin orchestration loop
- `stop()` — Graceful shutdown with worker cleanup
- `pause()` — Stop spawning new workers (monitoring only)
- `resume()` — Resume normal operation

---

### Scanner (`scanner.py`)

**Role:** Task discovery and eligibility filtering

**Query Logic:**
```python
status == 'active'
AND work_state IN ('not_started', 'ready')
AND sync_state IN (NULL, 'synced', 'local_changed')
AND _is_pickup_eligible(task)  # GitHub claim check if external
```

**Methods:**
- `get_eligible_tasks()` — Main work queue
- `get_unrouted_tasks()` — Tasks without explicit agent (for diagnostics)
- `get_projects()` — Active project list

**Eligibility Rules:**
- Internal tasks: always eligible if active/ready
- GitHub tasks: must be `eligible_for_claim` or `claimed_by_lobs`
- Sync conflicts: blocked until resolved

---

### Router (`router.py`)

**Role:** Agent type selection via keyword matching

**Routing Priority:**
1. **Explicit agent field** — `task.agent` directly specified
2. **Regex pattern matching** — Keyword detection in title/notes
3. **Default fallback** — "programmer" for unmatched tasks

**Pattern Rules:**
```python
"research", "investigate", "explore"        → researcher
"write doc", "documentation for"            → writer
"design system", "architect", "framework"   → architect
"code review", "audit", "failure analysis"  → reviewer
```

**Design Notes:**
- Stateless (pure function of task dict)
- Conservative (defaults to programmer to avoid misrouting)
- Extensible (pattern list easily configurable)

---

### Worker Manager (`worker_manager.py`)

**Role:** Worker lifecycle management and domain coordination

**Core Data Structures:**
```python
active_workers: dict[str, WorkerInfo]  # run_id → worker state
project_locks: dict[str, str]          # project_id → task_id
```

**Spawn Workflow:**
1. **Capacity check** — Enforce max concurrent workers (default: 5)
2. **Domain lock check** — One worker per project (prevent repo conflicts)
3. **Build prompt** — Use `Prompter.build_task_prompt()` with full context
4. **Model selection** — Call `ModelChooser.choose()` for preference list
5. **Gateway spawn** — HTTP POST to `/tools/invoke` with `sessions_spawn`
6. **Fallback chain** — Try next model if spawn fails (provider issues)
7. **Registration** — Add to `active_workers`, acquire project lock
8. **DB update** — Set `task.work_state = 'in_progress'`

**Health Monitoring:**
- Poll Gateway `/tools/invoke` with `sessions_list` to check status
- Detect stuck workers (no progress for >15 minutes)
- Handle zombie workers (session ended but DB not updated)
- Track runtime and resource usage

**Cleanup:**
- Release project locks on completion/failure
- Remove from active_workers
- Log metrics to usage tracking system

---

### Monitor Enhanced (`monitor_enhanced.py`)

**Role:** Health checks and auto-remediation

**Features:**

**1. Stuck Task Detection**
- Query: `work_state='in_progress' AND updated_at < (now - 15min)`
- Check worker heartbeat via `WorkerStatus` table
- Severity levels: medium (15m), high (30m), critical (60m)
- Auto-create inbox alerts for human review

**2. Auto-Unblock**
- Find tasks in `work_state='blocked'`
- Check if all `blocked_by` dependencies are completed
- Automatically transition to `not_started` if unblocked
- Append note to task with unblock timestamp

**3. Failure Pattern Detection**
- Track tasks failing repeatedly on same error
- Identify systemic issues (e.g., broken tests, missing dependencies)
- Create diagnostic alerts when patterns emerge

**4. System Health Summary**
- Active workers count and runtime
- Task queue depth by state
- Recent failure rates
- Circuit breaker status

---

### Escalation Manager Enhanced (`escalation_enhanced.py`)

**Role:** Multi-tier failure recovery

**Escalation Tiers:**

```
┌─────────────────────────────────────────────────────────┐
│ Tier 0: Initial State                                  │
│ • First attempt, no failures yet                       │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼ (failure)
┌─────────────────────────────────────────────────────────┐
│ Tier 1: Auto-Retry (max 2 retries)                     │
│ • Same agent type                                       │
│ • Reset to work_state='not_started'                     │
│ • Increment retry_count                                 │
│ • Example: "pytest failed" → retry, might be transient │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼ (still failing)
┌─────────────────────────────────────────────────────────┐
│ Tier 2: Agent Switch                                    │
│ • Try alternative agent type                            │
│ • programmer → architect or reviewer                    │
│ • researcher → writer or programmer                     │
│ • Different perspective may solve issue                 │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼ (still failing)
┌─────────────────────────────────────────────────────────┐
│ Tier 3: Diagnostic Run                                 │
│ • Spawn "reviewer" agent to analyze failure             │
│ • Provide full error log and task context              │
│ • Generate report with root cause analysis              │
│ • Suggest fixes or mark as needs-human-review           │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼ (still failing)
┌─────────────────────────────────────────────────────────┐
│ Tier 4: Human Escalation                               │
│ • Create inbox alert with full context                  │
│ • Include all attempt logs and diagnostics              │
│ • Set task.work_state = 'blocked'                       │
│ • Wait for manual intervention                          │
└─────────────────────────────────────────────────────────┘
```

**State Tracking:**
- `task.escalation_tier` — Current tier (0-4)
- `task.retry_count` — Number of retry attempts
- `task.last_retry_reason` — Why/how last retry was triggered
- `task.failure_reason` — Most recent error log (truncated to 1000 chars)

**Alternative Agents Map:**
```python
{
    "programmer": ["architect", "reviewer"],
    "architect": ["programmer", "researcher"],
    "researcher": ["writer", "programmer"],
    "writer": ["researcher", "programmer"],
    "reviewer": ["architect", "programmer"],
}
```

---

### Circuit Breaker (`circuit_breaker.py`)

**Role:** Prevent cascading failures from infrastructure issues

**Problem:** If OpenClaw Gateway is down or misconfigured, we don't want to waste retry attempts on tasks that will all fail for the same infrastructure reason.

**Solution:** Detect infrastructure failures vs. task failures, and pause spawning when infrastructure is broken.

**States:**
```
CLOSED → Normal operation, tasks spawn freely
   │
   ▼ (3+ consecutive infra failures)
OPEN → Spawning paused, cooldown timer starts
   │
   ▼ (after 5 minutes)
HALF_OPEN → Allow one probe spawn to test if issue resolved
   │
   ├─ Success → CLOSED (resume normal operation)
   └─ Failure → OPEN (cooldown restarts)
```

**Infrastructure Failure Patterns:**
```python
# Regex patterns that indicate infra issues, not task issues
"gateway.*auth.*failed"           → gateway_auth
"session file locked"             → session_lock
"No API key found for provider"   → missing_api_key
"ECONNREFUSED|ETIMEDOUT"          → service_unavailable
"All models failed"               → all_models_failed
"rate.?limit|429"                 → rate_limited
```

**Isolation Levels:**
- **Global circuit** — Affects all spawning (severe infra failure)
- **Project circuit** — Per-project isolation (repo-specific issues)
- **Agent circuit** — Per-agent-type isolation (agent config problems)

**Cooldown Period:** 5 minutes (configurable)

**Threshold:** 3 consecutive infrastructure failures

**Behavior:**
- Task failures classified as "task-level" reset the circuit
- Infrastructure failures increment counters
- When threshold reached, circuit opens and spawning pauses
- After cooldown, allow probe spawn to test recovery
- Success closes circuit, failure restarts cooldown

---

### Reflection Cycle (`reflection_cycle.py`)

**Role:** Strategic self-improvement for AI agents

**Purpose:** Periodically pause and analyze recent work to extract patterns, update heuristics, and compress learnings into agent identity.

**Cycle Types:**

**1. Strategic Reflection (every 3 hours)**
```
For each execution agent (programmer, researcher, etc.):
  1. Build context packet (last 6 hours of work)
  2. Spawn isolated reflection worker
  3. Prompt: "Analyze your recent work, extract patterns"
  4. Store result in AgentReflection table
  5. Mark for later compression
```

**Context Packet Contents:**
- Tasks completed, failed, blocked
- Common error patterns
- Success patterns
- Time/cost metrics
- Model performance stats

**2. Daily Compression (3 AM ET)**
```
For each agent with reflections in last 24 hours:
  1. Collect all strategic reflections
  2. Compress into updated identity text
  3. Extract changed heuristics and removed rules
  4. Validate with Lobs safety gate
  5. If valid → create new AgentIdentityVersion (active=True)
  6. If invalid → create candidate version (active=False)
  7. Deactivate previous version
```

**Lobs Validation Gate:**
Prevents regressions and ensures quality:
- Require meaningful data (not just raw transcripts)
- Check for excessive rule removal (>50% = suspicious)
- Verify heuristic changes are backed by evidence
- Block if no completed reflections in window

**Sweep Arbitration:**
After reflection batch completes, the sweep arbitrator:
- Analyzes cross-agent patterns
- Proposes system-level improvements
- Creates initiatives for architectural changes
- Flags conflicts or redundancies

**Storage:**
- `AgentReflection` — Individual reflection runs
- `AgentIdentityVersion` — Versioned identity snapshots
- `SystemSweep` — Batch metadata and cross-agent analysis

---

## Failure Escalation

### Complete Failure Flow

```
┌─────────────────────────────────────────────────────────┐
│ Worker fails (exit_code != 0 or error in transcript)   │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ WorkerManager.handle_completion()                      │
│ • Extract error log from transcript                     │
│ • Classify error type (task vs infra)                  │
└─────────────────────────────────────────────────────────┘
                    │
                    ├─────────────────────┐
                    ▼                     ▼
    ┌──────────────────────┐  ┌──────────────────────┐
    │ Task-level failure   │  │ Infra-level failure  │
    └──────────────────────┘  └──────────────────────┘
                    │                     │
                    │                     ▼
                    │         ┌──────────────────────┐
                    │         │ CircuitBreaker       │
                    │         │ .record_failure()    │
                    │         │                      │
                    │         │ • Increment counter  │
                    │         │ • Check threshold    │
                    │         │ • Open if ≥3 failures│
                    │         └──────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ EscalationManagerEnhanced.handle_failure()             │
│                                                         │
│ Read current escalation state from DB:                 │
│ • task.escalation_tier (0-4)                           │
│ • task.retry_count                                     │
│                                                         │
│ Decision logic:                                         │
│ • If tier ≤1 and retries <2 → Tier 1 (auto-retry)     │
│ • If tier=1 and retries ≥2 → Tier 2 (agent switch)    │
│ • If tier=2 → Tier 3 (diagnostic)                      │
│ • If tier≥3 → Tier 4 (human escalation)               │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Execute escalation action                              │
│                                                         │
│ Tier 1: Set work_state='not_started', agent=same      │
│ Tier 2: Set work_state='not_started', agent=alternative│
│ Tier 3: Spawn reviewer, set work_state='diagnostic'   │
│ Tier 4: Create inbox alert, set work_state='blocked'  │
│                                                         │
│ Update DB:                                              │
│ • task.escalation_tier = new_tier                      │
│ • task.retry_count += 1                                │
│ • task.failure_reason = error_log[:1000]               │
│ • task.updated_at = now                                │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Release resources                                       │
│ • Remove from active_workers                            │
│ • Release project lock                                  │
│ • Log metrics to usage system                          │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Next poll cycle picks up retried/escalated task       │
│ • Scanner finds task (work_state changed to eligible) │
│ • Router respects new agent assignment (if switched)   │
│ • Worker manager spawns with new context               │
└─────────────────────────────────────────────────────────┘
```

### Error Classification

**Task-Level Errors** (trigger escalation):
- Test failures
- Linting errors
- Missing files in repo
- Logic bugs in implementation
- Timeout due to complex task

**Infrastructure Errors** (trigger circuit breaker):
- Gateway authentication failures
- Session file locks
- Missing API keys
- Network connectivity issues
- Rate limiting
- All models failed to spawn

---

## Circuit Breaker

### State Machine

```
┌──────────────┐
│   CLOSED     │  Normal operation
│              │  • Spawn tasks freely
│              │  • Track success/failure
└──────────────┘
       │
       │ 3+ consecutive
       │ infra failures
       ▼
┌──────────────┐
│    OPEN      │  Infrastructure broken
│              │  • Block all spawning
│              │  • Wait cooldown (5 min)
│              │  • Log warnings
└──────────────┘
       │
       │ Cooldown
       │ elapsed
       ▼
┌──────────────┐
│  HALF_OPEN   │  Testing recovery
│              │  • Allow 1 probe spawn
│              │  • Watch result closely
└──────────────┘
       │
       ├─────────────┐
       │             │
   Success       Failure
       │             │
       ▼             ▼
┌──────────────┐  ┌──────────────┐
│   CLOSED     │  │    OPEN      │
│  (resumed)   │  │  (retry wait)│
└──────────────┘  └──────────────┘
```

### Isolation Levels

**Global Circuit:**
- Affects: All task spawning
- Trigger: Severe infrastructure failures (gateway down, auth broken)
- Impact: Complete spawn pause until resolved

**Project Circuit:**
- Affects: Tasks in specific project
- Trigger: Repo-specific issues (missing files, broken setup)
- Impact: Other projects continue working

**Agent Circuit:**
- Affects: Specific agent type
- Trigger: Agent config issues (bad prompts, tool failures)
- Impact: Other agent types continue working

### Recovery Flow

```
1. Infrastructure failure detected
   ↓
2. Classify failure type (gateway_auth, session_lock, etc.)
   ↓
3. Increment circuit counters (global/project/agent)
   ↓
4. Check threshold (≥3 failures?)
   ↓
5. Yes → Open circuit, start cooldown timer
   ↓
6. Log alert: "Circuit open: <reason>"
   ↓
7. Block subsequent spawns (return false from should_allow_spawn)
   ↓
8. Wait cooldown period (5 minutes)
   ↓
9. Transition to HALF_OPEN
   ↓
10. Allow one probe spawn
    ↓
11. Success? → Reset circuit (CLOSED)
    Failure? → Re-open circuit, restart cooldown
```

---

## Reflection Cycle

### Strategic Reflection Flow

```
┌─────────────────────────────────────────────────────────┐
│ Trigger: Every 3 hours (configurable)                   │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ For each execution agent (programmer, researcher, etc.) │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ ContextPacketBuilder.build_for_agent(agent, hours=6)   │
│                                                         │
│ Gather last 6 hours of activity:                       │
│ • Tasks completed (success/failure)                     │
│ • Common error patterns                                 │
│ • Time spent per task                                   │
│ • Model usage and costs                                 │
│ • Workflow bottlenecks                                  │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Create AgentReflection record (status='pending')       │
│ • reflection_type = 'strategic'                        │
│ • window_start = now - 6h                              │
│ • window_end = now                                      │
│ • context_packet = serialized data                      │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Build reflection prompt:                               │
│                                                         │
│ "You are <agent_type>.                                 │
│  Review your recent work over the last 6 hours.        │
│  Analyze patterns, successes, failures.                │
│  Extract lessons learned and update your heuristics.   │
│                                                         │
│  Context packet: <JSON data>                           │
│                                                         │
│  Output structured reflection with:                    │
│  - What worked well                                     │
│  - What didn't work                                     │
│  - Patterns to keep                                     │
│  - Patterns to change                                   │
│  - New heuristics to adopt"                             │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ ModelChooser.choose(agent, purpose='reflection')       │
│ • Select appropriate model tier for reflection         │
│ • Usually medium tier (balanced cost/quality)          │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Spawn reflection worker via Gateway                     │
│ • Isolated session (not tied to any task)              │
│ • Label: "reflection-<agent_type>"                     │
│ • Register as external worker                           │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Worker completes, webhook reports result               │
│ • Update AgentReflection.status = 'completed'          │
│ • Store result (structured insights)                   │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Create SystemSweep record                              │
│ • sweep_type = 'reflection_batch'                      │
│ • summary = {agents: N, spawned: M}                    │
│ • Set sweep_requested flag for arbitration             │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Wait for daily compression (next 3 AM ET)              │
└─────────────────────────────────────────────────────────┘
```

### Daily Compression Flow

```
┌─────────────────────────────────────────────────────────┐
│ Trigger: 3 AM ET daily                                  │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ For each execution agent with recent reflections       │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Collect reflections from last 24 hours                 │
│ • reflection_type IN ('strategic', 'diagnostic')       │
│ • status = 'completed'                                  │
│ • Order by created_at DESC, limit 100                  │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Compress reflections into identity update              │
│                                                         │
│ Extract:                                                │
│ • Changed heuristics (new patterns to adopt)           │
│ • Removed rules (outdated patterns to drop)            │
│ • Updated identity text (synthesized learnings)        │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Lobs Validation Gate                                    │
│                                                         │
│ Reject if:                                              │
│ • No completed reflections with meaningful data        │
│ • >50% of rules removed (suspicious regression)        │
│ • Changed heuristics lack supporting evidence          │
│                                                         │
│ Accept if:                                              │
│ • At least 1 reflection with structured insights       │
│ • Changes are incremental and justified                │
└─────────────────────────────────────────────────────────┘
                    │
                    ├────────────────┐
                    ▼                ▼
              Valid           Invalid
                    │                │
                    ▼                ▼
┌────────────────────────┐  ┌──────────────────────┐
│ Create new version     │  │ Create candidate     │
│ • active = True        │  │ • active = False     │
│ • Deactivate old       │  │ • Flag for review    │
│ • Increment version    │  │ • Keep previous      │
└────────────────────────┘  └──────────────────────┘
                    │                │
                    └────────┬───────┘
                             ▼
┌─────────────────────────────────────────────────────────┐
│ Store AgentIdentityVersion in DB                       │
│ • version = max + 1                                     │
│ • identity_text = compressed learnings                  │
│ • changed_heuristics = list of updates                 │
│ • removed_rules = list of deprecated patterns          │
│ • validation_status = passed/failed                    │
└─────────────────────────────────────────────────────────┘
```

---

## Model Routing

### Model Selection Flow

```
┌─────────────────────────────────────────────────────────┐
│ ModelChooser.choose(agent_type, task, purpose)         │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Read routing policy from DB (OrchestratorSetting)      │
│ • Provider preferences                                  │
│ • Model tier overrides                                  │
│ • Cost constraints                                      │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Determine base tier from task/agent:                   │
│                                                         │
│ • task.model_tier (explicit override)                  │
│ • OR infer from agent type and purpose:                │
│                                                         │
│   programmer + purpose=execution → strong              │
│   researcher + purpose=execution → medium              │
│   reflection → medium                                   │
│   diagnostic → standard                                 │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Build fallback chain from tier:                        │
│                                                         │
│ Tier hierarchy (best to worst):                        │
│ 1. strong  → claude-sonnet-4.5, gpt-5.3-codex          │
│ 2. standard → claude-sonnet-4, gpt-4.5-turbo           │
│ 3. medium  → claude-haiku-4, gpt-4-mini                │
│ 4. small   → ollama/qwen3.3:70b, gemini-flash          │
│ 5. micro   → ollama/qwen3.3:14b, ollama/phi4:14b       │
│                                                         │
│ Fallback: tier → tier-1 → tier-2 → fallback tier       │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Apply routing policy:                                   │
│ • Prefer providers with recent success                 │
│ • Avoid providers with recent failures                 │
│ • Respect cost constraints                              │
│ • Filter by availability (ProviderHealth)              │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Return ModelChoice:                                     │
│ • model: str (primary choice)                          │
│ • candidates: list[str] (fallback chain)               │
│ • routing_policy: dict (for worker context)            │
│ • audit: dict (decision metadata)                      │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ WorkerManager spawns with fallback chain              │
│ • Try primary model first                              │
│ • If spawn fails → try next in candidates list        │
│ • Record outcome in ProviderHealth                     │
│ • Continue until success or exhausted                  │
└─────────────────────────────────────────────────────────┘
```

### Model Tier Mapping

```
┌─────────────┬──────────────────────────────────────────┐
│ Tier        │ Models (in preference order)             │
├─────────────┼──────────────────────────────────────────┤
│ strong      │ claude-sonnet-4.5                        │
│             │ gpt-5.3-codex                            │
│             │ claude-opus-4                            │
├─────────────┼──────────────────────────────────────────┤
│ standard    │ claude-sonnet-4                          │
│             │ gpt-4.5-turbo                            │
│             │ claude-haiku-4 (fast fallback)           │
├─────────────┼──────────────────────────────────────────┤
│ medium      │ claude-haiku-4                           │
│             │ gpt-4-mini                               │
│             │ ollama/qwen3.3:70b                       │
├─────────────┼──────────────────────────────────────────┤
│ small       │ ollama/qwen3.3:70b                       │
│             │ gemini-2.0-flash-exp                     │
│             │ ollama/qwen3.3:14b                       │
├─────────────┼──────────────────────────────────────────┤
│ micro       │ ollama/qwen3.3:14b                       │
│             │ ollama/phi4:14b                          │
│             │ ollama/llama3.2:3b                       │
└─────────────┴──────────────────────────────────────────┘
```

### Strict Coding Tier

For `programmer` agent with `purpose=execution`:
- **Strict mode enabled** — Do NOT fall back below `standard` tier
- Prevents low-quality code generation from weak models
- Better to fail fast than generate broken code

---

## Data Flow Diagrams

### Complete System Data Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                         LOBS SERVER                                 │
│                                                                     │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐       │
│  │   SQLite DB  │────▶│   Scanner    │────▶│   Router     │       │
│  │              │     │              │     │              │       │
│  │ • Tasks      │     │ Find eligible│     │ Choose agent │       │
│  │ • Projects   │     │ tasks        │     │ type         │       │
│  │ • Workers    │     └──────────────┘     └──────────────┘       │
│  │ • Reflections│              │                     │             │
│  └──────────────┘              └─────────┬───────────┘             │
│         ▲                                 ▼                         │
│         │                      ┌──────────────────┐                │
│         │                      │ WorkerManager    │                │
│         │                      │                  │                │
│         │                      │ • Check capacity │                │
│         │                      │ • Build prompt   │                │
│         │                      │ • Choose model   │                │
│         │                      │ • Spawn worker   │                │
│         │                      └──────────────────┘                │
│         │                                 │                         │
│         │                                 │ HTTP POST               │
│         │                                 ▼                         │
└─────────┼─────────────────────────────────────────────────────────┘
          │                                 │
          │                    ┌────────────▼─────────────┐
          │                    │  OpenClaw Gateway        │
          │                    │                          │
          │                    │  POST /tools/invoke      │
          │                    │  {                       │
          │                    │    tool: sessions_spawn  │
          │                    │    params: {...}         │
          │                    │  }                       │
          │                    └────────────┬─────────────┘
          │                                 │
          │                                 ▼
          │                    ┌─────────────────────────┐
          │                    │  Agent Worker Session   │
          │                    │                         │
          │                    │  • Read context files   │
          │                    │  • Implement solution   │
          │                    │  • Run tests            │
          │                    │  • Write summary        │
          │                    └────────────┬────────────┘
          │                                 │
          │                                 │ Webhook POST
          │                                 ▼
          │                    ┌─────────────────────────┐
          │                    │  /api/webhook           │
          │                    │                         │
          │                    │  • Receive result       │
          │                    │  • Extract metrics      │
          │                    │  • Update task state    │
          │                    └────────────┬────────────┘
          │                                 │
          └─────────────────────────────────┘
                         Update DB with result
```

### Worker Lifecycle State Machine

```
┌─────────────────────────────────────────────────────────┐
│ Task State: work_state='ready'                         │
│ Worker: None                                            │
└─────────────────────────────────────────────────────────┘
                    │
                    │ Scanner picks up
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Task State: work_state='in_progress'                   │
│ Worker: Spawning                                        │
│ • Call Gateway sessions_spawn                           │
│ • Acquire project lock                                  │
│ • Add to active_workers map                             │
└─────────────────────────────────────────────────────────┘
                    │
                    │ Spawn successful
                    ▼
┌─────────────────────────────────────────────────────────┐
│ Task State: work_state='in_progress'                   │
│ Worker: Running                                         │
│ • Tracked by run_id + childSessionKey                  │
│ • Polled via sessions_list                             │
│ • Heartbeat monitored                                   │
└─────────────────────────────────────────────────────────┘
                    │
                    ├────────────────────┐
                    ▼                    ▼
         ┌──────────────────┐  ┌──────────────────┐
         │ Success          │  │ Failure          │
         │ (exit code 0)    │  │ (exit code != 0) │
         └──────────────────┘  └──────────────────┘
                    │                    │
                    ▼                    ▼
┌─────────────────────────────┐  ┌─────────────────────┐
│ work_state='ready_for_review'│ │ Escalation flow      │
│ Release project lock         │  │ • Circuit breaker   │
│ Remove from active_workers  │  │ • Tier-based retry  │
└─────────────────────────────┘  └─────────────────────┘
```

---

## Observability & Debugging

### Logging

All orchestrator components use structured logging with component tags:

```python
logger.info("[ENGINE] Starting orchestration loop")
logger.info("[SCANNER] Found 5 eligible tasks")
logger.info("[ROUTER] Regex matched 'researcher' for task abc123")
logger.info("[WORKER] Spawning worker for task abc123 (programmer)")
logger.info("[CIRCUIT] Infrastructure failure detected: gateway_auth")
logger.warning("[MONITOR] Stuck task detected: xyz789 (15m old)")
logger.error("[ESCALATION] Tier 4: Human escalation for task def456")
```

### Database Tables

**Orchestrator State:**
- `orchestrator_settings` — Configuration and runtime state
- `worker_status` — Active/historical worker tracking
- `worker_runs` — Detailed run logs with transcripts

**Task State:**
- `tasks` — Primary task records with state machine fields
- `task_work_state` — Lifecycle: not_started, ready, in_progress, blocked, completed
- `task_escalation_tier` — Failure handling tier (0-4)
- `task_retry_count` — Number of retry attempts

**Reflection System:**
- `agent_reflections` — Individual reflection runs
- `agent_identity_versions` — Versioned agent identities
- `system_sweeps` — Batch reflection metadata

**Usage Tracking:**
- `usage_events` — Token usage, costs, model performance
- Token counts extracted from session transcripts

### Metrics

Key metrics exposed via `/api/status`:
- Active workers count
- Tasks by state (ready, in_progress, blocked)
- Circuit breaker status (open/closed)
- Escalation tier distribution
- Recent failure rates
- Model usage and costs

---

## Testing Strategy

### Unit Tests

Each component should have isolated unit tests:

**Scanner:**
- Query logic for eligible tasks
- GitHub eligibility filtering
- Sync state compatibility

**Router:**
- Pattern matching correctness
- Explicit agent field handling
- Default fallback behavior

**Circuit Breaker:**
- Failure classification (infra vs task)
- Threshold detection
- Cooldown behavior
- Half-open probe logic

**Escalation Manager:**
- Tier progression logic
- Retry count tracking
- Agent switching
- Human escalation

### Integration Tests

**Worker Lifecycle:**
- Spawn → execute → complete (success)
- Spawn → execute → fail → escalate
- Spawn failure → fallback model chain

**Circuit Breaker + Escalation:**
- Multiple infra failures → circuit opens
- Task failures during open circuit → queue (don't escalate)
- Circuit closes → retry queued tasks

**Reflection Cycle:**
- Strategic reflection spawn
- Context packet building
- Daily compression validation
- Identity version management

### Observability Tests

**Monitor:**
- Stuck task detection
- Auto-unblock logic
- Health summary accuracy

**Logging:**
- Structured log format consistency
- Component tag coverage
- Error log completeness

---

## Performance Considerations

### Concurrency

- **Max workers:** 5 (default, configurable)
- **Polling interval:** 10s active, up to 60s idle
- **Database:** SQLite with WAL mode (concurrent reads)

### Bottlenecks

**Database Queries:**
- Scanner queries on every poll (mitigated by adaptive interval)
- Worker status updates (async, non-blocking)

**Gateway API:**
- HTTP round-trips for spawn/status (mitigated by HTTP/2)
- Rate limits (handled by circuit breaker)

**Model Availability:**
- Provider failures (handled by fallback chain)
- Cold start latency (Ollama models ~2-5s)

### Optimizations

**Adaptive Polling:**
- Fast poll (10s) when tasks are active
- Slow poll (60s) when idle
- Immediate wake on webhook

**Domain Locks:**
- One worker per project prevents repo conflicts
- Allows parallel work across projects

**Circuit Breaker:**
- Prevents wasted retries on infra failures
- Reduces DB churn from failed spawns

---

## Future Improvements

### Short-Term

1. **Parallel scanning** — Check multiple projects concurrently
2. **Webhook-based wakeup** — Instant poll on task creation
3. **Enhanced diagnostics** — Automatic root cause analysis
4. **Cost budgets** — Per-project/per-agent spending limits

### Medium-Term

1. **Multi-gateway support** — Distribute workers across gateways
2. **Smart batching** — Group related tasks for same worker
3. **Predictive routing** — ML-based agent selection
4. **Live dashboard** — Real-time orchestrator visualization

### Long-Term

1. **Distributed orchestration** — Scale across multiple servers
2. **Agent collaboration** — Multi-agent task solving
3. **Self-healing** — Automatic infra recovery
4. **Adaptive model selection** — Learn optimal models per task type

---

## Glossary

**Agent Type:** Role designation (programmer, researcher, writer, etc.)

**Circuit Breaker:** Infrastructure failure isolation mechanism

**Domain Lock:** Project-level concurrency control (one worker per project)

**Escalation Tier:** Failure handling level (0=initial, 4=human)

**Model Tier:** Quality/capability level (micro to strong)

**Reflection Cycle:** Periodic agent self-improvement process

**Routing Policy:** Configuration for model selection preferences

**Sweep:** Batch analysis of reflection results

**Work State:** Task lifecycle state (ready, in_progress, etc.)

**Worker:** Spawned AI agent session executing a task

---

*This document describes the orchestrator as of 2026-02-22. For implementation details, see source code in `app/orchestrator/`.*
