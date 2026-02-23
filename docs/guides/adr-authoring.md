# ADR Authoring Guide

**Last Updated:** 2026-02-22

Architecture Decision Records (ADRs) capture important architectural decisions, their context, and their consequences. This guide shows you how to write effective ADRs for lobs-server.

---

## What is an ADR?

An ADR documents **why** we made a significant architectural choice. It's a lightweight, versioned document that:

- Captures context that would otherwise be lost
- Explains tradeoffs to future developers (including AI agents)
- Records alternatives considered and why they were rejected
- Makes implicit decisions explicit

**When to write an ADR:**
- ✅ Changing core architecture (e.g., "embed orchestrator vs. external queue")
- ✅ Choosing between frameworks or libraries
- ✅ Database schema decisions with long-term impact
- ✅ API design patterns that affect multiple endpoints
- ✅ Security or performance tradeoffs

**When NOT to write an ADR:**
- ❌ Implementation details that can change easily
- ❌ Bug fixes (use git commit messages)
- ❌ Routine feature additions
- ❌ Coding style preferences (use linters/style guides)

---

## ADR Template

We use Michael Nygard's ADR format. The template lives at `docs/decisions/0000-template.md`.

### Required Sections

**1. Title** — Short, specific, actionable
```markdown
# 1. Embedded Task Orchestrator
```

**2. Metadata** — Date, status, deciders
```markdown
**Date:** 2026-02-22  
**Status:** Accepted  
**Deciders:** System architect, product owner
```

**Status values:**
- `Proposed` — Under discussion
- `Accepted` — Implemented or approved
- `Deprecated` — No longer recommended but still in use
- `Superseded` — Replaced by another ADR (link it)

**3. Context** — What problem are we solving?
```markdown
## Context

lobs-server needed a way to autonomously execute tasks without manual intervention.
The system had to:
- Find eligible tasks automatically
- Route them to appropriate agents
- Spawn worker processes via OpenClaw
- Monitor task progress and handle failures
```

**4. Decision** — What are we doing?
```markdown
## Decision

We embed the task orchestrator directly into the FastAPI application as an 
async background task that runs in the same process.

The orchestrator consists of:
- **Scanner** — Polls database for eligible tasks
- **Router** — Delegates routing decisions to project-manager agent
- **Engine** — Main polling loop coordinating scanner, router, worker
```

**5. Consequences** — What changes because of this?

```markdown
## Consequences

### Positive
- **Zero infrastructure complexity** — No separate job queue
- **Simple deployment** — Single process to run
- **Fast feedback loops** — Direct database access

### Negative
- **Single point of failure** — If FastAPI crashes, orchestration stops
- **Resource contention** — Orchestrator shares CPU/memory with API

### Neutral
- Orchestrator state lives in database, not in-memory queues
```

**6. Alternatives Considered** — What else did we evaluate?

```markdown
## Alternatives Considered

### Option 1: External Worker Pool (Celery/RQ)
- **Pros:** Industry standard, independent scaling, fault isolation
- **Cons:** Requires Redis/RabbitMQ, deployment complexity
- **Why rejected:** Infrastructure complexity outweighs benefits at current scale

### Option 2: Cron + CLI Scripts
- **Pros:** Simple, well-understood
- **Cons:** Fixed polling intervals, no live monitoring
- **Why rejected:** Too rigid for dynamic task routing
```

**7. References** — Links to code, docs, issues

```markdown
## References
- `app/orchestrator/engine.py` — Main orchestrator loop
- ARCHITECTURE.md — System overview
- [Task #38] — Original orchestrator implementation
```

---

## Writing Guidelines

### Be Specific

❌ **Vague:** "We need better performance"
✅ **Specific:** "Task scanning takes 2-3 seconds with 1000+ tasks in the database, blocking the orchestrator loop"

❌ **Vague:** "Use FastAPI"
✅ **Specific:** "Use FastAPI instead of Flask for async/await support, automatic OpenAPI docs, and Pydantic validation"

### Show Tradeoffs

Every decision has costs. Make them visible:

```markdown
### Positive
- **Fast local development** — No Docker Compose, just `./bin/run`

### Negative
- **Production complexity** — Need systemd/supervisor for process management
- **No horizontal scaling** — Can't run multiple orchestrator instances safely
```

### Explain the "Why Rejected"

Future readers will wonder why you didn't choose the obvious alternative. Tell them:

❌ **Weak:** "Redis is overkill"
✅ **Strong:** "Redis adds infrastructure complexity (deployment, monitoring, backups) for ~10 tasks/day. We can migrate later if volume grows 10x."

