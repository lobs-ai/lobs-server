# Memory System Design

The Lobs system uses a **3-layer memory architecture** to balance agent autonomy, learning, and consistency.

## Overview

```
┌─────────────────────────────────────────────────────────┐
│ Layer 1: Agent Identity Memory (SOUL.md, IDENTITY.md)  │
│ - Behavioral guidelines, role definition                │
│ - Self-editable by agents                               │
│ - Lobs refines during reflection/cleanup                │
└─────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────┐
│ Layer 2: Agent Experience Memory (memory/ dir)         │
│ - Daily work logs (YYYY-MM-DD.md)                       │
│ - Raw observations, decisions, context                  │
│ - Agent-managed, grows indefinitely                     │
└─────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────┐
│ Layer 3: Shared Documentation (docs/ dir)              │
│ - Global knowledge (architecture, standards)            │
│ - Read-only for worker agents                           │
│ - Only Lobs edits canonical docs                        │
└─────────────────────────────────────────────────────────┘
```

## Layer 1: Agent Identity Memory

### Purpose
Define **who the agent is** — their role, personality, operational guidelines, and behavioral patterns.

### Files
- **SOUL.md** — Core identity, role definition, values, communication style
- **IDENTITY.md** — Detailed operational guidelines, decision-making frameworks, edge cases

### Location
- **Templates:** `~/lobs-server/data/agent-templates/<agent-type>/`
- **Active workspaces:** `~/.openclaw/workspace-<agent-type>/`

### Ownership & Edit Rights
- **Agents can edit** — These files are part of the agent's workspace and can be modified during reflection
- **Lobs refines** — During cleanup cycles, Lobs reviews and refines identity files based on observed behavior and outcomes
- **No arbitrary drift** — Changes should reflect learned lessons, not random edits

### Example Content (SOUL.md)
```markdown
# Researcher - SOUL

You are the **Researcher** — the Lobs system's investigative agent.

## Core Identity
- Deep diver: exhaustive research, multiple sources, compare options
- Synthesizer: distill findings into clear, actionable insights
- Skeptical: verify claims, check biases, note limitations
- Thorough: cover edge cases, alternatives, trade-offs

## Values
- Accuracy over speed
- Primary sources over summaries
- Nuance over simplification
- Evidence over intuition
```

### Example Content (IDENTITY.md)
```markdown
# Researcher - IDENTITY

## Operational Guidelines
1. Always cite sources (URLs, papers, docs)
2. Compare at least 3 options when evaluating tools/approaches
3. Note limitations and gaps in research
4. Prefer primary sources (docs, whitepapers) over blog posts
5. Flag contradictory information

## Output Format
Research goes to `state/research/<topic>/findings.md`.
Include: summary, detailed findings, sources, recommendations.
```

### Learning Flow
1. Agent performs work
2. Agent logs observations/decisions to experience memory
3. During reflection cycle, Lobs reviews outcomes
4. Lobs updates SOUL.md/IDENTITY.md with learned patterns
5. Template updated for future workers

## Layer 2: Agent Experience Memory

### Purpose
Capture **what the agent has done** — raw work logs, observations, decisions made during task execution.

### Structure
```
memory/
├── 2026-02-19.md
├── 2026-02-18.md
├── 2026-02-17.md
└── .gitkeep
```

### Location
- **Each agent workspace:** `~/.openclaw/workspace-<agent-type>/memory/`
- **Also:** `~/lobs-mission-control/memory/` (programmer agent)

### Format
Daily markdown files (`YYYY-MM-DD.md`) with timestamped entries:

```markdown
# 2026-02-19 - Researcher

## 10:30 AM - Task #42: Research FastAPI vs Flask
- Compared performance benchmarks
- FastAPI: async-first, Pydantic integration, auto OpenAPI
- Flask: simpler, larger ecosystem, sync-first
- Recommendation: FastAPI for our async use case

## 2:15 PM - Task #43: Compare SQLite vs Postgres
- Single-user → SQLite sufficient
- Async support: aiosqlite works well
- No infrastructure overhead
- Decision: SQLite for now, can migrate later if multi-user needed
```

### Ownership & Edit Rights
- **Agents write freely** — This is their journal
- **No restrictions** — Agents decide what to log
- **Grows indefinitely** — No automatic cleanup (humans/Lobs compress manually if needed)

### Usage Patterns
- **Agents read recent days** (today + yesterday) to maintain context
- **Lobs reads for reflection** — Reviews recent logs to identify patterns, failures, lessons
- **Not for secrets** — Avoid logging sensitive credentials or personal info

### Cleanup Strategy
- **No automatic deletion** — Experience memory grows over time
- **Manual compression** — Lobs or humans can periodically compress old logs into summaries
- **Optional archival** — Move old logs to archive/ subdirectory after 90+ days

## Layer 3: Shared Documentation

### Purpose
Provide **global knowledge** that all agents can reference — architecture, standards, workflows, operational procedures.

### Location
`~/lobs-server/docs/`

### Structure
```
docs/
├── INDEX.md                  # Table of contents
├── system-overview.md        # Architecture overview
├── memory-system.md          # This document
├── agent-lifecycle.md        # Agent creation/management
├── coding-standards.md       # Code quality guidelines
├── git-workflow.md           # Branch strategy, commits, PRs
├── model-routing.md          # Model tier system, cost control
└── orchestrator.md           # Control loop, reflection, sweeps
```

