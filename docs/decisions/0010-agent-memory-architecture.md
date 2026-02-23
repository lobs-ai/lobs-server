# 10. Agent Memory Architecture (MEMORY.md vs. memory/)

**Date:** 2026-02-22  
**Status:** Accepted  
**Deciders:** System architect

## Context

AI agents in the lobs system need to **learn from experience** — remember patterns, avoid repeating mistakes, and build expertise over time. This requires persistent memory that:

- **Survives agent restarts** — Knowledge persists across task executions
- **Scales to many tasks** — Handle hundreds of task logs without bloating
- **Supports fast retrieval** — Agent can quickly find relevant past knowledge
- **Prevents conflicts** — Multiple concurrent agents don't corrupt memory
- **Separates concerns** — Distinguish between curated knowledge vs. raw logs

Early implementations used a single `MEMORY.md` file (Markdown document with all memories). This worked initially but broke down:

**Problems with single-file MEMORY.md:**
- **Concurrent write conflicts** — Two agents appending simultaneously → race conditions, lost updates
- **File bloat** — 500+ tasks → 50,000+ line file, slow to read and search
- **Poor retrieval** — Agent must read entire file to find relevant info (no indexing)
- **Hard to organize** — Chronological log doesn't cluster related knowledge
- **Difficult to clean** — Can't archive old entries without manual editing

We needed a memory architecture that:
- Scales to 1000+ tasks without performance degradation
- Prevents concurrent write conflicts
- Enables semantic search ("what did I learn about migrations?")
- Separates transient logs from reusable knowledge
- Supports both human and AI authoring

## Decision

We adopt a **dual-tier memory architecture** combining:
1. **`MEMORY.md`** — Curated, read-only knowledge base (agent reads, orchestrator writes)
2. **`memory/`** — Append-only daily logs (agent writes, human consolidates)

### Architecture Overview

```
workspace-programmer/
├── MEMORY.md              # Tier 1: Curated knowledge (READ ONLY for agents)
│                          # - Manually edited or auto-consolidated
│                          # - Organized by topic, not chronology
│                          # - Loaded into agent context on spawn
│
└── memory/                # Tier 2: Daily logs (WRITE ONLY for agents)
    ├── 2026-02-20.md      # One file per day
    ├── 2026-02-21.md      # Task entries with headers
    └── 2026-02-22.md      # Append-only, concurrent-safe
```

### Memory Tiers

#### Tier 1: MEMORY.md (Curated Knowledge)

**Purpose:** High-value, reusable knowledge that agents should know.

**Content:**
- Patterns learned ("Always run migrations with --dry-run first")
- Project-specific gotchas ("lobs-server uses UTC for all timestamps")
- Best practices ("Prefer factory_boy over manual test fixtures")
- Tool tips ("Use rg instead of grep for faster search")
- Decision rationale ("We chose FastAPI over Flask for async support")

**Structure:**
```markdown
# Memory - Programmer Agent

## Project: lobs-server

### Database Patterns
- Always use async sessions (`AsyncSession`)
- Migrations go in `alembic/versions/`
- Test with `pytest -k test_db`

### Testing
- Use `pytest.mark.asyncio` for async tests
- Factory files in `tests/factories/`
- Mock OpenClaw with `tests/fixtures/openclaw_mock.py`

## Lessons Learned

### 2026-02-15: SQLite Locking
- WAL mode prevents read locks but not write locks
- Use `busy_timeout=10000` to retry on contention
- Never hold transactions open during I/O

### 2026-02-18: Async Context Managers
- FastAPI lifespan needs `async with` not `with`
- Database sessions auto-commit on exit
```

**Access:**
- **Agent:** Read-only (loaded into context at spawn)
- **Orchestrator:** Read-write (consolidates from memory/)
- **Human:** Read-write (can manually edit for high-value insights)

**Update frequency:** Weekly or on-demand (not real-time)

#### Tier 2: memory/*.md (Daily Logs)

**Purpose:** Raw append-only logs of what each task did and learned.

**Content:**
- Task-specific work log ("Implemented JWT auth in src/auth.py")
- Immediate learnings ("Discovered FastAPI validators don't auto-strip whitespace")
- Gotchas ("Don't forget to await db.commit()")
- References ("See tests/test_auth.py for usage examples")

**Structure:**
```markdown
# memory/2026-02-22.md

## Task-6DC8F666: Populate ADR system
Project: lobs-server
- Wrote 5 ADRs documenting architectural decisions
- Learned: ADR template is in docs/decisions/0000-template.md
- Gotcha: Need to increment number manually (no auto-numbering)

## Task-ABC12345: Add calendar event validation
Project: lobs-server
- Added pydantic validators in app/schemas.py
- Learned: Use `@validator` for cross-field validation
- Tests: tests/test_calendar_validation.py
```

**Access:**
- **Agent:** Write-only (appends on task completion)
- **Orchestrator:** Read (for consolidation)
- **Human:** Read (for debugging, understanding what agent did)

**Update frequency:** Real-time (every task writes)

### Concurrent Safety

