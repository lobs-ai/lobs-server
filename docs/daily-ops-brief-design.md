# Daily Ops Brief — Design Document

**Status:** Ready for implementation  
**Created:** 2026-02-24  
**Task ID:** bc10aeab-75e0-4ac8-9df3-e78bffcc1b77  
**Risk tier:** B (approved feature)

---

## 1. Problem Statement

Rafe loses time each morning manually checking calendar, email, GitHub, and task queue. We need a single daily brief injected into the assistant chat thread at 8am ET that aggregates the most important information from all sources into a 60-second-readable markdown card. This is the first vertical slice of the unified integrations layer — a concrete proof of integration value.

---

## 2. Proposed Solution

### Architecture

```
OrchestratorEngine direct timer (once daily at _brief_hour_et=8 ET)
        │
        ▼
BriefService (app/services/brief_service.py)
        │
        ├── CalendarAdapter → GoogleCalendarService.get_rafe_schedule(days=1)
        │                     + internal ScheduledEvent table fallback
        ├── EmailAdapter    → EmailService.get_unread(max_results=20)
        ├── GitHubAdapter   → `gh issue list --label=blocker` via subprocess
        └── TasksAdapter    → DB query: top 5 tasks by sort_order/status (inbox, active)
        │
        ▼
BriefFormatter → markdown card string
        │
        ├── /api/brief/today (on-demand trigger, returns {markdown, sections, generated_at})
        └── store_message(role="assistant", session_key=BRIEF_CHAT_SESSION_KEY)
                + manager.broadcast_to_session(...)
```

### Normalized Event Schema

Simple Python dataclass — no new DB table needed. All data is read live from existing services.

```python
@dataclass
class BriefItem:
    source: str          # "calendar" | "email" | "github" | "tasks"
    title: str
    detail: Optional[str] = None
    priority: str = "normal"  # "high" | "normal" | "low"
    url: Optional[str] = None
    time: Optional[datetime] = None

@dataclass
class BriefSection:
    name: str            # "Calendar", "Priority Messages", "GitHub Blockers", "Agent Tasks"
    icon: str            # emoji
    items: list[BriefItem]
    error: Optional[str] = None   # filled if adapter failed
    available: bool = True        # False if integration not configured

@dataclass
class DailyBrief:
    generated_at: datetime
    sections: list[BriefSection]
    suggested_plan: str           # 1-3 sentence narrative
```

### Example Output

```markdown
## 🗓 Daily Ops Brief — Tuesday Feb 24

### 📅 Calendar Today
- **10:00–11:00** — Engineering Standup (Zoom)
- **14:00–15:00** — Design Review: Auth System

### 📨 Priority Messages
- `boss@example.com` — "Need PR review ASAP" *(3h ago)*
- `ci@github.com` — "Build failed on main" *(1h ago)*

### 🚧 GitHub Blockers
- [#142](https://github.com/...) — CI fails on main (lobs-server)
- [#88](https://github.com/...) — Merge conflict in feature/auth

### 🤖 Top Agent Tasks
1. **[high]** Implement learning phase 1.3 retry
2. **[medium]** Fix document lifecycle transition
3. **[medium]** Update ARCHITECTURE.md

**Suggested plan:** Start with #142 (CI blocker affects all dev work), then tackle the learning phase retry while CI runs. Design review at 2pm may touch auth — review #88 context beforehand.
```

---

## 3. Key Design Decisions

### No new DB tables
All data is read live. Brief items are ephemeral — they become chat messages in the existing `ChatMessage` table. No schema migrations needed.

### Graceful degradation
Every adapter is wrapped in try/except. If Google Calendar isn't configured, the Calendar section shows `*(not configured)*`. If `gh` CLI fails, GitHub section shows error note. The brief is always produced, even if some sections are empty.

### Session key for brief delivery
The brief is posted to a configurable session via env var `BRIEF_CHAT_SESSION_KEY` (default: `"main"`). This follows the existing `session_key` convention used throughout the chat system. The message role is `"assistant"` with metadata `{"source": "daily_brief", "generated_at": "..."}` so the UI can style it distinctively if desired.

### 8am trigger uses direct engine timer (same pattern as memory maintenance)
The engine already has a `_last_memory_maintenance_date_et` / `_daily_compression_hour_et` direct timer pattern. We follow this exactly: add `_brief_hour_et` and `_last_brief_date_et` to the engine, check them in the control loop, and persist the marker to `OrchestratorSetting`. This avoids a DB seed step (simpler) and is consistent with the existing pattern. The RoutineRunner approach was considered but rejected as over-engineering for a single daily trigger.

