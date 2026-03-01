# Design: Task Tier Classification + Session Cleanup + ModelChooser Overhaul

**Task ID:** FAD05A1F-5D54-4B27-A89B-BA876BF4AA4C  
**Date:** 2026-03-01  
**Author:** Architect

---

## Problem Statement

Three interconnected gaps in the current system:

1. **Tier classification happens mid-workflow** — `model_tier` is null at task creation. `spawn_agent` may pick wrong model because tier isn't known yet.

2. **ModelChooser complexity** — When `task.model_tier` is already set, `decide_models()` already uses it. The primary issue was tier was never set at creation. No major ModelChooser rewrite needed.

3. **Session cleanup is a no-op** — `NodeHandlers.delete_session()` in `workflow_nodes.py` is a placeholder. The real implementation exists in `worker_gateway.py` (`sessions.delete` WebSocket JSON-RPC) but is not wired to cleanup nodes.

---

## Current State (what we found)

### Tier classification
- `task.model_tier` field exists on `TaskCreate` schema (line 64 in schemas.py) and `Task` model — nullable.
- No classification at task creation — always null unless caller sets it.
- `_node_classify_model_tier` in `workflow_nodes.py` exists (line ~1643) but is NOT used in task-router seeds.

### ModelChooser
- `decide_models()` in `model_router.py` reads `task.get("model_tier")` as `explicit_tier` (line ~268).
- DB settings keys `model_router.tier.{micro,small,medium,standard,strong}` are already the source of truth for model lists.
- When tier is set at creation, ModelChooser already routes correctly. No rewrite needed.

### Session cleanup
- `NodeHandlers.delete_session()` in `workflow_nodes.py` lines 201-211: **no-op placeholder**.
- `WorkerGateway.delete_session()` in `worker_gateway.py` lines 382-422: **working WebSocket implementation** using `sessions.delete` JSON-RPC.
- `worker_monitor.py` line 366 calls `gateway.delete_session()` for timeout cleanup — proven path.
- task-router done node already has `session_refs` wired for all spawn nodes.
- `workflow_nodes.py` cleanup node already resolves `session_refs` — only the underlying delete call is broken.

---

## Proposed Solution

### 1. Tier classification service (`app/services/tier_classifier.py`)

New function `classify_task_tier(task_dict, db) -> str`:

```
1. If task.model_tier already set → return it (caller override wins)
2. Compute project minimum:
   - project_id contains 'lobs-server', 'lobs-mission-control', 'lobs-mobile' → min 'standard'
   - else: min None (no floor)
3. LLM classification via llm_direct (local qwen):
   - Prompt includes: title, notes, project_id, agent_type
   - System prompt defines tiers: small/standard/strong with examples
   - Timeout: 8s
   - Parse first word of response, validate against known tiers
   - On failure/timeout → fallback to 'standard'
4. Apply project minimum: if project_min='standard' and result='small' → return 'standard'
5. Return tier string
```

Wire in `create_task()` in `app/routers/tasks.py`:
- After `await db.flush()` / `await db.refresh(db_task)`
- Before return
- Wrap in try/except: any exception → log warning, keep model_tier as-is (null is fine, ModelChooser handles it)

### 2. ModelChooser — no change needed

`decide_models()` already reads task.model_tier. DB settings already store model lists per tier. When tier is set at creation, routing is automatic.

**Only change:** Remove `_node_classify_model_tier` dead code from `workflow_nodes.py` (cleanup, low priority).

### 3. Fix NodeHandlers.delete_session()

Replace no-op with gateway call:

```python
async def delete_session(self, session_key: str) -> None:
    if not session_key:
        return
    if self.worker_manager and hasattr(self.worker_manager, 'gateway'):
        success = await self.worker_manager.gateway.delete_session(session_key)
        if not success:
            logger.warning("[NODE] Failed to delete session %s via gateway", session_key)
    else:
        logger.warning("[NODE] No gateway available for session delete: %s", session_key)
```

Check: `NodeHandlers.__init__` takes `worker_manager` — verify it has `.gateway` attribute (it does: `WorkerManager` instantiates `WorkerGateway`).

---

## Implementation Plan

### Handoff 1: Tier classifier service + wire to task creation
**Files:** `app/services/tier_classifier.py` (new), `app/routers/tasks.py` (edit)

Acceptance criteria:
- POST /api/tasks with no model_tier → response has model_tier set
- POST /api/tasks with model_tier="strong" → keeps "strong" (no override)
- Task in project "lobs-server" gets minimum "standard" even if LLM says "small"
- LLM unavailable → task gets "standard", no error thrown
- Write unit tests: `tests/test_tier_classifier.py` — mock llm_direct, test project minimums, test fallback

### Handoff 2: Fix NodeHandlers.delete_session()
**File:** `app/orchestrator/workflow_nodes.py`

Acceptance criteria:
- Replace no-op with call to `self.worker_manager.gateway.delete_session(key)`
- No crash when gateway unavailable (log + continue)
- After programmer task completes, run `openclaw sessions list` — worker sessions are gone
- Write unit test: mock worker_manager.gateway, assert delete_session called

### Handoff 3: Remove classify_model_tier dead code (cleanup, can be deferred)
**File:** `app/orchestrator/workflow_nodes.py`

- Remove `@register_node("classify_model_tier")` and `_node_classify_model_tier` function
- No workflow uses it; removing prevents confusion

---

## Testing Strategy

**Unit:**
- `tests/test_tier_classifier.py`: mock `llm_direct`, test all 4 paths (caller override, project min, LLM result, fallback)
- Extend `tests/test_workflow_nodes.py`: mock `worker_manager.gateway`, assert delete_session invoked

**Integration (manual):**
1. POST task → check model_tier in response
2. Run a task cycle → check session count via `openclaw sessions list` before and after

---

## Risks

1. **LLM call adds latency to task creation** (~2s typical for local qwen). Acceptable because workers don't start for ≥30s. If latency becomes a problem, can defer to background after creation.

2. **worker_manager.gateway access** — if worker_manager is None in some code paths, guard with hasattr check avoids crash.

3. **session_refs resolve to None** when a branch skips a spawn node — already handled gracefully in cleanup node (logs debug, skips). No new risk.
