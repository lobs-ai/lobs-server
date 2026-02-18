# Known Issues

**Last Updated:** 2026-02-14

This document tracks known issues, limitations, and technical debt across lobs-server.

---

## Critical Issues

### 1. /api/worker/activity Endpoint Lacks Test Coverage

**Status:** 🔴 Critical — New endpoint untested  
**Affected:** `app/routers/worker.py::list_activity`  
**Impact:** Production endpoint deployed without verification

**Problem:**
The recently added `/api/worker/activity` endpoint (commit 0d7daa5, 2026-02-14) has zero test coverage:
- Queries database and joins with tasks
- Returns activity data with task details
- Handles pagination
- None of this is tested

**Missing Tests:**
- Empty activity list
- Activity with task details
- Activity with missing/null task_id
- Pagination (limit/offset)
- Response schema validation
- Task fields properly populated (task_title, project_id, agent)

**Workaround:** None — endpoint is in production use  
**Fix Required:** Add comprehensive test coverage in `tests/test_worker.py`  
**Priority:** Immediate — untested production code

**Reference:** [Code Quality Review 2026-02-14](~/self-improvement/review-notes.md)

---

### 2. WebSocket Test Infrastructure Broken

**Status:** 🔴 Critical — Tests failing  
**Affected:** `tests/test_chat.py::TestChatWebSocket` (6 tests)  
**Impact:** WebSocket functionality is untested in CI/CD

**Problem:**
Test suite uses `httpx.AsyncClient` which doesn't support WebSocket connections. All WebSocket tests fail with:
```
AttributeError: 'AsyncClient' object has no attribute 'websocket_connect'
```

**Failing Tests:**
- `test_websocket_connect`
- `test_websocket_send_message`
- `test_websocket_create_session`
- `test_websocket_list_sessions`
- `test_websocket_switch_session`
- `test_websocket_broadcast`

**Workaround:** Manual testing only  
**Fix Required:** Migrate to Starlette's `TestClient` or `websockets` library  
**Tracked In:** Programmer handoff created 2026-02-14

**Reference:** [Code Quality Review](~/self-improvement/review-notes.md)

---

### 3. Time-Based Test Race Condition

**Status:** 🔴 Critical — Flaky test  
**Affected:** `tests/test_status.py::test_costs_with_worker_data`  
**Impact:** Intermittent CI failures, test unreliability

**Problem:**
Test creates worker runs using timestamps relative to "now," but the endpoint queries data based on "today" boundaries. If the test runs near midnight UTC (23:00-01:00), the worker run may fall into yesterday while the query looks for today's data.

**Example Failure:**
```
Test setup at 23:55 UTC Feb 14:
  worker_run.started_at = now - timedelta(hours=1)  # 22:55 Feb 14

Endpoint execution at 00:05 UTC Feb 15:
  today_start = now.replace(hour=0, minute=0)  # 00:00 Feb 15
  Query: WHERE started_at >= '2026-02-15 00:00:00'
  
Result: No matches (worker run is from Feb 14) → Test fails
```

**Observed Failure:**
```
FAILED tests/test_status.py::test_costs_with_worker_data - assert 0 == 1000
```

**Root Cause:** Test data uses relative timestamps instead of fixed timestamps within a known "today" boundary.

**Impact:**
- Flaky test causes false CI failures
- Developers waste time debugging intermittent failures  
- Test suite loses trust

**Workaround:** Re-run tests during daytime UTC hours  
**Fix Required:** Use deterministic timestamps that are always within "today" (e.g., set test time to noon, or freeze time in test)  
**Priority:** Immediate — flaky tests erode confidence

**Reference:** [Code Quality Review 2026-02-14 Evening](~/self-improvement/review-notes.md)

---

## Important Issues

### 1. WorkerRun Schema Missing summary Field

**Status:** 🟡 Important — Schema incomplete  
**Affected:** `app/schemas.py::WorkerRunBase`, `app/routers/worker.py::list_activity`  
**Impact:** Activity endpoint may return incomplete data

**Problem:**
The `WorkerRun` model has a `summary` field (added 2026-02-14), but the Pydantic schema doesn't include it:

```python
# app/models.py (HAS the field)
class WorkerRun(Base):
    summary = Column(String)  # Work summary from .work-summary file

# app/schemas.py (MISSING the field)
class WorkerRunBase(BaseModel):
    # summary field not defined
```

**Impact:**
- `/api/worker/activity` endpoint doesn't return summaries
- Frontend can't display agent work summaries
- Schema validation may fail if summary is accessed

**Workaround:** None  
**Fix Required:** Add `summary: Optional[str] = None` to `WorkerRunBase` schema  
**Priority:** Important — affects new feature functionality

**Reference:** [Code Quality Review 2026-02-14](~/self-improvement/review-notes.md)

---

### 2. /api/worker/activity Has N+1 Query Problem

**Status:** 🟡 Important — Performance issue  
**Affected:** `app/routers/worker.py::list_activity`  
**Impact:** Slow response times with many worker runs

**Problem:**
The activity endpoint currently fetches task details with separate queries:

