# 9. Workspace Isolation Strategy

**Date:** 2026-02-22  
**Status:** Accepted  
**Deciders:** System architect

## Context

lobs-server spawns multiple AI agent workers concurrently via OpenClaw Gateway. Each agent needs:
- **Persistent workspace** — Store tools, memory, templates across tasks
- **File system access** — Read/write project files, create artifacts
- **Isolation** — Prevent agents from interfering with each other's work
- **Clean state** — Avoid pollution from previous tasks

Early implementations used a shared workspace (`~/.openclaw/workspace/`), which caused problems:
- **File conflicts** — Two agents modifying the same file simultaneously
- **Memory pollution** — Agent A seeing Agent B's MEMORY.md entries
- **Tool confusion** — Agent finding templates from different agent type
- **Git conflicts** — Multiple agents committing to same repository
- **State leakage** — Task-specific data persisting across tasks

We needed a strategy that:
- Prevents concurrent file access conflicts
- Provides clean, predictable environment per agent
- Allows persistent state (memory, learned patterns)
- Works with OpenClaw's workspace model
- Scales to many concurrent agents

## Decision

We adopt a **per-agent-type isolated workspace strategy** where each agent type gets a dedicated directory, and project work happens in separate repository clones.

### Workspace Structure

```
~/.openclaw/
├── workspace-programmer/      # Programmer agent's persistent workspace
│   ├── AGENTS.md              # (Read-only template, refreshed per task)
│   ├── SOUL.md                # (Read-only template)
│   ├── TOOLS.md               # (Read-only template)
│   ├── MEMORY.md              # (Read-only legacy memory)
│   └── memory/                # (Read-write daily logs)
│       ├── 2026-02-20.md
│       └── 2026-02-22.md
│
├── workspace-researcher/      # Researcher agent's persistent workspace
│   ├── AGENTS.md
│   ├── SOUL.md
│   ├── TOOLS.md
│   ├── MEMORY.md
│   └── memory/
│
├── workspace-architect/       # Architect agent's persistent workspace
│   └── ...
│
└── workspace-writer/          # Writer agent's persistent workspace
    └── ...
```

**Project repositories (separate from workspaces):**
```
~/lobs-server/                 # Project repo (shared, locked per worker)
~/lobs-mission-control/        # Another project repo
~/project-x/                   # Another project repo
```

### Isolation Rules

