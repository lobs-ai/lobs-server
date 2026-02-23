# Chaos Testing Guide

**Purpose:** Validate system resilience under failure conditions.

**When to use:** Before deploying changes to orchestrator, failure recovery, or circuit breakers.

---

## Overview

Chaos tests inject failures to validate that:
1. **Failures are detected** — Monitoring, logging, circuit breakers activate
2. **Recovery works** — Escalation, retries, fallbacks succeed
3. **State remains consistent** — Database integrity under concurrent failures
4. **Cascades are contained** — One failure doesn't bring down the system

Unlike integration tests (happy paths + simple failures), chaos tests **systematically inject failures** across components.

---

## Failure Injection Framework

### Architecture

```
┌─────────────────────────────────────────┐
│  Chaos Test                             │
│  - Configures failure scenarios        │
│  - Runs orchestrator                    │
│  - Validates resilience                 │
└────────────────────┬────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────┐
│  Failure Injector (Middleware)          │
│  - Wraps components                     │
│  - Injects faults based on config       │
│  - Records injected failures            │
└────────────────────┬────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────┐
│  Orchestrator Components                │
│  - Worker, Scanner, Router, DB          │
│  - Experiences injected failures        │
│  - Activates recovery mechanisms        │
└─────────────────────────────────────────┘
```

### Failure Types

| Category | Failure | Impact | Detection | Recovery |
|----------|---------|--------|-----------|----------|
| **Agent** | Timeout | Worker stuck | Monitor detects after WORKER_KILL_TIMEOUT | Force-kill worker, mark task failed |
| | Crash | Worker exits unexpectedly | Status poll finds no session | Mark task failed, escalate |
| | Malformed output | Parse error | Result processing fails | Mark task failed, escalate |
| | Infinite loop | Worker never completes | Timeout detection | Force-kill, escalate |
| **Database** | Lock timeout | Query blocked | SQLAlchemy timeout exception | Retry, backoff, escalate |
| | Connection drop | Session lost | Connection error | Reconnect, retry transaction |
| | Constraint violation | Data integrity error | SQL error | Rollback, log error |
| **Network** | Gateway timeout | No response from Gateway | HTTP timeout | Circuit breaker opens, retry |
| | 500 error | Gateway internal error | HTTP 500 | Retry with backoff, escalate |
| | Rate limit | 429 response | HTTP 429 | Backoff, circuit breaker |
| **Resource** | Max workers | Worker spawn fails | Spawn returns false | Queue task, wait for slot |
| | Disk full | Result write fails | IOError | Log error, continue |

---

## Directory Layout

```
tests/
└── chaos/
    ├── __init__.py
    ├── conftest.py                # Chaos fixtures
    ├── injectors/
    │   ├── __init__.py
    │   ├── agent_failures.py      # Agent timeout, crash, bad output
    │   ├── db_failures.py         # Lock timeout, connection drop
    │   ├── network_failures.py    # Gateway errors, timeouts
    │   └── resource_failures.py   # Max workers, disk full
    ├── test_agent_failures.py
    ├── test_db_failures.py
    ├── test_network_failures.py
    ├── test_cascading_failures.py
    └── test_recovery_mechanisms.py
```

---

## Failure Injectors

### Base Injector

```python
"""Base failure injector."""
from typing import Callable, Optional, Any
import asyncio
import random


class FailureInjector:
    """
    Base class for failure injection.
    
    Usage:
        injector = FailureInjector(
            fail_on_call=3,        # Fail on 3rd call
            error_type=TimeoutError,
            error_message="Simulated timeout"
        )
        
        @injector.wrap
        async def my_function():
            # ... normal code ...
    """
    
    def __init__(
        self,
        fail_on_call: Optional[int] = None,
        fail_probability: Optional[float] = None,
        error_type: type = Exception,
        error_message: str = "Injected failure",
        delay_seconds: float = 0.0
    ):
        self.fail_on_call = fail_on_call
        self.fail_probability = fail_probability
        self.error_type = error_type
        self.error_message = error_message
        self.delay_seconds = delay_seconds
        
        self.call_count = 0
        self.failures_injected = 0
    
    def should_fail(self) -> bool:
        """Determine if this call should fail."""
        self.call_count += 1
        
        if self.fail_on_call is not None:
            return self.call_count == self.fail_on_call
        
        if self.fail_probability is not None:
            return random.random() < self.fail_probability
        
        return False
    
    def wrap(self, func: Callable) -> Callable:
        """Wrap a function to inject failures."""
        if asyncio.iscoroutinefunction(func):
            async def wrapper(*args, **kwargs):
                if self.delay_seconds > 0:
                    await asyncio.sleep(self.delay_seconds)
                
                if self.should_fail():
                    self.failures_injected += 1
                    raise self.error_type(self.error_message)
                
                return await func(*args, **kwargs)
        else:
            def wrapper(*args, **kwargs):
                if self.should_fail():
                    self.failures_injected += 1
                    raise self.error_type(self.error_message)
                
                return func(*args, **kwargs)
        
        return wrapper
```

