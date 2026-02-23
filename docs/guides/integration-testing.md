# Integration Testing Guide

**Purpose:** Test multi-component workflows with realistic (but controlled) agent interactions.

**When to use:** Changes to orchestrator flow, agent handoffs, or multi-step workflows.

---

## Overview

Integration tests validate:
1. **Full task lifecycle** — Queued → spawned → running → completed
2. **Agent handoffs** — Task creates subtask, delegates to another agent
3. **Failure scenarios** — Task fails → escalation → reflection → retry
4. **Concurrent execution** — Multiple agents running on different projects
5. **State synchronization** — Database state matches worker state

Unlike unit tests (mocked dependencies) or E2E tests (real agents), integration tests use a **test harness** that simulates agent responses.

---

## Test Harness Architecture

```
┌────────────────────────────────────────┐
│  Integration Test                      │
│  - Sets up scenario                    │
│  - Starts orchestrator                 │
│  - Validates outcomes                  │
└────────────────────┬───────────────────┘
                     │
                     ▼
┌────────────────────────────────────────┐
│  Orchestrator Engine                   │
│  - Scanner finds tasks                 │
│  - Router delegates                    │
│  - Worker spawns agents                │
└────────────────────┬───────────────────┘
                     │
                     ▼
┌────────────────────────────────────────┐
│  Fake Gateway (Test Harness)           │
│  - Receives spawn requests             │
│  - Returns canned agent responses      │
│  - Simulates delays/failures           │
└────────────────────────────────────────┘
```

### Key Principle: Controlled Nondeterminism

- **Not mocked:** Database, orchestrator engine, scanner, router
- **Faked:** OpenClaw Gateway API (returns scripted responses)
- **Real but isolated:** Database (in-memory SQLite or transaction rollback)

---

## Directory Layout

```
tests/
└── integration/
    ├── __init__.py
    ├── conftest.py              # Shared fixtures (harness, db, orchestrator)
    ├── harness.py               # Fake Gateway implementation
    ├── scenarios/               # Scripted agent response sequences
    │   ├── programmer_success.json
    │   ├── task_handoff.json
    │   ├── escalation_flow.json
    │   └── concurrent_tasks.json
    ├── test_task_lifecycle.py
    ├── test_handoffs.py
    ├── test_escalation.py
    ├── test_concurrent_execution.py
    └── test_scheduler_integration.py
```

---

## Fake Gateway Harness

The test harness simulates the OpenClaw Gateway `/tools/invoke` endpoint.

### Implementation

