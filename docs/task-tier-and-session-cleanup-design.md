# Design: Task Tier Classification at Creation + Session Cleanup + ModelChooser Overhaul

**Task ID:** FAD05A1F-5D54-4B27-A89B-BA876BF4AA4C  
**Date:** 2026-03-01  
**Status:** Design complete → Programmer handoffs created

---

## Problem Statement

Three interconnected correctness issues:

1. **Tier classification happens mid-workflow** — tasks arrive at `spawn_agent` with no tier set, forcing mid-flight classification. Fragile and adds latency.

2. **ModelChooser bypasses task.model_tier** — `decide_models()` does fuzzy task analysis even when `task.model_tier` is already set. Direct table lookup is needed.

3. **Session cleanup broken in worker.py** — `worker.py._terminate_session()` calls `sessions_kill` via Gateway, which doesn't exist (verified: returns `Tool not available`). `workflow_nodes.py.delete_session()` is already correctly a no-op. Worker has the same bug.

---

## Proposed Solution

### Change 1: Tier classification at task creation (POST /api/tasks)

New service `app/services/tier_classifier.py`:

```python
async def classify_task_tier(payload: dict, db: AsyncSession) -> str
```

Classification logic (first match wins):
1. Caller supplied `model_tier` → keep it, skip classification
2. Project hard minimums: if `project_id` contains `lobs-server`, `lobs-mission-control`, or `lobs-mobile` → minimum `standard`
3. LLM classification via LM Studio (POST to `http://localhost:1234/v1/chat/completions`, 5s timeout, micro model). Prompt asks for one word: small|standard|strong.
4. Default: `standard`

**Integration in `app/routers/tasks.py`**, after `await db.flush()` / before commit:
```python
if not db_task.model_tier:
    tier = await classify_task_tier(payload, db)
    db_task.model_tier = tier
```

Remove `classify_model_tier` node from task-router workflow (the node type stays, just remove it from task-router seeds). `spawn_agent` reads `task.model_tier` directly.

### Change 2: ModelChooser short-circuit for known tier

In `ModelChooser.choose()`, after loading tiers from DB:

```python
task_tier = (task.get("model_tier") or "").strip().lower()
if task_tier and task_tier in tiers and tiers[task_tier]:
    candidates = list(tiers[task_tier])
    budget_lane = _tier_to_lane(task_tier)
else:
    # Existing decide_models() path
    decision = decide_models(...)
    candidates = list(decision.models)
```

New helper `_tier_to_lane(tier) -> str`: small/micro → background, standard/medium → standard, strong → critical.

All subsequent steps (routing policy, budget guards, health ranking) still run on candidates. This preserves all existing safeguards.

### Change 3: Fix worker.py `_terminate_session()` sessions_kill

Replace the broken Gateway API call with the same no-op pattern as `workflow_nodes.py.delete_session()`:

```python
async def _terminate_session(self, session_key: str, reason: str) -> bool:
    logger.debug(
        "[WORKER] Session %s will be auto-archived by OpenClaw (archiveAfterMinutes=5). reason=%s",
        session_key, reason
    )
    return True
```

OpenClaw auto-archives sessions after `archiveAfterMinutes=5` and kills on `runTimeoutSeconds=1800`. No Gateway delete API exists.

---

## Tradeoffs

- **LLM at creation adds ~200ms latency** to POST /api/tasks. Acceptable: task creation is async from user perspective, and LLM is local (qwen). If local unavailable, falls back to `standard` immediately.
- **Short-circuit in ModelChooser** preserves all budget/health guards. No regression risk.
- **Session cleanup no-op is correct** — auto-archiving handles cleanup. The missing API isn't a bug to fix, it's a non-existent feature. The real bug was the broken call in worker.py which we fix by removing it.

---

## Files Modified

- New: `app/services/tier_classifier.py`
- Modified: `app/routers/tasks.py` (call classifier after flush)
- Modified: `app/orchestrator/model_chooser.py` (short-circuit for known tier)
- Modified: `app/orchestrator/worker.py` (fix _terminate_session)
- Modified: `app/orchestrator/workflow_seeds.py` (remove classify_model_tier node from task-router, if present — currently task-router does NOT have classify_model_tier, so this may be a no-op)

No DB schema changes. `model_tier` column already exists.

---

## Testing Strategy

- Unit test `classify_task_tier`: mock LM Studio success, timeout, unavailable; project minimum; caller override
- Integration: POST /api/tasks → GET /api/tasks/{id} shows model_tier populated
- Unit test `ModelChooser.choose()`: with `task={'model_tier': 'small'}` → uses small tier list, `decide_models` not called
- Verify `_terminate_session` returns True without HTTP
