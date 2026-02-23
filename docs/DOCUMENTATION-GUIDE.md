# Documentation Guide — lobs-server

**Last Updated:** 2026-02-22  
**For:** Developers, AI agents, contributors

A guide to help you choose the right documentation format and location for different types of information in lobs-server.

---

## The Documentation System

lobs-server uses **four types of documentation**, each with a specific purpose:

| Type | Purpose | When to Use | Location |
|------|---------|-------------|----------|
| **ADRs** | Record architectural decisions | Major design choices that are hard to reverse | `docs/decisions/` |
| **ARCHITECTURE.md** | Explain system design | High-level overview of how the system works | Root directory |
| **Design Docs** | Plan and document features | Multi-component features, complex workflows | `docs/architecture/` or `docs/guides/` |
| **Inline Docs** | Explain code directly | Implementation details, API contracts, usage | Code files (docstrings, comments) |

**Golden rule:** Write documentation **during** the decision/implementation, not after. Future you will thank you.

---

## When to Use Each Type

### 1. Architecture Decision Records (ADRs)

**Purpose:** Capture **why** we made a significant architectural choice

**Use ADRs for:**
- ✅ Choosing core architecture patterns (e.g., "embedded orchestrator vs. external queue")
- ✅ Selecting frameworks, databases, or key libraries
- ✅ Security or performance tradeoffs with long-term implications
- ✅ API design patterns that affect multiple endpoints
- ✅ Database schema decisions that are costly to change

**Don't use ADRs for:**
- ❌ Routine feature additions (use design docs instead)
- ❌ Bug fixes (use git commit messages)
- ❌ Implementation details that can change easily (use inline docs)
- ❌ Coding style preferences (use linters/CONTRIBUTING.md)

