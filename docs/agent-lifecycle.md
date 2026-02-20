# Agent Lifecycle - Creation, Configuration, and Management

This document explains how agents are created, configured, discovered by the orchestrator, and managed over time.

## Overview

Agents in the Lobs system follow a **template-based architecture**:
1. **Templates** define agent identity and behavior
2. **Workers** are ephemeral OpenClaw sessions spawned per task
3. **Orchestrator** discovers templates, routes tasks, spawns workers

```
Agent Template              Worker Instance              Task
(permanent)                 (ephemeral)                  (persistent)
     │                           │                           │
     │  1. Task created          │                           │
     │◄──────────────────────────┼───────────────────────────┤
     │                           │                           │
     │  2. Orchestrator selects  │                           │
     │     agent type             │                           │
     ├───────────────────────────►                           │
     │                           │                           │
     │  3. Spawn worker with     │                           │
     │     template workspace    │                           │
     ├──────────────────────────►│                           │
     │                           │                           │
     │                           │  4. Execute task          │
     │                           ├──────────────────────────►│
     │                           │                           │
     │                           │  5. Complete, log results │
     │                           │◄──────────────────────────┤
     │                           │                           │
     │  6. Terminate worker      │                           │
     │◄──────────────────────────┤                           │
```

## Agent Templates

### Template Structure

Each agent type has a template workspace at:
```
~/lobs-server/data/agent-templates/<agent-type>/
├── SOUL.md          # Core identity, role, values
├── IDENTITY.md      # Operational guidelines, decision frameworks
├── AGENTS.md        # Workspace instructions, memory usage
├── TOOLS.md         # Tool-specific notes (API keys, preferences)
└── USER.md          # Information about the human (Rafe)
```

### File Purposes

**SOUL.md** — Who is this agent?
- Core identity and personality
- Role definition
- Values and principles
- Communication style

**IDENTITY.md** — How does this agent work?
- Operational guidelines
- Decision-making frameworks
- Edge case handling
- Output format specifications

**AGENTS.md** — Workspace setup and memory instructions
- Session startup checklist
- Memory file locations (identity, experience, shared docs)
- Safety guidelines
- Tool usage notes

**TOOLS.md** — Local tool configuration
- API keys, tokens
- Service endpoints
- Preferences (e.g., Discord user IDs, camera names)

**USER.md** — Human context
- Who is Rafe?
- Preferences, values, communication style
- Due date conventions, timezone
- Work context (student, GSI, etc.)

### Template Locations

**Production templates:**
```
~/lobs-server/data/agent-templates/
├── researcher/
├── architect/
├── reviewer/
├── writer/
└── programmer/
```

**Development/testing:**
```
~/.openclaw/workspace-<agent-type>/
```

For programmer agent, the workspace is:
```
~/lobs-mission-control/
```

## Creating a New Agent Type

### Step 1: Define the Agent Type

Decide on:
- **Name** — lowercase, single word (e.g., `analyst`, `deployer`)
- **Role** — What does this agent do?
- **Capabilities** — What tasks can it handle?
- **Output format** — Where do results go? (inbox, reports, research)

### Step 2: Create the Template Directory

```bash
cd ~/lobs-server/data/agent-templates
mkdir <agent-type>
cd <agent-type>
```

### Step 3: Write the Identity Files

**SOUL.md** — Start with this template:
```markdown
# <AgentType> - SOUL

You are the **<AgentType>** — the Lobs system's <role> agent.

## Core Identity
- <Key trait 1>
- <Key trait 2>
- <Key trait 3>

## Values
- <Value 1>
- <Value 2>
- <Value 3>

## Communication Style
- <Style guideline 1>
- <Style guideline 2>
```

**IDENTITY.md** — Define operational details:
```markdown
# <AgentType> - IDENTITY

## Operational Guidelines
1. <Guideline 1>
2. <Guideline 2>
3. <Guideline 3>

## Decision-Making Framework
- <When to do X>
- <When to escalate>
- <How to handle Y>

## Output Format
<Where results go, what format they take>

## Edge Cases
- <Edge case 1> → <how to handle>
- <Edge case 2> → <how to handle>
```

**AGENTS.md** — Copy from existing template, adjust as needed
**TOOLS.md** — Start empty, add as agent learns
**USER.md** — Copy from existing template (same Rafe context)

### Step 4: Register in CapabilityRegistry

Edit `~/lobs-server/app/orchestrator/capability_registry.py`:

```python
CAPABILITY_MAP = {
    # ... existing entries ...
    "analyst": {
        "capabilities": ["data_analysis", "visualization", "statistics"],
        "keywords": ["analyze", "chart", "graph", "trend", "metric"],
    },
}
```