```python
"""Fake OpenClaw Gateway for integration tests."""
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from aiohttp import web


@dataclass
class ScriptedResponse:
    """A scripted agent response."""
    run_id: str
    delay_seconds: float
    status: str  # "completed" | "failed" | "blocked"
    output: dict
    error: Optional[str] = None


class FakeGateway:
    """
    Simulates OpenClaw Gateway for integration tests.
    
    Usage:
        harness = FakeGateway()
        harness.add_scenario("programmer", ScriptedResponse(...))
        
        app = harness.create_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", 8765)
        await site.start()
    """
    
    def __init__(self):
        self.scenarios: Dict[str, ScriptedResponse] = {}
        self.spawn_calls: list[dict] = []  # Track all spawn requests
        self.active_runs: Dict[str, ScriptedResponse] = {}
    
    def add_scenario(self, agent_type: str, response: ScriptedResponse):
        """Add a scripted response for an agent type."""
        self.scenarios[agent_type] = response
    
    def load_scenario_file(self, path: Path):
        """Load a scenario from JSON file."""
        with open(path) as f:
            data = json.load(f)
        
        for agent_type, resp_data in data.items():
            self.add_scenario(agent_type, ScriptedResponse(**resp_data))
    
    def create_app(self) -> web.Application:
        """Create aiohttp app with fake Gateway endpoints."""
        app = web.Application()
        app.router.add_post("/tools/invoke", self.handle_invoke)
        app.router.add_post("/sessions/list", self.handle_sessions_list)
        app.router.add_get("/health", self.handle_health)
        return app
    
    async def handle_invoke(self, request: web.Request) -> web.Response:
        """Handle /tools/invoke (sessions_spawn)."""
        data = await request.json()
        
        # Extract agent type from params
        params = json.loads(data.get("params", "{}"))
        agent_type = params.get("label", "").split("-")[0]  # e.g., "programmer-task-123"
        
        # Record the spawn call
        self.spawn_calls.append({
            "agent_type": agent_type,
            "params": params,
            "timestamp": asyncio.get_event_loop().time()
        })
        
        # Get scripted response
        scenario = self.scenarios.get(agent_type)
        if not scenario:
            return web.json_response(
                {"error": f"No scenario for agent type: {agent_type}"},
                status=500
            )
        
        # Schedule completion after delay
        run_id = scenario.run_id
        self.active_runs[run_id] = scenario
        
        if scenario.delay_seconds > 0:
            asyncio.create_task(self._complete_after_delay(run_id, scenario.delay_seconds))
        
        # Return spawn response
        return web.json_response({
            "result": {
                "runId": run_id,
                "childSessionKey": f"session-{run_id}",
                "status": "running"
            }
        })
    
    async def handle_sessions_list(self, request: web.Request) -> web.Response:
        """Handle /sessions/list (worker status polling)."""
        sessions = []
        
        for run_id, scenario in self.active_runs.items():
            sessions.append({
                "runId": run_id,
                "status": scenario.status,
                "output": scenario.output if scenario.status != "running" else None,
                "error": scenario.error
            })
        
        return web.json_response({"sessions": sessions})
    
    async def handle_health(self, request: web.Request) -> web.Response:
        """Handle /health check."""
        return web.json_response({"status": "ok"})
    
    async def _complete_after_delay(self, run_id: str, delay: float):
        """Mark a run as completed after a delay."""
        await asyncio.sleep(delay)
        if run_id in self.active_runs:
            scenario = self.active_runs[run_id]
            scenario.status = "completed"  # or "failed"
```

### Fixture Setup

```python
"""conftest.py for integration tests."""
import pytest
import asyncio
from aiohttp import web
from tests.integration.harness import FakeGateway


@pytest.fixture
async def fake_gateway():
    """Start a fake Gateway server for tests."""
    harness = FakeGateway()
    
    app = harness.create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", 8765)
    await site.start()
    
    yield harness
    
    await runner.cleanup()


@pytest.fixture
async def orchestrator_with_harness(db_session, fake_gateway):
    """Orchestrator configured to use fake Gateway."""
    from app.orchestrator.engine import OrchestratorEngine
    
    # Override GATEWAY_URL to point to fake
    import app.orchestrator.config as config
    original_url = config.GATEWAY_URL
    config.GATEWAY_URL = "http://localhost:8765"
    
    engine = OrchestratorEngine(lambda: db_session)
    engine._openclaw_available = True
    
    yield engine
    
    # Restore original
    config.GATEWAY_URL = original_url
```

---

## Writing Integration Tests

### Example 1: Task Lifecycle

