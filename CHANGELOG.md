# Changelog

All notable changes to the lobs-server API will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added (2026-02-21 to 2026-02-23)
- **5-Tier Model Hierarchy** — Expanded from 3 tiers (cheap/standard/strong) to 5 tiers (micro/small/medium/standard/strong) for better cost control and local model support
- **Ollama Auto-Discovery** — System automatically detects and injects Ollama models into micro/small/medium tiers based on parameter count
- **Model Tier Field on Tasks** — Added `model_tier` field to Task model for explicit tier control (overrides auto-selection)
- **Token Usage Extraction** — Extract and track token usage from session transcripts via new dashboard endpoint
- **Reflection System Endpoint** — Added `POST /orchestrator/reflection/trigger` for manual reflection cycle triggering
- **Domain-Specific Reflection Prompts** — Custom reflection prompts for programmer, researcher, specialist, and writer agents (targeted insights per agent type)
- **Isolated Reflection Sessions** — Reflection results now sent to isolated Lobs session (not main agent session) to prevent context pollution
- **Broader Proposal Scope** — Reflection system now encourages feature proposals and new project ideas (not just maintenance/fixes)
- **Learning System Stats API** — Added `GET /api/learning/stats` endpoint for A/B test metrics, success rates, and improvement tracking
- **Learning System Health API** — Added `GET /api/learning/health` endpoint for learning system health checks and issue detection
- **Learning Validation CLI** — Added `app/cli/validate_learning.py` tool for offline analysis of learning system effectiveness

### Changed (2026-02-21 to 2026-02-22)
- **Model Tier Refactoring** — Migrated all model routing from 3-tier to 5-tier system
- **Model Hierarchy** — Codex 5.3 now primary model, Sonnet 4.5 fallback, with Gemini/Haiku for cheap tier
- **Local Model Tier** — Removed dedicated "local" tier; local models now auto-injected into micro/small/medium based on size
- **Reflection Isolation** — Reflection cycle spawns isolated session for Lobs review instead of using main session
- **Transcript Path Resolution** — Reflection system uses Gateway API to resolve transcript paths (not hardcoded directories)
- **Reflection Engine** — Uses engine flag instead of sharing DB session (prevents commit conflicts)
- **JSON Extraction** — Improved reflection JSON extraction, field mapping, and validation gate
- **Reflection Summary Delivery** — Sends summaries to `agent:main:main` (not `agent:main`)
- **Session History** — Status checks now use `sessions_history` (fixed DB commit conflict in persist)
- **Transcript Glob** — Prevented transcript glob fallback from matching unrelated sessions

### Fixed (2026-02-21 to 2026-02-22)
- **Reflection System** — Fixed JSON extraction, field mapping, validation gate, and Lobs summary delivery
- **Session Isolation** — Reflection results no longer pollute main agent context
- **DB Commit Conflict** — Fixed `persist()` method to avoid DB session conflicts
- **Transcript Matching** — Prevented unrelated session transcripts from being matched by glob patterns

### Earlier (Before 2026-02-21)
- **Agent Lifecycle Documentation Set** — Added canonical docs for agent lifecycle architecture, operator recruitment/management playbook, and API contracts (`docs/agent-lifecycle-architecture.md`, `docs/agent-operations-playbook.md`, `docs/agent-api-contracts.md`)
- **Task Improvements Roadmap (Phase 0.5–4 core)** — GitHub two-way sync metadata/conflict support, default inbox assignment for project-optional tasks, Intent Router v1, workspace tenancy/files/link graph APIs, governance registries (agent profiles + routines), and knowledge request model/backfill path
- **Concurrent Agent Execution** — Removed agent locks; can now run up to 5 concurrent workers (multiple programmers/writers/etc in parallel on different projects)
- **Enhanced Activity Endpoint** — `GET /api/worker/activity` captures agent result summaries from completed sessions
- **Notification Deduplication** — Tracker reminder notifications now deduplicate to prevent spam
- **Task Creation Deduplication** — Server-side enforcement prevents duplicate task creation via API (removed from PM agent logic)
- Task `work_state` field now accepts 'not_started' as initial state (in addition to 'ready')
- **Agent Concurrency** — Project locks (not agent locks) prevent repo conflicts; MAX_WORKERS=5 allows parallel execution
- **Session Result Capture** — Worker runs now store summary from session history (prefers session result over .work-summary file), truncated to 2000 chars
- **Task Deduplication** — Moved from PM agent prompt logic to server-side API enforcement
- **Agent Announcement Routing** — Worker completion announcements now route to suggester agent (claude-haiku) instead of main agent (opus) for cost efficiency
- **Sink Agent Model** — Uses placeholder Ollama model for zero-cost routing of announcements
- Project-manager agent now handles all inbox processing (inbox-responder agent removed)
- Scanner accepts both 'ready' and 'not_started' for eligible task states
- Scheduler defaults new tasks to `work_state='not_started'`
- Orchestrator no longer creates orchestrator-sink session (lean heartbeats)
- SQLite WAL mode enabled to prevent "database is locked" errors
- Recurring events now properly expand in calendar range endpoint

