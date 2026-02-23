# 11. Handoff Protocol and Task Assignment

**Date:** 2026-02-22  
**Status:** Accepted  
**Deciders:** System architect, product owner

## Context

Complex work often requires multiple specialized agents working in sequence:
- Architect designs system → Programmer implements → Reviewer audits
- Researcher investigates options → Architect chooses approach → Programmer builds
- Programmer implements feature → Writer documents → Reviewer checks docs

We needed a mechanism for agents to:
- **Delegate work** to other specialized agents
- **Provide context** so next agent understands the background
- **Track dependencies** (Task B depends on Task A completing)
- **Maintain initiative coherence** (related tasks grouped together)
- **Enable async workflows** (agent doesn't wait, hands off and exits)

Initial approaches were ad-hoc:
- Manual task creation (human creates follow-up tasks)
- Agent comments in code (`# TODO: Add tests for this`)
- Chat messages ("Hey programmer, can you implement this?")

**Problems:**
- **Lost context** — Follow-up tasks missing critical background
- **No automation** — Human had to manually create tasks based on agent suggestions
- **Unclear ownership** — Who's responsible for the TODO?
- **Broken workflows** — Research findings didn't flow into implementation
- **Timing issues** — Agent tries to hand off but next agent isn't available

We needed a formal protocol that:
- Is machine-readable (orchestrator can parse and create tasks)
- Preserves context (next agent sees why work is needed)
- Works async (agent exits, orchestrator handles handoff later)
- Supports multi-step workflows (chain of handoffs)
- Allows human oversight (review before execution)

## Decision

We adopt a **JSON-based handoff protocol** where agents write structured handoff files that the orchestrator automatically converts into tasks.

### Handoff File Format

**Location:** `.handoffs/{uuid}.json` in project repository

**Schema:**
```json
{
  "to": "programmer",
  "initiative": "user-authentication",
  "title": "Implement JWT middleware",
  "context": "Based on design in docs/auth-design.md. Use RS256 signing. Must handle token expiry and refresh.",
  "acceptance": "Working middleware with unit + integration tests. Handles invalid tokens gracefully.",
  "files": [
    "docs/auth-design.md",
    "src/auth/",
    "tests/test_auth.py"
  ]
}
```

**Fields:**

| Field | Required | Description | Example |
|-------|----------|-------------|---------|
| `to` | Yes | Target agent type | `programmer`, `researcher`, `writer`, `architect`, `reviewer` |
| `initiative` | Yes | High-level theme grouping related tasks | `user-authentication`, `api-redesign` |
| `title` | Yes | Specific task title | `Implement JWT middleware` |
| `context` | No | Background, constraints, why this is needed | `Based on design in...` |
| `acceptance` | No | Definition of done | `Working middleware with tests` |
| `files` | No | Relevant files for context | `["docs/design.md", "src/auth/"]` |

### Workflow

#### 1. Agent Creates Handoff

**When:** Agent completes work that requires follow-up by different specialist.

**Example (Architect → Programmer):**
```bash
# In architect agent
mkdir -p .handoffs
cat > .handoffs/$(uuidgen).json << 'EOF'
{
  "to": "programmer",
  "initiative": "notification-system",
  "title": "Implement WebSocket notification handler",
  "context": "See design doc at docs/notifications-design.md. Use FastAPI WebSocket. Store notifications in DB.",
  "acceptance": "Handler passes integration tests. Supports subscribe/unsubscribe. Handles client disconnect gracefully.",
  "files": ["docs/notifications-design.md", "app/routers/notifications.py"]
}
EOF
```

#### 2. Orchestrator Detects Handoffs

**When:** After task completes (via completion webhook).

**Process:**
```python
# In orchestrator completion handler
handoff_dir = project_path / ".handoffs"
if handoff_dir.exists():
    for handoff_file in handoff_dir.glob("*.json"):
        handoff = json.loads(handoff_file.read_text())
        create_task_from_handoff(handoff)
        handoff_file.unlink()  # Remove after processing
```

#### 3. Orchestrator Creates Tasks

**Process:**
```python
def create_task_from_handoff(handoff):
    task = Task(
        title=handoff["title"],
        notes=handoff.get("context", ""),
        work_state="todo",
        agent=handoff["to"],
        initiative_id=get_or_create_initiative(handoff["initiative"]),
        created_by="system:handoff",
        files=handoff.get("files", []),
    )
    db.add(task)
    db.commit()
```

#### 4. Next Agent Picks Up Task

**When:** Orchestrator scanner finds eligible task.

**Process:**
- Router checks `task.agent` field (explicit assignment from handoff)
- Worker spawns assigned agent type
- Agent sees task context (title, notes, files)
- Agent executes work

### Multi-Step Workflows

**Example: Research → Design → Implementation**

**Step 1:** Researcher investigates
```json
{
  "to": "architect",
  "initiative": "database-migration",
  "title": "Design migration strategy based on research findings",
  "context": "Research in docs/db-migration-research.md found 3 options. Recommend Alembic. Design incremental migration plan.",
  "files": ["docs/db-migration-research.md"]
}
```

**Step 2:** Architect designs
```json
{
  "to": "programmer",
  "initiative": "database-migration",
  "title": "Implement Alembic migration setup",
  "context": "See docs/db-migration-design.md. Set up Alembic, create initial migration, add migration scripts.",
  "acceptance": "Alembic configured, initial migration passes, documented in README.",
  "files": ["docs/db-migration-design.md"]
}
```

**Step 3:** Programmer implements (no handoff, workflow complete)

**Result:** Initiative "database-migration" has 3 tasks, executed in sequence, full context preserved.

### Handoff Validation

**Orchestrator validates before creating task:**

```python
def validate_handoff(handoff):
    # Required fields
    assert "to" in handoff, "Missing 'to' field"
    assert "initiative" in handoff, "Missing 'initiative' field"
    assert "title" in handoff, "Missing 'title' field"
    
    # Valid agent type
    assert handoff["to"] in VALID_AGENTS, f"Invalid agent: {handoff['to']}"
    
    # Title not empty
    assert len(handoff["title"]) > 5, "Title too short"
    
    # Initiative is valid slug
    assert re.match(r'^[a-z0-9-]+$', handoff["initiative"]), "Invalid initiative format"
```

**On validation failure:**
- Log error: `[HANDOFF] Invalid handoff in {project}: {error}`
- Create inbox item for human review (don't auto-create broken task)

## Consequences

### Positive

- **Async workflows** — Agent doesn't block, hands off and exits (orchestrator handles later)
- **Full context** — Next agent sees background, constraints, acceptance criteria
- **Type-safe** — JSON schema prevents malformed handoffs
- **Auditable** — Git history shows who created handoff and when
- **Workflow orchestration** — Multi-agent workflows happen automatically
- **Initiative coherence** — Related tasks grouped by initiative
- **Human oversight** — Can review handoffs before they become tasks (via git review)
- **Flexible** — Supports any agent-to-agent delegation

### Negative

- **Handoff lag** — Handoff processed after task completes (not real-time during task)
- **File proliferation** — Many handoffs → many .json files (cleaned up after processing)
- **No dependency tracking** — Handoff doesn't say "wait for task X to complete"
- **Context size limits** — Large context must be in files, not in JSON (JSON is summary)
- **Manual initiative names** — Agents must choose consistent initiative slugs

### Neutral

- Handoffs are write-once (created during task, deleted after processing)
- Agents can create multiple handoffs (one task can spawn several follow-ups)

## Alternatives Considered

### Option 1: Database-Based Task Queue

**How:**
- Agent calls API endpoint: `POST /api/tasks` to create follow-up task
- Orchestrator polls database for new tasks

**Pros:**
- Real-time (no wait for task completion)
- Centralized (all tasks in DB, no file handling)
- Transactional (ACID guarantees)

**Cons:**
- **Requires network** — Agent must have API access (not all do)
- **Coupling** — Agent tied to lobs-server API (breaks if server down)
- **No git history** — Can't see handoffs in version control
- **Hard to review** — Human can't review before task created

**Why rejected:** File-based handoffs decouple agent from server, enable git review, simpler.

### Option 2: Chat-Based Delegation

**How:**
- Agent sends message: "@programmer implement JWT middleware based on docs/auth-design.md"
- Human or orchestrator parses message, creates task

**Pros:**
- Natural language (easy to read)
- Flexible (no schema enforcement)
- Already have chat system

**Cons:**
- **Ambiguous** — Natural language is hard to parse reliably
- **No structure** — Hard to extract fields (to, initiative, context)
- **Lost in chat** — Messages scroll away, easy to miss
- **Manual intervention** — Human must create task (no automation)

**Why rejected:** Too informal, hard to automate, prone to missed handoffs.

### Option 3: Code Annotations (TODOs)

**How:**
- Agent writes code comment: `# TODO(programmer): Add tests for this`
- Orchestrator scans code for TODOs, creates tasks

**Pros:**
- Inline (context is in code)
- Familiar pattern (developers use TODOs)

**Cons:**
- **Buried in code** — Hard to find all TODOs (must scan every file)
- **No metadata** — Can't specify initiative, acceptance criteria
- **Language-dependent** — Comment syntax varies (Python `#`, JavaScript `//`, etc.)
- **Stale** — TODOs accumulate and never get addressed

**Why rejected:** Not rich enough for structured handoffs, hard to discover and automate.

### Option 4: Workflow Engine (Temporal, Airflow)

**How:**
- Define multi-agent workflows as DAGs (Directed Acyclic Graphs)
- Workflow engine orchestrates task execution
- Agents are workers in the workflow

**Pros:**
- Industry standard (battle-tested)
- Built-in dependency tracking, retries, monitoring
- Visual workflow editor
- Scales to complex workflows

**Cons:**
- **Heavy infrastructure** — Requires Temporal/Airflow server
- **Upfront design** — Must define workflows before execution
- **Rigid** — Hard for agents to create dynamic handoffs
- **Overkill** — Current workflows are simple (2-3 step chains)

**Why rejected:** Too heavy for current needs. Can migrate later if workflows become complex.

### Option 5: Event Bus (RabbitMQ, Kafka)

**How:**
- Agent publishes event: `{"type": "handoff", "to": "programmer", ...}`
- Orchestrator subscribes to events, creates tasks

**Pros:**
- Real-time (no polling)
- Decoupled (agents don't know about orchestrator)
- Scalable (handles high throughput)

**Cons:**
- **Infrastructure** — Requires message broker
- **No git history** — Events are ephemeral (can't review before processing)
- **Ordering issues** — Event order not guaranteed
- **Overkill** — 10-50 handoffs/day doesn't need Kafka

**Why rejected:** Overengineered. File-based handoffs are simpler and sufficient.

## Advanced Patterns

### Pattern 1: Conditional Handoffs

**Use case:** Only hand off if certain condition met.

**Implementation:**
```json
{
  "to": "programmer",
  "initiative": "feature-x",
  "title": "Implement feature X",
  "context": "Only proceed if design approved. Check docs/design-status.md",
  "acceptance": "Feature implemented per design."
}
```

**Human:** Reviews design-status.md, deletes handoff if not approved.

### Pattern 2: Parallel Handoffs

**Use case:** Multiple agents work concurrently.

**Implementation:**
```bash
# Create multiple handoffs
cat > .handoffs/$(uuidgen).json << 'EOF'
{"to": "programmer", "initiative": "api-v2", "title": "Implement auth endpoints"}
EOF

cat > .handoffs/$(uuidgen).json << 'EOF'
{"to": "programmer", "initiative": "api-v2", "title": "Implement data endpoints"}
EOF

cat > .handoffs/$(uuidgen).json << 'EOF'
{"to": "writer", "initiative": "api-v2", "title": "Write API documentation"}
EOF
```

**Result:** Three tasks created, can execute in parallel (different projects or same project if domain locks allow).

### Pattern 3: Approval Gates

**Use case:** Handoff requires human approval before execution.

**Implementation:**
```json
{
  "to": "programmer",
  "initiative": "security-patch",
  "title": "Apply security patch to auth system",
  "context": "CRITICAL: Requires human approval before execution. See security-advisory.md",
  "acceptance": "Patch applied, tests pass, no regressions."
}
```

**Process:**
- Orchestrator creates task in `work_state="needs_approval"`
- Human reviews, approves/rejects
- If approved, task moves to `todo`, orchestrator picks it up

### Pattern 4: Chained Handoffs

**Use case:** Task A → Task B → Task C (sequential dependencies).

**Implementation:**
- Task A creates handoff to Task B
- Task B creates handoff to Task C
- Each task only knows about next step (no global workflow definition)

**Example:**
```
Researcher → Architect → Programmer → Reviewer
```

**Orchestrator ensures sequential execution** via domain locks (one worker per project) or explicit dependencies (future enhancement).

## Error Handling

### Scenario 1: Invalid Handoff JSON

**Detection:** Orchestrator fails to parse JSON

**Action:**
- Log error: `[HANDOFF] Invalid JSON in {file}: {error}`
- Create inbox item: "Handoff parsing failed"
- Leave file in `.handoffs/` for human review

### Scenario 2: Unknown Agent Type

**Detection:** `handoff["to"]` not in valid agent types

**Action:**
- Log warning: `[HANDOFF] Unknown agent type: {agent}`
- Create inbox item with handoff content
- Delete handoff file

### Scenario 3: Missing Initiative

**Detection:** `handoff["initiative"]` missing or empty

**Action:**
- Use default initiative: `"uncategorized"`
- Create task with warning in notes
- Log warning

### Scenario 4: Circular Handoffs

**Detection:** Task A → Task B → Task A (infinite loop)

**Prevention:**
- Track handoff chain in task metadata
- Detect cycles (same agent type + initiative appears twice in chain)
- Break cycle: Create inbox item instead of task

**Note:** Not implemented yet (rare case, manual detection currently).

## Testing Strategy

**Handoff creation tests:**
- Agent creates .handoffs/xyz.json → Verify file exists, valid JSON

**Orchestrator processing tests:**
- Place handoff in .handoffs/ → Run orchestrator → Verify task created

**Validation tests:**
- Invalid JSON → Verify error logged, inbox item created
- Missing required field → Verify validation fails
- Unknown agent type → Verify handled gracefully

**Multi-step workflow tests:**
- Create chain: Researcher → Architect → Programmer
- Verify tasks created in order, context preserved

**Parallel handoff tests:**
- Create 3 handoffs in one task → Verify 3 tasks created

## Migration

**From:** Manual task creation or code TODOs  
**To:** Structured handoff files

**Steps:**
1. Add handoff validation to orchestrator (week 1)
2. Update agent templates to document handoff protocol (week 1)
3. Migrate existing TODOs to handoffs (week 2)
4. Monitor handoff processing for 1 month (ongoing)
5. Add advanced features (approval gates, dependency tracking) as needed

**Rollback:** Remove handoff processing from orchestrator (agents can still create handoffs, they just won't be processed)

## Future Enhancements

1. **Dependency tracking** — Handoff specifies "wait for task X"
2. **Approval gates** — Handoff requires human approval before task creation
3. **Conditional handoffs** — Execute handoff only if condition met
4. **Handoff templates** — Pre-defined handoff patterns (research → design → implement)
5. **Visual workflow editor** — UI to see handoff chains
6. **Handoff analytics** — Track which handoffs succeed, which fail

## References

- `worker-template/AGENTS.md` — Agent instructions on creating handoffs
- `app/orchestrator/engine.py` — Handoff processing logic
- `.handoffs/` directories in project repositories
- ADR-0003: Project Manager Delegation (routing)
- ADR-0008: Agent Specialization Model (agent types)

## Open Questions

1. **Should handoffs support priority (urgent vs. normal)?**  
   → Not yet. Use initiative + task ordering for priority.

2. **Should we version the handoff schema (v1, v2)?**  
   → Not yet. Current schema is simple. Add versioning if we need breaking changes.

3. **Should handoffs support attachments (embed files)?**  
   → No. Use `files` field to reference paths. Keep handoffs lightweight.

4. **Should we support cross-project handoffs?**  
   → Not yet. Handoffs are project-scoped. If needed, use initiative to group cross-project work.

---

*Based on Michael Nygard's ADR format*
