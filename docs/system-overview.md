# Lobs System Architecture Overview

## System Components

The Lobs system is a multi-agent AI assistant platform with three main components:

1. **lobs-server** (backend) — FastAPI + SQLite REST API, orchestrator built-in
2. **Lobs Mission Control** (macOS app) — SwiftUI command center for task/memory/chat management
3. **Lobs Mobile** (iOS app) — SwiftUI companion app for iPhone

All clients connect to lobs-server over Tailscale (private networking). **The server is the single source of truth** — all state lives in SQLite, all access is via REST API.

```
Mission Control (macOS) ─┐
                          ├──► lobs-server (FastAPI + SQLite) ◄──► Orchestrator (built-in)
Lobs Mobile (iOS) ───────┘         │                                      │
                                   │                                      ▼
Lobs (me, OpenClaw) ───────────────┘                              OpenClaw workers
```

## lobs-server (Backend)

**Location:** `~/lobs-server`  
**Stack:** FastAPI, async SQLAlchemy + aiosqlite, Pydantic v2, SQLite  
**Database:** `~/lobs-server/data/lobs.db`  
**Run:** `cd ~/lobs-server && source .venv/bin/activate && ./run.sh` (port 8000, binds 0.0.0.0)

### Key Routers
- **tasks** — CRUD, search, filtering, status updates
- **projects** — Project management, task grouping
- **agents** — Agent metadata, status, capabilities
- **inbox** — Action-required items for human review
- **memories** — Long-term memory storage/search
- **chat** — WebSocket + HTTP chat interface
- **calendar** — Scheduled events, cron recurrence, auto-task creation
- **status** — System overview, activity feed, cost tracking
- **worker** — Worker management, history, logs
- **research** — Research documents and findings
- **documents** — Report/document management
- **orchestrator** — Control endpoints (pause/resume/settings)
- **templates** — Agent identity templates
- **tracker** — Time/effort tracking
- **backup** — Database backup/restore

### Authentication
Bearer token on all endpoints except `/api/health`.  
Tokens generated via `bin/generate_token.py`.  
WebSocket auth via query param: `?token=...`

### Networking
Runs on VM, binds 0.0.0.0:8000. Clients connect via Tailscale IP (no public domain needed).

## Orchestrator (Built-in to Server)

The orchestrator runs as an asyncio background task inside the FastAPI server. It has **direct database access** (no HTTP layer), making it the most privileged component.

### Core Components

**Engine** (`app/orchestrator/engine.py`)  
- Main async polling loop (runs every POLL_INTERVAL seconds)
- Coordinates all subsystems
- Manages pause/resume state
- Handles graceful shutdown

**Scanner** (`app/orchestrator/scanner.py`)  
- Finds eligible tasks (status=pending, not assigned, dependencies met)
- Respects project locks (one worker per project at a time)
- Checks agent availability via capability registry

**Router** (`app/orchestrator/router.py`)  
- Maps tasks to agent types based on capabilities
- Consults CapabilityRegistry for agent→task matching

**WorkerManager** (`app/orchestrator/worker.py`)  
- Spawns OpenClaw workers via `openclaw sessions_spawn`
- Monitors worker health (heartbeat, logs)
- Handles worker completion, failures, timeouts
- Manages worker lifecycle (spawn → monitor → cleanup)

**MonitorEnhanced** (`app/orchestrator/monitor_enhanced.py`)  
- Health checks on running workers
- Log tailing, error detection
- Heartbeat tracking
- Escalation triggers

**CircuitBreaker** (`app/orchestrator/circuit_breaker.py`)  
- Prevents cascading failures
- Tracks failure rates per agent/project/task-type
- Opens circuit after threshold failures, closes after cooldown

**AgentTracker** (`app/orchestrator/agent_tracker.py`)  
- Tracks agent performance metrics
- Success/failure rates, average duration
- Used for routing decisions