### Write for Future Readers

Assume the reader:
- Knows the problem domain but not your codebase
- May be an AI agent reading this 6 months from now
- Wants to know **why**, not just **what**

### Include Reversibility

Can this decision be changed later? Say so:

```markdown
## Notes

This decision is **reversible**. If task volume grows significantly, we can:
1. Extract orchestrator to separate process (same codebase, different entry point)
2. Add message queue for task distribution
3. Migrate to Celery/Temporal
```

---

## Numbering and Naming

**File naming convention:**
```
docs/decisions/NNNN-short-title.md
```

Examples:
- `0001-embedded-orchestrator.md`
- `0002-sqlite-over-postgres.md`
- `0003-bearer-token-auth.md`

**Finding the next number:**
```bash
ls docs/decisions/ | grep -E '^[0-9]+' | sort -n | tail -1
```

Then increment by 1.

---

## Workflow

### 1. Draft Phase (Status: Proposed)

Create the ADR with `Status: Proposed` when you're exploring options:

```bash
cd docs/decisions
cp 0000-template.md 0003-api-versioning-strategy.md
# Edit the file, keep Status: Proposed
```

**Share for feedback:**
- Send to inbox as a proposal
- Discuss in chat or PR comments
- Iterate on alternatives

### 2. Acceptance Phase (Status: Accepted)

Once implemented or approved:
- Update `Status: Accepted`
- Add implementation references (file paths, PR numbers)
- Commit with message: `docs: ADR-0003 API versioning strategy`

### 3. Maintenance Phase

If circumstances change:

**Deprecating an ADR:**
```markdown
**Status:** Deprecated  
**Deprecated Date:** 2026-03-15  
**Reason:** New requirements for multi-region support made this approach unsuitable
```

**Superseding an ADR:**
```markdown
**Status:** Superseded  
**Superseded By:** ADR-0012 (GraphQL API)
**Superseded Date:** 2026-06-01
```

---

## Example: Good vs. Bad

### ❌ Bad ADR

```markdown
# Use Redis

We should use Redis for caching.

## Decision
Add Redis.

## Why
It's fast.
```

**Problems:**
- No context (what problem does this solve?)
- No alternatives considered
- No consequences
- No implementation details

### ✅ Good ADR

```markdown
# 4. Redis for Agent Session Caching

**Date:** 2026-02-22  
**Status:** Proposed  
**Deciders:** Backend team, project-manager agent

## Context

OpenClaw spawns agent workers that need session context (memory, project state, 
recent messages). Currently we:
- Fetch from SQLite on every agent spawn (~200-500ms)
- Re-query shared memory docs for each task
- Have no session reuse across multiple tasks

With 50-100 agent spawns per day, this causes:
- Slow agent startup (noticeable to users in chat)
- Redundant database queries
- Missed opportunities for session reuse (same agent, same project)

## Decision

Introduce Redis as a cache layer between the orchestrator and SQLite:

1. **Cache agent session packets** — Memory context, project state, recent chat
2. **TTL: 1 hour** — Balance freshness vs. cache hit rate
3. **Invalidation on mutations** — Clear cache when project/memory changes
4. **Optional dependency** — Falls back to direct SQLite if Redis unavailable

Implementation:
- Add `redis` to requirements.txt
- Create `app/services/session_cache.py` with get/set/invalidate methods
- Update `app/orchestrator/worker.py` to check cache before DB
- Add Redis to docker-compose (optional for local dev)

## Consequences

### Positive
- **Faster agent spawns** — Sub-50ms cache hits vs. 200-500ms DB queries
- **Reduced DB load** — Especially for high-frequency tasks
- **Session reuse** — Same agent can pick up where it left off

### Negative
- **Infrastructure dependency** — Need to run/monitor Redis in production
- **Cache invalidation complexity** — Risk of stale data if invalidation fails
- **Memory overhead** — Redis needs RAM for cached sessions (~50MB estimated)
- **Development friction** — Developers need to run Redis locally (or use fallback)

### Neutral
- Cache misses still hit SQLite (no behavior change)
- Cache is optional (works without Redis, just slower)

## Alternatives Considered

### Option 1: In-Memory Python Dict

- **Pros:** No external dependency, zero setup
- **Cons:** Lost on process restart, no TTL, no sharing across workers
- **Why rejected:** lobs-server restarts frequently (deployments, crashes). Cache would be empty too often.

### Option 2: SQLite with Aggressive Caching

- **Pros:** No new dependency, already using SQLite
- **Cons:** SQLite isn't designed for high-write caches, file locking issues, limited eviction strategies
- **Why rejected:** Mismatched tool for the job. Redis is purpose-built for this.

### Option 3: Embedded Cache (cachetools/DiskCache)

- **Pros:** Pure Python, no network overhead
- **Cons:** Same single-process limitation as Python dict, disk-based ones are slower than Redis
- **Why rejected:** Doesn't solve the process restart problem.

## References

- `app/orchestrator/worker.py` — Worker session initialization
- [Task #127] — Agent spawn performance investigation
- Related: ADR-0001 (Embedded Orchestrator) — Considered but rejected embedded cache due to process boundaries

## Notes

This is **reversible** — Redis is isolated to the cache layer. If it proves problematic:
- Remove Redis, fall back to direct SQLite (already implemented)
- Try Option 3 (embedded cache) with DiskCache for persistence

We can start with Redis optional (auto-detect) and make it required later if it proves valuable.

---

*Based on Michael Nygard's ADR format*
```

