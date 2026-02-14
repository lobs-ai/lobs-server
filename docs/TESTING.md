# Testing Guide

**Last Updated:** 2026-02-14

Complete guide to testing lobs-server.

---

## Quick Start

```bash
# Setup (first time)
cd ~/lobs-server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run tests
python -m pytest -v

# Run specific test file
python -m pytest tests/test_tasks.py -v

# Stop on first failure
python -m pytest -x

# Show print statements
python -m pytest -v -s

# Run only unit tests (skip slow integration tests)
python -m pytest -m "not integration" -v
```

---

## Test Structure

```
tests/
├── conftest.py              # Shared fixtures (db, client, test data)
├── test_tasks.py            # Task CRUD tests
├── test_projects.py         # Project CRUD tests
├── test_memories.py         # Memory system tests
├── test_inbox.py            # Inbox processing tests
├── test_documents.py        # Document management tests
├── test_topics.py           # Topics/knowledge tests
├── test_chat.py             # Chat & WebSocket tests (⚠️ WebSocket tests broken)
├── test_calendar.py         # Calendar/events tests
├── test_orchestrator.py     # Orchestrator logic tests
├── test_tracker.py          # Tracker tests
└── ...
```

---

## Test Database

Tests use **in-memory SQLite** for isolation and speed:

```python
# conftest.py creates a fresh test DB for each test
@pytest.fixture
async def db_session():
    """Provide clean test database for each test."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    # ... setup tables ...
    yield session
    # ... teardown ...
```

**Benefits:**
- ✅ Fast (no disk I/O)
- ✅ Isolated (no test pollution)
- ✅ Automatic cleanup

**Limitations:**
- ⚠️ May not catch SQLite-specific issues that only appear with persistent DB
- ⚠️ Concurrent access patterns differ from file-based SQLite

---

## Test Client

Uses `httpx.AsyncClient` for API testing:

```python
@pytest.fixture
async def client(db_session):
    """Provide test client with test database."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client
```

**Works for:** REST API endpoints  
**Doesn't work for:** WebSocket connections (see [Known Issues](KNOWN_ISSUES.md))

---

## Authentication in Tests

Most endpoints require authentication. Tests use a test token:

```python
@pytest.fixture
def auth_headers(db_session):
    """Provide Bearer token for authenticated requests."""
    # Create test token
    token = create_test_token(session=db_session, name="test-token")
    return {"Authorization": f"Bearer {token.token_value}"}

# Usage:
async def test_get_tasks(client, auth_headers):
    response = await client.get("/api/tasks", headers=auth_headers)
    assert response.status_code == 200
```

---

## Common Test Patterns

### Testing CRUD Endpoints

```python
async def test_create_task(client, auth_headers):
    """Test task creation."""
    payload = {
        "title": "Test Task",
        "description": "Test description",
        "status": "todo",
    }
    response = await client.post("/api/tasks", json=payload, headers=auth_headers)
    
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Test Task"
    assert "id" in data
```

### Testing Database Models

```python
async def test_task_model(db_session):
    """Test Task model directly."""
    task = Task(
        title="Test",
        description="Description",
        status="todo",
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    
    assert task.id is not None
    assert task.created_at is not None
```

### Testing Business Logic

```python
async def test_orchestrator_scanner(db_session):
    """Test scanner finds eligible tasks."""
    # Create test tasks
    ready_task = Task(title="Ready", work_state="ready")
    blocked_task = Task(title="Blocked", work_state="not_started")
    
    db_session.add_all([ready_task, blocked_task])
    await db_session.commit()
    
    # Run scanner
    eligible = await find_eligible_tasks(db_session)
    
    assert len(eligible) == 1
    assert eligible[0].id == ready_task.id
```

---

## Test Coverage

**Current Status:** 97.8% pass rate (269/275 tests)

| Category | Tests | Status |
|----------|-------|--------|
| REST API Endpoints | ~200 | ✅ Passing |
| Database Models | ~30 | ✅ Passing |
| Orchestrator Logic | ~20 | ✅ Passing |
| WebSocket | 6 | ❌ Broken (infrastructure issue) |

**See:** [KNOWN_ISSUES.md](KNOWN_ISSUES.md) for details on failing tests.

---

## Pytest Markers

Mark tests for selective running:

```python
@pytest.mark.integration
async def test_full_workflow(client, db_session):
    """Integration test (slower, uses multiple components)."""
    pass

@pytest.mark.slow
async def test_large_dataset(db_session):
    """Test with large dataset (takes >5 seconds)."""
    pass
```

**Run only fast tests:**
```bash
pytest -m "not slow and not integration"
```

**Currently available markers:**
- `integration` — Multi-component integration tests
- `slow` — Tests taking >5 seconds