**EventScheduler** (`app/orchestrator/scheduler.py`)  
- Polls for scheduled events every 60 seconds
- Handles cron recurrence (via `croniter`)
- Auto-creates tasks for due events
- Replaced old `Reminder` model

**RoutineRunner** (`app/orchestrator/routine_runner.py`)  
- Daily compression at configured hour (ET timezone)
- Periodic maintenance tasks

**InboxProcessor** (`app/orchestrator/inbox_processor.py`)  
- Converts completed research/reports to inbox items
- Surfaces deliverables for human review

**ReflectionCycleManager** (`app/orchestrator/reflection_cycle.py`)  
- Periodic self-reflection on system performance
- Learning from failures, identifying improvements

**SweepArbitrator** (`app/orchestrator/sweep_arbitrator.py`)  
- Periodic sweep for stuck/stale tasks
- Reassignment, escalation

**TaskAutoAssigner** (`app/orchestrator/auto_assigner.py`)  
- Automatically assigns agent types to new tasks based on heuristics

**DiagnosticTriggerEngine** (`app/orchestrator/diagnostic_triggers.py`)  
- Background diagnostic checks
- Detects anomalies, triggers alerts

**LobsControlLoopService** (`app/orchestrator/control_loop.py`)  
- High-level control loop orchestration
- Reflection, sweep, diagnostic triggers

### Data Flow: Task Lifecycle

```
1. Task created (via API or scheduled event)
   ↓
2. TaskAutoAssigner assigns agent_type (if missing)
   ↓
3. Scanner picks up eligible task
   ↓
4. Router confirms agent type
   ↓
5. WorkerManager spawns OpenClaw worker with agent template
   ↓
6. MonitorEnhanced polls worker health
   ↓
7. Worker completes → InboxProcessor creates inbox item (if report/research)
   ↓
8. Task marked done, worker session terminated
```

### Failure Handling

- **Worker timeout** → marked failed, CircuitBreaker increments failure count
- **Worker crash** → logs captured, task marked failed, escalated if repeated
- **Circuit open** → agent/project/task-type temporarily blocked
- **Reflection cycle** → analyzes failures, suggests improvements

## Agent Types

Each agent has a specialized role:

| Agent | Role | Capabilities |
|-------|------|--------------|
| **Lobs** | Chat interface | Talk to Rafe, create tasks, answer questions, set reminders |
| **Programmer** | Code implementation | Write code, fix bugs, run tests, refactor |
| **Writer** | Documentation | Create docs, write-ups, summaries, content |
| **Researcher** | Investigation | Research topics, compare options, analyze, synthesize findings |
| **Reviewer** | Quality assurance | Code review, quality checks, feedback |
| **Architect** | System design | Technical strategy, design docs, planning |

### Agent Identity Templates

Each agent type has a template workspace:
- **Location:** `~/lobs-server/data/agent-templates/<agent-type>/`
- **Files:** `SOUL.md`, `IDENTITY.md`, `AGENTS.md`, `TOOLS.md`, `USER.md`

When a worker is spawned, the orchestrator:
1. Creates a temporary workspace
2. Copies the agent template files
3. Injects task context
4. Spawns OpenClaw session with the workspace

Workers run as a single OpenClaw agent identity (`worker`) with templates swapped per task.

## Client Applications

### Lobs Mission Control (macOS)

**Location:** `~/lobs-mission-control`  
**Stack:** SwiftUI, SwiftData (local cache), async/await networking

**Key Features:**
- **Command Center** — Summary cards (tasks, inbox, memories, research)
- **Unified ⌘K** — Fuzzy finder (tasks, projects, memories, agents, quick actions)
- **Team View** — Agent status, worker history
- **Chat** — WebSocket-based real-time chat with Lobs
- **Memory Browser** — Search/browse memories, quick capture
- **Research Docs** — Two-panel research document viewer
- **Status Dashboard** — System health, costs, activity
- **Calendar** — Scheduled events, auto-task creation

