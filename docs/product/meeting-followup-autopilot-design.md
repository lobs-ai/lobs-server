# Meeting Follow-up Autopilot — Architecture Design

**Date:** 2026-02-25  
**Author:** architect  
**Status:** Draft — ready for implementation  
**Initiative:** 5b9744e0-f76d-4c0c-88dd-f62e0acae1bd  

---

## 1. Problem Statement

Engineering teams run meetings (standups, sprint planning, retros, 1:1s) and consistently lose track of the action items that come out of them. The pain:

- Action items live in someone's head or a Notion doc nobody checks
- No clear owner assigned, so everyone assumes someone else will do it
- No nudges → tasks drift, get forgotten, pile up to the next meeting
- Re-reviewing the same blockers week after week

**Target User:** Small engineering teams (5–25 engineers) using Linear + Slack that run regular syncs and need accountability without a dedicated PM.

---

## 2. Proposed Solution — Meeting Follow-up Autopilot

A standalone micro-SaaS (separate deployable service) that:

1. **Accepts** meeting notes/transcript (text paste, Markdown, or raw transcript)
2. **Extracts** action items with proposed owners and optional deadlines using AI
3. **Routes** each action item to the right place: Linear ticket + Slack DM/channel message
4. **Nudges** owners automatically if the ticket is unresolved after a configurable delay (default T+24h)

### MVP Flow

```
┌─────────────────────────────────────────────────────────────┐
│                   MEETING INPUT                             │
│                                                             │
│  User pastes notes / transcript into web UI or API          │
│  Fields: meeting_title, date, participants (optional),      │
│          raw_text                                           │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│              EXTRACTION WORKER (AI)                         │
│                                                             │
│  Standard-tier model (GPT-4 or equivalent)                 │
│  System prompt → structured JSON output                     │
│                                                             │
│  Output schema:                                             │
│  { action_items: [                                          │
│      { text, owner_name, deadline, priority }               │
│  ]}                                                         │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│              OWNER RESOLVER                                 │
│                                                             │
│  Fuzzy-match owner_name → TeamMember in workspace directory │
│  Unresolved → flag as "unassigned" (don't block)           │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│              INTEGRATION HUB                                │
│                                                             │
│  For each resolved action item:                             │
│  1. Create Linear issue (title, assignee, due_date)         │
│  2. Post Slack summary to configured channel                │
│  3. Send Slack DM to assigned owner                         │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│              FOLLOW-UP SCHEDULER                            │
│                                                             │
│  Cron job (every hour): check action_items where            │
│  status=open AND follow_up_due_at < NOW()                   │
│  → Send nudge via Slack DM + optional email                 │
│  → Mark follow_up_sent_at                                   │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. System Architecture

### 3.1 Deployment

**Standalone service** — separate git repo (`meeting-autopilot`), not embedded in lobs-server.

**Why separate:**
- Clean product boundary — can be sold/deployed independently
- Different deployment lifecycle from lobs-server
- Simpler for potential open-source / SaaS distribution

**Shared patterns from lobs-server:**
- FastAPI + SQLAlchemy async + aiosqlite (identical tech stack)
- Bearer token auth per workspace
- Same model routing tier system (call OpenClaw Gateway via HTTP)
- Same background task pattern for follow-up scheduler

### 3.2 Tech Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| API Framework | FastAPI | Same as lobs-server; proven pattern |
| Database | SQLite + aiosqlite | Single-file, zero ops for MVP |
| ORM | SQLAlchemy (async) | Consistent with lobs-server |
| AI | OpenClaw Gateway (standard tier) | Reuse existing model routing |
| Slack | Slack Bolt SDK (Python) | First-class Python support |
| Linear | Linear Python SDK / REST API | Simple REST with API key auth |
| Email | SMTP via `aiosmtplib` | Zero-dependency, provider-agnostic |
| Background jobs | APScheduler (in-process) | Simple cron; upgrade to Celery if needed |
| Frontend (MVP) | Single HTML page + htmx | Minimal; form + results, no JS framework needed |

### 3.3 Database Schema

```sql
-- Tenant/org unit
CREATE TABLE workspaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    api_key     TEXT NOT NULL UNIQUE,  -- hashed
    created_at  TIMESTAMP NOT NULL
);

-- Team member directory (manually populated in v1)
CREATE TABLE team_members (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT REFERENCES workspaces(id),
    name            TEXT NOT NULL,          -- display name
    email           TEXT,
    slack_user_id   TEXT,                   -- e.g. U1234567
    linear_user_id  TEXT,
    created_at      TIMESTAMP NOT NULL
);

-- Integration credentials per workspace
CREATE TABLE integrations (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT REFERENCES workspaces(id),
    provider        TEXT NOT NULL,  -- 'slack', 'linear', 'email'
    config          TEXT NOT NULL,  -- JSON blob of provider-specific config
    created_at      TIMESTAMP NOT NULL,
    UNIQUE(workspace_id, provider)
);

-- Raw meeting input
CREATE TABLE meetings (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT REFERENCES workspaces(id),
    title           TEXT,
    meeting_date    DATE,
    raw_text        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'processing',  -- processing | done | failed
    created_at      TIMESTAMP NOT NULL
);