---

## [2026-02-14] — Recent Features

### Added
- **Worker Result Summaries** — `/api/worker/activity` endpoint captures agent output summaries
- **Tiered Approval System** — Three-tier approval workflow (auto-approve, human review, escalate)
- **Project-Manager Agent** — Centralized coordinator for task routing and inbox processing
- **Topics Auto-Creation** — Researcher agent can autonomously create knowledge topics
- **Document Lifecycle** — State management for research documents (pending/approved/rejected)
- **Work Tracker Analysis** — AI-generated insights from work dumps
- **Calendar Event Types** — Support for categorized events (meeting, deadline, discussion, etc.)

### Changed
- **Task Orchestration** — Two-field system: `work_state` (orchestrator) + `status` (UI/user)
- **Inbox Processing** — Project-manager handles all inbox responses (inbox-responder deprecated)
- **Worker Enforcement** — Workers must produce file changes (doc-only runs rejected)
- **OpenClaw Integration** — Use Gateway `/tools/invoke` with `sessions_spawn` for workers
- **Calendar Range** — Now expands recurring events within requested date range

### Fixed
- Database locking issues (WAL mode + busy timeout)
- Scheduler datetime comparison and duplicate guard
- Memory sync unique constraint (composite index on path + agent)
- Scheduled tasks now create with `status=todo` for orchestrator pickup

---

## Historical Features (Pre-Changelog)

**Core features present before changelog started:**

### REST API
- Projects CRUD
- Tasks CRUD with kanban workflow
- Inbox (proposals and suggestions)
- Documents and Topics (knowledge system)
- Memories (daily notes, long-term memory, search)
- Chat (sessions, messages)
- Calendar (events, recurring schedules)
- System status and health monitoring
- Agent management
- Backup system

### WebSocket
- Real-time chat messaging
- Live system updates

### Orchestrator
- Automatic task scanning and delegation
- Worker spawning via OpenClaw Gateway
- Failure detection and escalation
- Background jobs (memory sync, schedule expansion, backups)

### Authentication
- Bearer token system
- Token management scripts (generate, list, revoke)

---

## API Stability

**Current Status:** Internal API (no external consumers)

**Breaking Changes:** Not tracked until public release

**Deprecation Policy:** None yet — internal use only

---

## Contributing

### When to Update This File

**Always update CHANGELOG.md when:**
- Adding new endpoints
- Changing request/response schemas
- Deprecating endpoints or fields
- Fixing bugs that affect API behavior
- Changing authentication or authorization

**Add entries under `[Unreleased]` section using these categories:**
- **Added** — New endpoints, fields, features
- **Changed** — Modifications to existing behavior
- **Deprecated** — Features marked for removal
- **Removed** — Deleted endpoints or features
- **Fixed** — Bug fixes that affect API
- **Security** — Security-related changes

**On release** (if we start versioning):
- Move `[Unreleased]` items to new version section
- Create new `[Unreleased]` section

---

## Notes

- **Date format:** YYYY-MM-DD
- **API versioning:** Not implemented yet (all endpoints at `/api/*`)
- **Version numbers:** Not assigned yet (internal development)
- **This changelog started:** 2026-02-14

For implementation details and internal changes, see git commit history.
