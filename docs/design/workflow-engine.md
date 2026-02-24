# Workflow Engine Design Document

**Author:** Lobs  
**Status:** Approved (Rafe directive)  
**Date:** 2025-07-27  

## 1. Problem Statement

The current orchestrator has hardcoded execution logic: scan → pick agent → spawn → monitor → succeed/fail. This causes:

1. **Cascading failures** — failed tasks spawn diagnostic tasks, which fail, spawning more diagnostics (25+ remediation tasks in one incident)
2. **No retry loops** — if tests/linting fail, the whole task fails; no ability to feed errors back to the same session
3. **No composability** — every task type reinvents the same steps (code → test → lint → commit)
4. **Behavior changes require code deploys** — changing how tasks execute means editing Python and restarting the server
5. **No observability** — you can see task status but not *which step* failed or why
6. **No recurring workflow support** — morning briefs, email triage, PR reviews all need custom code

## 2. Solution: Workflow Engine

Replace the inner execution logic with a **data-driven workflow engine**. Workflows are DAGs of typed nodes stored as JSON in the database. The orchestrator becomes a workflow executor that walks these graphs.

### Core Principles

- **Additive, not a rewrite** — the engine, scanner, and outer loop stay. The workflow engine replaces only the "spawn worker and hope" inner path.
- **Workflows are data** — JSON definitions in the DB, versioned, editable via API.
- **Deterministic execution** — given the same inputs and node outcomes, the engine produces the same execution trace.
- **Per-step failure handling** — each node declares retry/fallback/escalate/abort policy.
- **Session lifecycle is a workflow concern** — spawn early, reuse across fix loops, cleanup at the end.

## 3. Data Model

### 3.1 WorkflowDefinition

