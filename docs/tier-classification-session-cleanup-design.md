# Design: Task Tier Classification at Creation + Session Cleanup + ModelChooser Simplification

**Task ID:** FAD05A1F-5D54-4B27-A89B-BA876BF4AA4C  
**Date:** 2026-03-01  
**Status:** Design complete, ready for implementation

---

## Problem Statement

Three interconnected issues degrade orchestrator quality:

1. **Tier classified mid-workflow** — tier classification was meant to run as a workflow node
   before spawn, but `classify_model_tier` is NOT actually wired into any active workflow seed.
   Tasks get no model_tier until spawn_agent falls back to a default.

2. **Session cleanup** — `delete_session()` is a no-op because OpenClaw has no HTTP delete API.
   Sessions auto-archive after 5 minutes via `archiveAfterMinutes` config. The `session_refs`
   pattern is already wired in cleanup nodes. This is working as designed.

3. **ModelChooser** — already reads tier from DB settings (`MODEL_ROUTER_TIER_*` keys) and
   maps correctly. No overhaul needed.

---

## Current State (confirmed by code inspection)

- `task.model_tier` column exists on Task model.
- `classify_model_tier` workflow node exists but is NOT used in any active workflow seed.
- ModelChooser reads tier → model lists from DB settings. Already correct.
- `delete_session()` is documented no-op; OpenClaw auto-archives at 5 minutes.
- `session_refs` with `childSessionKey` already wired in task-router `done` node and
  standalone code-task/research-task seeds.
- `spawn_agent` node already reads `task.model_tier` from context/task.

---

## Proposed Solution

### Change 1: Tier classification at task creation

Add tier classification to `create_task()` in `app/routers/tasks.py`.
Create new service: `app/services/tier_classifier.py`.

**Logic (first-match wins):**
1. Caller supplies `model_tier` → keep it, done
2. Project hard minimums: project_id containing lobs-server, lobs-mission-control, lobs-mobile → minimum "standard"
3. LLM classification via `llm_direct` (local qwen, no agent spawn, ~500ms)
4. Default: "standard"

**Integration point** in `create_task()`:
```python
if not db_task.model_tier:
    tier = await classify_task_tier(db, payload)  # never raises
    db_task.model_tier = tier
```

### Change 2: ModelChooser — no changes needed

Already correct. Reads DB settings. `decide_models()` already maps tier to model list.
`spawn_agent` node already propagates `model_tier` from task to the spawned session.

### Change 3: Session cleanup — no changes needed

`delete_session()` is correctly documented as a no-op. OpenClaw auto-archives.
`session_refs` already wired. Working as intended.

---

## Implementation Plan

### Task 1: Create `app/services/tier_classifier.py`

```python
STANDARD_MINIMUM_PROJECTS = ["lobs-server", "lobs-mission-control", "lobs-mobile"]
VALID_TIERS = ["small", "standard", "strong"]
TIER_ORDER = {"small": 0, "standard": 1, "strong": 2}

async def classify_task_tier(db, task_payload: dict) -> str:
    """Returns 'small', 'standard', or 'strong'. Never raises."""
    # 1. Caller override
    if task_payload.get("model_tier") in VALID_TIERS:
        return task_payload["model_tier"]
    
    # 2. Project minimum
    project_id = (task_payload.get("project_id") or "").lower()
    minimum = "standard" if any(p in project_id for p in STANDARD_MINIMUM_PROJECTS) else None
    
    # 3. LLM classification
    tier = await _llm_classify_tier(task_payload)  # None on failure
    if not tier:
        tier = "standard"
    
    # Apply floor
    if minimum:
        if TIER_ORDER.get(tier, 0) < TIER_ORDER[minimum]:
            tier = minimum
    
    return tier
```

LLM prompt (send to LM Studio via `llm_direct`):
```
Classify the model tier for this task. Respond with JSON only.

Title: {title}
Project: {project_id}  
Agent: {agent}
Notes: {notes[:400]}

Tiers:
- small: boilerplate, docs, simple CRUD, config, scripts, experiments, draft content, simple obvious bug fixes
- standard: production features, multi-file changes, API endpoints, refactors, real bug fixes, research, integrations  
- strong: architecture, security-sensitive, complex debugging, system design, prod DB migrations

{"tier": "small|standard|strong", "reason": "one sentence"}
```

**Acceptance:**
- LM Studio down → returns "standard" (default)
- lobs-server project + LLM returns "small" → floors to "standard"
- Caller passes "strong" → returns "strong" unchanged
- Invalid LLM response → returns "standard"

### Task 2: Wire into `app/routers/tasks.py`

In `create_task()`, after `db.add(db_task)` and before `db.flush()`:

```python
from app.services.tier_classifier import classify_task_tier

if not db_task.model_tier:
    try:
        db_task.model_tier = await classify_task_tier(db, payload)
    except Exception as exc:
        logger.warning("[create_task] Tier classification failed: %s", exc)
        db_task.model_tier = "standard"
```

Also verify `TaskCreate` schema has `model_tier: Optional[str] = None`. If not, add it.

**Acceptance:**
- POST /api/tasks with no model_tier → response has non-null model_tier
- POST /api/tasks with model_tier="strong" → response keeps "strong"
- Tasks in lobs-server project → model_tier is at least "standard"

---

## Testing Strategy

New file: `tests/test_tier_classifier.py`
- Mock LM Studio unreachable → assert returns "standard"
- Mock LM Studio returns "small" for lobs-server → assert returns "standard" (floor)
- Mock returns "strong" → passes through
- Caller sets "strong" → not modified

Extend `tests/test_tasks.py`:
- POST task without model_tier → model_tier in response is not null
- POST task with explicit model_tier → preserved

---

## Files Changed

| File | Change |
|------|--------|
| `app/services/tier_classifier.py` | NEW — classification logic |
| `app/routers/tasks.py` | Wire classify_task_tier into create_task() |
| `app/schemas.py` | Verify/add model_tier to TaskCreate |
| `tests/test_tier_classifier.py` | NEW — unit tests |

## Files NOT Changed

- `app/orchestrator/model_chooser.py` — already correct, reads DB settings
- `app/orchestrator/workflow_nodes.py` — delete_session no-op is correct; session_ref support already exists
- `app/orchestrator/workflow_seeds.py` — session_refs already wired in active workflows
- `app/orchestrator/model_router.py` — already reads DB settings correctly