### On-demand endpoint
`GET /api/brief/today` lets Rafe (or the UI) trigger the brief anytime. Returns JSON with `markdown` string and structured `sections`. Optional `?send_to_chat=true` param injects it into the chat thread. This is useful for testing and for a "Refresh Brief" button in Mission Control.

---

## 4. Source Adapters — Detailed Specs

### CalendarAdapter
```python
async def fetch(self) -> BriefSection:
    # 1. Try GoogleCalendarService.get_rafe_schedule(days=1)
    #    → filter events where start_time is today (ET)
    # 2. Fallback: query internal ScheduledEvent table for today
    # 3. Return BriefItems sorted by start time
    # Error: mark section.error, section.available = False if not configured
```

### EmailAdapter
```python
async def fetch(self) -> BriefSection:
    # 1. Call EmailService.get_unread(max_results=20)
    # 2. Filter: sender in priority list OR subject contains urgent keywords
    #    Priority keywords: "urgent", "ASAP", "blocker", "critical", "action required"
    # 3. Return top 5 items sorted by recency
    # Note: EmailService already handles Gmail API + IMAP fallback
```

### GitHubAdapter
```python
async def fetch(self) -> BriefSection:
    # 1. Run: gh issue list --state=open --label=blocker --json number,title,url,updatedAt --limit=10
    #    (label "blocker" — adjust filter to project conventions)
    # 2. Also check: gh pr list --state=open --review-requested=@me (PRs needing Rafe's review)
    # 3. subprocess with 15s timeout; catch FileNotFoundError for missing gh CLI
    # 4. Return items sorted by updatedAt desc
```

### TasksAdapter
```python
async def fetch(self) -> BriefSection:
    # DB query: tasks WHERE status IN ('inbox', 'active') 
    #           ORDER BY sort_order ASC, created_at ASC 
    #           LIMIT 5
    # Note: priority_score is in the model but NOT the actual DB schema.
    # Use sort_order + created_at. All task items use priority='normal'.
    # This always works (no external dependency)
```

---

## 5. Implementation Plan

### Task 1 — BriefService + Adapters (medium)
**File:** `app/services/brief_service.py`

Create:
- `BriefItem`, `BriefSection`, `DailyBrief` dataclasses
- `CalendarAdapter`, `EmailAdapter`, `GitHubAdapter`, `TasksAdapter` classes
- `BriefService.generate(db)` → calls all adapters concurrently (asyncio.gather), returns `DailyBrief`
- `BriefFormatter.to_markdown(brief: DailyBrief) -> str` — renders the markdown card
- `BriefFormatter.suggest_plan(brief: DailyBrief) -> str` — simple heuristic: high-priority items first, blockers before meetings

**Acceptance criteria:**
- `BriefService.generate()` returns `DailyBrief` even when all adapters fail
- Each failed adapter has `section.error` set, not an exception raised
- `BriefFormatter.to_markdown()` produces valid markdown with all populated sections

### Task 2 — `/api/brief/today` endpoint (small)
**Files:** `app/routers/brief.py`, `app/main.py`

Create router with:
```python
GET /api/brief/today?send_to_chat=false
→ {"markdown": "...", "sections": [...], "generated_at": "..."}
```

- Wire into `main.py` like all other routers
- `send_to_chat=true` writes to `BRIEF_CHAT_SESSION_KEY` (env var, default `"main"`)
  - Use `store_message(session_key=..., role="assistant", content=markdown, metadata={"source": "daily_brief"})` 
  - Use `manager.broadcast_to_session(...)` to push to connected WebSocket clients

**Acceptance criteria:**
- `GET /api/brief/today` returns 200 with `markdown` key
- `GET /api/brief/today?send_to_chat=true` stores a ChatMessage with role=assistant
- Auth required (Bearer token)

### Task 3 — 8am direct engine timer (small)
**Files:** `app/orchestrator/engine.py`, `app/orchestrator/runtime_settings.py`

**Approach: direct timer pattern** — same as memory maintenance (lines 574-602 of engine.py). Simpler than RoutineRunner + DB seed; avoids a migration step entirely.

**Step A — app/orchestrator/runtime_settings.py**

Add two new constants alongside the `SETTINGS_KEY_DAILY_COMPRESSION_*` entries:
```python
SETTINGS_KEY_DAILY_BRIEF_HOUR_ET = 'orchestrator.daily_brief.hour_et'
SETTINGS_KEY_DAILY_BRIEF_LAST_DATE_ET = 'orchestrator.daily_brief.last_date_et'
```
Add to DEFAULT_RUNTIME_SETTINGS:
```python
SETTINGS_KEY_DAILY_BRIEF_HOUR_ET: 8,
SETTINGS_KEY_DAILY_BRIEF_LAST_DATE_ET: '',
```

