# Multi-Agent System Architecture

**Last Updated:** 2026-02-22  
**Version:** 1.0  
**Status:** Living Document

## Overview

The lobs-server implements a **multi-agent autonomous task execution system** where specialized AI agents work collaboratively to complete development tasks. The system consists of:

1. **Orchestrator** — Central coordinator that manages task lifecycle, routing, and worker execution
2. **Agent Workers** — Specialized AI agents (programmer, researcher, writer, architect, reviewer) spawned via OpenClaw Gateway
3. **Project Manager** — Meta-agent that makes intelligent routing decisions
4. **Supporting Infrastructure** — Circuit breakers, escalation, monitoring, health tracking

This document describes the complete architecture, agent lifecycle, communication protocols, and failure handling mechanisms.

---

## Table of Contents

- [System Components](#system-components)
- [Agent Lifecycle](#agent-lifecycle)
- [Task State Machine](#task-state-machine)
- [Orchestrator Responsibilities](#orchestrator-responsibilities)
- [Handoff Protocol](#handoff-protocol)
- [Sequence Diagrams](#sequence-diagrams)
- [Failure Handling](#failure-handling)
- [Concurrency Model](#concurrency-model)
- [Design Decisions](#design-decisions)

---

## System Components

### 1. Orchestrator Engine (`app/orchestrator/engine.py`)

**Purpose:** Central async polling loop that coordinates all task execution.

**Key Responsibilities:**
- Poll for eligible work every 10 seconds (adaptive backoff when idle)
- Spawn and track agent workers via Gateway API
- Monitor system health and detect stuck tasks
- Handle reflection cycles (strategic planning every 6 hours)
- Run daily compression (consolidate learnings)
- Process scheduled events and routines
- Sync with GitHub-backed projects
- Manage capability registry
- Enforce runtime-configurable settings

**Lifecycle:**
```
start() → _run_loop() → _run_once() (repeat) → stop()
```

**State:**
- `_running`: Engine is active
- `_paused`: Engine is active but not spawning new workers
- `_worker_manager`: Persistent worker tracker (survives across ticks)
- `provider_health`: Model provider health registry

### 2. Scanner (`app/orchestrator/scanner.py`)

**Purpose:** Find tasks eligible for execution.

**Eligibility Criteria:**
- Task `status='active'`
- Task `work_state` in `['not_started', 'ready']`
- Task `sync_state` is `null`, `'synced'`, or `'local_changed'`
- For GitHub tasks: must be claimable or already claimed by lobs

**Returns:** List of task dictionaries with all context needed for routing and spawning.

### 3. Router (`app/orchestrator/router.py`)

**Purpose:** Determine which agent should handle a task.

**Routing Priority:**
1. **Explicit assignment** — Task has `agent` field set
2. **Project-manager delegation** — Call project-manager agent for routing decision (see ADR-0003)
3. **Regex fallback** — Pattern matching on keywords (researcher, writer, architect, reviewer)
4. **Default** — Programmer (safest general-purpose agent)

**Why project-manager?** Allows context-aware, explainable routing decisions without hardcoded rules. See `docs/decisions/0003-project-manager-delegation.md`.

### 4. Worker Manager (`app/orchestrator/worker.py`)

**Purpose:** Spawn, track, and manage agent worker sessions via OpenClaw Gateway API.

**Key Features:**
- HTTP-based worker spawning via `/tools/invoke` → `sessions_spawn`
- Multi-tier model routing with fallback chains
- Provider health tracking and circuit breaking
- Domain locks (one worker per project to prevent repo conflicts)
- Token usage extraction and logging
- Automatic escalation on repeated failures

**Worker Tracking:**
```python
WorkerInfo:
  - run_id: OpenClaw run ID
  - child_session_key: Session identifier for status polling
  - task_id: Task being worked on
  - project_id: Project context
  - agent_type: Agent role (programmer/researcher/etc)
  - model: LLM model used
  - start_time: Unix timestamp
  - label: Human-readable label (e.g., "task-abc123de")
```

**Domain Locks:** `project_locks: dict[project_id, task_id]`
- Prevents concurrent work on the same repository
- Released when worker completes or terminates

**Capacity Management:**
- Default: 1 worker max (`MAX_WORKERS=1`)
- Configurable via environment variable
- Tasks queue when capacity is full

### 5. Monitor Enhanced (`app/orchestrator/monitor_enhanced.py`)

**Purpose:** Proactive health monitoring and auto-remediation.

**Detection:**
- **Stuck tasks** — Work state `'in_progress'` for >2 hours with no recent activity
- **Failed workers** — Completed but task not marked completed
- **Stale locks** — Project locks with no active worker
- **Failure patterns** — Repeated failures on same task/project/agent combo

**Actions:**
- Auto-unblock tasks that are safe to retry
- Escalate to higher-tier models
- Circuit-break problematic project+agent combinations
- Log issues for human review

### 6. Circuit Breaker (`app/orchestrator/circuit_breaker.py`)

**Purpose:** Prevent cascading failures by blocking spawn attempts that are likely to fail.

**Breach Conditions:**
- 3+ failures on same (project, agent, task) within 6 hours
- 5+ failures on same (project, agent) within 6 hours
- 10+ global task failures within 1 hour

**States:**
- `open` — Blocking spawns, cooldown in effect
- `closed` — Normal operation
- `half_open` — Testing after cooldown (allows single attempt)

**Cooldown:** 30 minutes (configurable)

### 7. Escalation Manager (`app/orchestrator/escalation_enhanced.py`)

**Purpose:** Multi-tier failure recovery.

**Escalation Levels:**
1. **Retry with same model** (1-2 attempts)
2. **Escalate to higher-tier model** (micro→small→medium→standard→strong)
3. **Escalate to different agent type** (e.g., programmer → architect for design issues)
4. **Create inbox item for human review**

**Tracked in:** `WorkerRun` table with `escalation_tier` and `escalation_history` fields.

### 8. Agent Tracker (`app/orchestrator/agent_tracker.py`)

**Purpose:** Track per-agent execution statistics.

**Metrics:**
- Total tasks handled
- Success/failure counts
- Last active timestamp
- Current status

**Stored in:** `AgentStatus` table

### 9. Reflection Cycle Manager (`app/orchestrator/reflection_cycle.py`)

**Purpose:** Strategic planning and self-improvement.

**Reflection Cycle (Every 6 Hours):**
1. Scan recent work (completed tasks, failures, feedback)
2. Spawn strategic reflection session for each agent type
3. Agents propose initiatives, identify inefficiencies, suggest improvements
4. Policy engine auto-approves low-risk initiatives
5. High-risk initiatives go to Lobs review
6. After all reflections complete, trigger sweep arbitrator
7. Send reflection summary to Lobs main session for decision-making

**Daily Compression (3 AM ET):**
- Consolidate daily learnings into long-term memory
- Prune ephemeral notes
- Update agent memory files

### 10. Model Router (`app/orchestrator/model_router.py`)

**Purpose:** Intelligent model selection with multi-provider fallback.

**Tier System:**
- **micro** — Simple tasks (summaries, triage, inbox processing)
- **small** — Standard tasks (most development work)
- **medium** — Complex tasks (architecture, refactoring)
- **standard** — High-complexity (large codebases)
- **strong** — Critical reasoning (system design, debugging)

**Fallback Chains:**
```python
{
    "inbox": ["subscription", "kimi", "minimax", "openai", "claude"],
    "quick_summary": ["subscription", "kimi", "minimax", "openai", "claude"],
    "triage": ["subscription", "kimi", "minimax", "openai", "claude"],
    "default": ["openai", "claude", "kimi", "minimax", "subscription"]
}
```

**Provider Health Tracking:**
- Records success/failure outcomes
- Penalizes providers with recent failures
- Auto-downgrades failing providers in fallback chain

---

## Agent Lifecycle

### Overview

Each agent worker progresses through a well-defined lifecycle from spawn to completion/termination.

```
┌─────────────────────────────────────────────────────────────┐
│ AGENT WORKER LIFECYCLE                                      │
│                                                             │
│  PENDING ──▶ SPAWNED ──▶ ACTIVE ──▶ COMPLETING ──▶ DONE   │
│                │            │            │                  │
│                ▼            ▼            ▼                  │
│             FAILED       STUCK      ESCALATING             │
│                │            │            │                  │
│                └────────────┴────────────┘                  │
│                            │                                │
│                            ▼                                │
│                       TERMINATED                            │
└─────────────────────────────────────────────────────────────┘
```

### States

#### 1. PENDING
**Entry:** Task eligible for execution, queued
**Conditions:**
- Task has `status='active'`, `work_state='not_started'` or `'ready'`
- No active worker on this task
- Project lock available (no other worker on same project)
- Worker capacity available (`active_workers < MAX_WORKERS`)

**Transitions:**
- → SPAWNED: Worker spawn initiated
- → FAILED: Spawn failed (circuit breaker blocked, API error, etc.)

#### 2. SPAWNED
**Entry:** Gateway API call succeeded, worker session created
**Tracked in:** `WorkerManager.active_workers` dict
**Database State:**
- Task `work_state='in_progress'`, `started_at=<now>`
- `WorkerStatus` updated with current task
- `WorkerRun` record created

**Transitions:**
- → ACTIVE: Worker acknowledged task, producing output
- → FAILED: Worker crashed before starting (timeout, environment error)

#### 3. ACTIVE
**Entry:** Worker is executing task, log file growing
**Monitoring:**
- WorkerManager polls Gateway `/tools/invoke` with `action=poll` every 10s
- Monitor checks for stuck workers (>2 hours no completion)

**Transitions:**
- → COMPLETING: Worker signaled completion
- → STUCK: Worker timeout or no progress
- → FAILED: Worker crashed (non-zero exit, OOM, etc.)

#### 4. COMPLETING
**Entry:** Worker session finished, processing results
**Actions:**
- Extract work summary from `.work-summary` file
- Parse transcript for token usage
- Update task state based on outcome
- Release project lock
- Record WorkerRun completion
- Log usage event

**Transitions:**
- → DONE: Successfully completed
- → ESCALATING: Completion indicated failure/blocker

#### 5. DONE
**Entry:** Task successfully completed
**Database State:**
- Task `work_state='ready_for_review'` or `'completed'`
- Task `finished_at=<now>`
- `WorkerRun.succeeded=true`

**Exit:** Worker removed from `active_workers`

#### 6. STUCK
**Entry:** Worker exceeded timeout threshold without completing
**Thresholds:**
- Warning: 30 minutes (`WORKER_WARNING_TIMEOUT`)
- Kill: 2 hours (`WORKER_KILL_TIMEOUT`)

**Actions:**
- Log warning at 30 minutes
- Terminate worker at 2 hours
- Release project lock
- Mark task for escalation

**Transitions:**
- → ESCALATING: Escalation manager decides next action
- → TERMINATED: Worker forcibly killed

#### 7. FAILED
**Entry:** Worker crashed, errored, or reported failure
**Causes:**
- Non-zero exit code
- OpenClaw session error
- Out of memory (OOM)
- Network timeout
- Circuit breaker opened
- Spawn failure (no model available, auth error, etc.)

**Actions:**
- Log failure details
- Record error type (rate_limit, auth_error, quota_exceeded, timeout, server_error, unknown)
- Update provider health registry
- Release project lock

**Transitions:**
- → ESCALATING: Automatic retry/escalation logic

#### 8. ESCALATING
**Entry:** Task failed, escalation manager deciding next action
**Logic (Multi-tier):**
1. Retry with same model (up to 2 attempts)
2. Escalate to higher-tier model
3. Escalate to different agent type (if pattern detected)
4. Create inbox item for human review

**Database State:**
- `WorkerRun.escalation_tier` incremented
- `WorkerRun.escalation_history` updated with decision

**Transitions:**
- → PENDING: Retry scheduled
- → TERMINATED: Max escalation reached, manual intervention required

#### 9. TERMINATED
**Entry:** Worker lifecycle ended, no further automatic action
**Database State:**
- Task `work_state` depends on outcome:
  - `'waiting_on'` if escalated to human
  - `'in_progress'` if available for manual retry
  - `'completed'` if done
- `WorkerRun.ended_at=<now>`

**Exit:** Worker removed from all tracking structures

---

## Task State Machine

Tasks have multiple orthogonal state dimensions:

### Status (Lifecycle Stage)
- `inbox` — Not yet triaged
- `active` — Being worked on or queued
- `completed` — Done
- `rejected` — Declined/not doing
- `waiting_on` — Blocked on external dependency

### Work State (Execution Progress)
- `not_started` — Never picked up
- `in_progress` — Worker actively working
- `ready` — Ready for pickup (after being started)
- `ready_for_review` — Work done, needs human review
- `blocked` — Cannot proceed (blocker in `blocked_by` field)

### Review State (Quality Gate)
- `null` — Not applicable
- `pending` — Awaiting review
- `approved` — Review passed
- `changes_requested` — Needs revisions
- `rejected` — Review failed

### Sync State (GitHub Integration)
- `null` — Not synced with external source
- `synced` — In sync with GitHub
- `local_changed` — Local changes not pushed
- `conflict` — GitHub state diverged, needs manual resolution

### State Transition Examples

**Happy Path (Simple Task):**
```
status=active, work_state=not_started
  ↓ (worker spawn)
status=active, work_state=in_progress
  ↓ (worker completes)
status=active, work_state=ready_for_review
  ↓ (human reviews)
status=completed, work_state=ready_for_review
```

**Failure with Escalation:**
```
status=active, work_state=in_progress
  ↓ (worker fails)
status=active, work_state=ready (escalation scheduled)
  ↓ (retry with higher-tier model)
status=active, work_state=in_progress
  ↓ (succeeds)
status=active, work_state=ready_for_review
```

**GitHub Workflow:**
```
status=active, work_state=not_started, sync_state=synced
  ↓ (claim issue via GitHub API)
status=active, work_state=in_progress, sync_state=synced
  ↓ (worker completes, pushes PR)
status=active, work_state=ready_for_review, sync_state=synced
  ↓ (PR merged on GitHub)
status=completed, work_state=ready_for_review, sync_state=synced
```

---

## Orchestrator Responsibilities

The orchestrator acts as the **central nervous system** of the multi-agent system. It does NOT write code or make product decisions — it coordinates.

### Core Loop (Every 10 Seconds)

```python
async def _run_once() -> bool:
    """Execute one orchestration cycle. Returns True if activity detected."""
    
    # 0. Refresh runtime settings from DB (every 60s)
    await _refresh_runtime_settings()
    
    # 1. Check scheduled events (every 60s)
    await scheduler.check_due_events()
    
    # 2. Process routine registry (every 60s)
    await routine_runner.process_due_routines()
    
    # 3. Sync GitHub projects (every 2 minutes)
    await github_sync_service.sync_all_projects()
    
    # 4. Sync OpenClaw model catalog (every 15 minutes)
    await fetch_openclaw_model_catalog()
    
    # 5. Process inbox threads (every 45s, if not paused)
    await inbox_processor.process_threads()
    
    # 6. Auto-assign agents to unassigned tasks (every 60s)
    await task_auto_assigner.run_once()
    
    # 7. Capability registry sync (every hour)
    await capability_registry.sync()
    
    # 8. Lobs-as-PM control loop (reflection + compression + task routing)
    await control_loop.run_once()
    
    # 9. Initiative sweep/arbitration (triggered after reflection batch completes)
    if worker_manager.sweep_requested:
        await sweep_arbitrator.run_once()
        await _send_reflection_summary_to_lobs()
    
    # 10. Reactive diagnostics (every 10 minutes)
    await diagnostic_engine.run_once()
    
    # 11. Check active workers (poll status, detect completions)
    await worker_manager.check_workers()
    
    # 12. Enhanced monitoring (stuck tasks, failure patterns, auto-unblock)
    await monitor_enhanced.run_full_check()
    
    # 13. Exit early if paused or OpenClaw unavailable
    if self._paused or not self._openclaw_available:
        return activity
    
    # 14. Scan for eligible tasks
    eligible_tasks = await scanner.get_eligible_tasks()
    
    # 15. For each eligible task:
    for task in eligible_tasks:
        # - Check if agent assigned; if not, request Lobs assignment
        if not task.agent:
            await _request_lobs_assignment(task)
            continue
        
        # - For GitHub tasks, claim issue before spawning
        if task.external_source == "github":
            claimed = await github_sync.claim_issue_for_task(task)
            if not claimed:
                continue  # Skip if claim failed
        
        # - Check circuit breaker
        allowed = await circuit_breaker.should_allow_spawn(task)
        if not allowed:
            continue
        
        # - Spawn worker
        spawned = await worker_manager.spawn_worker(task)
    
    return activity
```

### Responsibilities Summary

**1. Work Discovery**
- Scan for eligible tasks
- Apply filtering rules (status, work state, sync state)
- Respect GitHub claim handshake

**2. Worker Lifecycle**
- Spawn workers via OpenClaw Gateway API
- Track active workers
- Poll for completion
- Detect timeouts and stuck workers
- Terminate misbehaving workers

**3. Task Routing**
- Delegate to project-manager agent for intelligent routing
- Apply fallback logic (regex patterns, default agent)
- Respect explicit task assignments

**4. Concurrency Control**
- Enforce project locks (one worker per project)
- Respect max worker capacity
- Queue tasks when capacity full

**5. Failure Handling**
- Detect failures (crash, timeout, error exit)
- Classify error types (rate_limit, auth, quota, timeout, server_error)
- Trigger escalation logic
- Update provider health
- Open circuit breakers when needed

**6. Health Monitoring**
- Check worker heartbeats
- Detect stuck tasks
- Find failure patterns
- Auto-unblock safe-to-retry tasks

**7. Strategic Planning**
- Run reflection cycles (every 6 hours)
- Daily compression (3 AM ET)
- Memory maintenance
- Initiative sweep and arbitration
- Send reflection summaries to Lobs for review

**8. External Integrations**
- Sync with GitHub (claim issues, push PRs, update issue status)
- Sync OpenClaw model catalog
- Process scheduled events
- Execute routine hooks

**9. Observability**
- Log all state transitions
- Track token usage and costs
- Record escalation history
- Maintain audit trail

**10. Configuration**
- Respect runtime-configurable settings (refresh every 60s)
- Support pause/resume
- Allow API-triggered reflection cycles

### What Orchestrator Does NOT Do

- ❌ Write code (that's for agent workers)
- ❌ Make product decisions (that's for humans or Lobs agent)
- ❌ Directly manipulate git repos (agents use their own workspaces)
- ❌ Approve tasks or initiatives (policy engine + human approval)
- ❌ Retry infinitely (max escalation tiers enforced)

---

## Handoff Protocol

### Inter-Agent Communication

Agents can create handoffs to delegate work to other agents. This is coordinated through the task system.

#### Handoff Structure

```json
{
  "to": "programmer",
  "initiative": "feature-name",
  "title": "Implement X component",
  "context": "Part of the new auth system. See design doc at docs/auth-design.md",
  "acceptance": "Component passes integration tests, handles edge cases A, B, C.",
  "files": ["docs/auth-design.md"],
  "metadata": {
    "priority": "high",
    "estimated_effort": "medium",
    "dependencies": ["task-abc123"]
  }
}
```

#### Valid Handoffs (by Agent Type)

**Architect → Programmer:**
- Implementation tasks from design docs
- Must include: design doc path, acceptance criteria, test expectations

**Architect → Researcher:**
- Technical research needed before design decisions
- Must include: research question, decision context

**Programmer → Reviewer:**
- Code review requests
- Must include: changed files, PR link (if applicable)

**Researcher → Architect:**
- Research findings requiring design decisions
- Must include: summary of findings, options analyzed

**Any → Writer:**
- Documentation tasks
- Must include: target audience, doc location, content outline

**Any → Lobs (Human):**
- Product decisions, ambiguous requirements, approval requests
- Sent via inbox system

#### Handoff Execution Flow

```
1. Agent A completes work, determines handoff needed
2. Agent A creates handoff JSON (written to workspace or DB)
3. Agent A exits with success
4. Orchestrator detects completion
5. Orchestrator reads handoff
6. Orchestrator creates new task:
   - title = handoff.title
   - notes = handoff.context + acceptance criteria
   - agent = handoff.to
   - project_id = same as parent
   - metadata.parent_task = original task ID
7. New task enters queue (status=active, work_state=not_started)
8. Orchestrator spawns new worker when eligible
```

#### Handoff Best Practices

**For Handoff Creators:**
- Provide full context (don't assume agent has memory of prior work)
- Include paths to relevant files/docs
- Define clear acceptance criteria
- Specify dependencies explicitly
- Add metadata (priority, effort estimate, deadline)

**For Handoff Recipients:**
- Read full context before starting
- Verify dependencies are met
- Ask for clarification via inbox if unclear
- Complete acceptance criteria or explain why not possible

---

## Sequence Diagrams

### 1. Task Assignment Flow (Happy Path)

```
┌─────────────┐      ┌──────────────┐     ┌──────────────┐     ┌────────────┐     ┌──────────┐
│ Orchestrator│      │   Scanner    │     │    Router    │     │   Worker   │     │  Agent   │
│   Engine    │      │              │     │              │     │  Manager   │     │  Worker  │
└──────┬──────┘      └───────┬──────┘     └───────┬──────┘     └──────┬─────┘     └─────┬────┘
       │                     │                    │                   │                 │
       │ _run_once()         │                    │                   │                 │
       ├────────────────────▶│                    │                   │                 │
       │                     │ get_eligible_tasks()│                  │                 │
       │                     ├───────────────────▶│                   │                 │
       │                     │                    │                   │                 │
       │                     │  [Task: no agent]  │                   │                 │
       │                     ◀────────────────────┤                   │                 │
       │                     │                    │                   │                 │
       │  route_task()       │                    │                   │                 │
       ├────────────────────────────────────────▶ │                   │                 │
       │                     │                    │ call project-mgr  │                 │
       │                     │                    │ (routing decision)│                 │
       │                     │                    ├──────────────────▶│                 │
       │                     │                    │                   │                 │
       │                     │                    │ [agent=programmer]│                 │
       │                     │                    ◀───────────────────┤                 │
       │                     │                    │                   │                 │
       │   [agent assigned]  │                    │                   │                 │
       ◀────────────────────────────────────────────                  │                 │
       │                     │                    │                   │                 │
       │ spawn_worker(task, agent, project)       │                   │                 │
       ├──────────────────────────────────────────────────────────────▶                 │
       │                     │                    │                   │                 │
       │                     │                    │                   │ POST /tools/    │
       │                     │                    │                   │ invoke          │
       │                     │                    │                   │ sessions_spawn  │
       │                     │                    │                   ├────────────────▶│
       │                     │                    │                   │                 │
       │                     │                    │                   │  [run_id,       │
       │                     │                    │                   │   session_key]  │
       │                     │                    │                   ◀─────────────────┤
       │                     │                    │                   │                 │
       │                     │                    │                   │ Update DB:      │
       │                     │                    │                   │ task.work_state=│
       │                     │                    │                   │ 'in_progress'   │
       │                     │                    │                   │                 │
       │  [spawned=True]     │                    │                   │                 │
       ◀──────────────────────────────────────────────────────────────│                 │
       │                     │                    │                   │                 │
       │ Log: "Spawned worker for task abc123de"  │                   │                 │
       │                     │                    │                   │                 │
       │ Continue loop...    │                    │                   │  Agent executes │
       │                     │                    │                   │  task...        │
       │                     │                    │                   │                 │
```

### 2. Task Completion Flow (Success)

```
┌──────────┐     ┌────────────┐     ┌──────────────┐     ┌─────────────┐
│  Agent   │     │  Worker    │     │ Orchestrator │     │  Database   │
│  Worker  │     │  Manager   │     │   Engine     │     │             │
└────┬─────┘     └──────┬─────┘     └───────┬──────┘     └──────┬──────┘
     │                  │                   │                   │
     │ Task completed   │                   │                   │
     │ (writes .work-   │                   │                   │
     │  summary)        │                   │                   │
     │                  │                   │                   │
     │ Exit with code 0 │                   │                   │
     ├─────────────────▶│                   │                   │
     │                  │                   │                   │
     │                  │ check_workers()   │                   │
     │                  ◀───────────────────┤                   │
     │                  │                   │                   │
     │                  │ Poll session      │                   │
     │                  │ status (GET       │                   │
     │                  │ /tools/invoke)    │                   │
     │                  │                   │                   │
     │                  │ [status=completed]│                   │
     │                  │                   │                   │
     │                  │ Read .work-       │                   │
     │                  │ summary file      │                   │
     │                  │                   │                   │
     │                  │ Extract token     │                   │
     │                  │ usage from        │                   │
     │                  │ transcript        │                   │
     │                  │                   │                   │
     │                  │ Update task       │                   │
     │                  ├───────────────────────────────────────▶│
     │                  │ SET work_state=   │                   │
     │                  │ 'ready_for_review'│                   │
     │                  │ finished_at=NOW() │                   │
     │                  │                   │                   │
     │                  │ Create WorkerRun  │                   │
     │                  ├───────────────────────────────────────▶│
     │                  │ succeeded=true    │                   │
     │                  │ summary="..."     │                   │
     │                  │ input_tokens=X    │                   │
     │                  │ output_tokens=Y   │                   │
     │                  │                   │                   │
     │                  │ Log usage event   │                   │
     │                  ├───────────────────────────────────────▶│
     │                  │                   │                   │
     │                  │ Release project   │                   │
     │                  │ lock              │                   │
     │                  │                   │                   │
     │                  │ Remove from       │                   │
     │                  │ active_workers    │                   │
     │                  │                   │                   │
     │                  │ [completion ack]  │                   │
     │                  ├──────────────────▶│                   │
     │                  │                   │                   │
     │                  │                   │ Log: "Task       │
     │                  │                   │ abc123de         │
     │                  │                   │ completed"       │
     │                  │                   │                   │
```

### 3. Task Failure Flow (With Escalation)

```
┌──────────┐   ┌────────────┐   ┌──────────────┐   ┌───────────────┐   ┌─────────────┐
│  Agent   │   │  Worker    │   │ Orchestrator │   │  Escalation   │   │  Database   │
│  Worker  │   │  Manager   │   │   Engine     │   │  Manager      │   │             │
└────┬─────┘   └──────┬─────┘   └───────┬──────┘   └────────┬──────┘   └──────┬──────┘
     │                │                 │                   │                  │
     │ Task fails     │                 │                   │                  │
     │ (exception or  │                 │                   │                  │
     │  error message)│                 │                   │                  │
     │                │                 │                   │                  │
     │ Exit code 1    │                 │                   │                  │
     ├───────────────▶│                 │                   │                  │
     │                │                 │                   │                  │
     │                │ check_workers() │                   │                  │
     │                ◀─────────────────┤                   │                  │
     │                │                 │                   │                  │
     │                │ Poll session    │                   │                  │
     │                │ [status=failed, │                   │                  │
     │                │  error=...]     │                   │                  │
     │                │                 │                   │                  │
     │                │ Classify error  │                   │                  │
     │                │ type (rate_limit│                   │                  │
     │                │ /auth/timeout/  │                   │                  │
     │                │  server_error)  │                   │                  │
     │                │                 │                   │                  │
     │                │ Record failure  │                   │                  │
     │                │ in provider     │                   │                  │
     │                │ health          │                   │                  │
     │                │                 │                   │                  │
     │                │ Update WorkerRun│                   │                  │
     │                ├─────────────────────────────────────────────────────────▶│
     │                │ succeeded=false │                   │                  │
     │                │ error="..."     │                   │                  │
     │                │                 │                   │                  │
     │                │ Release project │                   │                  │
     │                │ lock            │                   │                  │
     │                │                 │                   │                  │
     │                │ Call escalation │                   │                  │
     │                │ manager         │                   │                  │
     │                ├─────────────────────────────────────▶│                  │
     │                │                 │                   │                  │
     │                │                 │                   │ Check escalation │
     │                │                 │                   │ history          │
     │                │                 │                   ├─────────────────▶│
     │                │                 │                   │                  │
     │                │                 │                   │ [attempt 1 of 3] │
     │                │                 │                   ◀──────────────────┤
     │                │                 │                   │                  │
     │                │                 │                   │ Decision: Retry  │
     │                │                 │                   │ with same model  │
     │                │                 │                   │                  │
     │                │                 │                   │ Update task:     │
     │                │                 │                   │ work_state=      │
     │                │                 │                   │ 'ready'          │
     │                │                 │                   ├─────────────────▶│
     │                │                 │                   │                  │
     │                │ [escalation:    │                   │                  │
     │                │  retry_same]    │                   │                  │
     │                ◀─────────────────────────────────────┤                  │
     │                │                 │                   │                  │
     │                │ [next cycle]    │                   │                  │
     │                │ spawn_worker()  │                   │                  │
     │                ├────────────────▶│                   │                  │
     │                │                 │                   │                  │
     │                │ [attempt 2]     │                   │                  │
     │                │                 │                   │                  │
     │   [IF FAILS    │                 │                   │                  │
     │    AGAIN]      │                 │                   │                  │
     │                │                 │                   │                  │
     │                │ Call escalation │                   │                  │
     │                ├─────────────────────────────────────▶│                  │
     │                │                 │                   │                  │
     │                │                 │                   │ [attempt 2 of 3] │
     │                │                 │                   │                  │
     │                │                 │                   │ Decision: Escalate│
     │                │                 │                   │ to higher-tier   │
     │                │                 │                   │ model (small→    │
     │                │                 │                   │ medium)          │
     │                │                 │                   │                  │
     │                │                 │                   │ Update task.     │
     │                │                 │                   │ model_tier=      │
     │                │                 │                   │ 'medium'         │
     │                │                 │                   ├─────────────────▶│
     │                │                 │                   │                  │
     │                │ [escalation:    │                   │                  │
     │                │  higher_model]  │                   │                  │
     │                ◀─────────────────────────────────────┤                  │
     │                │                 │                   │                  │
     │                │ [Cycle continues with medium-tier   │                  │
     │                │  model...]      │                   │                  │
     │                │                 │                   │                  │
     │   [IF MAX      │                 │                   │                  │
     │    ESCALATION  │                 │                   │                  │
     │    REACHED]    │                 │                   │                  │
     │                │                 │                   │                  │
     │                │                 │                   │ Decision: Create │
     │                │                 │                   │ inbox item for   │
     │                │                 │                   │ human review     │
     │                │                 │                   │                  │
     │                │                 │                   │ Create InboxItem │
     │                │                 │                   ├─────────────────▶│
     │                │                 │                   │ title="Task X    │
     │                │                 │                   │ needs help"      │
     │                │                 │                   │                  │
     │                │                 │                   │ Update task:     │
     │                │                 │                   │ work_state=      │
     │                │                 │                   │ 'waiting_on'     │
     │                │                 │                   ├─────────────────▶│
     │                │                 │                   │                  │
     │                │ [escalation:    │                   │                  │
     │                │  manual_review] │                   │                  │
     │                ◀─────────────────────────────────────┤                  │
     │                │                 │                   │                  │
     │                │ Log: "Task      │                   │                  │
     │                │ escalated to    │                   │                  │
     │                │ human"          │                   │                  │
     │                │                 │                   │                  │
```

### 4. Circuit Breaker Activation Flow

```
┌──────────────┐    ┌───────────────┐    ┌────────────────┐    ┌─────────────┐
│   Worker     │    │   Circuit     │    │  Orchestrator  │    │  Database   │
│   Manager    │    │   Breaker     │    │    Engine      │    │             │
└──────┬───────┘    └───────┬───────┘    └────────┬───────┘    └──────┬──────┘
       │                    │                      │                   │
       │ Spawn attempt      │                      │                   │
       │ (project X,        │                      │                   │
       │  agent=programmer) │                      │                   │
       │                    │                      │                   │
       │ should_allow_spawn │                      │                   │
       ├───────────────────▶│                      │                   │
       │                    │                      │                   │
       │                    │ Query failure history│                   │
       │                    ├──────────────────────────────────────────▶│
       │                    │                      │                   │
       │                    │ [3 failures on       │                   │
       │                    │  project X +         │                   │
       │                    │  programmer          │                   │
       │                    │  in last 6 hours]    │                   │
       │                    ◀───────────────────────────────────────────┤
       │                    │                      │                   │
       │                    │ BREACH DETECTED      │                   │
       │                    │                      │                   │
       │                    │ Update breaker state │                   │
       │                    ├──────────────────────────────────────────▶│
       │                    │ state='open'         │                   │
       │                    │ opened_at=NOW()      │                   │
       │                    │ cooldown_until=      │                   │
       │                    │ NOW() + 30 min       │                   │
       │                    │                      │                   │
       │ [allowed=False,    │                      │                   │
       │  reason="Circuit   │                      │                   │
       │  breaker open for  │                      │                   │
       │  project X"]       │                      │                   │
       ◀────────────────────┤                      │                   │
       │                    │                      │                   │
       │ Log: "Circuit      │                      │                   │
       │ breaker blocked    │                      │                   │
       │ spawn"             │                      │                   │
       │                    │                      │                   │
       │ Skip this task     │                      │                   │
       ├────────────────────────────────────────────▶                   │
       │                    │                      │                   │
       │                    │  [30 minutes pass]   │                   │
       │                    │                      │                   │
       │ [Next cycle]       │                      │                   │
       │ should_allow_spawn │                      │                   │
       ├───────────────────▶│                      │                   │
       │                    │                      │                   │
       │                    │ Check cooldown       │                   │
       │                    │ [NOW() > cooldown_   │                   │
       │                    │  until]              │                   │
       │                    │                      │                   │
       │                    │ Update breaker state │                   │
       │                    ├──────────────────────────────────────────▶│
       │                    │ state='half_open'    │                   │
       │                    │                      │                   │
       │ [allowed=True,     │                      │                   │
       │  reason="Half-open │                      │                   │
       │  (testing)"]       │                      │                   │
       ◀────────────────────┤                      │                   │
       │                    │                      │                   │
       │ Spawn worker...    │                      │                   │
       │                    │                      │                   │
       │ [IF SUCCESS]       │                      │                   │
       │                    │                      │                   │
       │ record_success()   │                      │                   │
       ├───────────────────▶│                      │                   │
       │                    │                      │                   │
       │                    │ Update breaker state │                   │
       │                    ├──────────────────────────────────────────▶│
       │                    │ state='closed'       │                   │
       │                    │ opened_at=NULL       │                   │
       │                    │                      │                   │
       │                    │ Circuit breaker      │                   │
       │                    │ RECOVERED            │                   │
       │                    │                      │                   │
```

---

## Failure Handling

### Failure Classification

Failures are classified into categories to enable intelligent escalation:

| Error Type | Description | Example | Recovery Strategy |
|------------|-------------|---------|-------------------|
| **rate_limit** | API rate limit exceeded | `429 Too Many Requests` | Wait + retry with backoff |
| **auth_error** | Authentication/authorization failure | `401 Unauthorized`, `403 Forbidden` | Notify human (broken API key) |
| **quota_exceeded** | Billing quota reached | `insufficient_quota` | Switch provider or notify human |
| **timeout** | Request timed out | `ETIMEDOUT`, `deadline exceeded` | Retry with longer timeout |
| **server_error** | Provider infrastructure failure | `500 Internal Server Error` | Retry with different provider |
| **unknown** | Unclassified error | Arbitrary exception | Escalate to higher-tier model |

### Multi-Tier Escalation Strategy

```
Attempt 1: Base model (e.g., small tier)
   ↓ FAIL
Attempt 2: Retry with same model
   ↓ FAIL
Attempt 3: Escalate to higher-tier model (medium)
   ↓ FAIL
Attempt 4: Escalate to higher-tier model (standard)
   ↓ FAIL
Attempt 5: Escalate to highest-tier model (strong)
   ↓ FAIL
Final: Create inbox item for human review
```

### Circuit Breaker Patterns

**Purpose:** Prevent cascading failures by blocking spawn attempts that are likely to fail.

**Tracked Dimensions:**
- `(project, agent, task)` — Specific task keeps failing
- `(project, agent)` — All tasks on this project+agent combo failing
- `(global)` — System-wide failure spike

**Breach Thresholds:**
- Task-specific: 3 failures within 6 hours
- Project+agent: 5 failures within 6 hours
- Global: 10 failures within 1 hour

**States:**
- **Closed** — Normal operation
- **Open** — Blocking spawns, cooldown in effect (30 min default)
- **Half-Open** — Testing after cooldown (allows single spawn attempt)

**Recovery:**
- After cooldown expires, breaker moves to half-open
- Single successful spawn → circuit breaker closes
- Another failure → circuit breaker reopens with longer cooldown

### Provider Health Tracking

**Purpose:** Track success/failure rates per model provider and downgrade unreliable providers in fallback chains.

**Tracked Metrics:**
- Recent outcomes (success/failure) with sliding window (1 hour)
- Error types (rate_limit, auth, quota, timeout, server_error)
- Response times (P50, P95, P99)

**Fallback Chain Adjustment:**
- Providers with recent failures moved to end of preference list
- Providers with repeated auth/quota errors disabled until manual re-enable
- Providers with high latency de-prioritized for time-sensitive tasks

**Example:**
```
Original chain: [openai, claude, kimi, minimax]
After OpenAI quota exhausted: [claude, kimi, minimax, openai]
After Kimi rate limit: [claude, minimax, openai, kimi]
```

### Stuck Task Detection

**Definition:** Task in `work_state='in_progress'` for >2 hours with no completion.

**Causes:**
- Worker process hung
- Infinite loop in agent code
- Network partition
- Gateway API failure

**Detection:** Monitor enhanced runs every 10s, checks for stale `started_at` timestamps.

**Actions:**
1. Log warning at 30 minutes
2. Attempt graceful termination at 1 hour (send interrupt signal)
3. Force-kill worker at 2 hours
4. Release project lock
5. Mark task for escalation
6. Record timeout reason in WorkerRun

---

## Concurrency Model

### Worker Capacity

**Default:** 1 concurrent worker (`MAX_WORKERS=1`)

**Rationale:**
- Prevents resource contention (CPU, memory, API rate limits)
- Simplifies debugging (single worker log at a time)
- Ensures predictable execution order

**Configurable:** Set `MAX_WORKERS` environment variable for higher concurrency.

### Project Locks (Domain Locks)

**Purpose:** Prevent concurrent work on the same repository (git conflicts).

**Mechanism:**
```python
project_locks: dict[project_id, task_id]
```

**Enforcement:**
- Before spawning worker, check if project is locked
- If locked, queue task (skip spawn)
- On worker completion/termination, release lock

**Scope:** Project-level (not agent-level)

**Example:**
```
Project A: locked by task-123 (programmer)
Project A: task-456 queued (cannot spawn, same project)
Project B: task-789 spawned (different project, OK)
```

### Agent Concurrency

**Old Behavior (Removed):** Agent-level locks (one programmer, one researcher, etc.)

**New Behavior:** Multiple instances of the same agent type can run concurrently on **different projects**.

**Why Changed:** Gateway sessions are fully isolated (each agent has its own workspace). No resource conflicts between agents of the same type as long as they're on different projects.

**Example (Now Allowed):**
```
Task A: programmer on project-X
Task B: programmer on project-Y  ← OK (different project)
Task C: programmer on project-X  ← BLOCKED (same project)
```

### Database Concurrency

**SQLite Mode:** WAL (Write-Ahead Logging)
- Allows concurrent readers and single writer
- Reader-writer locks managed automatically
- No explicit transaction isolation needed for reads

**Session Management:**
- Orchestrator engine uses long-lived DB session (with periodic refresh)
- Worker completion uses independent session to avoid lock contention
- Usage logging uses best-effort writes (rollback on conflict, no poison)

---

## Design Decisions

### Why Gateway API Instead of Subprocess?

**Old Approach:** `subprocess.Popen` to spawn local workers

**New Approach:** HTTP calls to OpenClaw Gateway `/tools/invoke` → `sessions_spawn`

**Reasons:**
1. **Model Control** — Can pass specific model preferences per task
2. **Stateless** — Orchestrator doesn't manage process lifecycle (Gateway does)
3. **Multi-Model Fallback** — Gateway handles fallback chains internally
4. **Isolation** — Workers run in separate Gateway sessions with their own workspace
5. **Observability** — Gateway provides transcript, usage metrics, status polling
6. **Scalability** — Can distribute workers across multiple Gateway instances

**Tradeoff:** Dependency on Gateway availability (but Gateway is core infrastructure anyway).

### Why Project Locks Instead of Agent Locks?

**Reason:** Git repository conflicts.

**Problem:** Two workers modifying the same repo simultaneously → merge conflicts, lost work.

**Solution:** One worker per project at a time.

**Why Not Agent Locks?**
- Gateway sessions are isolated (each has its own workspace)
- Programmer on project A doesn't conflict with programmer on project B
- Agent locks would unnecessarily serialize work

### Why LLM-Based Routing (Project-Manager Agent)?

See `docs/decisions/0003-project-manager-delegation.md` for full rationale.

**TL;DR:**
- Hardcoded rules became unmaintainable (too many edge cases)
- LLM routing is context-aware, explainable, adaptable
- Cost ($0.002/task) and latency (1-3s) are acceptable for current workload
- Can add rule-based fast path later if needed (premature optimization)

### Why Async Polling Instead of Event-Driven?

**Reason:** Simplicity and control.

**Polling Approach:**
- Orchestrator runs async loop every 10 seconds
- Checks DB for eligible tasks
- Polls Gateway for worker status
- Easy to reason about (single control flow)
- Adaptive backoff when idle

**Event-Driven Alternative:**
- Workers would push completion events
- Orchestrator reacts to events
- More efficient (no polling overhead)
- But: more complex (event queues, handlers, concurrency)

**Decision:** Polling is simple and performant enough for current scale (10-50 tasks/day). Can migrate to event-driven later if needed.

### Why SQLite Instead of Postgres?

See `docs/decisions/0002-sqlite-for-primary-database.md` for full rationale.

**TL;DR:**
- Simplicity (single file, no server)
- Good enough for current workload (<1000 tasks/month)
- WAL mode supports concurrent reads
- Easy backup (copy file)
- Can migrate to Postgres later if scale demands

---

## Observability

### Logging

**Levels:**
- `INFO` — State transitions, worker spawn/completion, escalations
- `WARNING` — Circuit breaker activations, stuck tasks, failures
- `ERROR` — Unexpected exceptions, system failures
- `DEBUG` — Detailed trace (only in development)

**Structured Logging:**
- All state-changing operations include task_id, project_id, agent_type
- Model routing decisions include full audit trail
- Escalation history tracked in DB

### Metrics Tracked

**Per Task:**
- Spawn timestamp
- Completion timestamp
- Duration
- Model used
- Token usage (input/output)
- Estimated cost
- Success/failure
- Escalation tier
- Error type (if failed)

**Per Agent:**
- Total tasks handled
- Success rate
- Avg tokens per task
- Total cost
- Last active timestamp

**Per Provider:**
- Success rate (sliding 1-hour window)
- Avg response time (P50, P95, P99)
- Error types (rate_limit, auth, quota, timeout, server_error)

**System-Level:**
- Active workers count
- Queued tasks count
- Circuit breaker states
- Reflection cycle cadence
- Memory maintenance status

### Audit Trail

**WorkerRun Table:**
- Complete history of every worker execution
- Includes: task_id, model, tokens, cost, success, error, summary

**AgentReflection Table:**
- Strategic reflection cycles
- Proposals, findings, initiatives

**AgentInitiative Table:**
- Proposed initiatives with approval status
- Tracks: who proposed, risk tier, owner, decision

**UsageEvent Table:**
- Per-task usage tracking
- Includes: route type, provider, model, tokens, cost

---

## Future Enhancements

### Planned Improvements

**1. Multi-Agent Tasks**
- Split complex tasks across multiple agents
- Parallel execution where possible
- Coordinator agent to merge results

**2. Learning from Outcomes**
- Track which routing decisions led to successful completion
- Train lightweight classifier on successful patterns
- Improve project-manager agent prompt based on learnings

**3. Workload Balancing**
- Track agent "busyness" (active tasks, queue depth)
- Route away from busy agents when alternatives exist
- Prevent single agent from becoming bottleneck

**4. Proactive Diagnostics**
- Predict likely failures before they occur
- Pre-warm alternative models when primary provider is flaky
- Schedule maintenance tasks during low-usage periods

**5. Cost Optimization**
- Cache routing decisions by task signature
- Use cheaper models for simple/repetitive tasks
- Batch similar tasks to amortize routing overhead

**6. Enhanced Reflection**
- Agent-to-agent communication (share learnings)
- Cross-agent patterns (e.g., architect → programmer handoffs that work well)
- Memory consolidation across all agents (not per-agent silos)

### Open Questions

**Q: Should we support parallel workers on the same project?**
- **Pro:** Faster completion for independent tasks
- **Con:** Git conflict complexity
- **Decision:** Defer until we have a safe git-merge strategy

**Q: Should we support human-in-the-loop approval before worker spawn?**
- **Pro:** Prevents unwanted work
- **Con:** Defeats autonomous execution
- **Decision:** Already supported via `waiting_on` status; use for high-risk tasks

**Q: Should we migrate to event-driven architecture?**
- **Pro:** More efficient than polling
- **Con:** More complex
- **Decision:** Revisit if task volume exceeds 1000/day

---

## Summary

The lobs-server multi-agent system is a **collaborative task execution engine** where specialized AI agents work together under orchestrator coordination. Key design principles:

1. **Autonomous by default** — Agents make decisions, humans approve when needed
2. **Fail safely** — Circuit breakers, escalation, and monitoring prevent cascading failures
3. **Learn continuously** — Reflection cycles and memory maintenance improve over time
4. **Explainable** — Every routing decision, escalation, and failure is logged with reasoning
5. **Adaptable** — LLM-based routing and runtime-configurable settings allow evolution without code changes

The system is designed to **scale with complexity, not just volume**. As task types diversify and projects grow, the multi-agent architecture provides the flexibility and robustness needed to maintain reliable autonomous execution.

---

## References

- [ADR-0001: Embedded Orchestrator](../decisions/0001-embedded-orchestrator.md)
- [ADR-0002: SQLite for Primary Database](../decisions/0002-sqlite-for-primary-database.md)
- [ADR-0003: Project Manager Agent for Task Routing](../decisions/0003-project-manager-delegation.md)
- [ARCHITECTURE.md](../../ARCHITECTURE.md) — High-level system overview
- [AGENTS.md](../../AGENTS.md) — API reference and development guide

---

*This is a living document. Update as the system evolves.*
