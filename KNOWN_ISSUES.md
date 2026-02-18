# Known Issues — lobs-server

**Last Updated:** 2026-02-14  
**Source:** Code quality review and handoff inventory

This file tracks known bugs, technical debt, and quality issues identified during code reviews. Issues here should have corresponding handoffs for programmer agents to fix.

---

## 🔴 Critical Issues

### WebSocket Test Infrastructure Broken
**Discovered:** 2026-02-14  
**Impact:** 6 WebSocket tests failing (97.8% pass rate instead of 100%)  
**Status:** ⏸️ Pending fix  
**Handoff:** `handoff-websocket-test-infrastructure.json`

**Problem:**  
All 6 WebSocket tests in `tests/test_chat.py` fail with:
```
AttributeError: 'AsyncClient' object has no attribute 'websocket_connect'
```

**Root Cause:**  
Tests use `httpx.AsyncClient.websocket_connect()`, which doesn't exist. HTTPX's AsyncClient doesn't support WebSocket connections.

**Solution:**  
Replace AsyncClient with Starlette's TestClient for WebSocket tests.

**Affected Tests:**
- `test_websocket_connect`
- `test_websocket_send_message`
- `test_websocket_create_session`
- `test_websocket_list_sessions`
- `test_websocket_switch_session`
- `test_websocket_broadcast`

**Note:** The REST chat endpoints are fully tested and passing. The WebSocket implementation itself is likely sound—this is purely a test infrastructure issue.

---

### Activity Endpoint Missing Tests
**Discovered:** 2026-02-14  
**Impact:** New production endpoint deployed without test coverage  
**Status:** ⏸️ Pending fix  
**Handoff:** `handoff-activity-endpoint-tests.json`

**Problem:**  
The new `/api/worker/activity` endpoint has no test coverage.

**Context:**  
This endpoint was added in commit `0d7daa5` to capture agent result summaries. It's actively used by Mission Control to display worker activity.

**Required Tests:**
- GET /api/worker/activity returns recent worker runs
- Response includes session summaries
- Pagination works correctly
- Empty state handled properly
- Filters work (by project, agent type, date range)

---

### WorkerRun Schema Missing Field
**Discovered:** 2026-02-14  
**Impact:** Schema/reality mismatch causes data loss  
**Status:** ⏸️ Pending fix  
**Handoff:** `handoff-workerrun-schema-fix.json`

**Problem:**  
WorkerRun database model has `summary` field, but WorkerRunSchema (Pydantic) doesn't expose it in API responses.

**Location:**  
- Model: `app/models.py` (has field)
- Schema: `app/schemas.py` (missing field)

**Impact:**  
Client applications can't access worker session summaries through the API even though the data is stored in the database.

---

## 🟡 Important Issues

### Activity Endpoint N+1 Query
**Discovered:** 2026-02-14  
**Impact:** Performance degradation under load  
**Status:** ⏸️ Pending fix  
**Handoff:** `handoff-activity-endpoint-performance.json`

**Problem:**  
The `/api/worker/activity` endpoint has an N+1 query problem when loading related data.

**Solution:**  
Use SQLAlchemy's `joinedload()` or `selectinload()` to eagerly load relationships in a single query.

---

### Pydantic V2 Migration Incomplete
**Discovered:** 2026-02-14  
**Impact:** Deprecation warnings, future compatibility risk  
**Status:** ⏸️ Pending fix  
**Handoff:** `handoff-pydantic-v2-migration.json`

**Problem:**  
Multiple models still use deprecated `class Config` pattern instead of Pydantic v2's `ConfigDict`.

**Affected Files:**
- `app/routers/chat.py`
- Various models in `app/models/`

**Deprecation Warnings:**
```
PydanticDeprecatedSince20: Support for class-based config is deprecated
```

**Migration Pattern:**
```python
# Old (deprecated)
class MyModel(BaseModel):
    class Config:
        from_attributes = True

# New (Pydantic v2)
from pydantic import ConfigDict

class MyModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
```

---