```python
"""Test full task lifecycle from queued to completed."""
import pytest
from datetime import datetime, timezone
from app.models import Task, Project
from tests.integration.harness import ScriptedResponse


@pytest.mark.integration
@pytest.mark.asyncio
async def test_task_completes_successfully(db_session, orchestrator_with_harness, fake_gateway):
    """Test task goes from queued to completed."""
    
    # Setup: Create project and task
    project = Project(
        id=1,
        name="Test Project",
        status="active",
        created_at=datetime.now(timezone.utc)
    )
    db_session.add(project)
    
    task = Task(
        id=100,
        project_id=1,
        title="Write tests",
        status="queued",
        agent="programmer",
        created_at=datetime.now(timezone.utc)
    )
    db_session.add(task)
    await db_session.commit()
    
    # Configure harness: Programmer succeeds after 2 seconds
    fake_gateway.add_scenario("programmer", ScriptedResponse(
        run_id="run-100",
        delay_seconds=2.0,
        status="completed",
        output={
            "status": "completed",
            "summary": "Wrote 3 new tests in tests/test_feature.py",
            "files_changed": ["tests/test_feature.py"]
        }
    ))
    
    # Act: Run orchestrator cycle
    await orchestrator_with_harness._run_once()  # Spawns worker
    
    # Verify task is spawned
    await db_session.refresh(task)
    assert task.status == "spawned"
    
    # Simulate time passing
    await asyncio.sleep(2.5)
    
    # Run again to process completion
    await orchestrator_with_harness._run_once()
    
    # Assert: Task is completed
    await db_session.refresh(task)
    assert task.status == "completed"
    assert "Wrote 3 new tests" in task.result_summary
```

### Example 2: Agent Handoff

```python
"""Test agent handoff (programmer creates tester subtask)."""
@pytest.mark.integration
@pytest.mark.asyncio
async def test_programmer_creates_tester_subtask(db_session, orchestrator_with_harness, fake_gateway):
    """Test programmer completes and creates tester handoff."""
    
    # Setup
    project = Project(id=1, name="Test Project", status="active")
    db_session.add(project)
    
    parent_task = Task(
        id=200,
        project_id=1,
        title="Implement feature X",
        status="queued",
        agent="programmer"
    )
    db_session.add(parent_task)
    await db_session.commit()
    
    # Programmer completes and creates handoff
    fake_gateway.add_scenario("programmer", ScriptedResponse(
        run_id="run-200",
        delay_seconds=1.0,
        status="completed",
        output={
            "status": "completed",
            "summary": "Implemented feature X. Created handoff for testing.",
            "handoff": {
                "to": "tester",
                "title": "Test feature X",
                "context": "Implementation in src/feature_x.py"
            }
        }
    ))
    
    # Act
    await orchestrator_with_harness._run_once()  # Spawn programmer
    await asyncio.sleep(1.5)
    await orchestrator_with_harness._run_once()  # Process completion
    
    # Assert: Subtask created
    result = await db_session.execute(
        select(Task).where(Task.parent_id == 200)
    )
    subtask = result.scalar_one_or_none()
    
    assert subtask is not None
    assert subtask.agent == "tester"
    assert "Test feature X" in subtask.title
    assert subtask.status == "queued"
```

### Example 3: Escalation Flow

```python
"""Test task failure triggers escalation."""
@pytest.mark.integration
@pytest.mark.asyncio
async def test_task_failure_triggers_escalation(db_session, orchestrator_with_harness, fake_gateway):
    """Test failed task creates escalation."""
    
    # Setup
    project = Project(id=1, name="Test Project", status="active")
    db_session.add(project)
    
    task = Task(
        id=300,
        project_id=1,
        title="Fix difficult bug",
        status="queued",
        agent="programmer"
    )
    db_session.add(task)
    await db_session.commit()
    
    # Programmer fails
    fake_gateway.add_scenario("programmer", ScriptedResponse(
        run_id="run-300",
        delay_seconds=1.0,
        status="failed",
        output={
            "status": "failed",
            "summary": "Unable to reproduce bug. Need more info."
        },
        error="Task failed: insufficient context"
    ))
    
    # Act
    await orchestrator_with_harness._run_once()  # Spawn
    await asyncio.sleep(1.5)
    await orchestrator_with_harness._run_once()  # Process failure
    
    # Assert: Escalation created
    from app.models import EscalationEvent
    result = await db_session.execute(
        select(EscalationEvent).where(EscalationEvent.task_id == 300)
    )
    escalation = result.scalar_one_or_none()
    
    assert escalation is not None
    assert escalation.reason == "task_failed"
    assert task.status == "failed"
```