### Agent Failure Injector

```python
"""Inject agent-level failures."""
import asyncio
from tests.chaos.injectors import FailureInjector


class AgentFailureInjector:
    """Inject failures in agent interactions."""
    
    @staticmethod
    def timeout(delay_seconds: float = 999):
        """Simulate agent timeout by never completing."""
        async def hang_forever(*args, **kwargs):
            await asyncio.sleep(delay_seconds)
            raise TimeoutError("Agent timed out")
        
        return hang_forever
    
    @staticmethod
    def crash():
        """Simulate agent crash."""
        def crash_immediately(*args, **kwargs):
            raise RuntimeError("Agent crashed unexpectedly")
        
        return crash_immediately
    
    @staticmethod
    def malformed_output():
        """Simulate agent returning malformed output."""
        def return_bad_output(*args, **kwargs):
            return {"invalid": "schema", "missing": "required_fields"}
        
        return return_bad_output
    
    @staticmethod
    def intermittent_failure(fail_probability: float = 0.3):
        """Fail probabilistically."""
        return FailureInjector(
            fail_probability=fail_probability,
            error_type=RuntimeError,
            error_message="Intermittent agent failure"
        )
```

### Database Failure Injector

```python
"""Inject database failures."""
from sqlalchemy.exc import OperationalError, IntegrityError
from tests.chaos.injectors import FailureInjector


class DBFailureInjector:
    """Inject database failures."""
    
    @staticmethod
    def lock_timeout(on_call: int = 1):
        """Simulate database lock timeout."""
        return FailureInjector(
            fail_on_call=on_call,
            error_type=OperationalError,
            error_message="database is locked"
        )
    
    @staticmethod
    def connection_drop(on_call: int = 1):
        """Simulate connection drop."""
        return FailureInjector(
            fail_on_call=on_call,
            error_type=OperationalError,
            error_message="connection lost"
        )
    
    @staticmethod
    def constraint_violation():
        """Simulate constraint violation."""
        return FailureInjector(
            fail_on_call=1,
            error_type=IntegrityError,
            error_message="UNIQUE constraint failed"
        )
```

### Network Failure Injector

```python
"""Inject network/Gateway failures."""
from aiohttp import ClientError, ServerTimeoutError
from tests.chaos.injectors import FailureInjector


class NetworkFailureInjector:
    """Inject network failures."""
    
    @staticmethod
    def gateway_timeout(delay: float = 30.0):
        """Simulate Gateway API timeout."""
        return FailureInjector(
            delay_seconds=delay,
            error_type=ServerTimeoutError,
            error_message="Gateway request timed out"
        )
    
    @staticmethod
    def gateway_500():
        """Simulate Gateway 500 error."""
        return FailureInjector(
            fail_on_call=1,
            error_type=ClientError,
            error_message="Gateway returned 500"
        )
    
    @staticmethod
    def rate_limit():
        """Simulate rate limit (429)."""
        return FailureInjector(
            fail_on_call=1,
            error_type=ClientError,
            error_message="Rate limit exceeded (429)"
        )
```

---

## Writing Chaos Tests

### Example 1: Agent Timeout