| Resource | Isolation Scope | Sharing Allowed? | Coordination |
|----------|----------------|------------------|--------------|
| **Workspace directory** | Per agent type | No (exclusive to agent type) | None needed |
| **Template files** (AGENTS.md, SOUL.md, etc.) | Refreshed per task | Read-only | Orchestrator refreshes |
| **Memory files** (memory/*.md) | Per agent type | Yes (across tasks, same agent) | Date-based filenames |
| **Project repositories** | Per project | No (domain locked) | Project-level locks |
| **Worker results** | Per task | No (unique task ID) | Unique directories |

### How It Works

**1. Agent Spawn:**
- Orchestrator spawns agent via OpenClaw Gateway: `agent:programmer:subagent:<uuid>`
- OpenClaw routes to workspace: `~/.openclaw/workspace-programmer/`
- Orchestrator refreshes template files (AGENTS.md, SOUL.md, TOOLS.md, etc.) from `lobs-server/agents/programmer/`

**2. Agent Execution:**
- Agent works in **workspace for context/memory** (reads MEMORY.md, writes to memory/)
- Agent works in **project repo for changes** (~/lobs-server/, ~/project-x/, etc.)
- Agent NEVER modifies template files (read-only)
- Agent ALWAYS appends to `memory/YYYY-MM-DD.md` with task ID header

**3. Agent Completion:**
- Agent writes `.work-summary` (optional)
- Orchestrator extracts transcript, handoffs, artifacts
- Project repo committed and pushed (if changes made)
- Workspace memory persists (carries to next task)

### Memory Isolation

**Problem:** Multiple agents running concurrently on the same agent type workspace (e.g., two programmer tasks).

**Solution:** Date-based memory files with task ID headers:

```markdown
# memory/2026-02-22.md

## Task-ABC123: Add user authentication
- Implemented JWT middleware in src/auth/middleware.py
- Added tests in tests/test_auth.py
- Learned: FastAPI dependency injection is cleaner than decorators

## Task-DEF456: Fix database migration
- Updated alembic migration in migrations/versions/001_add_users.py
- Learned: Always test migrations with rollback
```

**Concurrent safety:**
- Agents append to date file (file system handles concurrent appends)
- Task ID headers prevent confusion ("which task wrote this?")
- Orchestrator periodically consolidates into MEMORY.md (single-threaded)

### Project Repository Isolation

**Problem:** Two agents modifying same project simultaneously → merge conflicts.

**Solution:** Domain locks (one worker per project) in `WorkerManager`:

```python
# Before spawning worker:
if project_id in self.project_locks:
    return False  # Project locked, queue for later

self.project_locks[project_id] = task_id
# ... spawn worker on project ...
# On completion: del self.project_locks[project_id]
```

**Result:** Only one agent touches a project repo at a time, no conflicts.

## Consequences

### Positive

- **No file conflicts** — Agents never collide on same files (different workspaces)
- **Clean state** — Each agent type has isolated context (no cross-contamination)
- **Persistent memory** — Agents accumulate knowledge across tasks (memory/ directory)
- **Concurrent execution** — Multiple agent types can run simultaneously (programmer + researcher)
- **Simple debugging** — Look in workspace-{type}/ to see agent's full context
- **Tool isolation** — Programmer tools in workspace-programmer/, researcher tools elsewhere
- **Predictable paths** — Agent always knows its workspace location

### Negative

- **Disk usage** — 5 agent types × workspace = 5× storage (mostly memory files, ~10MB each)
- **No cross-agent memory** — Programmer doesn't see researcher's findings (must use handoffs)
- **Project-level bottleneck** — Only one agent per project (limits parallelism within a project)
- **Workspace sprawl** — Adding new agent types increases workspace count
- **Manual cleanup** — Old memory files accumulate (need periodic cleanup)

### Neutral

- Template files duplicated across workspaces (but refreshed from source, so always in sync)
- Memory consolidation is manual/periodic (not real-time)

## Alternatives Considered

### Option 1: Shared Workspace (No Isolation)

- **Pros:**
  - Simple (one workspace, all agents share)
  - Disk-efficient (no duplication)
  - Cross-agent memory visible (all see MEMORY.md)

- **Cons:**
  - **File conflicts** — Concurrent writes to MEMORY.md, templates
  - **Context pollution** — Agent A sees Agent B's tools, memory
  - **Hard to debug** — Whose memory entry is this?
  - **Race conditions** — Git operations from multiple agents

- **Why rejected:** We tried this. It broke constantly. File locks, merge conflicts, confusing memory.

### Option 2: Per-Task Isolated Workspaces

- **Pros:**
  - Complete isolation (no cross-task contamination)
  - Parallel execution within same agent type
  - Clean slate every task

- **Cons:**
  - **No persistent memory** — Agent forgets lessons from previous tasks
  - **Disk explosion** — 1000 tasks × workspace = 1000× storage
  - **Slow startup** — Must repopulate workspace every task
  - **Lost expertise** — Agent can't improve over time

- **Why rejected:** Memory is crucial for agent learning. Losing it defeats the purpose.

### Option 3: File-Level Locking (Shared Workspace + Locks)

- **Pros:**
  - Shared workspace (memory visible across agents)
  - Concurrent execution (lock only contested files)

- **Cons:**
  - **Complex** — Need lock manager, deadlock detection, timeout handling
  - **Distributed locks** — Requires Redis/etcd for multi-machine
  - **Lock contention** — MEMORY.md becomes bottleneck
  - **Failure modes** — Stale locks from crashed agents

- **Why rejected:** Over-engineered. Per-agent-type workspaces are simpler and avoid locks entirely.

### Option 4: Database-Backed Memory (No Workspace Files)

- **Pros:**
  - ACID transactions (no file conflicts)
  - Query memory efficiently (SQL search)
  - Centralized (no workspace sprawl)

- **Cons:**
  - **Disconnected from agent** — Agent expects file system (MEMORY.md)
  - **Sync complexity** — Need to sync DB ↔ files for agent access
  - **Harder to debug** — Can't just `cat workspace/MEMORY.md`
  - **Overkill** — File system is simpler for append-only logs

- **Why rejected:** Files are simpler, agent-native. Can add DB index later if search is slow.

## Implementation Details

### Workspace Initialization (Orchestrator)

Before spawning agent:
```python
workspace = Path(f"~/.openclaw/workspace-{agent_type}").expanduser()
workspace.mkdir(exist_ok=True)

# Refresh templates from source
templates = ["AGENTS.md", "SOUL.md", "TOOLS.md", "IDENTITY.md", "USER.md"]
for file in templates:
    src = Path(f"lobs-server/agents/{agent_type}/{file}")
    dst = workspace / file
    shutil.copy(src, dst)

# Ensure memory/ exists
(workspace / "memory").mkdir(exist_ok=True)
```

### Memory Append (Agent)

In agent's work:
```python
import datetime

task_id = os.getenv("TASK_ID", "unknown")
today = datetime.date.today().strftime("%Y-%m-%d")
memory_file = Path(f"memory/{today}.md")

entry = f"""
## Task-{task_id}: {task_title}
- What I did: ...
- What I learned: ...
- Gotchas: ...
"""

with open(memory_file, "a") as f:
    f.write(entry)
```

### Memory Consolidation (Orchestrator, Periodic)

Run daily or on-demand:
```python
# Collect all memory/*.md files
entries = []
for file in sorted(Path("workspace-programmer/memory").glob("*.md")):
    entries.append(file.read_text())

# Write to MEMORY.md (with deduplication, summarization)
consolidated = consolidate_entries(entries)
Path("workspace-programmer/MEMORY.md").write_text(consolidated)
```

## Testing Strategy

**Isolation tests:**
- Spawn two programmer tasks concurrently → Verify no file conflicts
- Spawn programmer + researcher concurrently → Verify independent workspaces
- Check memory/YYYY-MM-DD.md → Verify task ID headers prevent collisions

**Project lock tests:**
- Attempt to spawn two tasks on same project → Second task queued
- Complete first task → Second task starts, no merge conflicts

**Memory persistence tests:**
- Spawn task, write memory entry, complete
- Spawn second task → Verify sees previous memory

**Template refresh tests:**
- Modify AGENTS.md in source → Spawn task → Verify workspace has new version

## Migration

**From:** Shared workspace (`~/.openclaw/workspace/`)  
**To:** Per-agent-type workspaces (`~/.openclaw/workspace-{type}/`)

**Steps:**
1. Create new workspace directories for each agent type
2. Migrate existing MEMORY.md to appropriate agent workspace
3. Update OpenClaw routing to use per-type workspaces
4. Update orchestrator to refresh templates per spawn
5. Archive old shared workspace (don't delete, keep for reference)

**Rollback:** Revert OpenClaw routing to shared workspace (data safe, just reconfigure paths)

## Future Enhancements

1. **Vector search on memory** — Index memory/*.md for semantic search (agent asks "what do I know about SQLite migrations?")
2. **Cross-agent memory sharing** — Architect can query "what did researcher find about X?"
3. **Automatic memory cleanup** — Archive entries older than 90 days
4. **Memory tagging** — Tag entries by topic (database, testing, deployment) for better retrieval

## References

- `~/.openclaw/workspace-*/` — Agent workspaces
- `app/orchestrator/worker.py` — Worker spawn logic, workspace setup
- `worker-template/AGENTS.md` — Agent instructions on memory usage
- ADR-0007: State Management and Consistency Model
- ADR-0008: Agent Specialization Model

---

*Based on Michael Nygard's ADR format*