-- Extracted action items
CREATE TABLE action_items (
    id                  TEXT PRIMARY KEY,
    meeting_id          TEXT REFERENCES meetings(id),
    workspace_id        TEXT REFERENCES workspaces(id),
    text                TEXT NOT NULL,
    owner_id            TEXT REFERENCES team_members(id),  -- NULL if unresolved
    owner_name_raw      TEXT,       -- as extracted from notes
    deadline            DATE,
    priority            TEXT,       -- high | medium | low
    status              TEXT NOT NULL DEFAULT 'open',  -- open | done | cancelled
    linear_issue_id     TEXT,
    slack_message_ts    TEXT,
    follow_up_due_at    TIMESTAMP,  -- when to send nudge
    follow_up_sent_at   TIMESTAMP,  -- NULL until nudge sent
    created_at          TIMESTAMP NOT NULL
);

-- Audit log for integration events
CREATE TABLE integration_events (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT REFERENCES workspaces(id),
    action_item_id  TEXT REFERENCES action_items(id),
    provider        TEXT NOT NULL,
    event_type      TEXT NOT NULL,  -- created | notified | nudged | failed
    payload         TEXT,           -- JSON
    created_at      TIMESTAMP NOT NULL
);
```

### 3.4 API Endpoints

```
POST   /api/meetings          — Submit meeting notes for processing
GET    /api/meetings/{id}     — Get meeting + extracted action items
GET    /api/meetings          — List meetings (paginated)

GET    /api/action-items      — List items (filter: status, owner, meeting)
PATCH  /api/action-items/{id} — Update status (open/done/cancelled)

GET    /api/team              — List team members
POST   /api/team              — Add team member
DELETE /api/team/{id}         — Remove team member

GET    /api/integrations      — List configured integrations
PUT    /api/integrations/{provider} — Upsert integration config

GET    /api/health            — Health check (no auth)
```

### 3.5 AI Extraction Design

**System prompt approach** (single-shot, no multi-agent for MVP):

```
You are an expert meeting facilitator. Given raw meeting notes or a transcript,
extract all action items mentioned.

For each action item return:
- text: clear, actionable description
- owner_name: person assigned (or null if unspecified)
- deadline: ISO date if mentioned (or null)
- priority: "high" | "medium" | "low" based on urgency language

Return ONLY valid JSON:
{"action_items": [...]}

Do not invent items that weren't mentioned. If unsure about owner, return null.
```

**Validation:** Parse JSON response; retry once with corrective prompt on parse failure.  
**Model tier:** `standard` (needs quality; this is the core value-add).

### 3.6 Owner Resolution

Simple fuzzy matching:
1. Exact match on `team_members.name` (case-insensitive)
2. Contains match (e.g., "Rafe" → "Rafe Symonds")
3. No match → `owner_id = NULL`, `owner_name_raw` preserved

Unresolved items still get created but are flagged in the UI for manual assignment. This avoids blocking the entire flow on a single unresolved name.

### 3.7 Follow-up Scheduler

APScheduler runs inside the FastAPI process (same lifespan pattern as lobs-server's memory maintenance):

```python
# Runs every 30 minutes
async def send_pending_followups():
    items = await db.query(ActionItem).filter(
        ActionItem.status == 'open',
        ActionItem.follow_up_due_at <= now(),
        ActionItem.follow_up_sent_at == None
    )
    for item in items:
        await slack.send_dm(item.owner.slack_user_id, nudge_message(item))
        item.follow_up_sent_at = now()