```python
"""Test agent timeout detection and recovery."""
import pytest
from unittest.mock import patch
from tests.chaos.injectors.agent_failures import AgentFailureInjector


@pytest.mark.chaos
@pytest.mark.asyncio
async def test_agent_timeout_triggers_kill(db_session, orchestrator_engine):
    """Test worker is killed after timeout."""
    
    # Setup: Create task
    task = create_test_task(db_session, agent="programmer")
    
    # Inject: Agent never completes
    with patch("app.orchestrator.worker.WorkerManager.spawn_worker") as mock_spawn:
        mock_spawn.return_value = True  # Spawn succeeds
        
        with patch("app.orchestrator.worker.WorkerManager.poll_workers") as mock_poll:
            # Worker appears running forever
            mock_poll.return_value = [{"id": "run-1", "status": "running"}]
            
            # Run orchestrator
            await orchestrator_engine._run_once()  # Spawn
            
            # Simulate time passing (beyond WORKER_KILL_TIMEOUT)
            import time
            original_time = time.time
            time.time = lambda: original_time() + 3600  # +1 hour
            
            await orchestrator_engine._run_once()  # Detect timeout
            
            time.time = original_time
    
    # Assert: Task marked as failed
    await db_session.refresh(task)
    assert task.status == "failed"
    assert "timeout" in task.failure_reason.lower()
    
    # Assert: Escalation created
    escalations = await get_escalations_for_task(db_session, task.id)
    assert len(escalations) > 0
    assert escalations[0].reason == "worker_timeout"
```

### Example 2: Database Lock Timeout

```python
"""Test database lock timeout handling."""
@pytest.mark.chaos
@pytest.mark.asyncio
async def test_db_lock_timeout_retries(db_session):
    """Test orchestrator retries on DB lock timeout."""
    from tests.chaos.injectors.db_failures import DBFailureInjector
    
    # Setup
    task = create_test_task(db_session, agent="programmer")
    
    # Inject: First DB update fails with lock timeout
    lock_injector = DBFailureInjector.lock_timeout(on_call=1)
    
    with patch("app.orchestrator.scanner.Scanner.scan_for_work") as mock_scan:
        original_scan = mock_scan
        mock_scan.side_effect = lock_injector.wrap(original_scan)
        
        # Run orchestrator
        try:
            await orchestrator_engine._run_once()
        except OperationalError:
            pass  # Expected on first call
        
        # Retry should succeed (call #2 doesn't fail)
        await orchestrator_engine._run_once()
    
    # Assert: Eventually succeeds despite initial lock
    assert lock_injector.failures_injected == 1
    assert lock_injector.call_count == 2
```

### Example 3: Gateway 500 Error

```python
"""Test Gateway API error triggers circuit breaker."""
@pytest.mark.chaos
@pytest.mark.asyncio
async def test_gateway_error_opens_circuit_breaker(db_session, orchestrator_engine):
    """Test circuit breaker opens after repeated Gateway errors."""
    from tests.chaos.injectors.network_failures import NetworkFailureInjector
    
    # Setup: Multiple tasks
    tasks = [create_test_task(db_session, agent="programmer") for _ in range(5)]
    
    # Inject: Gateway returns 500 for all spawn attempts
    injector = NetworkFailureInjector.gateway_500()
    
    with patch("app.orchestrator.worker.WorkerManager._call_gateway") as mock_call:
        mock_call.side_effect = injector.wrap(lambda: {"error": "Internal error"})
        
        # Attempt to spawn multiple workers
        for _ in range(5):
            await orchestrator_engine._run_once()
    
    # Assert: Circuit breaker opened
    from app.orchestrator.circuit_breaker import circuit_breaker
    assert circuit_breaker.is_open("gateway_spawn")
    
    # Assert: Tasks remain queued (not marked failed)
    for task in tasks:
        await db_session.refresh(task)
        assert task.status in ["queued", "spawned"]  # Not failed
```

### Example 4: Cascading Failures

```python
"""Test cascading failure scenario."""
@pytest.mark.chaos
@pytest.mark.asyncio
async def test_cascading_failure_contained(db_session, orchestrator_engine):
    """
    Test cascading failure is contained:
    1. Agent times out
    2. Escalation spawns reflection agent
    3. Reflection agent also fails
    4. System doesn't spiral (max escalation depth)
    """
    
    # Setup
    task = create_test_task(db_session, agent="programmer")
    
    # Inject: All agents timeout
    with patch("app.orchestrator.worker.WorkerManager.spawn_worker") as mock_spawn:
        mock_spawn.return_value = True
        
        with patch("app.orchestrator.worker.WorkerManager.poll_workers") as mock_poll:
            mock_poll.return_value = [{"id": "run-1", "status": "running"}]  # Never completes
            
            # Simulate multiple cycles
            for i in range(10):
                # Advance time
                import time
                time.time = lambda: time.time() + 3600 * i
                
                await orchestrator_engine._run_once()
    
    # Assert: Escalation chain stopped at max depth
    escalations = await get_all_escalations(db_session)
    assert len(escalations) <= 3  # Max escalation depth
    
    # Assert: System didn't crash or spiral
    # (If it did, this test would hang or raise)
```

