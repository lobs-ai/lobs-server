# Design: Task Tier Classification + ModelChooser Simplification + Session Cleanup

**Date:** 2026-03-01  
**Task ID:** FAD05A1F-5D54-4B27-A89B-BA876BF4AA4C  
**Status:** Design complete — 3 programmer handoffs

---

## What We're Solving

Three interconnected problems in the orchestrator workflow:

1. **Race condition in task tier classification** — `model_tier` is set in a fire-and-forget background task. If the orchestrator picks up the task before it completes (common), `model_tier` is `None` and ModelChooser falls back to heuristics instead of using the classified tier.

2. **ModelChooser unnecessary complexity** — When `task.model_tier` is already set, ModelChooser still calls `decide_models()` for audit data, doing unnecessary heuristic work. Should short-circuit cleanly.

3. **Reflection cycle sessions never deleted** — `spawn_reflection_agents` spawns Gateway sessions but never deletes them after completion. Sessions accumulate in Gateway.

---

## What's Already Working (Don't Touch)

- `app/services/task_tier.py` — Classification logic with LLM + hard minimums is complete ✅
- `workflow_nodes.py` `NodeHandlers.delete_session()` → delegates to gateway ✅
- `worker_gateway.py` `delete_session()` → uses WebSocket `sessions.delete` JSON-RPC ✅  
  (**The "sessions_kill" bug mentioned in the task brief is ALREADY FIXED.**)
- cleanup nodes support `session_ref`, `session_refs` ✅
- task-router, code-task, research-task all have proper session_refs in cleanup ✅
- ModelChooser has explicit tier short-circuit when model_tier is set ✅
- All workflow seeds already updated with session_refs ✅

---

## Fix 1: Inline Task Tier Classification (eliminates race condition)

### Problem
```python
# tasks.py — current
asyncio.create_task(_classify_and_update())  # Fire and forget
return task_result  # Returns BEFORE model_tier is set
```

The orchestrator processes `TaskCreated` events. If it picks up the task before the background task commits, `model_tier` is `None`.

### Solution
Classify synchronously inline, before `db.flush()`. The `classify_task_tier()` function has a 2s timeout — acceptable.

```python
# After creating db_task, before db.flush():
if not db_task.model_tier:
    tier = await classify_task_tier(db_task, db)
    db_task.model_tier = tier
# Then flush + commit as usual
# Remove the asyncio.create_task() call entirely
```

**Tradeoff:** Up to 2s added to task creation when LM Studio is running. Acceptable — 2s cap is already enforced, defaults to "standard" on timeout. Background approach silently breaks tier routing.

### Acceptance Criteria
- POST /api/tasks returns task with non-null `model_tier`
- Caller-supplied `model_tier` in request body is preserved (no override)
- LM Studio down → `model_tier = "standard"` within ~2s
- `project_id` containing "lobs-server" → minimum "standard" (hard minimum applied)

---

## Fix 2: ModelChooser Clean Short-Circuit

### Problem
When explicit_tier is set, `decide_models()` is still called for audit purposes but its output is discarded:
```python
# Both branches call decide_models() even in the explicit-tier path
decision = decide_models(agent_type, task, ...)  
candidates = tier_candidates[:]  # Overrides decision.models anyway
```

### Solution
When `explicit_tier` is set and tier_candidates exist, skip `decide_models()` entirely. Create a minimal `_ExplicitTierDecision` dataclass for the audit trail that downstream code expects.

The minimal decision object needs:
- `.models` — the tier_candidates list
- `.criticality` — "standard" (safe default)
- `.complexity` — "medium" (safe default)
- `.audit` — dict with tier, tier_source, tier_models

```python
@dataclass
class _ExplicitTierDecision:
    models: list[str]
    criticality: str = "standard"
    complexity: str = "medium"
    audit: dict = field(default_factory=dict)
```

**Note:** tier->model mapping is read from DB settings (existing behavior). Do NOT hardcode model lists.

### Acceptance Criteria
- task.model_tier set → `audit["tier_source"] == "explicit"`, `decide_models()` not called
- task.model_tier=None → falls through to heuristic path unchanged
- Budget guards, health ranking, routing policy still run after candidate selection
- Tier list empty in DB → falls through to heuristic (existing behavior preserved)

---

## Fix 3: Reflection Cycle Session Cleanup

### Problem
`spawn_reflection_agents()` spawns Gateway sessions but `childSessionKey` is not stored in the returned `spawn_results`. So cleanup node can't reference them via session_refs. Sessions pile up.

### Solution — Two parts:

**Part A:** In `spawn_reflection_agents()`, store `childSessionKey` in each entry of `spawn_results`:
```python
spawn_results.append({
    "agent": agent,
    "reflection_id": reflection_id,
    "session_key": result.get("childSessionKey"),  # ADD THIS
    "model": choice.model,
    "status": "spawned",
})
```

**Part B:** In `check_reflections_complete()`, when `remaining == 0`, delete all session keys from spawn_results:
```python
if not remaining_workers:
    spawn_results = (context or {}).get("spawn_agents", {}).get("spawn_results", [])
    cleaned = 0
    for entry in spawn_results:
        key = entry.get("session_key")
        if key and worker_manager and worker_manager.gateway:
            try:
                await worker_manager.gateway.delete_session(key)
                cleaned += 1
            except Exception as exc:
                logger.warning("[REFLECTION_WF] Failed to delete session %s: %s", key, exc)
    return {"completed": True, "remaining": 0, "sessions_cleaned": cleaned}
```

### Acceptance Criteria
- After reflection-cycle completes, reflection-* sessions are deleted from Gateway
- Delete failures are non-fatal (log + continue)
- Cleanup only runs when all reflection workers are done (remaining == 0)
- `spawn_results` entries include `session_key` field for each spawned agent

---

## Implementation Order

1. Fix 1 (tasks.py) — eliminates the race condition, everything benefits
2. Fix 2 (model_chooser.py) — simplification, independent of #1
3. Fix 3 (workflow_reflection.py) — independent of both

---

## Files to Change

| Fix | File | Change |
|-----|------|--------|
| 1 | `app/routers/tasks.py` | Inline classification before flush(), remove asyncio.create_task |
| 2 | `app/orchestrator/model_chooser.py` | Add `_ExplicitTierDecision`, skip decide_models() when explicit tier set |
| 3 | `app/orchestrator/workflow_reflection.py` | Store session_key in spawn_results; delete sessions in check_reflections_complete |