### Step 5: Create Development Workspace

```bash
mkdir -p ~/.openclaw/workspace-<agent-type>
cp -r ~/lobs-server/data/agent-templates/<agent-type>/* ~/.openclaw/workspace-<agent-type>/
mkdir -p ~/.openclaw/workspace-<agent-type>/memory
touch ~/.openclaw/workspace-<agent-type>/memory/.gitkeep
```

### Step 6: Test the Agent

Manually spawn a worker to test:
```bash
openclaw sessions_spawn \
  --model "sonnet" \
  --sessionTarget "main" \
  --label "test-<agent-type>" \
  --instructions "Test task for <agent-type> agent" \
  --repo "~/.openclaw/workspace-<agent-type>"
```

Check logs:
```bash
tail -f ~/.openclaw/logs/openclaw.log
```

### Step 7: Create a Test Task

```bash
cd ~/lobs-server
source .venv/bin/activate
python bin/create_task.py "Test <agent-type> agent" \
  --project "test" \
  --agent-type "<agent-type>" \
  --notes "Verify agent behavior and output format"
```

Let the orchestrator pick it up, or manually trigger:
```bash
curl -X POST http://localhost:8000/api/orchestrator/force-scan \
  -H "Authorization: Bearer z5mr-WWjPxAAHvRd2ZULm7HLNW1oRubXmcMiBJoEmsU"
```

### Step 8: Refine Based on Results

Review:
- Worker logs (`state/workers/<session-id>/`)
- Task output (inbox, reports, research)
- Experience memory (`memory/YYYY-MM-DD.md`)

Update SOUL.md and IDENTITY.md based on observations.

## OpenClaw Configuration

### Worker Session Parameters

When the orchestrator spawns a worker, it uses `openclaw sessions_spawn`:

```bash
openclaw sessions_spawn \
  --model "sonnet" \                      # Model tier
  --sessionTarget "main" \                # Target session (main agent)
  --label "<agent-type>:<task-id>" \      # Human-readable label
  --instructions "<task description>" \   # Task context
  --repo "<workspace-path>" \             # Agent template workspace
  --runTimeoutSeconds 900                 # 15-minute timeout
```

### Model Selection

Default model tiers:
- **Haiku** — Simple, low-cost (cron jobs, reminders)
- **Sonnet** — Standard worker tasks (most agent work)
- **Opus** — Complex, high-value (architecture design, critical reviews)

Override via task metadata:
```python
task.metadata = {"model_override": "opus"}
```

### Workspace Isolation

Each worker gets its own workspace:
- **Template files** copied to temp directory
- **Memory directory** created (if not exists)
- **Task context** injected via instructions

Workers do NOT share workspaces (isolation prevents conflicts).

## Orchestrator Discovery

The orchestrator discovers agent types via:

1. **CapabilityRegistry** — Maps capabilities to agent types
2. **Template directory** — Scans `~/lobs-server/data/agent-templates/`
3. **Agent metadata** — Database records (`agents` table)

### Registration Flow

```python
# In app/orchestrator/engine.py (startup)
from app.orchestrator.capability_registry import CapabilityRegistrySync

registry = CapabilityRegistrySync()
agent_types = registry.get_all_agent_types()
# → ["researcher", "architect", "reviewer", "writer", "programmer"]

for agent_type in agent_types:
    # Check if agent exists in DB
    agent = await db.execute(select(Agent).filter_by(name=agent_type))
    if not agent:
        # Create agent record
        new_agent = Agent(name=agent_type, status="available")
        db.add(new_agent)
```

### Task Routing

When a task is created:
1. **TaskAutoAssigner** examines task title/description
2. **Matches keywords** to capability map
3. **Assigns agent_type** to task
4. **Scanner** picks up task and confirms agent type
5. **Router** validates and routes to worker

Override auto-assignment:
```python
task = Task(
    title="Custom task",
    agent_type="architect",  # Explicit override
    ...
)
```

## Agent Management

### Updating Agent Identity

**Via Lobs reflection cycle:**
1. Lobs reviews worker logs during reflection
2. Identifies patterns, lessons learned
3. Updates SOUL.md/IDENTITY.md in template directory
4. Git commits changes with message: "refine: <agent-type> identity based on <context>"

**Manual editing:**
```bash
cd ~/lobs-server/data/agent-templates/<agent-type>
vim SOUL.md
git add SOUL.md
git commit -m "refine: <agent-type> - <change description>"
git push
```

### Monitoring Agent Performance

**Via API:**
```bash
curl http://localhost:8000/api/agents \
  -H "Authorization: Bearer z5mr-WWjPxAAHvRd2ZULm7HLNW1oRubXmcMiBJoEmsU"
```