**Step B — app/orchestrator/engine.py**

In `__init__`: add `self._brief_hour_et = 8` and `self._last_brief_date_et: str | None = None`

In `_refresh_runtime_settings()`: load both new keys following the same pattern as `SETTINGS_KEY_DAILY_COMPRESSION_HOUR_ET` and `maint_marker_key`.

In the control loop, add AFTER the memory maintenance block:
```python
if (
    self._last_brief_date_et != today_key_et
    and now_et.hour >= self._brief_hour_et
):
    self._last_brief_date_et = today_key_et
    await self._run_daily_brief(db, today_key_et)
```

Add private method `_run_daily_brief(self, db, today_key)`:
1. Import BriefService/BriefFormatter from `app.services.brief_service`
2. Call `BriefService(db).generate()` → `DailyBrief`
3. Format markdown, call `store_message` + `manager.broadcast_to_session`
4. Persist marker to `OrchestratorSetting(key=SETTINGS_KEY_DAILY_BRIEF_LAST_DATE_ET, value=today_key)`
5. Logs `[BRIEF] Daily ops brief posted` on success
6. All wrapped in try/except — never raises

**Acceptance criteria:**
- Brief fires once per day at hour >= `_brief_hour_et` (default 8) ET
- `_last_brief_date_et` persisted to `OrchestratorSetting` — survives server restart without re-firing
- Engine crash-safe — exceptions in `_run_daily_brief` caught and logged with `[BRIEF]`
- `_brief_hour_et` and `_last_brief_date_et` appear in orchestrator status endpoint response

### Task 4 — Tests (small)
**File:** `tests/test_brief_service.py`

Write:
1. Unit test `BriefFormatter.to_markdown()` with a constructed `DailyBrief` (all sections populated)
2. Unit test `BriefFormatter.to_markdown()` with all sections errored (degraded mode)
3. Unit test `TasksAdapter.fetch()` with mocked DB (no external deps)
4. Integration test `GET /api/brief/today` returns 200 (use test client, mock adapters)

---

## 6. Tradeoffs Considered

| Option | Choice | Reason |
|--------|--------|--------|
| New DB table for `BriefItem` | No — use dataclasses | Brief items are ephemeral; message history suffices |
| Full integration plugin system | No — simple adapters | Plugin system ADR exists but isn't built yet; adapters are the simplest path |
| Async adapters with `asyncio.gather` | Yes | Parallel fetching keeps latency low (~1-2s total) |
| Hard-code session key "main" | No — use env var | "main" is the obvious default but may vary per deployment |
| Store brief in dedicated table | No — post as chat message | Reuses existing UI rendering and WebSocket delivery |
| Engine direct timer vs RoutineRunner | Direct timer (memory maintenance pattern) | Simpler, no DB seed required, consistent with existing memory_maintenance implementation |
| GitHub label filter ("blocker") | Configurable via env var `GITHUB_BLOCKER_LABEL` (default: "blocker") | Projects use different label conventions |

---

## 7. Testing Strategy

**Unit tests (no network):**
- `BriefFormatter` with mock data — verify markdown structure
- `TasksAdapter` with mock async DB session
- Each adapter handles service-not-configured gracefully

**Integration tests:**
- `GET /api/brief/today` with TestClient
- Calendar + email adapters skipped when env vars absent (graceful degradation)

**Manual verification:**
- After implementation, hit `GET /api/brief/today?send_to_chat=true` and verify message appears in chat UI
- Check that 8am trigger fires once (not twice on restart)

---

## 8. Files to Create/Modify

| File | Action |
|------|--------|
| `app/services/brief_service.py` | **Create** |
| `app/routers/brief.py` | **Create** |
| `app/main.py` | **Modify** — add brief router import |
| `app/orchestrator/engine.py` | **Modify** — add `_brief_hour_et` timer and `_run_daily_brief` method |
| `app/orchestrator/runtime_settings.py` | **Modify** — add `SETTINGS_KEY_DAILY_BRIEF_*` constants |
| `tests/test_brief_service.py` | **Create** |

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| GCal/email not configured | Adapter marks section unavailable, brief still generates |
| `gh` CLI not installed | Catch `FileNotFoundError`, mark GitHub section unavailable |
| Brief session key wrong | Env var `BRIEF_CHAT_SESSION_KEY` with clear docs; on-demand endpoint for testing |
| 8am trigger fires on every restart before 8am | Date key `_last_brief_date_et` prevents re-firing (same pattern as memory maintenance) |
| Slow adapter (email IMAP) blocks others | `asyncio.gather` with per-adapter timeout (10s) |
