# Task Improvements Roadmap — Implementation Notes

Implemented in this change set:

## Phase 0.5 — GitHub Issues source-of-truth + two-way sync/conflicts
- Enhanced `POST /api/projects/{project_id}/github-sync`:
  - Imports issues into tasks (`external_source=github`, `external_id`, `external_updated_at`)
  - Updates existing mapped tasks from GitHub
  - Detects simple bidirectional conflicts (`sync_state=conflict`, `conflict_payload`)
  - Optional push path (`push=true`) gated by `GITHUB_SYNC_PUSH_ENABLED`
- Added task sync metadata fields for conflict-aware workflows.

## Phase 1 — Project-optional tasks, default inbox, intent router v1, knowledge core
- Tasks can be created without `project_id`; server auto-assigns to default inbox project (`DEFAULT_INBOX_PROJECT_ID`, default `inbox`).
- Added intent router v1 endpoint: `POST /api/intent/route`.
- Added `knowledge_requests` model + governance APIs.

## Phase 2 — Workspace tenancy foundation + files API/link graph
- Added workspace models:
  - `workspaces`
  - `workspace_files`
  - `file_links`
- Added APIs under `/api/workspaces` to CRUD/list tenancy entities and link graph edges.

## Phase 3 — Agent profile registry/prompt config + routine registry/policy tiers
- Added models:
  - `agent_profiles` (prompt template + policy tier)
  - `routine_registry` (routine metadata + policy tier)
- Added governance APIs under `/api/governance`.

## Phase 4 — Research → Knowledge migration/backfill
- Added migration script: `migrations/phase4_research_to_knowledge.py`
- Added API backfill endpoint:
  - `POST /api/governance/knowledge-requests/backfill-from-research`

## Compatibility / Breaking Risk
- Existing task/project/research endpoints remain unchanged in shape.
- New fields are additive and optional.
- GitHub sync behavior is enhanced but still returns the existing high-level sync status.

## Follow-ups
- Move sync and conflict rules to a dedicated service layer with stronger deterministic merge policy.
- Add per-field conflict resolution endpoints and audit logs.
- Add pagination/filtering for workspaces/governance endpoints.
- Add explicit migration runner orchestration and idempotent migration registry.