**Key characteristics:**
- **Immutable** — Once accepted, ADRs are historical records (don't edit decisions after the fact)
- **Numbered** — Sequential numbering creates a decision timeline
- **Structured** — Standard template (context, decision, consequences, alternatives)

**Example ADR topics:**
- "Embedded Task Orchestrator" → Why we run orchestrator in-process vs. separate service
- "SQLite for Primary Database" → Why SQLite instead of Postgres/MySQL
- "Five-Tier Model Routing" → How we route tasks to AI models based on complexity

**Template:** `docs/decisions/0000-template.md`  
**Guide:** [ADR Authoring Guide](guides/adr-authoring.md)

---

### 2. ARCHITECTURE.md

**Purpose:** Explain **how the system works** at a high level

**Use ARCHITECTURE.md for:**
- ✅ System overview and component diagram
- ✅ Data flow between components
- ✅ Key subsystems and their responsibilities
- ✅ Technology stack summary
- ✅ Directory structure explanation
- ✅ Pointer to detailed documentation

**Don't use ARCHITECTURE.md for:**
- ❌ Rationale for decisions (use ADRs)
- ❌ Step-by-step implementation details (use design docs)
- ❌ API endpoint documentation (use AGENTS.md or inline docs)
- ❌ Historical context (use ADRs or git history)

**Key characteristics:**
- **Living document** — Update as the system evolves
- **High-level** — 5-10 minute read for new developers
- **Visual** — Diagrams and tables, not walls of text
- **Links to details** — Points to specific docs for deep dives

**What to include:**
```markdown
## System Overview
- What does this system do?
- Key components (with diagram)
- External dependencies

## Data Flow
- Request lifecycle
- Task execution flow
- WebSocket message routing

## Directory Structure
- What lives where
- Naming conventions

## Key Subsystems
- Orchestrator
- Chat system
- Memory system
- (brief description + link to detailed docs)

## Technology Stack
- FastAPI, SQLite, SQLAlchemy
- Why these choices (link to ADRs if relevant)

## Related Documentation
- Links to design docs, ADRs, guides
```

**Example:** `ARCHITECTURE.md` at project root (already exists)

---

### 3. Design Documents

**Purpose:** Plan and document **features, workflows, and complex implementations**

**Use design docs for:**
- ✅ Multi-component features (e.g., "Observability System", "State Management")
- ✅ Complex workflows (e.g., "Task Lifecycle", "Agent Onboarding")
- ✅ Implementation plans before building
- ✅ System behavior specifications
- ✅ Testing strategies

**Don't use design docs for:**
- ❌ Simple single-endpoint features (use inline docs or commit messages)
- ❌ Why we chose an approach (use ADRs for rationale)
- ❌ Code-level implementation (use inline docs)

**Key characteristics:**
- **Living documents** — Update as implementation progresses
- **Detailed** — Can be 5-20 pages for complex features
- **Audience: implementers** — Developers/agents building or maintaining the feature
- **Include diagrams** — Sequence diagrams, state machines, data models

**What to include:**
```markdown
## Overview
- What problem does this solve?
- Goals and non-goals

## Design
- Component interactions
- Data models
- API contracts
- State machines (if applicable)

## Implementation
- Phases/milestones
- Testing strategy
- Rollout plan

## Open Questions
- Unresolved decisions
- Tradeoffs to discuss

## References
- Related ADRs
- External documentation
```

**Locations:**
- **Architecture docs** (`docs/architecture/`) — System-wide designs (e.g., observability, multi-agent system)
- **Guides** (`docs/guides/`) — How-to guides and implementation patterns (e.g., testing guides, onboarding)
- **Runbooks** (`docs/runbooks/`) — Operational procedures (troubleshooting, deployment)

**Examples:**
- `docs/architecture/multi-agent-system.md` — How agents coordinate
- `docs/guides/contract-testing.md` — How to write contract tests
- `docs/guides/state-management-patterns.md` — Patterns for managing task state

---

### 4. Inline Documentation

**Purpose:** Explain **code directly** — what it does, how to use it, edge cases

**Use inline docs for:**
- ✅ Module/class/function docstrings (what, inputs, outputs, exceptions)
- ✅ Complex algorithms (why this approach, what the steps do)
- ✅ Non-obvious code (surprising behavior, workarounds)
- ✅ API contracts (validation rules, side effects)
- ✅ Configuration options

**Don't use inline docs for:**
- ❌ High-level architecture (use ARCHITECTURE.md)
- ❌ Rationale for design decisions (use ADRs)
- ❌ Long-form guides (use design docs)
- ❌ Obvious code (if the code is self-explanatory, no comment needed)

**Key characteristics:**
- **Close to the code** — Lives in the same file
- **Maintained with code** — Update docs when code changes
- **API-focused** — How to call it, what to expect

**Python docstring conventions:**
```python
def create_task(
    title: str,
    project_id: str,
    agent: str | None = None,
    dependencies: list[str] | None = None
) -> Task:
    """
    Create a new task in the orchestrator queue.
    
    Args:
        title: Human-readable task description
        project_id: Project ID (must exist in database)
        agent: Agent type to assign (programmer, researcher, writer).
               If None, project-manager will delegate.
        dependencies: List of task IDs that must complete first.
                     Validates that all dependencies exist.
    
    Returns:
        Task object with assigned ID and work_state='not_started'
    
    Raises:
        ValueError: If project_id doesn't exist
        ValueError: If agent type is invalid
        ValueError: If any dependency task doesn't exist
    
    Example:
        task = create_task(
            title="Implement auth middleware",
            project_id="lobs-server",
            agent="programmer"
        )
    """
    # Implementation...
```

**When to add comments:**
```python
# ✅ Good: Explains non-obvious behavior
# SQLite doesn't support DROP COLUMN, so we create a new table
# and migrate data instead
async def remove_column(table: str, column: str):
    ...

# ✅ Good: Explains workaround
# Pydantic v2 doesn't serialize SQLAlchemy models automatically,
# so we manually convert to dict
return Task.model_validate(db_task.__dict__)

# ❌ Bad: States the obvious
# Increment counter
counter += 1
```

**Where inline docs live:**
- Function/class docstrings
- Module-level docstrings (at top of file)
- Inline comments for tricky code
- README in subdirectories (e.g., `app/orchestrator/README.md`)

---

## Decision Tree

Use this flowchart to choose the right documentation type:

```
Is this a significant architectural decision?
├─ Yes → Write an ADR (docs/decisions/)
└─ No ↓

Is this explaining how the overall system works?
├─ Yes → Update ARCHITECTURE.md
└─ No ↓

Is this a multi-component feature or complex workflow?
├─ Yes → Write a design doc (docs/architecture/ or docs/guides/)
└─ No ↓

Is this explaining specific code or an API?
├─ Yes → Add inline documentation (docstrings, comments)
└─ No → Probably doesn't need documentation (or use git commit message)
```

---

## Concrete Examples

### Example 1: Adding a New API Endpoint

**Scenario:** Add `POST /api/tasks/{task_id}/subtasks` to create child tasks

**Documentation needed:**
- ❌ **ADR** — Not an architectural decision, just a new endpoint
- ❌ **ARCHITECTURE.md** — Too granular for high-level overview
- ❌ **Design doc** — Single endpoint, not complex enough
- ✅ **Inline docs** — Docstring on the route handler, explain parameters

```python
@router.post("/tasks/{task_id}/subtasks", response_model=Task)
async def create_subtask(
    task_id: str,
    subtask: TaskCreate,
    db: AsyncSession = Depends(get_db)
) -> Task:
    """
    Create a subtask under an existing parent task.
    
    Subtasks inherit the parent's project_id and are automatically
    added as dependencies to the parent (parent completes only when
    all subtasks are done).
    
    Args:
        task_id: Parent task ID
        subtask: Subtask creation parameters
        db: Database session
    
    Returns:
        Created subtask
    
    Raises:
        HTTPException 404: Parent task not found
    """
```

**Optional:** If this is part of a larger hierarchical task system, update design doc on task structure.

---

### Example 2: Choosing a Database

**Scenario:** Deciding between SQLite, Postgres, and MySQL for lobs-server

**Documentation needed:**
- ✅ **ADR** — Major architectural decision with long-term implications
- ✅ **ARCHITECTURE.md** — Update technology stack section to reflect choice
- ❌ **Design doc** — Not needed, the ADR captures the decision
- ❌ **Inline docs** — Too high-level for code comments

**Write:** `docs/decisions/0002-sqlite-for-primary-database.md`

**Include:**
- Context: Requirements (single-user, <100k records, fast local dev)
- Decision: SQLite with WAL mode
- Consequences: +Simple deployment, +No server, -No horizontal scaling
- Alternatives: Postgres (rejected: overkill), MySQL (rejected: similar tradeoffs to Postgres)

**Update:** `ARCHITECTURE.md` → Technology Stack section → "SQLite (see ADR-0002)"

---

### Example 3: Implementing Hierarchical Task System

**Scenario:** Add support for parent/child tasks, dependencies, and task trees

**Documentation needed:**
- ❌ **ADR** — Not changing architecture, just extending task model
- ✅ **ARCHITECTURE.md** — Update data model diagram to show task relationships
- ✅ **Design doc** — Complex feature with multiple components (DB schema, API, orchestrator changes, validation rules)
- ✅ **Inline docs** — Docstrings on new methods, comments on dependency resolution

**Write:** `docs/guides/hierarchical-tasks.md` or `docs/architecture/task-dependencies.md`

**Include:**
- Problem statement
- Data model (parent_id, dependencies fields)
- API changes (create subtask, update dependencies)
- Orchestrator behavior (dependency resolution order)
- Validation rules (no circular dependencies)
- Testing strategy

**Update:** `ARCHITECTURE.md` → Task System section → Link to new design doc

**Add:** Docstrings on `create_subtask()`, `resolve_dependencies()`, etc.

---

### Example 4: Refactoring a Function

**Scenario:** Rewrite task scanner for better performance (change algorithm, not architecture)

**Documentation needed:**
- ❌ **ADR** — Internal implementation detail, not a decision that affects other components
- ❌ **ARCHITECTURE.md** — No change to system design
- ❌ **Design doc** — Simple refactor, not a new feature
- ✅ **Inline docs** — Update function docstring if behavior changes, add comment explaining new algorithm
- ✅ **Git commit message** — Explain what changed and why

**Write:**
```python
async def scan_eligible_tasks(db: AsyncSession) -> list[Task]:
    """
    Find tasks ready for execution.
    
    Uses indexed query on (work_state, priority, created_at) for O(log n)
    performance instead of full table scan. See commit abc1234 for
    benchmark comparison.
    
    Returns tasks in priority order (high -> low -> medium), then by age.
    """
    # New implementation with indexed query...
```

**Git commit:**
```
refactor: optimize task scanner with indexed query

Replaced full table scan with indexed query on (work_state, priority,
created_at). Reduces scan time from 200ms to <10ms with 1000+ tasks.

Benchmark results in research/scanner-performance.md
```

---

## Maintenance Guidelines

### Keeping Docs Fresh

**ADRs:**
- ✅ Never edit the decision after it's accepted (historical record)
- ✅ Add notes at the end if circumstances change
- ✅ Write new ADR superseding the old one if decision is reversed

**ARCHITECTURE.md:**
- ✅ Update when system structure changes
- ✅ Add "Last Updated" date at top
- ✅ Link to ADRs when referencing architectural decisions

**Design docs:**
- ✅ Update as implementation progresses
- ✅ Mark sections as "Implemented", "In Progress", "Planned"
- ✅ Keep "Open Questions" section current (delete resolved, add new)

**Inline docs:**
- ✅ Update docstrings when function signatures change
- ✅ Remove comments when code becomes self-explanatory
- ✅ Add comments when you make code less obvious for performance

### Red Flags (Docs That Need Attention)

🚩 **ARCHITECTURE.md without "Last Updated" date** — Probably stale  
🚩 **Design doc with all "Open Questions" resolved** — Should be deleted or archived  
🚩 **ADR with Status: Proposed older than 30 days** — Accept or reject it  
🚩 **Function with 10+ parameters and no docstring** — Add inline docs  
🚩 **Complex feature with no design doc** — Write one before you forget why it works this way

---

## FAQ

**Q: I'm adding a small feature. Do I need documentation?**  
A: If it's a public API endpoint, add a docstring. If it's internal, a good git commit message is enough.

**Q: Should AI agents write ADRs?**  
A: Yes! Agents can draft ADRs when they make architectural decisions. Human review recommended before marking as Accepted.

**Q: Where do I document deployment procedures?**  
A: `docs/runbooks/` for operational procedures. Keep ARCHITECTURE.md focused on design, not operations.

**Q: What if I'm not sure whether to write an ADR or design doc?**  
A: Ask: "Is this about **why** we chose this approach (ADR) or **how** it works (design doc)?"

**Q: Do I update ARCHITECTURE.md every time I add a file?**  
A: No. Update when you add/change major components or workflows. Small files are fine in inline docs.

**Q: Can I delete old design docs?**  
A: Yes, if they're implemented and no longer useful. Move to `docs/archive/` or delete (git history preserves it).

**Q: How detailed should inline docs be?**  
A: Document the **contract** (inputs, outputs, side effects), not the **implementation** (unless it's surprising).

---

## Templates and Tools

**ADR Template:** `docs/decisions/0000-template.md`  
**Guide:** [ADR Authoring Guide](guides/adr-authoring.md)

**Design Doc Template:** (TBD — create if needed)

**Docstring Example:**
```python
"""
Brief one-line summary.

Longer description if needed. Explain what this does, when to use it,
any important behavior.

Args:
    param1: Description
    param2: Description

Returns:
    What this returns

Raises:
    ExceptionType: When and why

Example:
    >>> result = my_function("input")
    >>> print(result)
    "output"
"""
```

---

## Related Documentation

- [ADR Authoring Guide](guides/adr-authoring.md) — How to write effective ADRs
- [CONTRIBUTING.md](../CONTRIBUTING.md) — Development workflow and coding standards
- [ARCHITECTURE.md](../ARCHITECTURE.md) — System architecture overview
- [docs/decisions/](decisions/) — All architectural decision records

---

**Remember:** Good documentation is **clear**, **concise**, and **current**. Write for the developer (or AI agent) who will read this 6 months from now — they might be you.