**Auth:** Bearer token in Settings, sent on all API calls + WebSocket

### Lobs Mobile (iOS)

**Location:** `~/lobs-mobile` (TBD, shares API layer with Mission Control)  
**Stack:** SwiftUI, async/await networking  
**Scope:** Simplified mobile companion (tasks, inbox, chat, memories)

## Data Models

### Core Entities

**Project**  
- Grouping for tasks
- Has description, status, tags
- Enforces domain lock (one worker per project)

**Task**  
- Belongs to a project
- Has title, description, status, priority, due_date, agent_type
- Dependencies (blocked_by), tags, effort estimates

**Agent**  
- Metadata for agent types
- Status, capabilities, performance metrics

**InboxItem**  
- Action-required items for human review
- Created by InboxProcessor for completed research/reports

**Memory**  
- Long-term memory entries
- Content, tags, category, timestamp

**ScheduledEvent**  
- Cron-based recurrence
- Auto-creates tasks when due

**WorkerSession**  
- Tracks OpenClaw worker lifecycle
- Session ID, task ID, agent type, status, logs

### Supporting Models

**ControlLoopHeartbeat** — Tracks control loop runs  
**OrchestratorSetting** — Runtime configuration (reflection interval, sweep interval, etc.)  
**AgentPerformanceMetric** — Success rates, avg duration  
**CircuitBreakerState** — Failure tracking, circuit status

## Technology Stack

### Backend
- **FastAPI** — Async web framework
- **SQLAlchemy 2.0** — Async ORM
- **aiosqlite** — Async SQLite driver
- **Pydantic v2** — Data validation
- **croniter** — Cron expression parsing

### Clients
- **SwiftUI** — Declarative UI framework
- **Swift Concurrency** — async/await, actors
- **URLSession** — Networking
- **WebSocket** — Real-time chat

### Infrastructure
- **SQLite** — Single-user database (sufficient for this use case)
- **Tailscale** — Private networking between server and clients
- **OpenClaw** — AI agent runtime (spawns workers)

## Design Principles

### Server as Single Source of Truth
All state lives in the database. No git-based state management. Clients are pure API consumers — zero git, zero direct file access.

### Direct Database Access for Orchestrator
The orchestrator runs inside the server process and queries the database directly (not via HTTP). This ensures consistency and performance.

### One Worker Per Project
Domain locks prevent multiple workers from operating on the same project simultaneously. This avoids conflicts and ensures predictable behavior.

### Circuit Breaker for Reliability
The circuit breaker prevents cascading failures by temporarily blocking agents/projects/task-types after repeated failures. This gives the system time to recover.

### Reflection and Learning
The reflection cycle periodically analyzes system performance, learns from failures, and suggests improvements. This enables continuous improvement.

### Event-Driven Task Creation
Scheduled events with cron recurrence auto-create tasks when due. This enables recurring work (weekly reports, daily standups, etc.).

### Agent Specialization
Each agent type has a narrow, well-defined role. This improves output quality and makes the system more maintainable.

## Key Decisions

- **SQLite, not Postgres** — Simple, zero infrastructure, sufficient for single-user
- **Orchestrator built into server** — One process, one source of truth, direct DB access
- **Tailscale for networking** — Private, secure, zero-config NAT traversal
- **Token auth, server-side only** — No create-token API, tokens managed via scripts
- **Calendar replaces reminders** — Cron-based recurrence, more flexible
- **Scripts in bin/, not scripts/** — Rafe's preference
- **Due dates at 11:59 PM** — Rafe's convention (not the night before)

## Future Enhancements

- **Agent specialization refinement** — More granular capabilities, better routing
- **Multi-project workers** — Allow workers to operate across projects when safe
- **Proactive task creation** — Lobs suggests tasks based on context
- **Better failure recovery** — Automatic retry with backoff, partial task checkpointing
- **Cost optimization** — Dynamic model routing based on task complexity
- **Mobile app** — Full-featured iOS companion