### Ownership & Edit Rights
- **Read-only for worker agents** — Agents can read but should not edit
- **Only Lobs edits canonical docs** — Ensures consistency and avoids drift
- **Workers can propose updates** — Via inbox items or task deliverables

### Usage Patterns
- **Agents reference via memory search** — INDEX.md lists all docs
- **Lobs updates during reflection** — Refines docs based on observed gaps or confusion
- **Humans can edit directly** — For policy changes, new standards

### Discovery
Agents discover shared docs via a reference in their `AGENTS.md`:

```markdown
## Shared Documentation
Query shared docs via memory search. Index at `/Users/lobs/lobs-server/docs/INDEX.md`.
Key docs: system-overview, memory-system, agent-lifecycle, coding-standards, git-workflow, model-routing, orchestrator.
```

## Cross-Layer Interactions

### Agent Onboarding Flow
1. Worker spawned with agent template (Layer 1)
2. Reads SOUL.md, IDENTITY.md to understand role
3. Checks today's memory log (Layer 2) for recent context
4. References shared docs (Layer 3) for system knowledge
5. Begins work, logs to experience memory

### Learning Flow
1. Agent logs observations/decisions (Layer 2)
2. Lobs reviews during reflection cycle
3. Identifies patterns, lessons learned
4. Updates SOUL.md/IDENTITY.md (Layer 1)
5. Updates shared docs if broadly applicable (Layer 3)

### Failure Recovery Flow
1. Worker fails, logs captured (Layer 2)
2. Lobs reviews failure logs
3. Identifies root cause (code bug, unclear guidelines, etc.)
4. Updates IDENTITY.md with better guidelines (Layer 1)
5. Updates coding-standards.md if systemic issue (Layer 3)

## Memory Compression Strategy

### When to Compress
- **Experience memory (Layer 2)** grows large (>50 daily logs)
- **Redundant information** accumulates (repeated patterns)
- **Context becomes stale** (old logs no longer relevant)

### How to Compress
1. Lobs reviews memory/ logs
2. Extracts key decisions, lessons, patterns
3. Updates IDENTITY.md with distilled knowledge (Layer 1)
4. Archives old logs to `memory/archive/`
5. Keeps recent 30 days in main memory/ dir

### What to Keep
- **Significant decisions** — Architecture choices, trade-offs
- **Lessons learned** — Failed approaches, what worked
- **Context gaps** — Areas where guidelines were unclear

### What to Archive
- **Routine work logs** — Tasks completed without issues
- **Redundant information** — Already captured in identity/shared docs
- **Stale context** — No longer relevant to current work

## Lobs Cleanup Responsibilities

As the main orchestrating agent, Lobs has special responsibilities for memory hygiene:

### Daily
- Review worker logs in experience memory (Layer 2)
- Flag anomalies or repeated failures

### Weekly
- Reflection cycle: analyze patterns, update identity files (Layer 1)
- Update shared docs if gaps identified (Layer 3)

### Monthly
- Compress experience memory (Layer 2) → archive old logs
- Refine agent templates based on performance trends
- Update system-overview.md with architectural changes

## Best Practices

### For Worker Agents
- **Read your identity** (SOUL.md, IDENTITY.md) at session start
- **Log liberally** to experience memory — it's cheap storage
- **Reference shared docs** when unsure about standards/architecture
- **Propose updates** via inbox items if you notice gaps

### For Lobs (Main Agent)
- **Review experience memory regularly** — daily during reflection
- **Update identity files thoughtfully** — based on evidence, not hunches
- **Keep shared docs canonical** — don't let them drift or become stale
- **Compress experience memory periodically** — prevent bloat

### For Humans (Rafe)
- **Edit shared docs directly** for policy changes
- **Review Lobs's identity refinements** in git commits
- **Flag confusion** if agents seem to misunderstand their roles
- **Trust the system** — don't micromanage memory cleanup

## Migration Notes

### Legacy Systems
- **MEMORY.md** — Old long-term memory file for Lobs main agent. Still used in main session, but worker agents don't load it.
- **Git-based state** — Old lobs-control repo used git for state. Now superseded by SQLite + this memory system.

### Current State
- **Templates:** `~/lobs-server/data/agent-templates/` (Layer 1)
- **Worker memory:** `~/.openclaw/workspace-<type>/memory/` (Layer 2)
- **Shared docs:** `~/lobs-server/docs/` (Layer 3)

## Security Considerations

### Private Context
- **Layer 1 (identity)** — Safe to share across agents
- **Layer 2 (experience)** — May contain task-specific details, keep isolated per agent
- **Layer 3 (shared docs)** — Safe to share across all agents

### Secrets Management
- **Never log secrets** to experience memory
- **Use environment variables** or secure vaults for credentials
- **Reference secrets by name** (e.g., "GitHub token"), not value

### Cross-Agent Isolation
- **Workers don't share memory directories** — Each agent type has its own memory/
- **No cross-contamination** — Researcher memory stays separate from Programmer memory
- **Templates are starting points** — Copied to worker workspace, not shared

## Future Enhancements

- **Semantic search** across all memory layers
- **Automatic summarization** of experience logs
- **Version control** for identity files (track refinements over time)
- **Memory federation** — Share anonymized lessons across instances
- **Memory queries** — Natural language search across all layers
