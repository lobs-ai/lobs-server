# How to Add a Test

**Purpose:** Step-by-step guide for writing tests using the 4-tier testing architecture

**Related:** [ADR 0006: Distributed Testing Architecture](../decisions/0006-distributed-testing-architecture.md)

---

## Quick Reference

```bash
# Run all tests
pytest -v

# Run specific tier
pytest -m unit -v
pytest -m integration -v
pytest -m chaos -v

# Run specific file
pytest tests/test_orchestrator_engine.py -v

# Run with coverage
pytest --cov=app --cov-report=html
```

---

## Test Tier Decision Tree

```
What are you testing?
│
├─ Single function/class logic? 
│  └─ → Unit Test (Tier 1)
│
├─ Agent input/output format?
│  └─ → Contract Test (Tier 2)
│
├─ Multi-component workflow?
│  └─ → Integration Test (Tier 3)
│
└─ Failure scenario or resilience?
   └─ → Chaos Test (Tier 4)
```

---

## Tier 1: Unit Tests

### When to Use
- Testing individual functions
- Testing class methods in isolation
- Testing business logic without external dependencies

### Structure

```python
# tests/test_my_feature.py
import pytest
from app.my_module import my_function

def test_my_function_happy_path():
    """Test normal operation."""
    result = my_function("input")
    assert result == "expected"

def test_my_function_edge_case():
    """Test boundary condition."""
    result = my_function("")
    assert result is None

def test_my_function_error_handling():
    """Test error scenarios."""
    with pytest.raises(ValueError):
        my_function(None)
```

### Using Fixtures

```python
# tests/conftest.py (shared fixtures)
import pytest
from app.database import AsyncSessionLocal
from app.models import Base

@pytest.fixture
async def db_session():
    """Provide clean database session for each test."""
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()

# tests/test_tasks.py
async def test_create_task(db_session):
    """Test task creation."""
    from app.routers.tasks import create_task
    
    task_data = {"title": "Test", "work_state": "queued"}
    task = await create_task(db_session, task_data)
    
    assert task.title == "Test"
    assert task.work_state == "queued"
```

### Mocking External Dependencies

```python
from unittest.mock import AsyncMock, patch

async def test_worker_spawn():
    """Test worker spawning without calling real OpenClaw."""
    with patch("app.orchestrator.worker.gateway_spawn") as mock_spawn:
        mock_spawn.return_value = {"session_id": "test-123"}
        
        worker = WorkerManager(db)
        result = await worker.spawn_worker(task, "programmer")
        
        assert result["session_id"] == "test-123"
        mock_spawn.assert_called_once()
```

### Example: Testing Router Logic

```python
# tests/test_router.py
from app.orchestrator.router import Router

async def test_router_selects_explicit_agent(db_session):
    """Router should use task.agent_type if specified."""
    router = Router(db_session)
    
    task = {"id": "task-123", "agent_type": "programmer"}
    agent_type = await router.route_task(task)
    
    assert agent_type == "programmer"

async def test_router_fallback_to_project_manager(db_session):
    """Router delegates to project-manager if no explicit agent."""
    router = Router(db_session)
    
    task = {"id": "task-456", "agent_type": None}
    agent_type = await router.route_task(task)
    
    assert agent_type == "project-manager"
```

---

## Tier 2: Contract Tests

### When to Use
- Validating agent prompt structure
- Testing agent result parsing
- Ensuring backward compatibility with agent responses

### Structure

```python
# tests/contracts/test_programmer_contract.py
import pytest
import json

def test_programmer_prompt_format():
    """Programmer prompt should include required sections."""
    from app.orchestrator.prompter import build_prompt
    
    task = {
        "id": "task-789",
        "title": "Add feature X",
        "description": "Detailed description",
        "acceptance_criteria": "Works correctly"
    }
    
    prompt = build_prompt(task, "programmer")
    
    # Verify required sections
    assert "## Task" in prompt
    assert "## Acceptance Criteria" in prompt
    assert "## When You're Done" in prompt

def test_programmer_result_parsing():
    """Parser should handle real programmer output."""
    # Load historical agent output
    with open("tests/contracts/fixtures/programmer_success.json") as f:
        agent_output = json.load(f)
    
    from app.orchestrator.worker import parse_agent_result
    
    result = parse_agent_result(agent_output)
    
    assert result["status"] == "completed"
    assert "summary" in result
    assert result["files_changed"] > 0

def test_programmer_error_parsing():
    """Parser should handle error responses."""
    with open("tests/contracts/fixtures/programmer_error.json") as f:
        agent_output = json.load(f)
    
    from app.orchestrator.worker import parse_agent_result
    
    result = parse_agent_result(agent_output)
    
    assert result["status"] == "failed"
    assert result["error_message"] is not None
```

### Creating Contract Fixtures

**1. Capture real agent output:**
```bash
# Save agent transcript to fixture
cp ~/lobs-control/state/transcripts/task-abc123.json \
   tests/contracts/fixtures/programmer_success.json
```

