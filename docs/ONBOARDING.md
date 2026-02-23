# Developer Onboarding Guide

**Last Updated:** 2026-02-22  
**Estimated Time:** 30-45 minutes  
**For:** New developers and returning contributors

Welcome to lobs-server! This guide gets you from zero to productive in under an hour.

---

## Table of Contents

1. [What is lobs-server?](#what-is-lobs-server)
2. [Quick Setup (5 minutes)](#quick-setup-5-minutes)
3. [Your First Task (10 minutes)](#your-first-task-10-minutes)
4. [System Architecture Overview](#system-architecture-overview)
5. [Key Concepts](#key-concepts)
6. [Multi-Agent Coordination](#multi-agent-coordination)
7. [Development Workflow](#development-workflow)
8. [Where to Find Help](#where-to-find-help)
9. [Next Steps](#next-steps)

---

## What is lobs-server?

lobs-server is the **central backend for the Lobs multi-agent system** — a FastAPI + SQLite REST API with a built-in task orchestrator that autonomously delegates work to specialized AI agents.

### What It Does

- **Manages tasks, projects, and workflows** — Full CRUD API with kanban-style status tracking
- **Orchestrates AI agents** — Automatically routes tasks to specialized agents (programmer, researcher, writer, specialist)
- **Provides intelligent delegation** — project-manager agent makes context-aware routing decisions
- **Tracks system health** — Activity timeline, cost tracking, usage monitoring
- **Enables multi-agent collaboration** — Agents can create handoffs, share context, and build on each other's work

### The Lobs Ecosystem

lobs-server is part of a larger system:

```
┌─────────────────────────────────────────────────────┐
│                  Lobs Ecosystem                     │
│                                                     │
│  ┌──────────────┐       ┌──────────────────┐      │
│  │ lobs-mission │       │  lobs-server     │      │
│  │  -control    │◀─────▶│  (YOU ARE HERE)  │      │
│  │  (Frontend)  │       │  (Backend API)   │      │
│  └──────────────┘       └────────┬─────────┘      │
│                                   │                 │
│                                   ▼                 │
│                         ┌─────────────────┐        │
│                         │ OpenClaw Gateway │       │
│                         │  (Agent Runtime) │       │
│                         └─────────────────┘        │
│                                   │                 │
│                 ┌─────────────────┼─────────────┐  │
│                 ▼                 ▼             ▼  │
│           programmer         researcher      writer│
│                                                     │
└─────────────────────────────────────────────────────┘
```

**Key Repositories:**
- **lobs-server** (this repo) — Backend API and orchestrator
- **lobs-mission-control** — Web UI dashboard
- **lobs-shared-memory** — Cross-project knowledge base
- **self-improvement** — Code quality and handoff tracking system

**For full ecosystem documentation**, see:
- [LOBS_ECOSYSTEM.md](~/self-improvement/docs/LOBS_ECOSYSTEM.md)
- [GETTING_STARTED.md](~/self-improvement/docs/GETTING_STARTED.md)

---

## Quick Setup (5 minutes)

### Prerequisites

- Python 3.11+
- Git with SSH configured
- OpenClaw Gateway (for agent features) — optional but recommended

### Installation

```bash
# 1. Clone the repository
git clone git@github.com:RafeSymonds/lobs-server.git
cd lobs-server

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Generate API token
python bin/generate_token.py my-token
# Save the token output — you'll need it for API requests

# 5. Start the server
./bin/run
# or: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Server runs at:** http://localhost:8000

### Verify Setup

```bash
# Health check (no auth required)
curl http://localhost:8000/api/health
# Expected: {"status":"ok", "uptime":"..."}

# Authenticated request
export TOKEN="your-token-from-step-4"
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/projects
# Expected: [] (empty array on fresh install)
```

**Interactive API docs:** http://localhost:8000/docs

---

## Your First Task (10 minutes)

Let's walk through creating a task, seeing it get assigned to an agent, and watching it execute.

### Step 1: Create a Task

```bash
export TOKEN="your-api-token"

curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Write a hello world script",
    "notes": "Create a simple Python script that prints Hello, World!",
    "status": "active"
  }'
```

**Response:**
```json
{
  "id": "abc123de",
  "title": "Write a hello world script",
  "status": "active",
  "work_state": "not_started",
  "agent": null,
  "created_at": "2026-02-22T17:30:00Z"
}
```

### Step 2: Watch the Orchestrator

The orchestrator polls every 10 seconds. Watch the logs:

```bash
tail -f logs/orchestrator.log
```

You'll see:
```
[17:30:05] Scanner found 1 eligible task
[17:30:06] Routing task abc123de to project-manager
[17:30:08] project-manager assigned: programmer, model: small
[17:30:09] Spawning programmer worker for task abc123de
[17:30:10] Worker spawned: session-xyz789
```

### Step 3: Check Task Status

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/abc123de
```

**Response:**
```json
{
  "id": "abc123de",
  "status": "active",
  "work_state": "in_progress",
  "agent": "programmer",
  "started_at": "2026-02-22T17:30:09Z"
}
```

### Step 4: Wait for Completion

The programmer agent will:
1. Read the task
2. Write a hello_world.py script
3. Test it
4. Update the task status

After ~30-60 seconds:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/abc123de
```

**Response:**
```json
{
  "id": "abc123de",
  "status": "active",
  "work_state": "ready_for_review",
  "agent": "programmer",
  "result": "Created hello_world.py script. Tested and working.",
  "finished_at": "2026-02-22T17:30:45Z"
}
```

### Step 5: Review the Work

Check what the agent produced:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/abc123de/transcript
```

This shows the agent's full work log, including:
- Code written
- Tests run
- Summary of changes

**Congratulations! You just orchestrated your first AI agent task.** 🎉

---

## System Architecture Overview

### High-Level Components

```
┌──────────────────────────────────────────────────────────┐
│                    lobs-server                           │
│                                                          │
│  ┌────────────────────────────────────────────────┐    │
│  │  REST API (app/routers/)                       │    │
│  │  • /api/tasks      • /api/calendar             │    │
│  │  • /api/projects   • /api/status               │    │
│  │  • /api/memories   • /api/orchestrator         │    │
│  │  • /api/topics     • /api/agents               │    │
│  │  • /api/inbox      • /api/chat (WebSocket)     │    │
│  └────────────────────────────────────────────────┘    │
│                          │                              │
│                          ▼                              │
│  ┌────────────────────────────────────────────────┐    │
│  │  Task Orchestrator (app/orchestrator/)         │    │
│  │                                                 │    │
│  │  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │    │
│  │  │ Scanner  │─▶│ Router   │─▶│ Worker      │  │    │
│  │  │          │  │          │  │ Manager     │  │    │
│  │  └──────────┘  └──────────┘  └─────────────┘  │    │
│  │                                     │           │    │
│  │  ┌──────────┐  ┌──────────────────┐│          │    │
│  │  │ Monitor  │  │ Circuit Breaker  ││          │    │
│  │  │          │  │                  ││          │    │
│  │  └──────────┘  └──────────────────┘│          │    │
│  │                                     │           │    │
│  └─────────────────────────────────────┼───────────┘    │
│                                        │                │
│  ┌─────────────────────────────────────▼──────────┐    │
│  │  Database (SQLite + SQLAlchemy)                │    │
│  │  • tasks, projects, memories                   │    │
│  │  • worker_runs, agent_status                   │    │
│  │  • chat_sessions, calendar_events              │    │
│  └────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
                           │
                           ▼
           ┌───────────────────────────────────┐
           │   OpenClaw Gateway                │
           │   POST /tools/invoke              │
           │   action=sessions_spawn           │
           └───────────────┬───────────────────┘
                           │
           ┌───────────────┴───────────────┐
           │                               │
           ▼                               ▼
    ┌─────────────┐               ┌──────────────┐
    │ programmer  │               │ researcher   │
    │ (session)   │               │ (session)    │
    └─────────────┘               └──────────────┘
```

### Core Subsystems

#### 1. REST API (app/routers/)

19 API routers providing endpoints for:
- Tasks, projects, and inbox management
- Memory system (daily notes, long-term memory, search)
- Topics and documents (knowledge organization)
- Real-time chat (WebSocket + HTTP)
- Calendar and scheduling
- System status and monitoring

**All endpoints require Bearer token authentication** (except `/api/health`).

#### 2. Task Orchestrator (app/orchestrator/)

Autonomous background engine that:
- Scans for eligible tasks every 10 seconds
- Routes tasks to appropriate agents
- Spawns worker sessions via OpenClaw Gateway
- Monitors task progress and detects failures
- Handles escalation (retry → higher model → different agent → human)
- Manages circuit breakers to prevent cascading failures

**Key files:**
- `engine.py` — Main polling loop
- `scanner.py` — Finds eligible tasks
- `router.py` — Delegates to project-manager for routing
- `worker.py` — Spawns and tracks OpenClaw workers
- `monitor_enhanced.py` — Stuck task detection and auto-remediation
- `escalation_enhanced.py` — Multi-tier failure handling

#### 3. Database Layer (SQLite + SQLAlchemy)

**15+ tables** including:
- `tasks` — Task state, assignments, results
- `projects` — Project metadata and settings
- `memories` — Daily notes and long-term memory
- `worker_runs` — Agent execution history
- `agent_status` — Per-agent statistics
- `chat_sessions`, `chat_messages` — Real-time messaging
- `calendar_events` — Scheduled tasks and events

**SQLite configuration:**
- WAL mode (concurrent reads + single writer)
- Async via aiosqlite
- Auto-checkpoint every 1000 pages

#### 4. Agent Workers (via OpenClaw)

AI agents spawned as isolated sessions:
- Each agent has dedicated workspace
- Full tool access (file I/O, shell commands, web search)
- Memory system for continuity across tasks
- Automatic result reporting back to server

---

## Key Concepts

### 1. Task State Machine

Tasks have **independent state dimensions**:

#### Status (Lifecycle)
- `inbox` — Not yet triaged
- `active` — Being worked on or queued
- `completed` — Done
- `rejected` — Not doing
- `waiting_on` — Blocked on external dependency

#### Work State (Execution Progress)
- `not_started` — Never picked up
- `ready` — Ready for pickup
- `in_progress` — Worker actively working
- `ready_for_review` — Work done, needs human review
- `blocked` — Cannot proceed

#### Example Flow:
```
status=active, work_state=not_started
  ↓ (orchestrator spawns worker)
status=active, work_state=in_progress
  ↓ (worker completes)
status=active, work_state=ready_for_review
  ↓ (human reviews)
status=completed, work_state=ready_for_review
```

### 2. Agent Routing

Tasks are routed to agents via **project-manager delegation**:

1. **Explicit assignment** — Task has `agent` field set → skip routing
2. **Project-manager routing** — Orchestrator calls project-manager agent with task context
3. **Project-manager analyzes** — Considers capabilities, complexity, history
4. **Returns assignment** — `{agent: "programmer", model_tier: "medium", reasoning: "..."}`

**Why LLM-based routing?**
- Context-aware (understands nuance)
- Explainable (provides reasoning)
- Adaptable (evolves without code changes)
- Handles ambiguity gracefully

See [ADR-0003](decisions/0003-project-manager-delegation.md) for full rationale.

### 3. Model Tiers

5-tier system matching task complexity to model capability:

| Tier | Use Case | Primary Model | Fallback |
|------|----------|---------------|----------|
| **micro** | Trivial (renaming, cleanup) | gpt-4o-mini | qwen2.5:3b |
| **small** | Simple (known patterns) | gpt-4o-mini | qwen2.5:7b |
| **medium** | Standard (most tasks) | gpt-4o | qwen2.5:14b |
| **standard** | Complex (architecture) | gpt-4o | qwen2.5-coder:32b |
| **strong** | Novel (research, hard debug) | o1 | qwq:32b |

**Automatic fallback:**
- Primary fails → try fallback provider
- All providers fail → escalate to higher tier

See [ADR-0004](decisions/0004-five-tier-model-routing.md) for details.

### 4. Failure Handling

Multi-tier escalation strategy:

```
Attempt 1: Base model (e.g., small)
   ↓ FAIL
Attempt 2: Retry with same model
   ↓ FAIL
Attempt 3: Escalate to higher tier (medium)
   ↓ FAIL
Attempt 4: Escalate to higher tier (standard)
   ↓ FAIL
Attempt 5: Escalate to strongest model (strong)
   ↓ FAIL
Final: Create inbox item for human review
```

**Circuit breakers** prevent cascading failures:
- 3 failures on same (project, agent, task) → circuit opens
- 5 failures on same (project, agent) → circuit opens
- 10 global failures in 1 hour → circuit opens
- Cooldown: 30 minutes

### 5. Memory System

**Three layers:**

#### Shared Memory (~/lobs-shared-memory)
- Cross-project knowledge base
- Indexed by vector search
- Contains: architecture docs, design patterns, investigation findings

#### Project Memory (Database)
- Task history and context
- Daily notes and long-term memory
- Session transcripts

#### Agent Workspaces (~/.openclaw/workspace-<agent>)
- Agent-specific accumulated knowledge
- Daily logs and topic files
- Searchable via memory tools

---

## Multi-Agent Coordination

Agents collaborate through **structured handoff protocols**. Deep dive: [AGENT-COORDINATION.md](AGENT-COORDINATION.md)

### Core Patterns

#### 1. Task Delegation
```
Architect designs → Creates task for Programmer
Researcher investigates → Creates task for Writer (documentation)
Programmer implements → Creates task for Reviewer
```

#### 2. Context Sharing
Agents include links to relevant docs/findings:
```json
{
  "title": "Implement Redis caching",
  "notes": "See docs/research/caching-evaluation.md for design decisions",
  "parent_task_id": "research-task-123"
}
```

#### 3. Human-in-the-Loop
Agents request approval via inbox:
```
Agent: "I need to add a new dependency (redis-py). Approve?"
Human: "Approved"
Agent: Proceeds with implementation
```

**For complete coordination patterns**, see [AGENT-COORDINATION.md](AGENT-COORDINATION.md).

---

## Development Workflow

### Making Changes

1. **Create feature branch**
```bash
git checkout -b feature/my-feature
```

2. **Make your changes**
- Edit code in `app/`
- Server auto-reloads (if using `--reload` flag)

3. **Write tests**
```bash
# Run existing tests
pytest -v

# Write new tests in tests/
# Follow patterns from existing test files
```

4. **Update documentation**
- Update CHANGELOG.md for API changes
- Update relevant docs/ files
- Add ADR for architectural decisions

5. **Commit and push**
```bash
git add .
git commit -m "feat: add rate limiting middleware"
git push origin feature/my-feature
```

### Working with the Orchestrator

**Pause orchestrator** (stops spawning new workers):
```bash
curl -X POST http://localhost:8000/api/orchestrator/pause \
  -H "Authorization: Bearer $TOKEN"
```

**Resume orchestrator**:
```bash
curl -X POST http://localhost:8000/api/orchestrator/resume \
  -H "Authorization: Bearer $TOKEN"
```

**Check orchestrator status**:
```bash
curl http://localhost:8000/api/orchestrator/status
```

### Testing Locally

**Run tests:**
```bash
source .venv/bin/activate
pytest -v
```

**Test specific module:**
```bash
pytest tests/test_orchestrator.py -v
```

**Test with coverage:**
```bash
pytest --cov=app --cov-report=html
open htmlcov/index.html
```

See [docs/TESTING.md](TESTING.md) for comprehensive testing guide.

### Common Development Tasks

**Add a new API endpoint:**
1. Create/edit router in `app/routers/`
2. Add Pydantic schemas in `app/schemas.py`
3. Update database models in `app/models.py` (if needed)
4. Write tests in `tests/`
5. Update CHANGELOG.md
6. Update AGENTS.md with endpoint documentation

**Add a new agent type:**
1. Create directory in `agents/<new-type>/`
2. Write AGENTS.md, SOUL.md, TOOLS.md, IDENTITY.md, USER.md
3. Register capabilities in `app/orchestrator/registry.py`
4. Update routing prompts in `app/orchestrator/prompter.py`
5. Test with a simple task assignment

**Change orchestrator behavior:**
1. Edit files in `app/orchestrator/`
2. Consider: Does this need an ADR? (If architectural, yes)
3. Test locally with pause/resume
4. Monitor logs during test runs
5. Update [multi-agent-system.md](architecture/multi-agent-system.md)

---

## Where to Find Help

### Documentation Hierarchy

**Start here:**
- **[QUICKSTART.md](../QUICKSTART.md)** — 5-minute setup guide
- **[This file (ONBOARDING.md)]** — Comprehensive onboarding (you are here)
- **[ARCHITECTURE.md](../ARCHITECTURE.md)** — High-level system design

**Go deeper:**
- **[AGENTS.md](../AGENTS.md)** — Complete API reference
- **[AGENT-COORDINATION.md](AGENT-COORDINATION.md)** — Multi-agent patterns
- **[docs/architecture/multi-agent-system.md](architecture/multi-agent-system.md)** — Agent lifecycle and orchestrator
- **[docs/guides/multi-agent-onboarding.md](guides/multi-agent-onboarding.md)** — Agent roles and workflows

**Specialized topics:**
- **[TESTING.md](TESTING.md)** — How to run and write tests
- **[CONTRIBUTING.md](../CONTRIBUTING.md)** — Contribution guidelines
- **[CHANGELOG.md](../CHANGELOG.md)** — API changes and version history
- **[decisions/README.md](decisions/README.md)** — Architecture decision records

### Ecosystem Documentation

**Cross-project guides** (in ~/self-improvement/docs/):
- [LOBS_ECOSYSTEM.md](~/self-improvement/docs/LOBS_ECOSYSTEM.md) — Full system architecture
- [GETTING_STARTED.md](~/self-improvement/docs/GETTING_STARTED.md) — 20-30 min ecosystem tour
- [TECH_STACK_REFERENCE.md](~/self-improvement/docs/TECH_STACK_REFERENCE.md) — Technology choices

### By Use Case

**"I want to..."**

- **...understand how tasks flow through the system**  
  → [architecture/multi-agent-system.md](architecture/multi-agent-system.md)

- **...add a new API endpoint**  
  → [AGENTS.md](../AGENTS.md) + [CONTRIBUTING.md](../CONTRIBUTING.md)

- **...understand agent routing decisions**  
  → [decisions/0003-project-manager-delegation.md](decisions/0003-project-manager-delegation.md)

- **...debug a stuck task**  
  → [guides/multi-agent-onboarding.md#troubleshooting](guides/multi-agent-onboarding.md#troubleshooting)

- **...understand why we chose SQLite**  
  → [decisions/0002-sqlite-for-primary-database.md](decisions/0002-sqlite-for-primary-database.md)

- **...write tests**  
  → [TESTING.md](TESTING.md)

- **...add a new agent type**  
  → [guides/multi-agent-onboarding.md#agent-anatomy](guides/multi-agent-onboarding.md#agent-anatomy)

- **...understand model tier selection**  
  → [decisions/0004-five-tier-model-routing.md](decisions/0004-five-tier-model-routing.md)

### Interactive Docs

- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

### Getting Unstuck

1. **Check the logs:**
   ```bash
   tail -f logs/orchestrator.log
   tail -f logs/app.log
   ```

2. **Search shared memory:**
   ```bash
   # If you have OpenClaw CLI
   openclaw memory search "your question"
   ```

3. **Review recent changes:**
   ```bash
   git log --oneline -20
   cat CHANGELOG.md
   ```

4. **Check known issues:**
   - [KNOWN_ISSUES.md](KNOWN_ISSUES.md)

5. **Ask the agents:**
   - Create a task: "Investigate why X is happening"
   - Assign to `researcher`

---

## Next Steps

### Recommended Learning Path

**Week 1: Fundamentals**
- ✅ Complete this onboarding
- ✅ Run your first task
- □ Read [ARCHITECTURE.md](../ARCHITECTURE.md)
- □ Explore the API via http://localhost:8000/docs
- □ Read [multi-agent-system.md](architecture/multi-agent-system.md)

**Week 2: Deep Dive**
- □ Read all ADRs in [decisions/](decisions/)
- □ Read [AGENT-COORDINATION.md](AGENT-COORDINATION.md)
- □ Try creating different task types (code, research, docs)
- □ Watch orchestrator logs while tasks execute
- □ Read [TESTING.md](TESTING.md) and run the test suite

**Week 3: Contributing**
- □ Pick a small issue or improvement
- □ Write tests for your change
- □ Submit a PR (or create a task for an agent to implement)
- □ Review [CONTRIBUTING.md](../CONTRIBUTING.md)

**Ongoing:**
- Monitor CHANGELOG.md for API changes
- Review new ADRs as they're added
- Contribute to shared memory (add learnings to ~/lobs-shared-memory)

### Suggested First Contributions

**Good first tasks:**
- Add tests for existing endpoints
- Improve error messages
- Add examples to documentation
- Fix typos or clarify confusing docs
- Add logging to underinstrumented code paths

**Intermediate tasks:**
- Add a new API endpoint
- Improve orchestrator monitoring
- Add a new agent capability
- Write an ADR for a decision you notice is undocumented

**Advanced tasks:**
- Implement new agent coordination patterns
- Optimize database queries
- Add observability/metrics
- Design and implement new orchestrator features

---

## Summary

You now know:
- ✅ What lobs-server is and how it fits in the ecosystem
- ✅ How to set up and run the server
- ✅ How to create and track a task
- ✅ The high-level architecture (API, orchestrator, agents, database)
- ✅ Key concepts (task states, routing, model tiers, failure handling)
- ✅ Where to find documentation for deeper learning
- ✅ How to get unstuck

**You're ready to contribute!**

Start with something small. Read the docs as you go. The agents are here to help — don't hesitate to create tasks for them when you need investigation or implementation work.

Welcome to the team! 🚀

---

## Feedback

This is a living document. If you found something confusing or missing:
- Open an issue
- Submit a PR with improvements
- Create a task: "Improve ONBOARDING.md section on X"

We want this guide to be maximally useful for new developers and returning contributors. Your feedback makes it better.

---

**Questions?** Check docs/ or create a task assigned to `researcher` with your question. The agents are surprisingly good at answering questions about the system!