### Example 4: Concurrent Execution

```python
"""Test multiple agents running concurrently on different projects."""
@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_tasks_on_different_projects(db_session, orchestrator_with_harness, fake_gateway):
    """Test orchestrator handles concurrent tasks correctly."""
    
    # Setup: Two projects, two tasks
    project1 = Project(id=1, name="Project A", status="active")
    project2 = Project(id=2, name="Project B", status="active")
    db_session.add_all([project1, project2])
    
    task1 = Task(id=400, project_id=1, title="Task A", status="queued", agent="programmer")
    task2 = Task(id=401, project_id=2, title="Task B", status="queued", agent="researcher")
    db_session.add_all([task1, task2])
    await db_session.commit()
    
    # Both complete after 1 second
    fake_gateway.add_scenario("programmer", ScriptedResponse(
        run_id="run-400",
        delay_seconds=1.0,
        status="completed",
        output={"status": "completed", "summary": "Done A"}
    ))
    fake_gateway.add_scenario("researcher", ScriptedResponse(
        run_id="run-401",
        delay_seconds=1.0,
        status="completed",
        output={"status": "completed", "summary": "Done B"}
    ))
    
    # Act: Spawn both
    await orchestrator_with_harness._run_once()
    
    # Both should spawn (different projects = no domain lock conflict)
    await db_session.refresh(task1)
    await db_session.refresh(task2)
    assert task1.status == "spawned"
    assert task2.status == "spawned"
    
    # Wait and process
    await asyncio.sleep(1.5)
    await orchestrator_with_harness._run_once()
    
    # Both complete
    await db_session.refresh(task1)
    await db_session.refresh(task2)
    assert task1.status == "completed"
    assert task2.status == "completed"
```

---

## Best Practices

### ✅ Do

- **Test realistic workflows** — Use scenarios that actually happen in production
- **Isolate tests** — Each test creates its own data, doesn't depend on others
- **Use async properly** — Use `await asyncio.sleep()` for time simulation, not `time.sleep()`
- **Verify state transitions** — Check task status at each step
- **Check side effects** — Verify DB state, logs, metrics
- **Parameterize scenarios** — Use `@pytest.mark.parametrize` for variations

### ❌ Don't

- **Don't test implementation details** — Focus on observable outcomes
- **Don't make tests flaky** — Avoid tight timing assumptions (use delays + margin)
- **Don't skip cleanup** — Use fixtures for setup/teardown
- **Don't test too much** — Integration tests are slower; focus on critical paths

---

## Running Integration Tests

```bash
# Run all integration tests
pytest -m integration -v

# Run specific suite
pytest tests/integration/test_task_lifecycle.py -v

# Run with coverage
pytest -m integration --cov=app/orchestrator --cov-report=html

# Run in parallel (if tests are independent)
pytest -m integration -n 4
```

---

## Debugging Integration Tests

### Enable detailed logging

```python
@pytest.fixture(autouse=True)
def configure_logging():
    import logging
    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger("app.orchestrator").setLevel(logging.DEBUG)
```

### Inspect harness calls

```python
def test_something(fake_gateway, ...):
    # ... test code ...
    
    # Check what spawn calls were made
    print("Spawn calls:", fake_gateway.spawn_calls)
    
    # Check active runs
    print("Active runs:", fake_gateway.active_runs)
```

### Use pdb

```python
@pytest.mark.integration
async def test_something(...):
    await orchestrator._run_once()
    
    import pdb; pdb.set_trace()  # Pause here
    
    # Inspect state interactively
```

---

## Related

- [ADR 0006: Distributed Testing Architecture](../decisions/0006-distributed-testing-architecture.md)
- [Contract Testing Guide](contract-testing.md)
- [Chaos Testing Guide](chaos-testing.md)
