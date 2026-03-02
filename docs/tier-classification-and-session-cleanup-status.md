# Task Tier + Session Cleanup: Current Status & Remaining Work

**Date:** 2026-03-01  
**Task ID:** FAD05A1F-5D54-4B27-A89B-BA876BF4AA4C  
**Status:** Mostly implemented. One gap remains.

---

## What's Already Done

### 1. Task tier classification at creation ✅
- `app/services/task_tier.py` — full classification service exists
  - Caller override (keeps existing model_tier)
  - Project hard minimums (lobs-server/lobs-mission-control/lobs-mobile → standard min)
  - LM Studio inference (2s timeout, POST to localhost:1234)
  - Default to 'standard' on failure
- `app/routers/tasks.py` — calls `classify_task_tier()` after `db.flush()`
  - ⚠️ **Currently runs as `asyncio.create_task()` (background)** — race condition, see gap below

### 2. ModelChooser explicit tier short-circuit ✅
- `app/orchestrator/model_chooser.py` — `explicit_tier = task.get("model_tier")` check exists
- If tier is set and tier list is non-empty in DB → uses tier directly, bypasses heuristics
- Falls back to heuristics if tier is None or tier list is empty in DB
- Reads model lists from DB settings (MODEL_ROUTER_TIER_* keys) — not hardcoded
- All budget guardrails, health ranking, routing policy still run after tier selection

### 3. Session cleanup in workflows ✅
- `app/orchestrator/worker_gateway.py` — `delete_session()` uses WebSocket `sessions.delete` (JSON-RPC)
- `app/orchestrator/workflow_nodes.py` — `_exec_cleanup` supports `session_ref` and `session_refs`
- `app/orchestrator/workflow_seeds.py` — cleanup nodes have `session_refs` wired for all major workflows

### 4. No classify_model_tier in mid-workflow ✅
- No classify_tier/classify_model_tier node type in any workflow seed

---

## The One Remaining Gap

### Race Condition: Background Classification Can Lose to Orchestrator

**Problem:** `create_task()` uses `asyncio.create_task(_classify_and_update())` — fire-and-forget. Orchestrator can pick up the task before the 2s LM Studio call completes → `task.model_tier` is null at workflow start.

**Fix:** Make classification synchronous inline in `create_task`, before returning the response.

```python
# In create_task(), after db.flush()/refresh and BEFORE returning:
if not db_task.model_tier:
    tier = await classify_task_tier(db_task, db)
    db_task.model_tier = tier
    await db.commit()
    await db.refresh(db_task)
task_result = Task.model_validate(db_task)
# Remove asyncio.create_task(_classify_and_update())
return task_result
```

Worst case: +2s latency on task creation when LM Studio is cold. Acceptable.
