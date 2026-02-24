# Daily Ops Brief — Design Document

**Status:** Ready for implementation  
**Author:** Architect  
**Date:** 2026-02-24  
**Task ID:** bc10aeab-75e0-4ac8-9df3-e78bffcc1b77

---

## Problem Statement

Rafe spends non-trivial time each morning mentally assembling context from disparate sources: Google Calendar, email, GitHub issues, and the agent task queue. This design delivers an automated 8am summary directly into the assistant chat thread, aggregating all four sources into a single markdown card.

Secondary goal: prove that the integration layer (existing Google Calendar, Gmail, GitHub services) can be composed into user-facing value quickly.

---

## Proposed Solution

### Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│  RoutineRunner (engine.py, every 60s poll)              │
│  → triggers "daily_ops_brief" hook at 8am ET daily     │
│         │                                               │
│         ▼                                               │
│  BriefService.assemble(db)                              │
│  ├── GoogleCalendarService.get_rafe_schedule(days=1)   │
│  ├── EmailService.list_messages(unread=True, limit=20) │
│  ├── gh issue list --state open (GitHub blockers)      │
│  └── DB query: top 3 tasks by priority                 │
│         │                                               │
│         ▼                                               │
│  Format markdown card                                   │
│         │                                               │
│         ▼                                               │
│  Persist ChatMessage (role=assistant) + WS broadcast   │
└─────────────────────────────────────────────────────────┘

Also exposed as:
  GET  /api/brief/today        → JSON data (all sections)
  POST /api/brief/today/send   → generate + post to chat
```

### New Files

| File | Purpose |
|------|---------|
| `app/services/brief_service.py` | Core aggregation logic |
| `app/routers/brief.py` | `/api/brief/today` endpoints |

### Modified Files

| File | Change |
|------|--------|
| `app/orchestrator/engine.py` | Register `daily_ops_brief` hook in RoutineRunner |
| `app/main.py` | Include `brief.router` |

### No New DB Tables

Brief data is fetched fresh at runtime. `RoutineAuditEvent` already captures result JSON as a side-effect of routine execution. No schema migration needed.

---

## BriefService Design (`app/services/brief_service.py`)

```python
class BriefService:
    def __init__(self, db: AsyncSession): ...
    
    async def assemble(self, target_date: date | None = None) -> BriefData:
        """Gather all sections. Sections that fail degrade gracefully."""
        
    async def format_markdown(self, data: BriefData) -> str:
        """Render BriefData to markdown card string."""
        
    async def post_to_chat(self, markdown: str, session_key: str = "assistant") -> str:
        """Persist ChatMessage(role='assistant') + broadcast via manager. Returns message id."""
```

**BriefData** is a plain dataclass:
```python
@dataclass
class BriefData:
    date: date
    calendar_events: list[CalendarEvent]  # today's time blocks
    priority_emails: list[EmailSummary]   # unread priority emails
    github_blockers: list[GitHubIssue]    # open blocker-labeled issues
    top_tasks: list[TaskSummary]          # top 3 agent tasks by priority
    errors: list[str]                     # sections that failed (degrade gracefully)
```

### Section Specs

**Calendar events:**
- Call `GoogleCalendarService.get_rafe_schedule(days=1)`, filter to today only
- Include: title, start_time, end_time, location (if any)
- If Google Calendar not configured: skip section, note in errors

**Priority emails:**
- Call `EmailService.list_messages()` — get unread emails
- Filter heuristic: `is:unread is:important` (Gmail IMPORTANT label)
- Limit to 5 most recent
- Include: sender name/email, subject, snippet (first 100 chars)
- If email not configured: skip, note in errors

**GitHub blockers:**
- Run `gh issue list --state open --label blocker --json number,title,url,assignees`
- Across all GitHub-tracked projects in the DB
- Deduplicate by issue URL
- Limit to 10
- If `gh` not available or no projects: skip, note in errors

**Top agent tasks:**
- DB query: tasks where `status IN ('pending', 'in_progress')` AND `priority IN ('high', 'urgent')` (or any priority if fewer than 3 high/urgent), ordered by priority desc then created_at desc, limit 3
- Include: title, project name, status, assigned_agent

### Markdown Card Format

```markdown
## 📋 Daily Ops Brief — {date}

### 📅 Today's Calendar
- **9:00–10:00** — Weekly standup (Google Meet)
- **14:00–15:30** — Code review session
*(no events today)* ← if empty

### 📧 Priority Email ({count} unread)
- **Alice Smith** — "Re: Deploy blocked" — *needs your sign-off on...*
- **GitHub** — "PR #142 approved" — *Your PR was reviewed...*
*(no priority emails)* ← if empty

