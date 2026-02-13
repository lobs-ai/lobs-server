# Document Lifecycle: Writer Agent Knowledge Maintenance

## Problem

Documents accumulate without maintenance. Related documents within a topic aren't merged, duplicates aren't consolidated, and there's no way to track when a document was last reviewed/maintained. The writer agent needs API support to perform knowledge base maintenance tasks.

## Proposed Solution

Three incremental changes:

### 1. `last_maintained_at` Field on AgentDocument

Add `last_maintained_at` (DateTime, nullable) to the `agent_documents` table and schemas. Updated whenever a document is edited or involved in a merge. This lets the orchestrator find stale documents that need maintenance.

### 2. Document Merge Endpoint

`POST /api/docs/merge` — merges 2+ documents into one.

**Request:**
```json
{
  "source_document_ids": ["doc-1", "doc-2", "doc-3"],
  "target_title": "Merged: API Patterns",
  "target_content": "... merged content ...",
  "target_summary": "Consolidated from 3 docs about API patterns"
}
```

**Behavior:**
1. Validate all source documents exist and belong to the same topic
2. Create a new document with the merged content (inherits `topic_id`, `project_id` from sources)
3. Set `source` = "writer", `status` = "pending"
4. Archive source documents (set `status` = "archived")
5. Set `last_maintained_at` = now on the new document
6. Return the new document

**Why create new + archive old instead of mutating?** Preserves history. If a merge is bad, the archived originals still exist. Reversible > optimal.

**Same-topic constraint:** Merging across topics is a content decision, not maintenance. Keep it scoped.

### 3. Orchestrator Periodic Writer Tasks

Add a maintenance check to the orchestrator's polling loop (similar to how `EventScheduler` runs). Every 24 hours, check for topics with:
- 3+ active (non-archived) documents, OR
- Documents where `last_maintained_at` is NULL or older than 14 days

If found, create a writer task: "Review and maintain documents in topic X. Merge related documents, update stale content, consolidate duplicates."

**Implementation:** New file `app/orchestrator/knowledge_maintenance.py` with a `KnowledgeMaintenance` class following the same pattern as `EventScheduler`. Engine calls it on its interval.

## Schema Changes

### Model (app/models.py)
```python
# Add to AgentDocument class:
last_maintained_at = Column(DateTime, nullable=True)
merged_from = Column(JSON, nullable=True)  # list of source doc IDs
```

### Schema (app/schemas.py)
```python
# Add to AgentDocumentBase:
last_maintained_at: Optional[datetime] = None
merged_from: Optional[list[str]] = None

# New schema:
class DocumentMergeRequest(BaseModel):
    source_document_ids: list[str]  # min 2
    target_title: str
    target_content: str
    target_summary: Optional[str] = None
```

### DB Migration
ALTER TABLE to add `last_maintained_at` and `merged_from` columns. Both nullable, no default needed.

## API Changes

### New Endpoint
| Method | Path | Purpose |
|--------|------|---------|
| POST | /api/docs/merge | Merge documents |

### Modified Endpoints
- GET/PUT on documents now include `last_maintained_at` and `merged_from` fields
- PUT auto-updates `last_maintained_at` to current time

## Tradeoffs

| Choice | Alternative | Why |
|--------|------------|-----|
| Archive sources on merge | Delete sources | Reversible, preserves history |
| Same-topic merge only | Cross-topic | Simpler, safer, covers 90% of cases |
| 24h maintenance check | Per-commit or real-time | Low overhead, maintenance isn't time-critical |
| New doc on merge | Mutate first source | Cleaner audit trail, no confusion about which doc "won" |
| `merged_from` JSON field | Separate merge_history table | Simpler, sufficient for tracking lineage |

## Testing Strategy

- **Unit tests for merge endpoint:** Happy path (2 docs same topic), 3+ docs, docs from different topics (should fail), non-existent doc IDs, single doc (should fail)
- **Unit tests for last_maintained_at:** Updated on PUT, set on merge
- **Integration test for knowledge_maintenance:** Mock DB with stale docs, verify task creation

## Implementation Plan

### Task 1: Add `last_maintained_at` and `merged_from` fields (small)
- Add columns to `AgentDocument` model
- Update all schemas (`AgentDocumentBase`, `AgentDocumentUpdate`, `AgentDocument`)
- Add DB migration (ALTER TABLE)
- Update PUT endpoint to set `last_maintained_at = now()` on edit
- **Tests:** Verify field appears in responses, updates on PUT

### Task 2: Document merge endpoint (medium)
- Add `DocumentMergeRequest` schema
- Add `POST /api/docs/merge` to documents router
- Validate same-topic, all exist, min 2 sources
- Create new doc, archive sources
- **Tests:** Merge 2 docs, merge 3 docs, cross-topic rejection, missing doc, single doc

### Task 3: Knowledge maintenance orchestrator module (medium)
- Create `app/orchestrator/knowledge_maintenance.py`
- Query for topics with 3+ active docs or stale `last_maintained_at`
- Create writer tasks for maintenance
- Wire into `OrchestratorEngine` polling loop (same pattern as EventScheduler)
- Add cooldown (don't create duplicate maintenance tasks)
- **Tests:** Stale docs trigger task creation, recent maintenance skipped, cooldown works