```

Default nudge delay: 24 hours from creation. Configurable per workspace.

---

## 4. Tradeoffs

### Build separate service vs extend lobs-server

**Chose:** Separate service.

Why: lobs-server is personal infrastructure. Meeting Autopilot targets external paying customers. Mixing tenant data into personal backend creates auth/isolation complexity and limits independently deploying the product.

Cost: More setup work, duplicate some boilerplate.

### SQLite vs Postgres

**Chose:** SQLite for MVP.

Why: Zero ops, single file, perfect for early-stage product with few tenants. Clear upgrade path: swap SQLAlchemy DB URL, no schema changes needed.

Cost: Can't scale horizontally. Accept this for MVP.

### APScheduler vs dedicated queue (Celery/RQ)

**Chose:** APScheduler in-process.

Why: Simplest thing that works. Follow-ups are low-volume (a few nudges per hour). No need for distributed task queue at MVP scale.

Cost: Scheduler lost on restart (brief). Nudges delayed by up to 30 minutes in worst case. Acceptable.

### Single-pass AI vs multi-agent pipeline

**Chose:** Single-pass extraction for MVP.

Why: Multi-agent (separate extraction + enrichment + validation agents) adds significant complexity with modest quality gain for meeting notes. A well-designed single prompt reliably extracts structured data.

Upgrade path: If quality is a persistent issue, add a validation pass.

### htmx minimal UI vs React

**Chose:** htmx for MVP.

Why: This product's core value is the API + AI pipeline, not the UI. htmx lets us ship a functional UI in a day without JS toolchain. Easy to replace with React later if needed.

---

## 5. Implementation Plan

### Phase 1 — Core Pipeline (Week 1-2)

**1a. Project scaffolding + database** (programmer, small)
- Create repo `meeting-autopilot` with FastAPI skeleton
- SQLAlchemy models for all 6 tables
- Database init on startup
- Health endpoint

**1b. AI Extraction Worker** (programmer, medium)
- `MeetingParser` class: accepts raw text, calls OpenClaw Gateway
- Structured JSON output + retry on parse failure
- Unit tests: 5 fixture transcripts with expected action items
- Edge cases: empty transcript, no action items, no owners

**1c. Owner Resolver** (programmer, small)
- `OwnerResolver` class: fuzzy match owner_name_raw → TeamMember
- Unit tests: exact, fuzzy, no-match scenarios

**1d. Meeting API endpoints** (programmer, medium)
- POST /api/meetings — ingest + trigger async extraction
- GET /api/meetings/{id} — return meeting + action items
- Integration test: POST → poll → verify items extracted

### Phase 2 — Integrations (Week 2-3)

**2a. Linear integration** (programmer, medium)
- `LinearConnector`: create issue with title, assignee, due_date
- Store linear_issue_id on action_item
- Mock in tests; integration test requires Linear API key

**2b. Slack integration** (programmer, medium)
- `SlackConnector`: post channel summary, DM per owner
- Store slack_message_ts on action_item
- Mock in tests

**2c. Follow-up scheduler** (programmer, small)
- APScheduler job: check pending follow-ups, send Slack DM nudges
- Mark follow_up_sent_at after sending
- Unit tests: verify nudge sent when due, not sent if done

### Phase 3 — Auth + Team Directory + UI (Week 3)

**3a. Auth middleware + workspace isolation** (programmer, small)
- Bearer token per workspace, validated on all endpoints
- Row-level filtering on workspace_id throughout

**3b. Team directory endpoints** (programmer, small)
- CRUD for team_members
- Integration config CRUD (Slack token, Linear API key)

**3c. Minimal htmx UI** (programmer, medium)
- Single-page form: paste meeting notes, submit
- Results view: table of extracted action items with owners
- Status badges and manual status update

---

## 6. Testing Strategy

### Unit Tests
- `MeetingParser`: 5+ fixture transcripts → expected JSON output (use recorded AI responses, not live calls)
- `OwnerResolver`: matrix of name strings vs member directory
- `FollowUpScheduler`: time-mocked tests for nudge timing logic

### Integration Tests
- End-to-end: POST /api/meetings → poll GET until done → verify action items
- Slack/Linear: mock external HTTP calls; verify correct API calls made

### Manual Smoke Test Checklist
1. Create workspace + API key
2. Add 3 team members
3. Configure Slack integration with bot token
4. POST meeting notes from a real engineering standup
5. Verify action items extracted correctly
6. Verify Slack channel message posted
7. Verify Slack DM sent to assigned owner
8. Wait for follow-up window or manually trigger → verify nudge sent

---

## 7. Risks and Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| AI extraction quality poor on noisy transcripts | Medium | Tune prompt with real examples; add retry |
| Linear/Slack APIs change | Low | Pin SDK versions; integration tests catch changes |
| Owner resolution fails frequently | Medium | Allow manual assignment; don't block on mismatch |
| SQLite write contention with concurrent requests | Low | WAL mode + async; fine for MVP scale |
| Follow-up nudges sent too aggressively | Low | Configurable delay per workspace; cap at 1 nudge per item per day |

---

## 8. Future Work (Not in MVP)

- Audio/video transcription via Whisper (paste transcript for MVP)
- Google Calendar webhook → auto-process meetings after they end
- Recurring meeting pattern detection (standup vs planning vs retro context)
- GitHub Issues integration alongside Linear
- Multi-workspace billing / SaaS monetization
- Slack Slash command `/autopilot` for inline submission
- Mobile-friendly UI with PWA

---

## Files to Create

```
meeting-autopilot/
├── README.md
├── requirements.txt
├── app/
│   ├── main.py              — FastAPI app + lifespan (scheduler start)
│   ├── models.py            — SQLAlchemy models (6 tables)
│   ├── database.py          — Async engine + session factory
│   ├── auth.py              — Bearer token middleware
│   ├── routers/
│   │   ├── meetings.py      — Meeting ingestion + retrieval
│   │   ├── action_items.py  — Item management
│   │   ├── team.py          — Team directory CRUD
│   │   └── integrations.py  — Integration config CRUD
│   ├── services/
│   │   ├── meeting_parser.py   — AI extraction
│   │   ├── owner_resolver.py   — Fuzzy name matching
│   │   ├── linear_connector.py — Linear API client
│   │   ├── slack_connector.py  — Slack SDK wrapper
│   │   └── followup_scheduler.py — APScheduler follow-up job
│   └── templates/
│       └── index.html       — htmx UI (single file)
└── tests/
    ├── fixtures/
    │   └── transcripts/     — Sample meeting notes for tests
    ├── test_meeting_parser.py
    ├── test_owner_resolver.py
    ├── test_scheduler.py
    └── test_api.py
```
