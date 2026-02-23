# Test Helpers Library

Shared pytest fixtures, factories, mocks, and assertions to reduce duplication across lobs-server tests.

## Quick Start

All helpers are automatically available in your tests via `conftest.py`:

```python
import pytest
from httpx import AsyncClient
from tests.helpers import (
    create_task_data,
    mock_worker_manager,
    assert_response_success,
)

@pytest.mark.asyncio
async def test_example(client: AsyncClient):
    # Use factory for test data
    task_data = create_task_data(title="My Task", status="queued")
    
    # Make API request
    response = await client.post("/api/tasks", json=task_data)
    
    # Use assertion helper
    assert_response_success(response, expected_status=200)
```

## Modules

### `factories.py` - Data Factories

Create test data with sensible defaults and optional overrides:

```python
from tests.helpers import create_project_data, create_task_data

# Simple usage with defaults
project = create_project_data()
# {"id": "test-project-a1b2c3", "title": "Test Project", ...}

# Override specific fields
task = create_task_data(
    title="Important Task",
    status="queued",
    agent="programmer",
    deadline="2024-12-31"
)
```

**Available factories:**
- `create_project_data(id, title, **overrides)` - Project data
- `create_task_data(id, title, project_id, **overrides)` - Task data
- `create_inbox_data(id, title, **overrides)` - Inbox item data
- `create_document_data(id, title, **overrides)` - Document data
- `create_agent_data(agent_id, **overrides)` - Agent status data
- `create_memory_data(id, title, **overrides)` - Memory entry data
- `create_topic_data(id, title, **overrides)` - Topic data
- `create_calendar_event_data(id, title, **overrides)` - Calendar event data
- `create_template_data(id, name, **overrides)` - Template data

### `mocks.py` - Mock Helpers

Pre-configured mocks for common components:

```python
from unittest.mock import patch
from tests.helpers import mock_worker_manager, mock_scanner

@pytest.mark.asyncio
async def test_orchestrator_spawn():
    with patch('app.orchestrator.engine.WorkerManager') as MockWorker:
        # Configure mock to simulate successful spawn
        MockWorker.return_value = mock_worker_manager(spawn_result=True)
        
        # ... test code ...
```

**Available mocks:**
- `mock_worker_manager(spawn_result, active_workers, sweep_requested)` - WorkerManager mock
- `mock_scanner(work_items)` - Scanner mock
- `mock_monitor(stuck_tasks)` - MonitorEnhanced mock
- `mock_scheduler(due_events)` - EventScheduler mock
- `mock_reflection_manager(should_reflect)` - ReflectionCycleManager mock
- `mock_openclaw_bridge(webhook_result)` - OpenClawBridge mock
- `mock_routine_runner(routines)` - RoutineRunner mock
- `mock_sweep_arbitrator(should_sweep)` - SweepArbitrator mock
- `mock_inbox_processor(processed_count)` - InboxProcessor mock
- `mock_db_session()` - Database session mock

All mocks accept `**kwargs` to set additional attributes.

### `assertions.py` - Assertion Helpers

Readable assertions with helpful error messages:

```python
from tests.helpers import (
    assert_response_success,
    assert_has_fields,
    assert_task_status,
)

@pytest.mark.asyncio
async def test_api():
    response = await client.get("/api/tasks/123")
    
    # Assert successful response
    assert_response_success(response, expected_status=200)
    
    data = response.json()
    
    # Assert required fields present
    assert_has_fields(data, ["id", "title", "status", "created_at"])
    
    # Assert specific values
    assert_task_status(data, expected_status="completed")
```

**Available assertions:**
- `assert_response_success(response, expected_status, message)` - Assert successful HTTP response
- `assert_response_error(response, expected_status, expected_detail)` - Assert error response
- `assert_has_fields(data, required_fields, message)` - Assert dict has required keys
- `assert_task_status(task_data, expected_status, message)` - Assert task status
- `assert_list_response(response, min_length, max_length, item_schema)` - Assert and return list response
- `assert_pagination_headers(response, expected_total)` - Assert pagination headers
- `assert_timestamp_fields(data, fields)` - Assert timestamp fields are valid
- `assert_db_object_matches(db_obj, expected_data, exclude_fields)` - Assert DB object matches data
- `assert_json_schema(data, schema)` - Assert data matches type schema

### `fixtures.py` - Additional Fixtures

Extended fixtures automatically available via `conftest.py`:

```python
@pytest.mark.asyncio
async def test_with_multiple_tasks(multiple_tasks):
    # multiple_tasks fixture creates 5 tasks in various states
    assert len(multiple_tasks) == 5
    statuses = [t["status"] for t in multiple_tasks]
    assert "queued" in statuses
    assert "completed" in statuses
```

