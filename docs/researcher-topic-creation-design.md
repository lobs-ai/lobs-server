# Researcher Autonomous Topic Creation — Design

## Problem

Researchers discover new areas worth tracking but can't create topics. Topics are only created manually. This means valuable research threads get lost or dumped into existing topics that don't fit.

## Existing Infrastructure

- `POST /api/topics` already exists with duplicate title detection
- `Topic` model has `auto_created` boolean field (already there)
- `TopicCreate` schema accepts `id`, `title`, `description`, `icon`, `linked_project_id`, `auto_created`
- Agents work via file-based handoff (write files → orchestrator reads on finalization)
- Agents do NOT call the lobs-server API directly

## Proposed Solution

### 1. Agent-side: `.new-topics.json` output file

Researcher writes a `.new-topics.json` file in its workspace when it discovers something worth a new topic:

```json
[
  {
    "title": "WebSocket Performance Patterns",
    "description": "Research on WS connection pooling, message batching, and backpressure strategies"
  }
]
```

**Why file-based instead of API calls?** Matches existing agent pattern (agents write files, orchestrator processes). No need to inject API tokens into agent environments. Keeps agents decoupled from server internals.

### 2. Orchestrator-side: Process `.new-topics.json` on finalization

In the worker finalization step (where `.work-summary` is already read), also check for `.new-topics.json`. For each entry:

1. Check if topic with same title already exists (case-insensitive) → skip if so
2. Create topic with `auto_created=True`, generate UUID for `id`
3. Log created topics in worker run metadata

### 3. Researcher prompt update

Add to researcher agent guidance in `prompter.py`:

```
## Topic Creation

If your research uncovers a distinct area worth tracking separately, create a new topic.
Write `.new-topics.json` in your workspace:

[{"title": "Topic Name", "description": "Why this topic matters and what it covers"}]

**Create a new topic when:**
- You discover a genuinely new area not covered by existing topics
- The research spans multiple existing topics and deserves its own category
- You find a recurring theme that will have ongoing research value

**Don't create a topic when:**
- It fits within an existing topic (add to that topic instead)
- It's too narrow (one-off finding, not a research area)
- It's too broad (would overlap heavily with existing topics)
```

## Tradeoffs

| Choice | Alternative | Why |
|--------|------------|-----|
| File-based handoff | Direct API calls | Consistent with agent pattern, no token injection |
| Orchestrator creates topics | Agent creates via API | Centralized control, can validate/dedupe |
| Case-insensitive title check | Exact match only | Prevents "WebSocket" vs "Websocket" duplicates |
| Skip duplicates silently | Error/warn | Non-disruptive; researcher shouldn't need to know existing topics |

## Implementation Plan

### Task 1: Orchestrator processes `.new-topics.json` on finalization (small)
- In worker finalization (where `.work-summary` is read), check for `.new-topics.json`
- Parse JSON array of `{title, description}` objects
- For each: check existing topics (case-insensitive title match), create if new
- Set `auto_created=True`, generate UUID `id`
- Log created topics count
- **Tests:** Creates topic from file, skips duplicate titles, handles missing file, handles malformed JSON gracefully

### Task 2: Update researcher prompt with topic creation instructions (small)
- Update `_build_agent_specific_guidance` in `prompter.py` for researcher type
- Add the topic creation guidance block
- Include existing topic list in researcher prompt context (query topics, include titles so researcher knows what exists)
- **Tests:** Verify researcher prompt includes topic creation guidance

### Dependencies
- Task 1 and Task 2 are independent, can be done in parallel
- Both are small tasks suitable for a single programmer handoff

## Testing Strategy

- **Unit test:** Worker finalization reads `.new-topics.json`, creates topics with `auto_created=True`
- **Unit test:** Duplicate title (case-insensitive) is skipped
- **Unit test:** Missing/malformed file doesn't crash finalization
- **Integration test:** Full worker run with `.new-topics.json` → topic appears in DB
