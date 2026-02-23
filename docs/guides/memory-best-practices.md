# Memory System Best Practices

**Last Updated:** 2026-02-22  
**For:** AI agents and developers using the Lobs memory system

This guide explains how to write effective, searchable memories in the Lobs multi-agent system. Good memory practices enable continuity, prevent repeated work, and help agents build on past learnings.

---

## Table of Contents

1. [Memory Architecture Overview](#memory-architecture-overview)
2. [MEMORY.md vs memory/ Directory](#memorymd-vs-memory-directory)
3. [Writing Searchable Memories](#writing-searchable-memories)
4. [When to Create New Files vs Append](#when-to-create-new-files-vs-append)
5. [Vector Search Optimization](#vector-search-optimization)
6. [Examples: Good vs Bad](#examples-good-vs-bad)
7. [Daily Memory Workflow](#daily-memory-workflow)
8. [Memory Maintenance](#memory-maintenance)

---

## Memory Architecture Overview

Each OpenClaw agent workspace (`~/.openclaw/workspace-{agent}/`) has two memory mechanisms:

```
workspace-{agent}/
├── MEMORY.md              # Curated, high-signal summary (loads every session)
└── memory/                # Source material (searchable but not loaded by default)
    ├── YYYY-MM-DD.md      # Daily session logs
    ├── topic-slug.md      # Topic-specific learnings
    └── ...
```

**Key principles:**
- **MEMORY.md is precious real estate** — it loads into every agent session, so keep it concise and high-signal
- **memory/ files are permanent source material** — detailed context that's searchable but doesn't bloat session context
- **Both are synced to the database** — accessible via `/api/memories` and searchable via full-text search

---

## MEMORY.md vs memory/ Directory

### MEMORY.md — The Executive Summary

**Purpose:** High-signal, curated knowledge that the agent needs *every session*

**What belongs here:**
- Active project context and current focus
- Architecture decisions and key patterns
- Critical gotchas and lessons learned
- Important paths and file locations
- User preferences and working style
- Cross-cutting concerns (git workflow, coding standards, deployment)

**What doesn't belong:**
- Detailed tutorials or step-by-step guides (summarize to bullets)
- Historical session logs (move to memory/ files)
- Verbose examples (link to memory/ files instead)
- Duplicate information (already in "Shared Context" section)
- Stale or outdated information

**Target length:** 200-500 lines (keep it scannable)

**Structure:**
```markdown
# MEMORY.md

## Current Focus
- Active projects, current sprint goals

## Architecture Decisions
- Key choices that affect daily work

## Important Patterns
- Coding patterns, conventions, anti-patterns

## Critical Gotchas
- Common mistakes, edge cases, debugging tips

## Shared Context (from Lobs)
*Auto-managed section — do not edit*
```

### memory/ Directory — The Knowledge Base

**Purpose:** Detailed source material organized by topic and date

**File types:**

1. **Daily logs** (`YYYY-MM-DD.md`) — append-only session journal
2. **Topic files** (`topic-slug.md`) — evergreen knowledge on a specific subject
3. **Project files** (`project-x-notes.md`) — project-specific context

**What belongs here:**
- Detailed research findings
- Step-by-step guides and tutorials
- Session-specific learnings (what you did today)
- Comprehensive examples and code snippets
- Historical context and decision rationale
- Debugging sessions and problem-solving journeys

**When to search here:**
- Starting work on a familiar topic (check past learnings)
- Debugging a recurring issue (check past solutions)
- Understanding project history (check dated logs)

---

## Writing Searchable Memories

Memory files are searchable via full-text search. Make them easy to find:

### 1. Use Clear, Descriptive Titles

**Good:**
```markdown
# FastAPI SQLAlchemy Async Patterns
# Git Workflow: Branch Strategy and Gotchas
# Model Tier Benchmarking Results
```

**Bad:**
```markdown
# Notes
# Stuff I Learned
# February Work
```

### 2. Use Descriptive Headers

Headers are weighted more heavily in search. Use them strategically:

```markdown
# FastAPI SQLAlchemy Async Patterns

## Background Context Managers
Pattern: Use `async with` for session management

## Common Mistake: Forgetting to await
Always await query execution: `await db.execute(query)`

## Performance Tip: Use `scalars()` for Single Column
Faster than fetching full rows when you only need IDs
```

### 3. Include Searchable Keywords

Think about how you'd search for this later:

```markdown
# Git Workflow Gotchas

## Problem: Detached HEAD after rebase
**Keywords:** detached head, rebase, git checkout, HEAD state
**Solution:** `git checkout main` to return to branch
```

### 4. Front-Load Important Information

Put the most important context first (search snippets show beginning of match):

**Good:**
```markdown
## Memory Sync API Endpoint
**Endpoint:** `POST /api/memories/sync`  
**Purpose:** Syncs filesystem memories to database  
**Use case:** After manually editing MEMORY.md or memory/ files
```

**Bad:**
```markdown
## Memory Sync
There's this API endpoint that we sometimes use when we need to...
Oh right, it's at /api/memories/sync and it does memory syncing.
```

### 5. Use Examples

Concrete examples are easier to search and recognize:

```python
# ✅ Good: Includes actual code
async def get_project_tasks(project_id: int, db: AsyncSession):
    result = await db.execute(
        select(Task).where(Task.project_id == project_id)
    )
    return result.scalars().all()
```

---

## When to Create New Files vs Append

### Create a New Topic File When:

1. **You learned something genuinely new** — a pattern, tool, or technique you'll reuse
2. **It's conceptually distinct** — doesn't fit existing topic files
3. **It's evergreen** — not tied to a specific date or session
4. **You'll search for it by topic** — not by date

**Examples:**
- `memory/fastapi-async-patterns.md` — reusable patterns
- `memory/git-workflow-gotchas.md` — recurring issues
- `memory/model-routing-decisions.md` — architectural context

### Append to Existing Topic File When:

1. **You learned MORE about an existing topic** — expanding on known patterns
2. **It refines or corrects previous knowledge** — updates to existing content
3. **It's a natural extension** — fits under existing headers

**How to append:**
```bash
# Add new section to existing file
echo "\n## New Pattern: Bulk Updates\nDetails..." >> memory/fastapi-patterns.md
```

### Use Daily Logs When:

1. **It's session-specific** — "what I did today"
2. **It's exploratory** — trying things out, not final learnings
3. **It's time-sensitive** — relates to current work
4. **You're not sure if it's important yet** — capture now, curate later

**Daily log structure:**
```markdown
# 2026-02-22 Research Session

## Task
What you were assigned or chose to work on

## What I Did
Chronological log of activities

## Key Learnings
Important takeaways (candidates for topic files)

## Resources
Links, docs, code locations

## Next Steps
Where to pick up next time
```

### Decision Matrix

| Characteristic | New Topic File | Append to Topic | Daily Log |
|----------------|----------------|-----------------|-----------|
| Reusable pattern | ✅ | ✅ | ❌ |
| Session-specific | ❌ | ❌ | ✅ |
| Extends known topic | ❌ | ✅ | ❌ |
| Genuinely new topic | ✅ | ❌ | ❌ |
| Not sure yet | ❌ | ❌ | ✅ |
| Time-sensitive | ❌ | ❌ | ✅ |

---

## Vector Search Optimization

The Lobs memory system uses **full-text search** (not vector embeddings yet, though that's planned). Optimize for text matching:

### 1. Use Natural Language

Write how you'd naturally search:

**Good:**
```markdown
## How to Fix Detached HEAD State
When git rebase puts you in detached HEAD...
```

**Bad:**
```markdown
## Detached HEAD Resolution Algorithm
Implement HEAD state restoration via checkout...
```

### 2. Include Synonyms

Different ways of describing the same thing:

```markdown
## Database Session Management (SQLAlchemy Async Context)
**Also known as:** async session handling, database connection lifecycle

When working with async SQLAlchemy, use async context managers...
```

### 3. Avoid Over-Abstraction

Concrete > abstract:

**Good:** "FastAPI dependency injection pattern for database sessions"  
**Bad:** "Resource lifecycle management in request scope"

### 4. Use Code as Documentation

Code snippets are highly searchable:

```markdown
## Pattern: Authenticated Endpoint

```python
@router.get("/projects")
async def list_projects(
    _token: str = Depends(require_auth),  # ← searchable pattern
    db: AsyncSession = Depends(get_db)
):
    ...
```
```

### 5. Cross-Reference Related Topics

Help future searches discover connections:

```markdown
## FastAPI Background Tasks
Related: async patterns, task orchestrator, job scheduling

Use FastAPI's BackgroundTasks for fire-and-forget work...
See also: memory/task-orchestrator-patterns.md
```

---

## Examples: Good vs Bad

### Example 1: Documenting a Bug Fix

**❌ Bad:**
```markdown
Fixed that thing where the API was broken. Used async/await.
```

**✅ Good:**
```markdown
## Bug Fix: Memory Sync Race Condition (2026-02-18)

**Problem:** `/api/memories/sync` was failing intermittently with 
"database locked" errors.

**Root cause:** Multiple agents syncing simultaneously without lock.

**Solution:** Added file-based lock in `memory_sync.py`:
```python
async with aiofiles.open("/tmp/memory_sync.lock", "w") as f:
    await f.write(str(os.getpid()))
    # ... sync logic
```

**Prevention:** All memory sync operations now use `async with sync_lock()`.

**Related files:** 
- `app/services/memory_sync.py`
- `app/routers/memories.py`
```

### Example 2: Recording Research Findings

**❌ Bad:**
```markdown
Researched some stuff about model routing. There's like 5 different models
and they have different prices. Haiku is cheap. We should use it more.
```

**✅ Good:**
```markdown
## Model Tier Benchmarking Results

**Context:** Research on cost optimization via model tier selection (2026-02-22)

**Key findings:**
1. **Pricing:** Haiku ($1 in / $5 out) is 3x cheaper than Sonnet ($3/$15)
2. **Usage:** 57% of tasks use $0 subscription routes, Sonnet dominates paid
3. **Opportunity:** Inbox triage, simple docs = 66-80% cost savings with Haiku
4. **Threshold:** Full benchmark justified at ≥5K tasks/month

**Immediate action:** Force Haiku for inbox triage (zero-cost change)

**Implementation:**
```python
if task_type == "inbox_triage":
    task["model_tier"] = "medium"  # Forces Haiku via model router
```

**Resources:**
- Pricing: https://platform.claude.com/docs/models/overview
- Code: `/Users/lobs/lobs-server/app/orchestrator/model_router.py`
- Full analysis: `memory/model-tier-benchmarking.md`
```

### Example 3: Daily Session Log

**❌ Bad:**
```markdown
# 2026-02-22
Did some work. Fixed bugs. Tested stuff.
```

**✅ Good:**
```markdown
# 2026-02-22 Writer Agent Session

## Task Assignment
Document memory system best practices and performance metrics

## What I Did
1. Read memory system source code (`memory_sync.py`, `memory_maintenance.py`)
2. Read researcher's baseline metrics (`memory/model-tier-benchmarking.md`)
3. Created two comprehensive guides:
   - `docs/guides/memory-best-practices.md` (this file)
   - `docs/guides/performance.md`

## Key Learnings
- Memory system has two layers: MEMORY.md (curated) + memory/ (detailed)
- Daily maintenance runs automatic curation when MEMORY.md > 500 lines
- Full-text search currently, vector search planned
- Performance metrics: baseline established for model tier benchmarking

## Files Created
- `/Users/lobs/lobs-server/docs/guides/memory-best-practices.md`
- `/Users/lobs/lobs-server/docs/guides/performance.md`

## Next Session
Consider adding vector search documentation when implemented
```

---

## Daily Memory Workflow

Recommended workflow for agents at the end of each work session:

### 1. Capture Session Summary (Required)

Append to today's daily log:

```bash
# Create or append to today's log
DATE=$(date +%Y-%m-%d)
cat >> memory/$DATE.md << 'EOF'

## [Task Name] (HH:MM)
What I did, key learnings, blockers, next steps
EOF
```

### 2. Extract Important Learnings (If Any)

If you learned something reusable, add to topic file:

```bash
# Option A: Create new topic file
cat > memory/new-pattern-name.md << 'EOF'
# New Pattern Name
Context, usage, examples
EOF

# Option B: Append to existing topic
echo "\n## New Section\nContent..." >> memory/existing-topic.md
```

### 3. Update MEMORY.md (If Needed)

Only update MEMORY.md if:
- You learned something that affects ALL future sessions
- Current focus or priorities changed
- Critical gotcha that prevents bugs

**Don't update MEMORY.md for:**
- Session-specific details (those go in daily log)
- Detailed examples (those go in topic files)
- Information already in Shared Context

### 4. Let Automatic Curation Handle It

The system runs daily memory maintenance (`memory_maintenance.py`) which:
- Cleans up old session data
- Propagates shared context from main workspace
- Auto-curates MEMORY.md when it exceeds 500 lines
- Syncs filesystem changes to database

**You don't need to manually curate MEMORY.md** — the system will do it. Just focus on capturing good source material in memory/ files.

---

## Memory Maintenance

### Automatic Maintenance

Daily maintenance runs automatically and handles:

1. **Session cleanup** — Removes stale spawn/autoassign sessions > 1 hour old
2. **Context propagation** — Syncs "Shared Context" from main workspace to all agents
3. **Intelligent curation** — Spawns curator workers when MEMORY.md > 500 lines
4. **Database sync** — Updates `/api/memories` with filesystem changes

**Implementation:** `app/orchestrator/memory_maintenance.py`

### Manual Sync

If you manually edit memory files, trigger a sync:

```bash
# Via API
curl -X POST http://localhost:8000/api/memories/sync \
  -H "Authorization: Bearer YOUR_TOKEN"

# Via Python
from app.services.memory_sync import sync_agent_memories
await sync_agent_memories(db, agent="researcher")
```

### Curation Process

When MEMORY.md exceeds 500 lines, the system spawns a curator worker that:

1. **Reads** all memory files (MEMORY.md + memory/\*.md)
2. **Keeps** genuinely important information:
   - Architecture decisions and patterns
   - Critical gotchas and lessons
   - Active context for current work
   - User preferences
3. **Removes** bloat:
   - Stale/outdated information
   - Verbose tutorials (summarizes to bullets)
   - Duplicate information
   - Low-signal noise
4. **Preserves** the "Shared Context" section verbatim
5. **Writes** curated MEMORY.md (source files untouched)

**Note:** The curator is instructed to "keep all important info" — length doesn't matter, only signal quality.

### Best Practices for Curators

If you're writing a curator or reviewing curation results:

**Keep:**
- Anything that prevents bugs or saves time
- Architecture decisions that affect daily work
- Patterns and conventions you use regularly
- Critical file paths and commands
- User preferences and working style

**Remove:**
- Historical session logs (already in dated files)
- Step-by-step tutorials (summarize, link to memory/ file)
- Information duplicated in Shared Context
- Outdated information that's no longer true
- Verbose examples (keep one concise example, link to memory/ for more)

**When in doubt, keep it.** Better to have slightly verbose MEMORY.md than to lose important context.

---

## See Also

- **[Performance Guide](performance.md)** — Understanding baseline metrics and profiling
- **[Multi-Agent Onboarding](multi-agent-onboarding.md)** — How agents collaborate
- **[State Management Patterns](state-management-patterns.md)** — Working with shared state

**API Documentation:**
- `POST /api/memories` — Create memory
- `GET /api/memories` — List memories (filter by agent/type)
- `GET /api/memories/search` — Full-text search
- `POST /api/memories/sync` — Sync filesystem to database
- `POST /api/memories/quick-capture` — Quick note capture

**Source Code:**
- `app/services/memory_sync.py` — Filesystem ↔ database sync
- `app/orchestrator/memory_maintenance.py` — Daily maintenance
- `app/routers/memories.py` — API endpoints
- `app/models.py` — Memory data model

---

**Last Updated:** 2026-02-22  
**Maintained by:** Writer agent  
**Feedback:** Report issues or suggest improvements via inbox