**Note:** Markers must be registered in `pyproject.toml` to avoid warnings (see [KNOWN_ISSUES.md](KNOWN_ISSUES.md#3-unregistered-pytest-marker)).

---

## Debugging Tests

### Show SQL Queries

Set `echo=True` in engine creation:
```python
engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    echo=True,  # Show all SQL
)
```

### Show Print Statements

```bash
pytest -v -s  # -s shows print() output
```

### Run Single Test

```bash
pytest tests/test_tasks.py::test_create_task -v
```

### Use Debugger

```python
import pdb; pdb.set_trace()  # Set breakpoint

# Or use pytest --pdb to drop into debugger on failure
pytest --pdb
```

---

## Known Issues & Limitations

### 1. WebSocket Tests Broken

**Problem:** `httpx.AsyncClient` doesn't support WebSocket connections.

**Affected:** `tests/test_chat.py::TestChatWebSocket` (6 tests)

**Workaround:** Manual testing with real WebSocket client.

**Fix Required:** Migrate to Starlette's `TestClient`:
```python
from starlette.testclient import TestClient

def test_websocket_connect():
    with TestClient(app).websocket_connect("/api/chat/ws?token=...") as ws:
        data = ws.receive_json()
        assert data["type"] == "connected"
```

**See:** [KNOWN_ISSUES.md](KNOWN_ISSUES.md#1-websocket-test-infrastructure-broken)

### 2. Integration Tests Not Separated

Some tests are marked `@pytest.mark.integration` but marker isn't registered.

**Fix:**
Add to `pyproject.toml`:
```toml
[tool.pytest.ini_options]
markers = [
    "integration: marks tests as integration tests",
    "slow: marks tests as slow (>5 seconds)",
]
```

---

## Adding New Tests

### 1. Choose Test File

- **Endpoint tests** → `tests/test_<router_name>.py`
- **Model tests** → `tests/test_models.py` (or inline in endpoint tests)
- **Orchestrator** → `tests/test_orchestrator.py`
- **New feature** → `tests/test_<feature_name>.py`

### 2. Use Fixtures

```python
async def test_my_feature(client, db_session, auth_headers):
    # client: HTTP test client
    # db_session: Test database session
    # auth_headers: Authentication headers
    pass
```

### 3. Follow Naming Convention

```python
# Test functions start with test_
async def test_create_task():
    pass

# Test classes start with Test
class TestTaskWorkflow:
    async def test_full_cycle(self):
        pass
```

### 4. Write Clear Assertions

```python
# ✅ Good - clear, specific
assert response.status_code == 201
assert "id" in response.json()
assert response.json()["title"] == "Expected Title"

# ❌ Bad - vague
assert response.status_code != 500
assert response.json()  # What are we checking?
```

### 5. Test Happy Path + Edge Cases

```python
async def test_create_task_success(client, auth_headers):
    """Test successful task creation."""
    # Happy path
    response = await client.post("/api/tasks", json=valid_payload, headers=auth_headers)
    assert response.status_code == 201

async def test_create_task_missing_title(client, auth_headers):
    """Test task creation fails without title."""
    # Edge case - missing required field
    response = await client.post("/api/tasks", json={}, headers=auth_headers)
    assert response.status_code == 422

async def test_create_task_invalid_status(client, auth_headers):
    """Test task creation fails with invalid status."""
    # Edge case - invalid enum value
    payload = {"title": "Test", "status": "invalid"}
    response = await client.post("/api/tasks", json=payload, headers=auth_headers)
    assert response.status_code == 422
```

---

## CI/CD Integration

Tests run automatically on push (if CI/CD configured).

**Recommended CI workflow:**
```yaml
- name: Run tests
  run: |
    source .venv/bin/activate
    python -m pytest -v --tb=short
```

**Current limitation:** WebSocket tests will fail (6 failures) until infrastructure is fixed.

---

## Performance Testing

For load/performance testing:

```bash
# Install locust or similar
pip install locust

# Create locustfile.py
# Run: locust -f locustfile.py
```

**Not yet implemented** — manual performance testing only.

---

## Resources

- **Pytest Docs:** https://docs.pytest.org/
- **FastAPI Testing:** https://fastapi.tiangolo.com/tutorial/testing/
- **SQLAlchemy Testing:** https://docs.sqlalchemy.org/en/20/orm/session_transaction.html#joining-a-session-into-an-external-transaction-such-as-for-test-suites

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'pytest'"

**Solution:**
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### "Database is locked"

**Usually not an issue with in-memory test DB.**

If using file-based test DB, enable WAL mode:
```python
await conn.execute(text("PRAGMA journal_mode=WAL"))
```

### "No tests collected"

**Check:**
1. Are you in the right directory? (should be `~/lobs-server/`)
2. Is .venv activated?
3. Do test files start with `test_`?
4. Do test functions start with `test_`?

### Tests Pass Locally, Fail in CI

**Common causes:**
1. Different Python version
2. Missing dependencies
3. Timezone differences
4. File system differences (case sensitivity)

---

**Last Updated:** 2026-02-14  
**Next Review:** When WebSocket test infrastructure is fixed
