# 8. Agent Types and Specialization Model

**Date:** 2026-02-22  
**Status:** Accepted  
**Deciders:** System architect, product owner

## Context

The lobs multi-agent system needs to handle diverse work: code implementation, research, documentation, system design, and code review. We needed to decide:

- **Should we use a single general-purpose agent or multiple specialized agents?**
- **How should specialization be structured?**
- **How do we balance flexibility (one agent does everything) vs. expertise (agents have clear roles)?**

Early experiments with a single "do-everything" agent showed problems:
- Prompt bloat (trying to cover all skills in one context)
- Confusion about role boundaries (when to write code vs. research alternatives)
- Inconsistent output quality (expert at coding, mediocre at writing)
- Tool access conflicts (should research agent have git commit access?)

We needed a model that:
- Provides clear role boundaries
- Allows agents to develop deep expertise in their domain
- Supports workflow handoffs (research → design → implementation)
- Scales to new capabilities without polluting existing agents

## Decision

We adopt a **capability-based specialization model** with **five core agent types**, each with distinct roles, tool access, and expertise:

### Agent Types

| Agent Type | Role | Capabilities | Model Tier | Tools |
|------------|------|--------------|------------|-------|
| **programmer** | Code implementation, bug fixes, testing | `code`, `test`, `refactor`, `debug` | Primary (Codex) | read/write/edit, exec, browser |
| **researcher** | Investigation, analysis, proof-of-concepts | `research`, `investigate`, `analyze`, `evaluate` | Standard | read, web_search, web_fetch, browser |
| **architect** | System design, technical strategy, planning | `design`, `plan`, `strategize`, `analyze` | Primary | read, write, web_search |
| **writer** | Documentation, reports, guides, summaries | `write`, `document`, `summarize`, `explain` | Standard | read, write, browser |
| **reviewer** | Code review, audits, quality checks | `review`, `audit`, `assess`, `critique` | Standard | read, exec (read-only), browser |

### Specialization Structure

Each agent is defined by **five context files** in `agents/{type}/`:

1. **AGENTS.md** — Role-specific instructions, workflow, constraints
2. **SOUL.md** — Personality, values, decision-making philosophy
3. **TOOLS.md** — Available tools and usage patterns
4. **IDENTITY.md** — Metadata (model, capabilities, proactive behaviors)
5. **USER.md** — Presentation layer (how agent introduces itself)

**Example: Programmer vs. Researcher**

```
agents/programmer/
├── AGENTS.md       → "You write code. Test before finishing."
├── SOUL.md         → "Pragmatic. Ship working code."
├── TOOLS.md        → "read/write/edit, exec for testing"
├── IDENTITY.md     → "Capabilities: code, test, refactor"
└── USER.md         → "I'm a programmer agent..."

agents/researcher/
├── AGENTS.md       → "You investigate. No code changes."
├── SOUL.md         → "Curious. Explore alternatives."
├── TOOLS.md        → "web_search, web_fetch, read-only"
├── IDENTITY.md     → "Capabilities: research, analyze"
└── USER.md         → "I'm a research agent..."
```

### Capability Matching

The orchestrator uses **capability strings** to route tasks:
- Task: "Implement user auth" → Matches `code` capability → Routes to **programmer**
- Task: "Research SQLite alternatives" → Matches `research` capability → Routes to **researcher**
- Task: "Write API documentation" → Matches `write` capability → Routes to **writer**

Capabilities are:
- **Defined in IDENTITY.md** — Explicit, searchable, version-controlled
- **Used by project-manager** — Intelligent routing based on task requirements
- **Extensible** — New capabilities can be added without code changes

### Proactive Behaviors

Some agents have **proactive capabilities** (work they can initiate autonomously):

- **programmer**: `scan_todos`, `fix_warnings`, `small_improvements`
- **architect**: `scan_complexity`, `identify_patterns`

Proactive tasks are:
- Optional (agent runs in proactive mode periodically)
- Low-risk (small improvements, not product changes)
- Auditable (creates proposals for larger work)

## Consequences

### Positive

- **Clear role boundaries** — Each agent knows exactly what it does (no "am I supposed to code or research?")
- **Optimized prompts** — Programmer gets code-specific instructions, researcher gets investigation patterns
- **Tool safety** — Researcher can't accidentally commit code, programmer can't modify production configs
- **Better output quality** — Each agent becomes expert in its domain
- **Parallel workflows** — Research and implementation can happen simultaneously on different projects
- **Easy to extend** — Add new agent types (e.g., "devops", "analyst") without modifying existing ones
- **Natural handoffs** — Architect designs → Programmer implements → Reviewer audits

### Negative

