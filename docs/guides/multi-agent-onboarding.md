# Multi-Agent Onboarding Guide

**Last Updated:** 2026-02-22

Welcome to the lobs-server multi-agent system! This guide helps you understand how AI agents work together, their roles, communication patterns, and the resources they rely on.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Agent Roles](#agent-roles)
3. [Agent Anatomy](#agent-anatomy)
4. [Communication Patterns](#communication-patterns)
5. [Task Lifecycle](#task-lifecycle)
6. [Resources and Memory](#resources-and-memory)
7. [Common Scenarios](#common-scenarios)
8. [Troubleshooting](#troubleshooting)

---

## System Overview

lobs-server is a **multi-agent orchestration system** where specialized AI agents collaborate to complete tasks.

```
┌─────────────────────────────────────────────────────────┐
│                    lobs-server                          │
│                                                         │
│  ┌──────────────┐         ┌─────────────────────────┐  │
│  │  REST API    │◀───────▶│  Task Orchestrator      │  │
│  │              │         │  • Scanner              │  │
│  │  • Tasks     │         │  • Router               │  │
│  │  • Projects  │         │  • Worker Spawner       │  │
│  │  • Memory    │         │  • Monitor              │  │
│  └──────────────┘         └─────────────────────────┘  │
│         │                            │                  │
│         │                            ▼                  │
│         │                 ┌──────────────────────┐     │
│         └────────────────▶│   SQLite Database    │     │
│                           │  • Tasks             │     │
│                           │  • Projects          │     │
│                           │  • Memory            │     │
│                           │  • Agent Sessions    │     │
│                           └──────────────────────┘     │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
           ┌───────────────────────────────────────┐
           │      OpenClaw Gateway (Agents)        │
           │                                       │
           │  ┌─────────────────┐                 │
           │  │ project-manager │ ◀── Orchestrator│
           │  └─────────────────┘                 │
           │         │                             │
           │         ├─▶ programmer                │
           │         ├─▶ researcher                │
           │         ├─▶ writer                    │
           │         ├─▶ specialist                │
           │         └─▶ [custom agents]           │
           │                                       │
           └───────────────────────────────────────┘
```

**Key Concepts:**
- **Tasks** are stored in the database with status (pending, in_progress, completed, failed)
- **Orchestrator** scans for eligible tasks and routes them to agents
- **Agents** are spawned as subprocesses via OpenClaw
- **project-manager** makes delegation decisions (which agent, which model)
- **Workers** execute tasks and report results back to the server

---

## Agent Roles

### project-manager

**Role:** Strategic orchestrator and task delegator

**Responsibilities:**
- Review incoming tasks and decide which agent should handle them
- Choose appropriate model tier (micro/small/medium/standard/strong)
- Handle escalations from failed tasks
- Make architectural decisions (via ADRs)
- Coordinate complex multi-step workflows

**When to use:**
- Autonomous routing (orchestrator delegates to project-manager)
- Manual task assignment (you can explicitly assign to project-manager)
- High-level planning and coordination

**Capabilities:**
- `planning`, `coordination`, `decision-making`, `architecture`

**Example task:**
```json
{
  "title": "Design API for calendar integration",
  "assigned_agent": "project-manager",
  "priority": "high"
}
```

---

### programmer

**Role:** Software engineer — writes, tests, and debugs code

**Responsibilities:**
- Implement features, fix bugs, refactor code
- Write tests (unit, integration, e2e)
- Review and improve code quality
- Debug production issues
- Update dependencies

**When to use:**
- Feature development
- Bug fixes
- Performance optimization
- Refactoring
- API implementation

**Capabilities:**
- `coding`, `debugging`, `testing`, `api-design`, `database`

**Example task:**
```json
{
  "title": "Add pagination to /api/tasks endpoint",
  "assigned_agent": "programmer",
  "priority": "medium",
  "context": "Current endpoint returns all tasks, causing slow response with 1000+ tasks"
}
```

---

### researcher

**Role:** Investigative analyst — researches, explores, and documents findings

**Responsibilities:**
- Investigate technical problems and unknowns
- Research libraries, frameworks, APIs
- Explore codebases and understand architecture
- Document findings and create technical write-ups
- Prototype and spike solutions

**When to use:**
- "How does X work?" investigations
- Library/framework evaluations
- Performance profiling
- Security audits
- Technical spikes before implementation

**Capabilities:**
- `research`, `investigation`, `analysis`, `documentation`, `prototyping`

**Example task:**
```json
{
  "title": "Investigate best practices for WebSocket scaling",
  "assigned_agent": "researcher",
  "priority": "low",
  "context": "Planning for 100+ concurrent chat connections"
}
```

---

### writer

**Role:** Technical writer — creates clear, well-structured documentation

**Responsibilities:**
- Write READMEs, guides, API docs
- Create ADRs (Architecture Decision Records)
- Polish research findings into readable docs
- Write changelogs, release notes
- Improve clarity and structure of existing docs

**When to use:**
- Documentation creation or updates
- ADR authoring
- README improvements
- User guides, tutorials
- Changelog generation

**Capabilities:**
- `documentation`, `technical-writing`, `editing`, `clarity`

**Example task:**
```json
{
  "title": "Write testing guide for lobs-server",
  "assigned_agent": "writer",
  "priority": "medium",
  "context": "New contributors need clear guidance on running and writing tests"
}
```

---

### specialist

**Role:** Domain expert — handles specialized tasks outside core agent capabilities

**Responsibilities:**
- DevOps and infrastructure (Docker, CI/CD)
- Security audits and hardening
- Data migrations and schema changes
- Performance tuning and optimization
- Complex integrations (third-party APIs)

**When to use:**
- Infrastructure work (Dockerfiles, deployment scripts)
- Database migrations
- Security-sensitive changes
- Performance optimization
- Domain-specific expertise

**Capabilities:**
- `devops`, `security`, `performance`, `infrastructure`, `data-engineering`

**Example task:**
```json
{
  "title": "Optimize SQLite WAL checkpoint performance",
  "assigned_agent": "specialist",
  "priority": "high",
  "context": "Database growing to 500MB, checkpoints blocking writes for 2-3 seconds"
}
```

---

## Agent Anatomy

Every agent is defined by five markdown files in `agents/<type>/`:

### 1. AGENTS.md
**Purpose:** Instructions specific to this project  
**Contains:**
- What the agent should do in this codebase
- Project-specific workflows
- Constraints and rules
- Where to put output

**Example (programmer/AGENTS.md):**
```markdown
# Programmer Agent

You write code for lobs-server (FastAPI backend).

## Your Job
- Implement features from tasks
- Write tests for new code
- Fix bugs and refactor

## Constraints
- ❌ Do NOT run git commands (orchestrator handles this)
- ❌ Do NOT write to state/ directories
- ✅ DO write tests for new endpoints
- ✅ DO update CHANGELOG.md for API changes
```

---

### 2. SOUL.md
**Purpose:** Core identity and values  
**Contains:**
- Who the agent is (personality, approach)
- Core principles and values
- Communication style

**Example (writer/SOUL.md):**
```markdown
# SOUL.md - Writer

You are a clear, effective writer. You communicate ideas well.

## Core Truths
**Clarity is kindness.** Every moment a reader spends confused is your failure.
**Respect the reader's time.** Get to the point. Cut the fluff.
**Edit ruthlessly.** First drafts are never done. Cut, refine, improve.
```

---

### 3. TOOLS.md
**Purpose:** Tool usage patterns and examples  
**Contains:**
- How to use available tools effectively
- Common patterns (e.g., reading codebases, running tests)
- Best practices for this agent's role

**Example (researcher/TOOLS.md):**
```markdown
# TOOLS.md - Researcher

## Investigation Workflow
1. Form hypothesis
2. Gather data (read code, run experiments, search docs)
3. Analyze findings
4. Document results

## Tools You'll Use
- **read** — Explore codebases
- **exec** — Run experiments, profiling
- **web_search** — Find documentation, research papers
```

---

### 4. IDENTITY.md
**Purpose:** Machine-readable metadata  
**Contains:**
- Model tier
- Capabilities (for routing)
- Proactive behaviors (optional)

**Example (programmer/IDENTITY.md):**
```markdown
# programmer

**Agent Type:** programmer

- **Model:** medium
- **Capabilities:** coding, debugging, testing, api-design, database, refactoring
- **Proactive:** code-review, dependency-updates
```

**Parsed by:** `app/orchestrator/registry.py`

---

### 5. USER.md
**Purpose:** User-facing description  
**Contains:**
- Who the agent is (for humans)
- What it's good at
- When to assign tasks to it

**Example (specialist/USER.md):**
```markdown
# Specialist Agent

I'm a domain expert who handles specialized work outside the core agent capabilities.

**Good at:**
- DevOps and infrastructure
- Security hardening
- Performance optimization
- Complex third-party integrations

**Assign me when:** You need expertise in a specific domain (security, infra, data).
```

---

## Communication Patterns

### 1. Task Assignment (Human → Server)

**Via REST API:**
```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Fix memory leak in WebSocket connections",
    "assigned_agent": "programmer",
    "priority": "high",
    "context": "Memory usage grows 50MB/hour under load"
  }'
```

**Via Web UI:**
- Create task in Mission Control dashboard
- Assign to specific agent or leave unassigned (project-manager will route)

---

### 2. Orchestrator → project-manager (Routing Decision)

**Flow:**
1. Orchestrator scanner finds eligible task
2. Orchestrator calls project-manager agent with task details
3. project-manager analyzes task and responds with:
   - `assigned_agent`: which agent type (e.g., "programmer")
   - `model_tier`: which model to use (e.g., "medium")
   - `reasoning`: why this choice

**Example conversation:**
```
Orchestrator: "Task #42: 'Add rate limiting to API'. Who should handle this?"

project-manager: "Assign to programmer (coding capability). Use medium model 
(straightforward feature, well-documented pattern). Reasoning: Standard API 
middleware implementation, no novel complexity."
```

---

### 3. Agent → Server (Task Updates)

**During execution, agents report progress:**

**Status updates:**
```json
PATCH /api/tasks/42
{
  "status": "in_progress",
  "progress_notes": "Implemented rate limiter middleware, writing tests"
}
```

**Completion:**
```json
PATCH /api/tasks/42
{
  "status": "completed",
  "result": "Added rate limiting (100 req/min per IP). Tests pass. Updated CHANGELOG.md"
}
```

**Failure:**
```json
PATCH /api/tasks/42
{
  "status": "failed",
  "error_message": "Missing dependency: redis-py. Need approval to add to requirements.txt"
}
```

---

### 4. Agent ↔ Agent (Indirect via Task Handoffs)

Agents **don't communicate directly**. Instead:

1. **Agent A completes task, creates follow-up task**
```python
# researcher finishes investigation, creates task for programmer
POST /api/tasks
{
  "title": "Implement Redis caching based on research findings",
  "assigned_agent": "programmer",
  "parent_task_id": 38,
  "context": "See Task #38 for research. Use redis-py, TTL=1h, cache session data."
}
```

2. **Orchestrator routes new task to Agent B**

3. **Agent B reads context from parent task**

---

### 5. Agent → Human (Inbox for Decisions)

When an agent needs human input:

**Send to inbox:**
```bash
cd ~/lobs-control
python3 bin/send-to-inbox \
  --title "Add Redis dependency?" \
  --body "Task #42 requires redis-py. Adds caching capability but introduces infrastructure dependency." \
  --type proposal \
  --author programmer \
  --project lobs-server
```

**Human reviews in Mission Control → Approves/Rejects**

---

## Task Lifecycle

### 1. Creation
```
Status: pending
assigned_agent: null | <specific-agent>
```

Created via API, web UI, or by another agent.

---

### 2. Scanning (Orchestrator)

**Eligible if:**
- Status = `pending`
- No `assigned_agent` OR agent is available
- Priority vs. current task load

**Scanner SQL:**
```sql
SELECT * FROM tasks 
WHERE status = 'pending' 
  AND (scheduled_for IS NULL OR scheduled_for <= NOW())
ORDER BY priority DESC, created_at ASC
LIMIT 10
```

---

### 3. Routing (project-manager)

**If assigned_agent is null:**
- Orchestrator calls project-manager
- project-manager analyzes task
- Returns `assigned_agent` + `model_tier`

**If assigned_agent is set:**
- Skip routing, go directly to worker spawning

---

### 4. Execution (Worker Agent)

```
Status: in_progress
assigned_to_session: <openclaw-session-id>
started_at: <timestamp>
```

**Worker does:**
- Load context (project, memory, parent tasks)
- Execute task (code, research, write docs)
- Update progress (`PATCH /api/tasks/{id}`)
- Report result (completed/failed)

---

### 5. Completion

**Success:**
```
Status: completed
completed_at: <timestamp>
result: <summary-of-work>
```

**Failure:**
```
Status: failed
error_message: <what-went-wrong>
```

**Blocked:**
```
Status: blocked
blocker: <reason-cannot-proceed>
```

---

### 6. Escalation (If Failed)

**Monitor detects failure → Escalation policy:**

1. **First failure:** Retry with same agent, higher model tier
2. **Second failure:** Escalate to project-manager for re-routing
3. **Third failure:** Mark as `blocked`, send to inbox for human

---

## Resources and Memory

### Shared Memory (~/lobs-shared-memory)

**What:** Cross-project knowledge base indexed by vector search

**Contains:**
- Technical documentation
- Architecture decisions
- Code patterns and best practices
- Investigation findings

**Accessed via:**
```python
# Agents automatically have access via OpenClaw memory search
memory_search("WebSocket scaling patterns")
```

**Location:** `/Users/lobs/lobs-shared-memory/docs/`

---

### Project Memory (Database)

**What:** Task history, project context, session transcripts

**Stored in SQLite:**
- `tasks` — All tasks and their history
- `projects` — Project metadata, workspaces
- `memories` — Daily notes, long-term memory
- `sessions` — Agent session logs (what was said, what was done)

**Queried via:** REST API (`/api/tasks`, `/api/projects`, `/api/memories`)

---

### Agent Workspaces

**What:** Isolated work directories for agents

**Structure:**
```
~/.openclaw/workspace-<agent-type>/
├── AGENTS.md      # Project instructions (injected)
├── SOUL.md        # Agent identity (injected)
├── TOOLS.md       # Tool usage guide (injected)
├── memory/        # Agent's long-term memory
│   ├── 2026-02-22.md       # Daily log
│   ├── lobs-server.md      # Project knowledge
│   └── debugging-tips.md   # Reusable patterns
└── .work-summary  # Output summary (read by orchestrator)
```

**Agents can:**
- Read/write to `memory/` — Accumulate knowledge across tasks
- Write to `.work-summary` — Communicate results to orchestrator

---

### Model Tiers

**5-tier system with automatic Ollama fallback:**

| Tier       | Primary (OpenAI)      | Fallback (Ollama)       | Use Case                     |
|------------|-----------------------|-------------------------|------------------------------|
| `micro`    | gpt-4o-mini           | qwen2.5:3b              | Trivial (renaming, cleanup)  |
| `small`    | gpt-4o-mini           | qwen2.5:7b              | Simple (known patterns)      |
| `medium`   | gpt-4o                | qwen2.5:14b             | Standard (most tasks)        |
| `standard` | gpt-4o                | qwen2.5-coder:32b       | Complex (architecture)       |
| `strong`   | o1                    | qwq:32b                 | Novel (research, hard debug) |

**Routing happens in:**
- `app/orchestrator/model_router.py` — Tier selection logic
- `app/orchestrator/model_chooser.py` — Provider selection with fallback

---

## Common Scenarios

### Scenario 1: New Feature Request

**Flow:**
1. Human creates task: "Add rate limiting to API"
2. Leaves `assigned_agent` blank (let project-manager decide)
3. Orchestrator scans, finds task
4. Orchestrator calls project-manager: "Who should handle this?"
5. project-manager responds: `programmer`, `medium` model
6. Orchestrator spawns programmer agent
7. Programmer implements feature, writes tests, updates CHANGELOG
8. Programmer completes task, commits code

---

### Scenario 2: Bug Fix

**Flow:**
1. Human creates task: "Memory leak in WebSocket handler", assigned to `programmer`
2. Orchestrator scans, spawns programmer (skip routing, already assigned)
3. Programmer investigates, finds issue, fixes code
4. Programmer writes regression test
5. Programmer completes task

---

### Scenario 3: Research → Implementation

**Flow:**
1. Human creates task: "Research caching strategies", assigned to `researcher`
2. Researcher investigates Redis, Memcached, in-memory options
3. Researcher writes findings to `docs/research/caching-evaluation.md`
4. Researcher creates follow-up task: "Implement Redis caching", assigned to `programmer`
5. Programmer reads researcher's findings
6. Programmer implements Redis caching
7. Both tasks completed

---

### Scenario 4: Escalation After Failure

**Flow:**
1. programmer attempts to implement complex feature
2. Task fails: "Cannot determine correct approach"
3. Monitor detects failure → Escalation tier 1: Retry with `strong` model
4. programmer (strong model) attempts again
5. Still fails: "Need architectural decision"
6. Escalation tier 2: Route to project-manager
7. project-manager creates ADR, makes decision
8. project-manager creates new task for programmer with clear direction
9. programmer completes task

---

### Scenario 5: Human Approval Required

**Flow:**
1. programmer working on task: "Optimize database queries"
2. programmer realizes: "Need to add index, but it will slow down writes"
3. programmer sends to inbox: "Proposal: Add index on tasks.status (speeds up reads, slows writes by 10%)"
4. Human reviews in Mission Control
5. Human approves with comment: "Acceptable tradeoff"
6. programmer proceeds, adds index, completes task

---

## Troubleshooting

### Agent Not Picking Up Tasks

**Check:**
1. Is orchestrator running? `curl http://localhost:8000/api/orchestrator/status`
2. Is task eligible? Status should be `pending`, not `in_progress` or `completed`
3. Is agent type valid? Check `app/orchestrator/registry.py` for available agents
4. Are there too many concurrent tasks? (Default limit: 3)

**Fix:**
- Restart orchestrator: `curl -X POST http://localhost:8000/api/orchestrator/restart`
- Check logs: `tail -f logs/orchestrator.log`

---

### Task Stuck in "in_progress"

**Cause:** Agent crashed or lost connection

**Check:**
- Monitor logs: `tail -f logs/orchestrator.log`
- Check OpenClaw gateway: `openclaw gateway status`

**Fix:**
- Reset task: `PATCH /api/tasks/{id}` with `status: pending`
- Monitor will auto-escalate after timeout (default: 30 minutes)

---

### Agent Chose Wrong Model Tier

**Cause:** project-manager's routing logic needs tuning

**Check:**
- Review routing decision in task history
- Check `app/orchestrator/model_chooser.py` for tier selection logic

**Fix:**
- Override manually: `PATCH /api/tasks/{id}` with `model_override: strong`
- Update routing prompts in `app/orchestrator/prompter.py`

---

### Agent Can't Access Shared Memory

**Cause:** Vector search index missing or stale

**Check:**
```bash
ls ~/lobs-shared-memory/docs/
openclaw memory status
```

**Fix:**
- Rebuild index: `openclaw memory rebuild`
- Check permissions: Agent workspace should have read access

---

### Multiple Agents Claiming Same Task

**Cause:** Race condition in orchestrator scanner

**Should not happen** — Scanner uses database transactions

**If it does:**
- File bug report with logs
- Workaround: Add `assigned_agent` explicitly to prevent routing

---

## Next Steps

- **Read:** [ARCHITECTURE.md](../../ARCHITECTURE.md) — System design
- **Read:** [Testing Guide](testing-guide.md) — How to test the orchestrator
- **Read:** [Operations Runbook](operations-runbook.md) — Day-to-day operations
- **Explore:** `agents/` directory — Agent definitions
- **Try:** Create a test task and watch it flow through the system

---

**Questions?** Check `docs/` or ask in chat. The agents are here to help!