### 🚫 GitHub Blockers ({count})
- [#88](url) — Cannot deploy to prod (unassigned)
*(no open blockers)* ← if empty

### ✅ Top Agent Tasks
1. **[high]** Implement auth middleware — *programmer* (in_progress)
2. **[high]** Fix memory search latency — *researcher* (pending)
3. **[normal]** Update CHANGELOG — *writer* (pending)

---
*Generated at {time} ET · [Refresh](/api/brief/today/send)*
```

---

## Router Design (`app/routers/brief.py`)

```python
GET  /api/brief/today
     → BriefService.assemble()
     → return BriefData as JSON (all sections)
     → query param: ?date=YYYY-MM-DD (default: today)

POST /api/brief/today/send
     → BriefService.assemble()
     → BriefService.post_to_chat()
     → return {"status": "sent", "message_id": "..."}
     → query param: ?session_key=assistant (default: "assistant")
```

---

## Routine Hook Registration

In `engine.py`, the RoutineRunner hooks dict already accepts arbitrary keys:

```python
runner = RoutineRunner(db, hooks={
    "noop": ...,
    "daily_ops_brief": _make_daily_brief_hook(db),
})
```

The hook function:
```python
async def _daily_brief_hook(routine: RoutineRegistry) -> dict:
    svc = BriefService(db)
    data = await svc.assemble()
    md = await svc.format_markdown(data)
    msg_id = await svc.post_to_chat(md)
    return {"status": "ok", "message_id": msg_id, "errors": data.errors}
```

### RoutineRegistry DB Seed

The routine must be seeded into the `routine_registry` table. The programmer should add a migration script or use the existing `/api/routines` endpoint (if it exists) to insert:

```json
{
  "key": "daily_ops_brief",
  "label": "Daily Ops Brief",
  "description": "8am daily summary: calendar, email, GitHub blockers, top tasks",
  "schedule": "0 13 * * *",
  "timezone": "America/New_York",
  "execution_policy": "auto",
  "enabled": true
}
```

Note: `0 13 * * *` = 1pm UTC = 8am ET (EST). In EDT (summer), use `0 12 * * *`. **Best approach:** store in UTC. The programmer should check if the RoutineRegistry model supports timezone-aware cron or if UTC offset is manual.

**Simpler alternative:** Seed with `0 13 * * *` and add a comment in the code. If Rafe wants timezone-aware scheduling, that's a separate feature.

---

## Tradeoffs

### Real-time fetch vs cached brief
- **Chose: real-time** — Calendar/email/GitHub change constantly. Cache would go stale. Acceptable performance for once-daily generation.

### LLM summarization vs template formatting
- **Chose: template formatting** — Faster, cheaper, more reliable, no model dependency. The data is already structured; a template produces a perfectly readable card. LLM can optionally be added later as a "smart summary" layer.

### New router vs extending integrations.py
- **Chose: new router** — `/api/brief/today` is a first-class user-facing feature. Integrations.py is plumbing. Separation keeps each file focused.

### Graceful degradation
- Each section is fetched independently. If Google Calendar credentials expired, we still deliver the GitHub + task sections. Errors are listed at the bottom of the brief. **This is the most important reliability tradeoff.**

### Timezone handling
- Cron stored in UTC, hardcoded offset for now. The scheduler already uses `compute_next_fire_time()` with croniter; no change needed. If summer/winter offset matters, Rafe can adjust via RoutineRegistry update.

---

## Testing Strategy

### Unit tests (brief_service.py)

1. `test_assemble_all_sources_ok` — mock all services, verify BriefData fields populated
2. `test_assemble_calendar_fails` — mock calendar raising exception, verify error in data.errors and other sections still populated
3. `test_assemble_email_fails` — same for email
4. `test_assemble_github_fails` — same for GitHub
5. `test_format_markdown_full` — verify markdown output contains expected sections
6. `test_format_markdown_empty_sections` — verify "no events today" fallbacks render correctly
7. `test_top_tasks_query` — seed 5 tasks with varying priorities, verify top 3 returned correctly

### Integration tests (test_brief_router.py)

1. `test_get_brief_today` — GET /api/brief/today returns 200 with expected JSON shape
2. `test_post_brief_send` — POST /api/brief/today/send creates a ChatMessage in DB
3. `test_brief_no_auth` — returns 401 without token

### Manual smoke test

1. Call `POST /api/brief/today/send` via curl
2. Verify message appears in chat with all 4 sections (or graceful skips)
3. Verify RoutineAuditEvent shows `status=ok`

---

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| Google Calendar OAuth expired | Medium | Section skips with error note; existing pattern in integrations.py |
| `gh` CLI not in PATH at runtime | Low | Subprocess fallback with clear error message |
| Email service IMAP/SMTP auth failure | Medium | Skip section, log, continue |
| Cron fires at wrong time (DST) | Low | Document the UTC offset; trivially adjustable |
| Chat session "assistant" doesn't exist yet | Low | `ensure_session_exists()` pattern already in chat.py — replicate it |

---

## Implementation Plan

Ordered tasks for the programmer:

1. **`app/services/brief_service.py`** (medium) — Core aggregation + markdown formatting
   - BriefData dataclass
   - assemble() with graceful degradation per section
   - format_markdown()
   - post_to_chat() using DB write + manager.broadcast_to_session()
   - See chat.py for the exact pattern to persist ChatMessage and broadcast

2. **`app/routers/brief.py`** (small) — Two endpoints
   - GET /brief/today
   - POST /brief/today/send

3. **`app/main.py`** (trivial) — Include brief.router

4. **`app/orchestrator/engine.py`** (small) — Register `daily_ops_brief` hook in RoutineRunner hooks dict

5. **DB seed: RoutineRegistry** (small) — Add seed script or migration to insert the daily_ops_brief routine at 8am ET

6. **Tests** (`tests/test_brief_service.py`, `tests/test_brief_router.py`) (medium) — Unit + integration tests per testing strategy above

**All 6 tasks can be done in a single programmer session.** They're tightly coupled (the service is referenced by both the router and the engine hook) and collectively form the complete vertical slice.