**Available fixtures:**
- `multiple_projects` - Create 3 test projects
- `multiple_tasks` - Create 5 tasks in different statuses
- `agent_status` - Create an agent status record
- `sample_topic` - Create a test topic
- `sample_memory` - Create a test memory entry
- `queued_task` - Create a task ready for orchestrator
- `blocked_task` - Create a blocked task
- `completed_task` - Create a completed task
- `mock_datetime_now` - Mock datetime.now() to fixed time
- `disable_orchestrator` - Disable orchestrator for test
- `api_base_headers` - Get auth + content-type headers

## Examples

### Example 1: API Endpoint Test

```python
import pytest
from httpx import AsyncClient
from tests.helpers import create_task_data, assert_response_success, assert_has_fields

@pytest.mark.asyncio
async def test_create_task(client: AsyncClient, sample_project):
    """Test task creation endpoint."""
    # Use factory to create test data
    task_data = create_task_data(
        title="New Task",
        project_id=sample_project["id"],
        status="inbox"
    )
    
    # Make request
    response = await client.post("/api/tasks", json=task_data)
    
    # Use assertion helpers
    assert_response_success(response, expected_status=200)
    
    result = response.json()
    assert_has_fields(result, ["id", "title", "status", "created_at", "updated_at"])
    assert result["title"] == "New Task"
```

### Example 2: Orchestrator Test with Mocks

```python
import pytest
from unittest.mock import patch
from tests.helpers import mock_worker_manager, mock_scanner
from app.orchestrator.engine import OrchestratorEngine

@pytest.mark.asyncio
async def test_orchestrator_spawns_worker(db_session):
    """Test orchestrator spawns worker for eligible task."""
    with patch('app.orchestrator.engine.WorkerManager') as MockWorker, \
         patch('app.orchestrator.engine.Scanner') as MockScanner:
        
        # Configure mocks
        MockWorker.return_value = mock_worker_manager(spawn_result=True)
        MockScanner.return_value = mock_scanner(work_items=[
            {"id": 123, "title": "Task", "agent": "programmer"}
        ])
        
        # Run orchestrator
        engine = OrchestratorEngine(db_session)
        await engine.process_cycle()
        
        # Verify worker was spawned
        MockWorker.return_value.spawn_worker.assert_called_once()
```

### Example 3: Using Multiple Fixtures

```python
import pytest
from tests.helpers import assert_list_response

@pytest.mark.asyncio
async def test_list_tasks_filtered(client, multiple_tasks):
    """Test listing tasks with status filter."""
    # multiple_tasks fixture creates 5 tasks in different statuses
    
    # Request only completed tasks
    response = await client.get("/api/tasks?status=completed")
    
    # Use assertion that validates list and returns it
    tasks = assert_list_response(
        response,
        min_length=1,
        item_schema=["id", "title", "status"]
    )
    
    # All returned tasks should be completed
    assert all(t["status"] == "completed" for t in tasks)
```

## Benefits

1. **Reduced Duplication** - No more copy-pasting test data creation
2. **Consistency** - All tests use same data patterns
3. **Readability** - Clear, descriptive helpers make tests easier to understand
4. **Maintainability** - Changes to test patterns happen in one place
5. **Discoverability** - Auto-complete shows available helpers

## Migration Guide

### Before (duplicated code):

```python
@pytest.mark.asyncio
async def test_old_way(client):
    project_data = {
        "id": "proj-1",
        "title": "Project",
        "notes": "Notes",
        "archived": False,
        "type": "kanban",
        "sort_order": 0
    }
    response = await client.post("/api/projects", json=project_data)
    assert response.status_code == 200
    # ... more boilerplate ...
```

### After (using helpers):

```python
from tests.helpers import create_project_data, assert_response_success

@pytest.mark.asyncio
async def test_new_way(client):
    project_data = create_project_data(title="Project")
    response = await client.post("/api/projects", json=project_data)
    assert_response_success(response)
    # Clean, concise, readable
```

## Contributing

When adding new helpers:

1. **Factories** - Add to `factories.py` if creating test data
2. **Mocks** - Add to `mocks.py` if mocking common components
3. **Assertions** - Add to `assertions.py` if checking common patterns
4. **Fixtures** - Add to `fixtures.py` if creating reusable test objects
5. **Export** - Add to `__init__.py` for easy imports
6. **Document** - Update this README with usage examples

## Testing the Helpers

The helpers library has its own test suite in `tests/test_helpers.py` to ensure correctness.

Run helper tests:
```bash
pytest tests/test_helpers.py -v
```