```python
for run in runs:
    if run.task_id:
        task = await get_task(db, run.task_id)  # N separate queries
```

For 50 worker runs, this makes 50+ database queries instead of 1 join query.

**Impact:**
- Slow API responses with many runs
- Unnecessary database load
- Poor scalability

**Workaround:** Use pagination (limit results)  
**Fix Required:** Rewrite query to use SQL join instead of loop  
**Priority:** Important — affects performance

**Reference:** [Code Quality Review 2026-02-14](~/self-improvement/review-notes.md)

---

### 3. Pydantic v1 Configuration (Deprecated)

**Status:** 🟡 Important — Works but deprecated  
**Affected:** `app/routers/chat.py` (2 models)  
**Impact:** Will break on Pydantic v3 release

**Problem:**
Using deprecated class-based `Config` instead of Pydantic v2's `ConfigDict`:

```python
# Current (deprecated):
class ChatMessageResponse(BaseModel):
    class Config:
        orm_mode = True

# Should be:
from pydantic import ConfigDict

class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
```

**Warnings:**
```
PydanticDeprecatedSince20: Support for class-based `config` is deprecated,
use ConfigDict instead.
```

**Workaround:** None needed — still works  
**Fix Required:** Migrate to `ConfigDict`  
**Priority:** Before upgrading to Pydantic v3

---

### 3. Unregistered Pytest Marker

**Status:** 🟡 Minor — Tests work but warning appears  
**Affected:** `tests/test_software_updates.py:480`  
**Impact:** Confusing pytest output

**Problem:**
Using `@pytest.mark.integration` without registering the marker in pytest config.

**Warning:**
```
PytestUnknownMarkWarning: Unknown pytest.mark.integration
```

**Fix:**
Add to `pyproject.toml`:
```toml
[tool.pytest.ini_options]
markers = [
    "integration: marks tests as integration tests (deselect with '-m \"not integration\"')",
]
```

---

### 4. Test Dependencies Not in pyproject.toml

**Status:** 🟡 Documentation issue  
**Affected:** Developer setup  
**Impact:** New developers can't run tests without extra setup

**Problem:**
- `pyproject.toml` lists only runtime dependencies
- Test dependencies (pytest, httpx, etc.) only in `requirements.txt`
- No `[project.optional-dependencies]` section for test deps

**Current State:**
```toml
[project]
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    # ... (no test deps)
]
```

**Recommended:**
```toml
[project.optional-dependencies]
test = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "httpx>=0.25.0",
]
```

Then install with: `pip install -e ".[test]"`

---

## Limitations & Design Decisions

### 5. WebSocket Reconnection Handling

**Status:** ℹ️ By Design  
**Context:** Client is responsible for reconnection logic

**Decision:**
Server provides WebSocket endpoint but doesn't enforce reconnection strategy. Clients (Mission Control, Mobile) implement their own exponential backoff and reconnection.

**Rationale:**
- Simpler server implementation
- Clients have better context for their network conditions
- Documented in `research-findings.md`

**Reference:** [WebSocket Research](research-findings.md)

---

### 6. SQLite Database Locking (Fixed)

**Status:** ✅ Resolved  
**Fixed In:** Commit `529295b`

**Was:**
- Database locking under concurrent access
- `OperationalError: database is locked`

**Solution:**
Enabled SQLite WAL mode + busy timeout:
```python
engine = create_async_engine(
    DATABASE_URL,
    connect_args={
        "timeout": 30,
        "check_same_thread": False,
    },
    echo=False,
)

# Enable WAL mode
await conn.execute(text("PRAGMA journal_mode=WAL"))
await conn.execute(text("PRAGMA busy_timeout=30000"))
```

---

## Test Coverage Gaps

Current test pass rate: **97.8%** (269/275 tests passing)

**Not Tested:**
- WebSocket functionality (6 tests broken)
- Scheduler `work_state` field interaction with scanner (handoff created)

**Well Tested:**
- REST API endpoints (✅ passing)
- Database operations (✅ passing)
- Task/project CRUD (✅ passing)
- Inbox processing (✅ passing)

---

## Technical Debt

### Code Quality
- Pydantic v1 config in chat.py → Migrate to ConfigDict
- Some unused variables (see code quality review)

### Documentation
- No ARCHITECTURE.md (AGENTS.md serves this purpose for now)
- Test setup could be clearer in README

### Testing
- WebSocket test infrastructure needs redesign
- Missing integration tests for scheduler ↔ scanner interaction

---

## Tracking & Updates

**How to Update This Document:**
1. Add new issues as they're discovered
2. Move fixed issues to "Resolved" section with fix commit
3. Update status markers (🔴 critical, 🟡 important, ℹ️ info, ✅ resolved)
4. Link to relevant handoffs, commits, or documentation
5. Include dates for context

**Related Documents:**
- [Code Quality Review](~/self-improvement/review-notes.md) — Latest review findings
- [docs/README.md](README.md) — Full documentation index
- [AGENTS.md](../AGENTS.md) — API and architecture reference

**Last Review:** 2026-02-14