### Example 5: Concurrent Failure Isolation

```python
"""Test failure in one project doesn't affect others."""
@pytest.mark.chaos
@pytest.mark.asyncio
async def test_failure_isolation_between_projects(db_session, orchestrator_engine):
    """Test project A failure doesn't block project B."""
    
    # Setup: Two projects
    project_a = create_test_project(db_session, id=1, name="Project A")
    project_b = create_test_project(db_session, id=2, name="Project B")
    
    task_a = create_test_task(db_session, project_id=1, agent="programmer")
    task_b = create_test_task(db_session, project_id=2, agent="researcher")
    
    # Inject: Project A's agent crashes, B's succeeds
    def conditional_failure(agent_type, *args, **kwargs):
        if agent_type == "programmer":
            raise RuntimeError("Agent crashed")
        return True  # Researcher succeeds
    
    with patch("app.orchestrator.worker.WorkerManager.spawn_worker", side_effect=conditional_failure):
        await orchestrator_engine._run_once()
    
    # Assert: Task A failed, Task B succeeded
    await db_session.refresh(task_a)
    await db_session.refresh(task_b)
    
    assert task_a.status == "failed"
    assert task_b.status == "spawned"  # Or completed, depending on timing
```

---

## Observability Validation

Chaos tests should validate that failures are **observable**:

```python
"""Validate observability during failures."""
@pytest.mark.chaos
async def test_failure_observability(db_session, orchestrator_engine, caplog):
    """Test failures are logged and tracked."""
    
    # Inject failure
    # ... (setup failure injector) ...
    
    # Run orchestrator
    await orchestrator_engine._run_once()
    
    # Assert: Error logged
    assert "ERROR" in caplog.text
    assert "Agent crashed" in caplog.text
    
    # Assert: Metric incremented (if using metrics)
    # from app.metrics import get_metric
    # assert get_metric("orchestrator.worker.failures") > 0
    
    # Assert: Database record exists
    from app.models import ErrorLog
    errors = await db_session.execute(select(ErrorLog))
    assert errors.scalar_one_or_none() is not None
```

---

## Best Practices

### ✅ Do

- **Test realistic failure sequences** — Combine multiple failures (network + DB + agent)
- **Validate observability** — Check logs, metrics, error records
- **Test recovery, not just detection** — Verify system returns to healthy state
- **Use deterministic failures** — Avoid random failures in CI (flaky tests)
- **Document expected behavior** — Each test should clearly state what "correct" looks like

### ❌ Don't

- **Don't test impossible scenarios** — Focus on failures that actually happen
- **Don't make tests too complex** — One failure type per test
- **Don't skip cleanup** — Restore state after injecting failures
- **Don't ignore flakiness** — If a chaos test is flaky, fix it or remove it

---

## Running Chaos Tests

```bash
# Run all chaos tests
pytest -m chaos -v

# Run specific failure type
pytest tests/chaos/test_agent_failures.py -v

# Run with detailed logging
pytest -m chaos -v --log-cli-level=DEBUG

# Run slowly (to observe behavior)
pytest -m chaos -v -s
```

---

## Chaos Test Checklist

When adding a new component or recovery mechanism, write chaos tests for:

- [ ] **Timeout** — Component times out
- [ ] **Crash** — Component raises unexpected exception
- [ ] **Malformed input** — Component receives invalid data
- [ ] **Resource exhaustion** — Component hits limits (connections, memory)
- [ ] **Transient failure** — Component fails then succeeds on retry
- [ ] **Permanent failure** — Component fails repeatedly
- [ ] **Cascading failure** — Component failure triggers downstream failures
- [ ] **Recovery** — System returns to healthy state after failure

---

## Related

- [ADR 0006: Distributed Testing Architecture](../decisions/0006-distributed-testing-architecture.md)
- [Contract Testing Guide](contract-testing.md)
- [Integration Testing Guide](integration-testing.md)
- [Circuit Breaker Implementation](../architecture/circuit-breaker.md)
- [Escalation System](../architecture/escalation.md)