**2. Create fixture file:**
```json
{
  "session_id": "test-session",
  "status": "completed",
  "transcript": [
    {"role": "user", "content": "Task prompt..."},
    {"role": "assistant", "content": "I'll implement feature X..."},
    {"role": "tool_call", "tool": "write", "result": "..."}
  ],
  "result": {
    "summary": "Implemented feature X",
    "files_changed": 3
  }
}
```

**3. Test against fixture:**
```python
@pytest.mark.parametrize("fixture", [
    "programmer_success.json",
    "programmer_partial.json",
    "programmer_blocked.json",
])
def test_programmer_contract(fixture):
    """Verify parser handles all programmer response types."""
    with open(f"tests/contracts/fixtures/{fixture}") as f:
        output = json.load(f)
    
    result = parse_agent_result(output)
    assert result["status"] in ["completed", "blocked", "failed"]
```

---

## Tier 3: Integration Tests

### When to Use
- Testing full task lifecycle (queued → completed)
- Testing agent handoffs and delegation
- Testing concurrent workflows
- Testing state synchronization between components

### Structure

```python
# tests/integration/test_task_lifecycle.py
import pytest
from tests.integration.fake_gateway import FakeGateway

@pytest.fixture
async def fake_gateway():
    """Fake OpenClaw Gateway for controlled responses."""
    gateway = FakeGateway()
    gateway.start()
    yield gateway
    gateway.stop()

async def test_task_completes_successfully(db_session, fake_gateway):
    """Happy path: task queued → spawned → completed."""
    from app.orchestrator.engine import OrchestratorEngine
    
    # Setup
    fake_gateway.add_response("programmer", {
        "status": "completed",
        "result": {"summary": "Feature added"}
    })
    
    # Create task
    task = await create_task(db_session, {
        "title": "Add feature",
        "agent_type": "programmer",
        "work_state": "queued"
    })
    
    # Run orchestrator cycle
    engine = OrchestratorEngine(db_session, fake_gateway)
    await engine.run_cycle()
    
    # Verify task completed
    updated_task = await get_task(db_session, task.id)
    assert updated_task.work_state == "completed"
    assert updated_task.result_summary == "Feature added"
```

### Testing Handoffs

```python
async def test_task_creates_handoff(db_session, fake_gateway):
    """Agent creates handoff to delegate work."""
    # Agent response includes handoff
    fake_gateway.add_response("programmer", {
        "status": "completed",
        "result": {"summary": "Needs testing"},
        "handoff": {
            "to": "tester",
            "title": "Test feature X",
            "context": "Feature implemented, ready for testing"
        }
    })
    
    task = await create_task(db_session, {
        "title": "Build feature",
        "agent_type": "programmer",
        "work_state": "queued"
    })
    
    engine = OrchestratorEngine(db_session, fake_gateway)
    await engine.run_cycle()
    
    # Original task completed
    assert (await get_task(db_session, task.id)).work_state == "completed"
    
    # Handoff task created
    handoff_tasks = await get_tasks_by_agent(db_session, "tester")
    assert len(handoff_tasks) == 1
    assert handoff_tasks[0].title == "Test feature X"
```

### Testing Concurrent Execution

```python
async def test_concurrent_tasks(db_session, fake_gateway):
    """Multiple tasks execute concurrently."""
    # Create 3 tasks
    tasks = []
    for i in range(3):
        task = await create_task(db_session, {
            "title": f"Task {i}",
            "agent_type": "programmer",
            "work_state": "queued"
        })
        tasks.append(task)
        fake_gateway.add_response("programmer", {"status": "completed"})
    
    # Run orchestrator
    engine = OrchestratorEngine(db_session, fake_gateway)
    await engine.run_cycle()
    
    # All tasks should complete
    for task in tasks:
        updated = await get_task(db_session, task.id)
        assert updated.work_state == "completed"
```

### Fake Gateway Implementation

```python
# tests/integration/fake_gateway.py
class FakeGateway:
    """Simulates OpenClaw Gateway for integration tests."""
    
    def __init__(self):
        self.responses = {}
        self.spawned_sessions = []
    
    def add_response(self, agent_type: str, response: dict):
        """Add canned response for agent type."""
        self.responses[agent_type] = response
    
    async def spawn_session(self, agent: str, **kwargs):
        """Simulate worker spawn."""
        session_id = f"test-{agent}-{len(self.spawned_sessions)}"
        self.spawned_sessions.append(session_id)
        
        # Return canned response after simulated delay
        await asyncio.sleep(0.1)
        return {
            "session_id": session_id,
            **self.responses.get(agent, {"status": "completed"})
        }
```

---

## Tier 4: Chaos Tests

### When to Use
- Testing resilience under failure conditions
- Validating circuit breakers and escalation
- Testing timeout handling
- Testing resource exhaustion

### Structure