```sql
CREATE TABLE workflow_definitions (
    id TEXT PRIMARY KEY,           -- UUID
    name TEXT NOT NULL UNIQUE,     -- e.g. "code-task", "morning-brief"
    description TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    nodes JSON NOT NULL,           -- Array of WorkflowNode definitions
    edges JSON NOT NULL,           -- Array of {from, to, condition?}
    trigger JSON,                  -- {type: "task_match"|"schedule"|"event", ...}
    metadata JSON,                 -- tags, author, etc.
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 3.2 WorkflowRun

```sql
CREATE TABLE workflow_runs (
    id TEXT PRIMARY KEY,           -- UUID
    workflow_id TEXT NOT NULL REFERENCES workflow_definitions(id),
    workflow_version INTEGER NOT NULL,
    task_id TEXT REFERENCES tasks(id),          -- NULL for non-task workflows
    trigger_type TEXT NOT NULL,    -- "task"|"schedule"|"event"|"manual"
    trigger_payload JSON,          -- context from trigger
    status TEXT NOT NULL DEFAULT 'pending',  -- pending/running/completed/failed/cancelled
    current_node TEXT,             -- ID of currently executing node
    node_states JSON NOT NULL DEFAULT '{}',  -- {node_id: {status, output, attempts, ...}}
    context JSON NOT NULL DEFAULT '{}',       -- shared workflow context (accumulated outputs)
    session_key TEXT,              -- OpenClaw session (if active)
    error TEXT,
    started_at DATETIME,
    finished_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 3.3 WorkflowNode (embedded in WorkflowDefinition.nodes JSON)

```json
{
    "id": "run_tests",
    "type": "tool_call",          // spawn_agent | tool_call | branch | gate | reflect | notify | cleanup
    "config": {
        "command": "cd {repo_path} && python -m pytest --tb=short 2>&1",
        "timeout_seconds": 300
    },
    "on_success": "lint",         // next node ID
    "on_failure": {
        "retry": 0,              // retries before moving to fallback
        "fallback": "fix_tests", // node ID to route to on failure
        "escalate_after": 3,     // total attempts before escalating
        "abort_on": ["timeout"]  // error types that skip retry
    },
    "inputs": ["spawn_agent.session_key"],  // data dependencies from prior nodes
    "timeout_seconds": 300
}
```

### 3.4 Node Types

| Type | Description | Config |
|------|-------------|--------|
| `spawn_agent` | Create an OpenClaw session with a prompt | `{agent_type, prompt_template, model_tier}` |
| `send_to_session` | Send a message to an existing session (fix loops) | `{session_ref, message_template}` |
| `tool_call` | Execute a command directly (no LLM) | `{command, timeout_seconds, capture_output}` |
| `branch` | Conditional routing based on prior output | `{conditions: [{match, goto}], default}` |
| `gate` | Pause for human approval | `{prompt, timeout_hours, auto_approve_after}` |
| `reflect` | Run an agent reflection with structured output | `{agent_type, reflection_prompt}` |
| `notify` | Send a notification (Discord, inbox, etc.) | `{channel, message_template}` |
| `cleanup` | Delete session, archive artifacts | `{delete_session, archive_artifacts}` |
| `sub_workflow` | Execute another workflow as a step | `{workflow_id}` |

## 4. Execution Model

### 4.1 Workflow Executor

The executor is a new module (`app/orchestrator/workflow_executor.py`) that:

1. Takes a `WorkflowRun` and advances it one step at a time.
2. Each tick: check current node status → resolve next node → execute → update state.
3. Is called by the engine on each poll cycle (like `check_workers` today).

```python
class WorkflowExecutor:
    async def advance(self, run: WorkflowRun) -> bool:
        """Advance a workflow run by one step. Returns True if work was done."""
        
        node = self.get_current_node(run)
        if not node:
            return False
            
        node_state = run.node_states.get(node.id, {})
        
        match node_state.get("status"):
            case None | "pending":
                await self.start_node(run, node)
                return True
            case "running":
                completed, output = await self.check_node(run, node)
                if completed:
                    await self.complete_node(run, node, output)
                    return True
                return False
            case "completed":
                next_node = self.resolve_next(run, node, node_state)
                if next_node:
                    run.current_node = next_node.id
                    return True
                else:
                    await self.finish_run(run, "completed")
                    return True
            case "failed":
                handled = await self.handle_failure(run, node, node_state)
                return handled
```

### 4.2 Node Execution

Each node type has a handler:

```python
class NodeHandlers:
    async def execute_spawn_agent(self, run, node) -> NodeResult:
        """Spawn an OpenClaw session."""
        prompt = self.render_template(node.config["prompt_template"], run.context)
        result = await self.worker_manager.spawn_session(
            agent_type=node.config["agent_type"],
            prompt=prompt,
            model=node.config.get("model_tier")
        )
        # Store session_key in run context for later nodes
        run.context[f"{node.id}.session_key"] = result["childSessionKey"]
        return NodeResult(status="running", output=result)
    
    async def execute_tool_call(self, run, node) -> NodeResult:
        """Execute a command directly (no LLM)."""
        command = self.render_template(node.config["command"], run.context)
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=node.timeout)
        success = result.returncode == 0
        return NodeResult(
            status="completed" if success else "failed",
            output={"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
        )
    
    async def execute_send_to_session(self, run, node) -> NodeResult:
        """Send a message to an existing session (for fix loops)."""
        session_key = run.context[node.config["session_ref"]]
        message = self.render_template(node.config["message_template"], run.context)
        await gateway.sessions_send(session_key, message)
        return NodeResult(status="running")  # Wait for response
    
    async def execute_branch(self, run, node) -> NodeResult:
        """Evaluate conditions and route."""
        for condition in node.config["conditions"]:
            if self.evaluate_condition(condition["match"], run.context):
                return NodeResult(status="completed", output={"goto": condition["goto"]})
        return NodeResult(status="completed", output={"goto": node.config["default"]})
```

### 4.3 Failure Handling

```python
async def handle_failure(self, run, node, node_state):
    policy = node.get("on_failure", {})
    attempts = node_state.get("attempts", 1)
    
    # Check abort conditions
    error_type = node_state.get("error_type")
    if error_type in policy.get("abort_on", []):
        await self.finish_run(run, "failed", error=node_state.get("error"))
        return True
    
    # Retry
    max_retries = policy.get("retry", 0)
    if attempts <= max_retries:
        node_state["status"] = "pending"
        node_state["attempts"] = attempts + 1
        return True
    
    # Fallback node
    fallback = policy.get("fallback")
    if fallback and attempts <= policy.get("escalate_after", 999):
        run.current_node = fallback
        return True
    
    # Escalate
    await self.escalate(run, node, node_state)
    return True
```

## 5. Trigger System (Event Bus)

### 5.1 Event Model

```sql
CREATE TABLE workflow_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,       -- "task.created"|"schedule.fired"|"github.pr_opened"|"email.received"
    payload JSON NOT NULL,
    source TEXT,                    -- "orchestrator"|"webhook"|"cron"|"manual"
    processed BOOLEAN DEFAULT FALSE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE workflow_subscriptions (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflow_definitions(id),
    event_pattern TEXT NOT NULL,    -- glob/regex for event_type matching
    filter_conditions JSON,        -- additional payload filters
    is_active BOOLEAN DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 5.2 Event Sources

| Source | Events | Integration |
|--------|--------|-------------|
| Task lifecycle | `task.created`, `task.completed`, `task.failed` | Emit from task router/worker completion |
| Scheduler (cron) | `schedule.fired` | Existing `EventScheduler` emits to event bus |
| GitHub webhooks | `github.pr_opened`, `github.ci_failed`, `github.review_requested` | Existing webhook router emits events |
| Internal | `workflow.completed`, `worker.failed` | Workflow executor emits on completion |
| Manual | `manual.trigger` | API endpoint to fire events |

### 5.3 Event Processing

Added to the engine loop (new step after scheduler check):

```python
# Process workflow events
events = await event_bus.get_unprocessed_events(limit=10)
for event in events:
    subscriptions = await event_bus.match_subscriptions(event)
    for sub in subscriptions:
        await workflow_executor.start_run(
            workflow_id=sub.workflow_id,
            trigger_type="event",
            trigger_payload=event.payload
        )
    event.processed = True
```

## 6. Recurring Workflows

### 6.1 Schedule Triggers

Workflow definitions can have a `trigger` field:

```json
{
    "type": "schedule",
    "cron": "0 7 * * *",           // 7 AM daily
    "timezone": "America/New_York"
}
```

The existing `EventScheduler` checks these and emits `schedule.fired` events. This replaces the need for custom cron logic per recurring task.

### 6.2 Example: Morning Brief

```json
{
    "name": "morning-brief",
    "trigger": {"type": "schedule", "cron": "0 7 * * 1-5", "timezone": "America/New_York"},
    "nodes": [
        {"id": "fetch_calendar", "type": "tool_call", "config": {"command": "curl -s localhost:8000/api/calendar/today -H 'Authorization: Bearer {token}'"}, "on_success": "fetch_tasks"},
        {"id": "fetch_tasks", "type": "tool_call", "config": {"command": "curl -s localhost:8000/api/tasks?status=active -H 'Authorization: Bearer {token}'"}, "on_success": "fetch_inbox"},
        {"id": "fetch_inbox", "type": "tool_call", "config": {"command": "curl -s localhost:8000/api/inbox -H 'Authorization: Bearer {token}'"}, "on_success": "compose"},
        {"id": "compose", "type": "spawn_agent", "config": {"agent_type": "writer", "prompt_template": "Compose a morning brief from this data:\n\nCalendar: {fetch_calendar.output}\nTasks: {fetch_tasks.output}\nInbox: {fetch_inbox.output}"}, "on_success": "deliver"},
        {"id": "deliver", "type": "notify", "config": {"channel": "discord", "message_template": "{compose.output}"}, "on_success": "cleanup"},
        {"id": "cleanup", "type": "cleanup", "config": {"delete_session": true}}
    ]
}
```

### 6.3 Example: Code Task (with fix loops)

```json
{
    "name": "code-task",
    "trigger": {"type": "task_match", "agent_types": ["programmer"]},
    "nodes": [
        {"id": "write_code", "type": "spawn_agent", "config": {"agent_type": "programmer", "prompt_template": "{task.title}\n\n{task.notes}"}, "on_success": "run_tests"},
        {"id": "run_tests", "type": "tool_call", "config": {"command": "cd {project.repo_path} && python -m pytest --tb=short 2>&1", "timeout_seconds": 300}, "on_success": "run_lint", "on_failure": {"fallback": "fix_tests", "escalate_after": 3}},
        {"id": "fix_tests", "type": "send_to_session", "config": {"session_ref": "write_code.session_key", "message_template": "Tests failed. Fix these errors:\n\n{run_tests.output.stderr}\n\n{run_tests.output.stdout}"}, "on_success": "run_tests"},
        {"id": "run_lint", "type": "tool_call", "config": {"command": "cd {project.repo_path} && ruff check . 2>&1", "timeout_seconds": 120}, "on_success": "commit", "on_failure": {"fallback": "fix_lint", "escalate_after": 3}},
        {"id": "fix_lint", "type": "send_to_session", "config": {"session_ref": "write_code.session_key", "message_template": "Lint errors found. Fix them:\n\n{run_lint.output.stdout}"}, "on_success": "run_lint"},
        {"id": "commit", "type": "tool_call", "config": {"command": "cd {project.repo_path} && git add -A && git commit -m 'agent(programmer): {task.title}'"}, "on_success": "notify_complete"},
        {"id": "notify_complete", "type": "notify", "config": {"channel": "internal", "message_template": "Task completed: {task.title}"}, "on_success": "cleanup"},
        {"id": "cleanup", "type": "cleanup", "config": {"delete_session": true}}
    ]
}
```

## 7. Integration Plan

### 7.1 Engine Integration

The workflow executor hooks into the existing engine loop with **minimal changes**:

```python
# In engine._run_once(), after step 7 (scan eligible tasks):

# NEW: Advance active workflow runs
active_runs = await workflow_executor.get_active_runs()
for run in active_runs:
    await workflow_executor.advance(run)

# MODIFIED: When spawning for a task, check if a workflow matches
for task in eligible_tasks:
    workflow = await workflow_executor.match_workflow(task)
    if workflow:
        await workflow_executor.start_run(workflow, task=task)
    else:
        # Existing spawn logic (backwards compatible)
        await worker_manager.spawn_worker(task=task, ...)
```

### 7.2 Migration Strategy

1. **Phase 1:** Schema + executor + default workflow that mirrors current behavior → zero behavior change
2. **Phase 2:** Convert code-task flow to use test/lint/fix loops → first real value
3. **Phase 3:** Event bus + recurring workflows (morning brief, etc.)
4. **Phase 4:** API endpoints for CRUD + agent-proposed workflow edits

### 7.3 What Changes in Existing Code

| File | Change | Risk |
|------|--------|------|
| `engine.py` | Add workflow advancement step to `_run_once()` | Low — additive |
| `worker.py` | Extract `_spawn_session` to be callable from workflow executor | Low — refactor |
| `scanner.py` | No changes | None |
| `models.py` | Add new models (workflow_definitions, workflow_runs, workflow_events, workflow_subscriptions) | Low — additive |
| `config.py` | No changes | None |
| New: `workflow_executor.py` | Core executor logic | New file |
| New: `workflow_nodes.py` | Node type handlers | New file |
| New: `workflow_events.py` | Event bus | New file |
| New: `routers/workflows.py` | API endpoints | New file |
| New: migration | Schema migration | Standard |

## 8. API Endpoints

```
GET    /api/workflows                    — List workflow definitions
POST   /api/workflows                    — Create workflow definition
GET    /api/workflows/:id                — Get workflow definition
PUT    /api/workflows/:id                — Update workflow definition
DELETE /api/workflows/:id                — Delete workflow definition

GET    /api/workflows/:id/runs           — List runs for a workflow
POST   /api/workflows/:id/runs           — Manually trigger a run
GET    /api/workflow-runs/:id            — Get run details + node states
GET    /api/workflow-runs/:id/trace      — Execution trace (timing, I/O per node)
POST   /api/workflow-runs/:id/cancel     — Cancel a running workflow

GET    /api/workflow-events              — List recent events
POST   /api/workflow-events              — Manually emit an event
GET    /api/workflow-subscriptions       — List subscriptions
POST   /api/workflow-subscriptions       — Create subscription
```

## 9. Observability

Every workflow run produces a trace:

```json
{
    "run_id": "abc123",
    "workflow": "code-task",
    "status": "completed",
    "duration_seconds": 342,
    "nodes": [
        {"id": "write_code", "status": "completed", "duration": 180, "attempts": 1},
        {"id": "run_tests", "status": "completed", "duration": 45, "attempts": 2, "note": "failed first, fixed by agent"},
        {"id": "fix_tests", "status": "completed", "duration": 60, "attempts": 1},
        {"id": "run_lint", "status": "completed", "duration": 12, "attempts": 1},
        {"id": "commit", "status": "completed", "duration": 5, "attempts": 1},
        {"id": "cleanup", "status": "completed", "duration": 2, "attempts": 1}
    ]
}
```

This replaces opaque "task succeeded/failed" with exactly where things went right or wrong.