### Worker Test Coverage Missing
**Discovered:** 2026-02-14  
**Impact:** Recent features lack test coverage  
**Status:** ⏸️ Pending fix  
**Handoff:** `handoff-worker-test-coverage.json`

**Problem:**  
Worker session summary feature (added in commit `0d7daa5`) lacks comprehensive test coverage.

**Missing Tests:**
- Summary extraction from session history
- Fallback to .work-summary file
- Summary truncation (2000 char limit)
- Summary storage in WorkerRun model

---

### Activity Endpoint Type Safety
**Discovered:** 2026-02-14  
**Impact:** Weak type safety, unclear API contract  
**Status:** ⏸️ Pending fix  
**Handoff:** `handoff-activity-type-safety.json`

**Problem:**  
The `/api/worker/activity` endpoint response needs stronger type definitions.

**Recommendation:**  
Create dedicated ActivityResponse and ActivityItem Pydantic schemas instead of using generic dict responses.

---

## 🔵 Minor Issues

### Pytest Marker Not Registered
**Discovered:** 2026-02-14  
**Impact:** Test warning, documentation gap  
**Status:** ⏸️ Pending fix  
**Handoff:** `handoff-pytest-marker-registration.json`

**Problem:**  
Tests use `@pytest.mark.integration` but the marker isn't registered in `pyproject.toml`.

**Warning:**
```
PytestUnknownMarkWarning: Unknown pytest.mark.integration
```

**Solution:**  
Add to `pyproject.toml`:
```toml
[tool.pytest.ini_options]
markers = [
    "integration: marks tests as integration tests (deselect with '-m \"not integration\"')",
]
```

---

### Test Environment Setup Documentation
**Discovered:** 2026-02-14  
**Impact:** Developer onboarding friction  
**Status:** ⏸️ Pending fix  
**Handoff:** `handoff-test-environment.json`

**Problem:**  
Test environment setup isn't documented. New developers don't know how to set up `.venv` and run tests.

**Missing Documentation:**
- How to create virtual environment
- How to install test dependencies
- How to run test suite
- How to run specific test categories (unit vs integration)

**Suggested Location:** Add "Testing" section to `README.md`

---

### Scheduler Work State Tests
**Discovered:** 2026-02-14  
**Impact:** New field lacks comprehensive test coverage  
**Status:** ⏸️ Pending fix  
**Handoff:** `handoff-scheduler-tests.json`

**Problem:**  
The `work_state` field defaulting behavior needs more test coverage.

**Context:**  
The scheduler now sets `work_state='not_started'` on newly created tasks (commit `0bd60a1`), but this behavior isn't fully tested.

---

## Issue Lifecycle

### States
- ⏸️ **Pending** — Issue identified, handoff created
- 🔨 **In Progress** — Programmer assigned
- ✅ **Fixed** — Fix implemented and merged
- 📦 **Deployed** — Fix in production
- ❌ **Won't Fix** — Decided not to fix (with reason)

### When to Update This File

**Add issue:**
- During code reviews
- After identifying bugs
- When creating handoffs

**Update status:**
- When programmer starts work (→ In Progress)
- When fix is merged (→ Fixed)
- When fix is deployed (→ Deployed)

**Remove issue:**
- After verification in production
- Move to COMPLETED.md with resolution date

---

## Statistics

**Total Issues:** 11  
**By Priority:**
- 🔴 Critical: 3
- 🟡 Important: 5
- 🔵 Minor: 3

**By Status:**
- ⏸️ Pending: 11
- 🔨 In Progress: 0
- ✅ Fixed: 0

**Test Pass Rate:** 97.8% (269/275 tests passing)

---

## Related Documentation

- [Handoffs Inventory](/Users/lobs/self-improvement/HANDOFFS_INVENTORY.md) — Full handoff details
- [Review Notes](/Users/lobs/self-improvement/review-notes.md) — Latest code quality review
- [CONTRIBUTING.md](CONTRIBUTING.md) — Development guidelines
- [ARCHITECTURE.md](ARCHITECTURE.md) — System architecture

---

**Next Review:** Weekly (every Friday)