```python
# tests/chaos/test_agent_failures.py
import pytest
from tests.chaos.fault_injector import inject_failure

async def test_agent_timeout_triggers_escalation(db_session):
    """Agent timeout should create escalation task."""
    from app.orchestrator.engine import OrchestratorEngine
    
    # Create task with short timeout
    task = await create_task(db_session, {
        "title": "Will timeout",
        "agent_type": "programmer",
        "work_state": "queued",
        "timeout_minutes": 1
    })
    
    # Inject timeout failure
    with inject_failure("worker.spawn", "timeout"):
        engine = OrchestratorEngine(db_session)
        await engine.run_cycle()
    
    # Task should fail
    updated_task = await get_task(db_session, task.id)
    assert updated_task.work_state == "failed"
    
    # Escalation created
    escalations = await get_escalations_for_task(db_session, task.id)
    assert len(escalations) == 1
    assert escalations[0].tier == "reflection"

async def test_database_lock_timeout(db_session):
    """Concurrent task updates should handle lock timeouts."""
    # Create task
    task = await create_task(db_session, {"title": "Test"})
    
    # Simulate lock contention
    with inject_failure("database.update", "lock_timeout"):
        # Should retry, not crash
        result = await update_task_status(db_session, task.id, "running")
        assert result is not None

async def test_circuit_breaker_opens_on_repeated_failures(db_session):
    """Circuit breaker should open after threshold failures."""
    from app.orchestrator.circuit_breaker import CircuitBreaker
    
    cb = CircuitBreaker("test-provider", failure_threshold=3)
    
    # Trigger 3 failures
    for _ in range(3):
        with inject_failure("provider.api", "500_error"):
            try:
                await cb.call(lambda: mock_api_call())
            except Exception:
                pass
    
    # Circuit should be open
    assert cb.state == "open"
    
    # Subsequent calls should fail fast
    with pytest.raises(CircuitBreakerOpenError):
        await cb.call(lambda: mock_api_call())
```

### Fault Injector

```python
# tests/chaos/fault_injector.py
from contextlib import contextmanager
from unittest.mock import patch

@contextmanager
def inject_failure(component: str, failure_type: str):
    """Inject specific failure into component.
    
    Args:
        component: "worker.spawn", "database.update", etc.
        failure_type: "timeout", "500_error", "lock_timeout", etc.
    """
    if component == "worker.spawn" and failure_type == "timeout":
        with patch("app.orchestrator.worker.spawn_session") as mock:
            mock.side_effect = asyncio.TimeoutError("Worker spawn timeout")
            yield
    
    elif component == "database.update" and failure_type == "lock_timeout":
        with patch("app.database.execute") as mock:
            mock.side_effect = OperationalError("database is locked")
            yield
    
    # Add more failure scenarios as needed
```

---

## Test Organization

```
tests/
├── __init__.py
├── conftest.py                    # Shared fixtures
│
├── test_*.py                      # Unit tests (Tier 1)
│
├── contracts/                     # Contract tests (Tier 2)
│   ├── fixtures/
│   │   ├── programmer_success.json
│   │   ├── researcher_partial.json
│   │   └── writer_error.json
│   └── test_*_contract.py
│
├── integration/                   # Integration tests (Tier 3)
│   ├── fake_gateway.py
│   ├── test_task_lifecycle.py
│   ├── test_handoffs.py
│   └── test_concurrent_workflows.py
│
└── chaos/                         # Chaos tests (Tier 4)
    ├── fault_injector.py
    ├── test_agent_failures.py
    ├── test_database_failures.py
    └── test_circuit_breaker.py
```

---

## Running Tests in CI

```yaml
# .github/workflows/test.yml
name: Test

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-asyncio pytest-cov
      
      - name: Run unit tests
        run: pytest -m unit -v
      
      - name: Run contract tests
        run: pytest -m contract -v
      
      - name: Run integration tests
        run: pytest -m integration -v --timeout=60
      
      - name: Run chaos tests
        run: pytest -m chaos -v --timeout=120
      
      - name: Upload coverage
        run: pytest --cov=app --cov-report=xml
```

---

## Troubleshooting

### "Fixture not found"
```bash
# Make sure conftest.py is in tests/ directory
# Check fixture scope: function, module, or session
```

### "Async test hangs"
```python
# Add timeout to async tests
@pytest.mark.timeout(30)
async def test_my_async_function():
    ...
```

### "Database state leaks between tests"
```python
# Use rollback in fixture cleanup
@pytest.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()  # ← Clean up after test
```

### "Flaky integration test"
```python
# Add retries for timing-dependent tests
@pytest.mark.flaky(reruns=3)
async def test_eventual_consistency():
    ...
```

---

## Examples from Codebase

See these existing tests for patterns:

- **Unit:** `tests/test_orchestrator_engine.py`
- **Integration:** `tests/test_pipeline_integration.py`
- **Contract:** `tests/contracts/` (to be created)
- **Chaos:** `tests/test_circuit_breaker.py` (partial)

---

## Next Steps

1. **Write your test** following the appropriate tier
2. **Run locally** to verify it passes
3. **Add to CI** if creating new test category
4. **Update this runbook** if you discover better patterns