**Problem:** Multiple agents running simultaneously might write to same memory file.

**Solution 1 (Current):** Date-based files with task ID headers
- Each task appends to `memory/YYYY-MM-DD.md`
- File system handles concurrent appends (atomic on POSIX)
- Task ID headers prevent confusion about authorship
- Risk: Interleaved writes (Task A writes line 1, Task B writes line 2)
  - Acceptable: Logs are append-only, order doesn't matter within a day

**Solution 2 (Future):** Per-task files
- `memory/task-ABC123.md`, `memory/task-DEF456.md`
- No concurrent writes (each task has unique file)
- Cons: File proliferation (1000 tasks = 1000 files)
- Migrate if concurrent append causes problems

### Consolidation Process

**Trigger:** Weekly or on-demand (e.g., "memory getting large")

**Steps:**
1. Orchestrator reads all `memory/*.md` files
2. Groups entries by topic/pattern using LLM
3. Summarizes into reusable knowledge
4. Appends to MEMORY.md (or updates existing sections)
5. Archives old memory/*.md files (move to `memory/archive/`)

**Example consolidation:**

**Input (from memory/):**
```
Task-123: Learned to use async sessions
Task-456: Forgot to await db.commit(), got error
Task-789: SQLite WAL mode needs busy_timeout
```

**Output (to MEMORY.md):**
```markdown
### Database Sessions (updated 2026-02-22)
- Always use async sessions (`AsyncSession`)
- Don't forget to await `db.commit()` after writes
- Set `busy_timeout=10000` to handle WAL mode contention
```

## Consequences

### Positive

- **Concurrent-safe writes** — Agents can write simultaneously (date-based files)
- **Scales to many tasks** — One file per day, not per task (manageable growth)
- **Fast agent reads** — MEMORY.md is curated, small (~500 lines vs. 50,000)
- **Organized knowledge** — MEMORY.md grouped by topic, not chronology
- **Preserves raw logs** — memory/ keeps original context (debugging, audit trail)
- **Human + AI collaboration** — Humans can edit MEMORY.md, agents append to memory/
- **Incremental consolidation** — Consolidate in batches, not real-time (lower overhead)

### Negative

- **Two memory systems** — Agents must check MEMORY.md for knowledge, memory/ for recent logs
- **Consolidation lag** — Valuable insights in memory/ not visible in MEMORY.md until consolidation
- **Manual process** — Consolidation requires orchestrator or human action (not automatic yet)
- **Storage growth** — memory/ accumulates over time (need periodic archival)
- **Discovery challenge** — Agent may not find relevant knowledge if it's only in memory/, not MEMORY.md

### Neutral

- MEMORY.md is read-only for agents (prevents accidental corruption)
- memory/ files are permanent (never deleted, only archived)

## Alternatives Considered

### Option 1: Single MEMORY.md (Append-Only)

- **Pros:**
  - Simple (one file, one location)
  - All knowledge in one place
  - No consolidation needed

- **Cons:**
  - **Concurrent write conflicts** — Race conditions, lost updates
  - **File bloat** — Becomes unmanageable at scale (50,000+ lines)
  - **Slow to read** — Agent must parse entire file
  - **Poor organization** — Chronological log, hard to find related knowledge

- **Why rejected:** This was the original approach. It broke with >100 tasks.

### Option 2: Database-Backed Memory

- **Pros:**
  - ACID transactions (no write conflicts)
  - SQL search (fast query by topic, date, project)
  - Scalable (handles millions of entries)
  - Centralized (no file sprawl)

- **Cons:**
  - **Complex** — Need schema, ORM, migrations
  - **Agent integration** — Agent expects files, need sync layer
  - **Debugging** — Can't just `cat MEMORY.md`, need DB client
  - **Overkill** — File system works fine for current scale (1000 tasks/year)

- **Why rejected:** Files are simpler and agent-native. Can migrate later if scale demands it.

### Option 3: Separate File Per Task

- **Pros:**
  - No concurrent writes (each task has unique file)
  - Easy to isolate task memory
  - Simple cleanup (delete old task files)

- **Cons:**
  - **File explosion** — 1000 tasks = 1000 files (hard to navigate)
  - **Hard to consolidate** — Must read 1000 files to summarize
  - **No cross-task patterns** — Agent doesn't see related task learnings

- **Why rejected:** Too granular. Date-based grouping is sweet spot (5-20 tasks/day).

### Option 4: Vector Database (Embeddings Search)

- **Pros:**
  - Semantic search ("what did I learn about migrations?")
  - Scales to large memory (millions of embeddings)
  - No manual consolidation (agent queries directly)

- **Cons:**
  - **Infrastructure** — Need vector DB (Pinecone, Weaviate, Chroma)
  - **Cost** — Embedding API calls for every memory write
  - **Complexity** — Embedding pipeline, index management
  - **Overkill** — Current memory is ~1000 entries/agent, grep works fine

- **Why rejected:** Premature optimization. Can add vector search later if keyword search is too slow.

### Option 5: Hybrid (MEMORY.md + Vector Index)

- **Pros:**
  - Best of both worlds (human-readable + semantic search)
  - Agent can query "similar past tasks"
  - Still have file-based backup

- **Cons:**
  - Most complex option
  - Requires vector DB + file sync
  - Need to keep vector index in sync with files

- **Why rejected:** Wait until semantic search is clearly needed. Current retrieval (grep, AI reads MEMORY.md) works.

## Search and Retrieval

### Current (Phase 1): Agent Reads MEMORY.md

**How:**
- MEMORY.md loaded into agent context at spawn (full text)
- Agent sees all curated knowledge (up to ~2000 lines)
- Agent greps memory/ for recent logs if needed

**Pros:** Simple, reliable, no external dependencies  
**Cons:** Limited to ~2000 lines (context window constraints)

### Future (Phase 2): Keyword Search

**How:**
- Agent uses `grep` or `rg` (ripgrep) to search memory/
- Search MEMORY.md + memory/ simultaneously
- Return relevant snippets

**Pros:** Fast, works offline  
**Cons:** Requires exact keyword match (won't find synonyms)

### Future (Phase 3): Semantic Search

**How:**
- Embed all memory entries (MEMORY.md + memory/)
- Store in vector DB (Chroma, Weaviate, or Pinecone)
- Agent queries: "What did I learn about database migrations?"
- Return top-k relevant entries by cosine similarity

**Pros:** Finds related knowledge even without exact keywords  
**Cons:** Requires vector DB, embedding API calls, index updates

**When to implement:** When memory exceeds ~10,000 entries per agent (1-2 years at current pace)

## Consolidation Strategy

### Manual Consolidation (Current)

**Trigger:** Human or orchestrator notices memory/ is large (>50 entries)

**Process:**
1. Human reads memory/ entries
2. Identifies patterns or reusable knowledge
3. Writes summary to MEMORY.md
4. Archives old memory/ files to `memory/archive/`

**Frequency:** Weekly or biweekly

### Semi-Automated Consolidation (Future)

**Trigger:** Scheduled (weekly) or on-demand

**Process:**
1. Orchestrator spawns "memory-consolidator" agent
2. Agent reads memory/*.md (last 7 days)
3. Agent groups by topic, summarizes patterns
4. Agent drafts updates to MEMORY.md
5. Human reviews and approves
6. Apply changes, archive memory/ files

**Frequency:** Weekly

### Fully Automated Consolidation (Long-Term)

**Trigger:** Continuous (after each task batch)

**Process:**
1. After each 10 tasks, auto-consolidate
2. LLM summarizes new entries
3. Auto-append to MEMORY.md (with human audit trail)
4. Archive memory/ files older than 30 days

**Safety:** Human can review git diffs if consolidation looks wrong

## Testing Strategy

**Write concurrency tests:**
- Spawn 5 agents concurrently, all append to same date file
- Verify no lost writes, no corrupted file

**Retrieval tests:**
- Write entry to memory/2026-02-20.md
- Spawn agent → Verify agent can find entry via grep

**Consolidation tests:**
- Populate memory/ with 100 entries
- Run consolidation → Verify MEMORY.md updated
- Verify old memory/ files archived

**Size limits:**
- MEMORY.md should stay under 2000 lines (fits in agent context)
- If exceeded, split into MEMORY-core.md + MEMORY-archive.md

## Migration

**From:** Single MEMORY.md (append-only)  
**To:** MEMORY.md (curated) + memory/ (logs)

**Steps:**
1. Create memory/ directory in each workspace
2. Copy existing MEMORY.md to MEMORY.md (no changes yet)
3. Update agent templates: "Write to memory/YYYY-MM-DD.md, read from MEMORY.md"
4. Run agents for 1 week (populate memory/)
5. Consolidate memory/ → MEMORY.md (extract high-value entries)
6. Archive original MEMORY.md to MEMORY-legacy.md

**Rollback:** Revert agent templates to write to MEMORY.md (data safe in both locations)

## References

- `worker-template/AGENTS.md` — Agent instructions on memory usage
- `~/.openclaw/workspace-*/MEMORY.md` — Curated knowledge files
- `~/.openclaw/workspace-*/memory/` — Daily log directories
- ADR-0009: Workspace Isolation Strategy
- ADR-0008: Agent Specialization Model

## Open Questions

1. **Should we set a hard limit on MEMORY.md size (e.g., 1500 lines)?**  
   → Yes. If exceeded, split into MEMORY.md (recent/core) + MEMORY-archive.md (historical).

2. **Should consolidation be automatic or manual?**  
   → Start manual (weekly), move to semi-automated (agent drafts, human approves), eventually fully automated with audit trail.

3. **Should we add vector search now or wait?**  
   → Wait until memory size justifies it (10,000+ entries per agent). Current keyword search is sufficient.

4. **How long should we keep memory/ files before archiving?**  
   → 90 days active, then archive to memory/archive/. Delete after 1 year (only if consolidated into MEMORY.md).

---

*Based on Michael Nygard's ADR format*