**Why this is good:**
- Clear problem statement with metrics
- Detailed decision with implementation plan
- Honest tradeoffs (positive AND negative)
- Multiple alternatives with reasoning
- Reversibility acknowledged
- References to code and related decisions

---

## Common Mistakes

### 1. Writing After the Fact

❌ Writing an ADR 6 months after the decision was made
- Memory is fuzzy, alternatives forgotten
- Rationalization instead of honest evaluation

✅ Write the ADR **during** the decision process
- Alternatives are fresh in your mind
- Tradeoffs are actively being weighed
- Future you will thank you

### 2. Hiding the Negatives

❌ Only listing positive consequences
- Looks like marketing, not engineering

✅ Be honest about costs and limitations
- Builds trust with readers
- Shows you considered tradeoffs
- Helps future teams make informed decisions

### 3. Too Much Detail

❌ Pasting entire code snippets or config files

✅ Link to code, summarize key points
- ADRs are for decisions, not implementation manuals
- Code changes, decisions persist

### 4. Jargon Without Context

❌ "Use CAP-theorem-compliant distributed consensus"

✅ "Use Raft consensus (etcd) to ensure all nodes agree on task assignments, even during network partitions"

---

## Templates by Decision Type

### Technology Choice (Library/Framework)

```markdown
# [Number]. [Technology] for [Purpose]

## Context
- What are we trying to do?
- What are the requirements?
- What constraints exist?

## Decision
- Which technology did we choose?
- How will we use it?

## Consequences
### Positive
- Performance, developer experience, ecosystem
### Negative
- Learning curve, vendor lock-in, limitations

## Alternatives Considered
- List 2-3 serious alternatives
- Why each was rejected
```

### Architecture Pattern

```markdown
# [Number]. [Pattern Name]

## Context
- What architectural challenge are we solving?
- What are the forces (scalability, complexity, cost)?

## Decision
- Which pattern are we adopting?
- How does it work in our system?
- Diagram if helpful

## Consequences
- How does this affect modularity, testability, deployment?

## Alternatives
- Other patterns considered
- Why they didn't fit
```

### Process/Workflow Change

```markdown
# [Number]. [New Workflow]

## Context
- What's broken with the current process?
- Who is affected?

## Decision
- New process steps
- Tools involved
- Roles and responsibilities

## Consequences
- What becomes easier?
- What becomes harder?
- Who needs training?

## Alternatives
- Other workflows considered
- Why they were less suitable
```

---

## FAQ

**Q: Can AI agents write ADRs?**  
A: Yes! Agents can draft ADRs when they make significant architectural decisions. Human review recommended for Status: Accepted.

**Q: How detailed should "Alternatives Considered" be?**  
A: 2-3 sentences per alternative is usually enough. Focus on pros/cons and why rejected.

**Q: Should I update an ADR after implementation?**  
A: Yes! Add references to PRs, actual metrics, lessons learned. Keep the original decision intact, add notes at the end.

**Q: What if we made the wrong decision?**  
A: Write a new ADR superseding the old one. Explain what changed and why. Don't delete the old ADR — it's part of the history.

**Q: Do I need an ADR for every decision?**  
A: No. Only for decisions that are **hard to reverse** or have **significant architectural impact**. Use judgment.

---

## Further Reading

- [Michael Nygard's original blog post](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
- [ADR GitHub organization](https://adr.github.io/) — Tools and examples
- [When to write an ADR](https://github.com/joelparkerhenderson/architecture-decision-record#when-should-we-write-an-architecture-decision-record)

---

**Start writing:** Copy `docs/decisions/0000-template.md` and fill in the sections. When in doubt, err on the side of **too much context** rather than too little.