**Via Mission Control:**
- Team View → Agent status cards
- Click agent → See performance metrics, recent tasks

**Via database query:**
```sql
SELECT 
    name,
    status,
    success_count,
    failure_count,
    avg_duration_seconds
FROM agents
ORDER BY success_count DESC;
```

### Disabling an Agent

**Temporary (circuit breaker):**
```bash
curl -X POST http://localhost:8000/api/orchestrator/circuit-breaker/open \
  -H "Authorization: Bearer z5mr-WWjPxAAHvRd2ZULm7HLNW1oRubXmcMiBJoEmsU" \
  -H "Content-Type: application/json" \
  -d '{"agent_type": "researcher", "reason": "repeated failures"}'
```

**Permanent:**
```sql
UPDATE agents SET status = 'disabled' WHERE name = 'researcher';
```

Tasks assigned to disabled agents will remain pending until:
1. Agent is re-enabled
2. Task is manually reassigned
3. Sweep arbitrator escalates

### Retiring an Agent

1. Mark agent as `disabled` in database
2. Remove from CapabilityRegistry
3. Archive template directory
4. Update shared docs (remove from agent-lifecycle.md)

```bash
cd ~/lobs-server/data/agent-templates
mv <agent-type> ../archived-templates/
```

## Worker Lifecycle

### Spawn
1. Orchestrator detects eligible task
2. Creates temp workspace, copies template files
3. Spawns OpenClaw session with `sessions_spawn`
4. Records WorkerSession in database

### Monitor
1. MonitorEnhanced polls worker health (every 10s)
2. Checks for heartbeat (last activity timestamp)
3. Tails logs for errors
4. Updates WorkerSession status

### Complete
1. Worker finishes task, updates task status
2. InboxProcessor creates inbox item (if applicable)
3. Orchestrator marks WorkerSession as `completed`
4. OpenClaw terminates session

### Fail
1. Worker crashes or times out
2. MonitorEnhanced detects failure
3. Logs captured and stored
4. Task marked as `failed`
5. CircuitBreaker increments failure count
6. Orchestrator terminates session

### Timeout
Workers have a default timeout (900s = 15 minutes). Override per task:
```python
task.metadata = {"timeout_seconds": 1800}  # 30 minutes
```

## Best Practices

### Agent Design
- **Narrow scope** — Each agent has a specific, well-defined role
- **Clear output format** — Agents know where results go (inbox, reports, research)
- **Fail gracefully** — Handle errors, escalate when stuck
- **Log liberally** — Experience memory is cheap, use it

### Template Maintenance
- **Version control** — Commit all identity changes
- **Document rationale** — Explain why changes were made
- **Test before deploying** — Spawn test workers before updating production templates
- **Review regularly** — Monthly check for drift or staleness

### Worker Management
- **Monitor failure rates** — High failures → review identity files
- **Tune timeouts** — Adjust based on observed task duration
- **Respect circuit breakers** — Don't force-spawn workers when circuit is open
- **Clean up stale workers** — Monitor for hung sessions

### Task Routing
- **Use auto-assignment** — Let TaskAutoAssigner handle most tasks
- **Override when needed** — Explicit agent_type for special cases
- **Tag tasks** — Use tags for filtering, search, routing hints
- **Set dependencies** — blocked_by ensures correct execution order

## Troubleshooting

### Worker won't spawn
- Check agent status: `curl http://localhost:8000/api/agents`
- Check circuit breaker: `curl http://localhost:8000/api/orchestrator/circuit-breaker/status`
- Check orchestrator logs: `tail -f ~/.openclaw/logs/orchestrator.log`
- Verify template exists: `ls ~/lobs-server/data/agent-templates/<agent-type>/`

### Worker hangs
- Check OpenClaw session: `openclaw sessions_list`
- Check logs: `tail -f ~/.openclaw/logs/openclaw.log`
- Kill session: `openclaw sessions_kill --sessionId <session-id>`
- Review timeout settings

### Wrong agent assigned
- Check CapabilityRegistry mapping
- Review TaskAutoAssigner keywords
- Override with explicit `agent_type`

### Agent produces poor output
- Review worker logs
- Check experience memory for patterns
- Refine SOUL.md/IDENTITY.md with better guidelines
- Test with manual spawn before updating template

## Future Enhancements

- **Multi-agent collaboration** — Agents work together on complex tasks
- **Agent learning** — Agents update their own identity files (with Lobs approval)
- **Dynamic capability expansion** — Agents acquire new capabilities over time
- **Agent specialization** — Fine-tune templates based on task domain
- **Worker pooling** — Keep warm workers ready for fast task pickup
