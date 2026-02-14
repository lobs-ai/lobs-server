# Work Dump Analysis: Proactive Task Creation from Tracker Entries

## Problem

User logs work entries (struggles, progress, deadlines) but nothing acts on them. A note like "struggling with thread sync in Java" should trigger a research task on concurrency patterns. Currently, entries are just stored — no intelligence extracts actionable follow-ups.

## Proposed Solution

### Flow

```
User submits tracker entry (POST /tracker/entries)
  → Entry saved to DB (existing behavior)
  → Background: LLM analyzes entry
  → Creates inbox suggestions (NOT auto-created tasks)
  → User reviews/approves in inbox
```

### Key Decision: Inbox Suggestions, Not Auto-Created Tasks

**Decision:** Analysis creates inbox items, not tasks directly.

**Why:**
- Avoids junk tasks polluting the task board
- User stays in control of what becomes work
- Inbox already has a review workflow (read/unread, triage)
- Auto-creation would require high confidence thresholds and still produce noise
- Inbox items are cheap to dismiss, tasks require cleanup

### Architecture

**1. Analysis Service** (`app/services/entry_analyzer.py`)

A service class that takes a tracker entry and returns suggested actions. Uses the server's LLM integration (same pattern as other LLM features if they exist, otherwise direct OpenAI/Anthropic call via httpx).

```python
class EntryAnalyzer:
    async def analyze(self, entry: TrackerEntry) -> list[SuggestedAction]:
        """Analyze entry text and return suggested tasks/research."""
```

**Suggested action types:**
- `research_task` — "Research X" (creates inbox item suggesting a researcher task)
- `study_guide` — "Create study guide for X" (creates inbox item suggesting writer task)
- `prep_task` — "Prepare for X" (creates inbox item suggesting a task)

**LLM Prompt:** Structured prompt that receives the entry text and returns JSON with 0-3 suggestions. Prompt instructs the model to only suggest when there's clear signal — "struggling with X" → research, "exam on X" → study guide, "project deadline" → prep tasks. No suggestions for routine entries.

**2. Background Trigger**

After `create_tracker_entry` saves the entry, fire analysis as a background task via `asyncio.create_task()` (FastAPI pattern). Don't block the response.

```python
@router.post("/entries")
async def create_tracker_entry(entry: TrackerEntryCreate, db=Depends(get_db)):
    db_entry = TrackerEntryModel(**entry.model_dump())
    db.add(db_entry)
    await db.flush()
    await db.refresh(db_entry)
    
    # Fire and forget analysis
    asyncio.create_task(analyze_and_suggest(db_entry.id))
    
    return TrackerEntry.model_validate(db_entry)
```

**Why background, not synchronous?** Entry creation should be instant. Analysis takes 1-3s for LLM call. User doesn't need suggestions immediately — they appear in inbox.

**3. Inbox Item Creation**

Analysis results become inbox items with a distinctive source marker:

```python
InboxItem(
    id=uuid4(),
    title="Research: Java Thread Synchronization Patterns",
    content="Based on your work log about struggling with thread sync...\n\nSuggested: Create a research task...",
    summary="Auto-suggested from work tracker entry",
    is_read=False,
)
```

**4. Entry Model Addition**

Add `analyzed` boolean to TrackerEntry so we don't re-analyze on server restart or retry:

```python
analyzed = Column(Boolean, default=False, nullable=False)
```

## Schema Changes

### Model (app/models.py)
```python
# Add to TrackerEntry:
analyzed = Column(Boolean, default=False, nullable=False)
```

### Schema (app/schemas.py)
```python
# Add to TrackerEntryBase:
analyzed: bool = False
```

### New Service (app/services/entry_analyzer.py)
- `EntryAnalyzer` class
- `SuggestedAction` dataclass: `type`, `title`, `description`, `agent_type`
- LLM prompt template
- `analyze_and_suggest()` standalone function for background execution

## LLM Integration

**Decision:** Use httpx to call OpenAI-compatible API directly. Keep it simple — one function, one prompt, structured JSON output.

**Prompt design:**
```
You analyze work tracker entries and suggest helpful follow-up tasks.
Given this entry, suggest 0-3 follow-up actions. Only suggest when there's clear signal.

Entry: "{raw_text}"
Type: {type} (work_session/deadline/note)
Category: {category}

Respond with JSON array. Each item: {"type": "research|study_guide|prep", "title": "...", "description": "..."}
Return empty array [] if no suggestions are warranted.
```

**Config:** API key and model from environment variables (`OPENAI_API_KEY`, `ANALYSIS_MODEL`). If not configured, analysis silently skips (graceful degradation).

## Tradeoffs

| Choice | Alternative | Why |
|--------|------------|-----|
| Inbox suggestions | Auto-create tasks | User control, no junk tasks, cheap to dismiss |
| Background async | Synchronous | Don't block entry creation |
| Direct LLM call | Orchestrator agent | Simpler, faster, no worker spawn overhead for a quick analysis |
| 0-3 suggestions max | Unlimited | Prevents noise, forces quality |
| `analyzed` flag | Idempotency key | Simpler, prevents re-analysis |
| Graceful skip if no API key | Hard fail | Server works without LLM config |

## Risks

1. **LLM quality:** May suggest irrelevant tasks. Mitigated by inbox review — user just ignores bad suggestions.
2. **Cost:** Each entry = 1 LLM call (~$0.001-0.01). At <20 entries/day, negligible.
3. **Latency:** Background task, so user doesn't notice. But DB session management needs care (new session for background task).
4. **No API key configured:** Silently skip. Log at debug level.

## Testing Strategy

- **Unit test EntryAnalyzer:** Mock LLM response, verify inbox items created
- **Unit test: no suggestions returned:** Empty array → no inbox items
- **Unit test: graceful degradation:** No API key → no crash, no inbox items
- **Unit test: analyzed flag:** Entry marked analyzed after processing
- **Integration test:** Create entry → verify inbox item appears (with mocked LLM)

## Implementation Plan

### Task 1: Add `analyzed` field to TrackerEntry + EntryAnalyzer service (medium)
- Add `analyzed` column to TrackerEntry model/schema
- Create `app/services/entry_analyzer.py` with LLM call and inbox creation
- Config: `OPENAI_API_KEY` and `ANALYSIS_MODEL` env vars
- Graceful skip when not configured
- **Tests:** Mock LLM, verify inbox creation, verify graceful degradation

### Task 2: Wire analysis into tracker entry creation (small)
- Modify `POST /tracker/entries` to fire `asyncio.create_task(analyze_and_suggest(entry_id))`
- Background task gets its own DB session
- Sets `analyzed=True` after completion
- **Tests:** Entry creation still returns immediately, inbox item appears after analysis
