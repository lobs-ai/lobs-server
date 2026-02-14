# Known Issues

**Last Updated:** 2026-02-14

This document tracks known issues, limitations, and technical debt across lobs-server.

---

## Critical Issues

### 1. WebSocket Test Infrastructure Broken

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

## Important Issues

### 2. Pydantic v1 Configuration (Deprecated)

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
