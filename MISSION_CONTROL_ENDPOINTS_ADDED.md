# Mission Control API Endpoints — Implementation Summary

## What Was Added

### 1. Sweeps Endpoints (`app/routers/orchestrator_reflections.py`)

**`GET /api/orchestrator/intelligence/sweeps`**
- Lists system sweeps with decision counts
- Query params: `limit` (default 50)
- Response includes: `id`, `sweep_type`, `status`, `summary`, `total_proposed`, `approved_count`, `rejected_count`, `deferred_count`, timestamps
- Handles different summary formats (initiative_sweep vs other sweep types)

**`GET /api/orchestrator/intelligence/sweeps/{sweep_id}`**
- Returns detailed sweep with associated initiative decisions
- Includes full sweep info + array of decisions with initiative context
- Each decision includes: `id`, `initiative_id`, `initiative_title`, `decision`, `decided_by`, `decision_summary`, `task_id`, `created_at`

### 2. Knowledge Endpoints (`app/routers/knowledge.py`) — NEW ROUTER

Serves content from `~/lobs-shared-memory` git repository.

**`GET /api/knowledge`**
- Browse/search knowledge entries
- Query params: `path`, `type`, `tags`, `search`, `limit`
- Classifies files by directory: `research/`, `decisions/`, `design/`, `docs/`
- Returns entries with: `id`, `path`, `title`, `type`, `tags`, `summary`, `content_hash`, timestamps
- Extracts title from first `# Heading` or filename

**`GET /api/knowledge/feed`**
- Recent entries sorted by modification time (newest first)
- Query params: `limit` (default 50), `since` (ISO timestamp)
- Perfect for "what's new" views

**`GET /api/knowledge/content`**
- Read file content
- Query param: `path` (relative to lobs-shared-memory)
- **Security**: Validates path doesn't escape repository root (blocks `../` attacks)
- Returns: `{path, content}`

**`POST /api/knowledge/sync`**
- Triggers `git pull --rebase` in the knowledge base
- Returns: `{status, stdout, stderr, exit_code}`
- 30-second timeout

### 3. Fixed Reflections Response Format (`app/routers/orchestrator_reflections.py`)

**`GET /api/orchestrator/intelligence/reflections`**
- **Changed**: Now returns batch-level reflections (grouped by `window_start`)
- Aggregates data from all agents in the same batch
- Response format:
  ```json
  {
    "reflections": [
      {
        "id": "<first_reflection_id>",
        "batch_id": "<window_start_iso>",
        "agents": ["programmer", "architect", ...],
        "status": "completed",
        "started_at": "...",
        "completed_at": "...",
        "inefficiencies": [...merged from all agents...],
        "missed_opportunities": [...merged...],
        "system_risks": [...merged...],
        "identity_adjustments": [...merged...],
        "proposed_initiatives": [...merged...],
        "error_message": null
      }
    ],
    "total": N
  }
  ```
- Status is "completed" only if all reflections in batch are completed

## Files Modified

- `app/routers/orchestrator_reflections.py` — Added sweeps endpoints, fixed reflections format
- `app/routers/knowledge.py` — NEW FILE (knowledge base API)
- `app/main.py` — Registered knowledge router

## Testing

All endpoints tested successfully:

```bash
# Sweeps list
curl -H "Authorization: Bearer <token>" \
  'http://localhost:8000/api/orchestrator/intelligence/sweeps?limit=50'

# Sweep detail
curl -H "Authorization: Bearer <token>" \
  'http://localhost:8000/api/orchestrator/intelligence/sweeps/{sweep_id}'

# Knowledge browse
curl -H "Authorization: Bearer <token>" \
  'http://localhost:8000/api/knowledge?type=research&limit=100'

# Knowledge feed
curl -H "Authorization: Bearer <token>" \
  'http://localhost:8000/api/knowledge/feed?limit=50'

# Knowledge content
curl -H "Authorization: Bearer <token>" \
  'http://localhost:8000/api/knowledge/content?path=docs/README.md'

# Knowledge sync
curl -X POST -H "Authorization: Bearer <token>" \
  'http://localhost:8000/api/knowledge/sync'

# Reflections (batch format)
curl -H "Authorization: Bearer <token>" \
  'http://localhost:8000/api/orchestrator/intelligence/reflections?limit=50'
```

## Security

- All endpoints require authentication (Bearer token)
- Knowledge content endpoint validates paths to prevent directory traversal
- Git sync uses 30-second timeout to prevent hangs

## Deployment

Server restarted successfully:
```bash
launchctl bootout gui/$(id -u)/com.lobs.server
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lobs.server.plist
```

All endpoints return 200 with correct JSON format.