- **More configuration** — Five agent types × five files = 25 config files to maintain
- **Context switching** — Task handoffs require new agent spawn (latency + cost)
- **Capability gaps** — Some tasks don't fit neatly (e.g., "implement then document" spans two agents)
- **Coordination overhead** — Multi-agent workflows require explicit handoffs
- **Model cost** — Running multiple agents costs more than single agent (though routing optimizes this)

### Neutral

- Agents are stateless between tasks (context rebuilt each spawn)
- Same agent type can run multiple instances concurrently (isolated by workspace)

## Alternatives Considered

### Option 1: Single General-Purpose Agent

- **Pros:**
  - Simple (one agent, one configuration)
  - No handoffs (one agent does entire workflow)
  - Lower latency (no context switching)
  - Unified memory (sees all past work)

- **Cons:**
  - **Prompt pollution** — Single prompt must cover all skills (coding, writing, research)
  - **Tool confusion** — Hard to control access (should agent have git push for docs?)
  - **Quality dilution** — Jack of all trades, master of none
  - **Hard to optimize** — Can't tune model/tools for specific tasks

- **Why rejected:** We tried this first. The prompt became 5000+ tokens and the agent was mediocre at everything. Specialization dramatically improved quality.

### Option 2: Skill-Based Prompting (Dynamic Role)

- **Pros:**
  - Single agent, role specified per task
  - Flexible (can be "programmer" for one task, "researcher" for next)
  - Simpler configuration

- **Cons:**
  - No persistent identity (agent doesn't develop expertise)
  - Tool access still needs switching logic
  - Prompt still bloated (must include all role patterns)
  - Harder to reason about ("which role is this agent in right now?")

- **Why rejected:** Adds complexity without clear benefit. Specialization is cleaner.

### Option 3: Capability Tags on Single Agent

- **Pros:**
  - One agent, capabilities declared in task metadata
  - Routing based on tags (`task.capabilities = ['code', 'test']`)

- **Cons:**
  - Still requires mode-switching logic
  - Doesn't solve prompt bloat or tool access control
  - Capability definitions scattered (task metadata vs. agent config)

- **Why rejected:** Doesn't address core problems (prompt size, tool safety, quality).

### Option 4: Fine-Grained Agents (10+ Types)

- **Pros:**
  - Hyper-specialized (e.g., "python-tester", "api-documenter", "database-designer")
  - Extremely focused prompts
  - Tight tool control

- **Cons:**
  - **Configuration explosion** — 50+ files, hard to maintain
  - **Routing complexity** — Project-manager needs to know all micro-roles
  - **Workflow rigidity** — Every task needs explicit routing through many agents
  - **Diminishing returns** — "Python tester" vs. "JavaScript tester" not meaningfully different

- **Why rejected:** Premature specialization. Five agent types handle 95% of work well. Can add more if clear need emerges.

## Design Principles

1. **Specialize by workflow stage, not technology**
   - ✅ `programmer` (implements in any language)
   - ❌ `python-programmer`, `typescript-programmer`

2. **Agents own outcomes, not tasks**
   - Programmer delivers working, tested code
   - Researcher delivers analysis with recommendations
   - Not just "make changes" or "write text"

3. **Capabilities are verbs, not tools**
   - ✅ `code`, `research`, `design`
   - ❌ `git`, `web_search`, `bash`

4. **Agents hand off work, not credentials**
   - Architect creates handoff tasks for programmer
   - Programmer doesn't get "read architecture docs and design solution" as single task

## Testing Strategy

**Capability routing tests:**
- Task with "implement feature" → Routes to programmer
- Task with "research alternatives" → Routes to researcher
- Task with "write documentation" → Routes to writer

**Tool access tests:**
- Researcher cannot execute `git commit`
- Programmer can execute tests but not deploy
- Reviewer has read-only exec access

**Cross-agent workflows:**
- Architect → Programmer handoff (design doc → implementation)
- Researcher → Architect handoff (investigation → system design)
- Programmer → Reviewer handoff (code → review)

## Evolution Path

**Phase 1** (current): Five core agents, clear boundaries ✅

**Phase 2** (future): Add specialist agents as needs emerge:
- `devops` — Deployment, infrastructure, monitoring
- `analyst` — Data analysis, metrics, reporting
- `security` — Security review, threat modeling

**Phase 3** (future): Multi-agent orchestration:
- Project-manager can spawn coordinated workflows
- Agents can request assistance from other agents
- Hybrid tasks (e.g., "implement with tests and docs") auto-split

## References

- `agents/*/IDENTITY.md` — Agent capability definitions
- `app/orchestrator/registry.py` — Agent config loader
- `app/orchestrator/router.py` — Capability-based routing
- ADR-0003: Project Manager Delegation
- ADR-0009: Workspace Isolation Strategy

---

*Based on Michael Nygard's ADR format*
