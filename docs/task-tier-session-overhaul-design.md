# Design: Task Tier Classification + Session Cleanup + Model Routing Overhaul

**Date:** 2026-03-01  
**Task ID:** FAD05A1F-5D54-4B27-A89B-BA876BF4AA4C  
**Status:** Design Complete — Handoffs in `docs/handoffs/task-tier-session-overhaul-handoffs.json`

---

## Problem Statement

Three interconnected issues affecting task routing and session hygiene:

1. **Task tier classification is async (race condition)** — `POST /api/tasks` fires classification as a background asyncio task and returns immediately. The orchestrator can pick up the task before `model_tier` is set, causing `spawn_agent` to fall back to heuristics instead of the user-intended tier.

2. **`send_to_session` session_ref paths are wrong** — The `fix_tests` and `fix_lint` nodes in `programmer-task` workflow use `session_ref: "write_code.session_key"`, but `spawn_agent` stores the session key at `write_code.output.childSessionKey`. The send never reaches the programmer session.

3. **`delete_session()` is a no-op** — The Gateway exposes only `sessions_list`, `sessions_history`, `sessions_send`, `sessions_spawn`. There is no `sessions_kill` or `sessions_delete` tool. The session store currently has 1274+ entries. Bulk cleanup is available via `openclaw sessions cleanup` CLI but not individual session deletion.

---

## Investigation Findings

### Gateway Tools Available
Confirmed via `/tools/invoke`:
- `sessions_list` ✅
- `sessions_history` ✅  
- `sessions_send` ✅
- `sessions_spawn` ✅
- `sessions_kill` ❌ (tool not found)
- `sessions_delete` ❌ (tool not found)

### `openclaw sessions cleanup` Available
The CLI has `openclaw sessions cleanup [--enforce] [--dry-run]` which runs maintenance on the session store (evicts old entries by age/count). This can be invoked as a subprocess for bulk cleanup.

### Current Session Accumulation
1274+ sessions in the store — real accumulation. `archiveAfterMinutes=5` comment in the code does not correspond to effective automatic archival.

### workflow_seeds.py Current State
- `programmer-task` cleanup correctly uses `"write_code.output.childSessionKey"` ✅
- `programmer-task` fix loop uses `"write_code.session_key"` ❌ (wrong path)
- `research-task` cleanup correctly uses `"research.output.childSessionKey"` ✅
- Reflection workflow: session cleanup via `session_refs` with `childSessionKey` paths ✅

### ModelChooser Current State
`ModelChooser.choose()` already has an explicit tier short-circuit: if `task.model_tier` is set and in `TIER_ORDER`, it reads the tier list from DB and uses those candidates. The model lists are already read from DB settings (`MODEL_ROUTER_TIER_*` keys). No hardcoding exists. The main issue is that `task.model_tier` is often `None` at workflow time because of the race condition at creation.

---

## Proposed Solution

### Fix 1: Make Task Tier Classification Synchronous

Change `app/routers/tasks.py` `create_task()` to classify tier **before** committing to DB and returning the response.

**Tradeoff:** Adds up to 2 seconds latency to `POST /api/tasks` when LM Studio is up, ~0ms when it's down (falls back to `standard` immediately). Acceptable — task creation is not a hot path.

Pattern:
```python
# After db.flush() / db.refresh(db_task)
# Before db.commit()
tier = await classify_task_tier(db_task, db)
db_task.model_tier = tier
await db.commit()
return Task.model_validate(db_task)
```

Remove the `_classify_and_update()` async background task and `asyncio.create_task(...)` call entirely.

**Edge case:** `classify_task_tier()` already respects caller-set `model_tier` (returns it immediately if set), so caller override still works.

### Fix 2: Correct `session_ref` Paths for `send_to_session`

In `workflow_seeds.py`, the `programmer-task` workflow has two `send_to_session` nodes using wrong paths:

| Node | Current (broken) | Correct |
|------|-----------------|---------|
| `fix_tests` | `"write_code.session_key"` | `"write_code.output.childSessionKey"` |
| `fix_lint` | `"write_code.session_key"` | `"write_code.output.childSessionKey"` |

Fix: update both config values to `"write_code.output.childSessionKey"`.

Why: `spawn_agent` returns `NodeResult(output={"childSessionKey": ...})`. The workflow context stores this under `write_code.output.childSessionKey`. The broken path `"write_code.session_key"` doesn't exist in context.

### Fix 3: Session Cleanup via `openclaw sessions cleanup`

Since Gateway has no per-session delete API, implement `delete_session()` to trigger bulk cleanup when accumulated session count is high:

```python
async def delete_session(self, session_key: str) -> None:
    """Best-effort session cleanup.
    
    Gateway has no per-session delete API. When session store
    accumulates >100 sessions, trigger openclaw sessions cleanup
    to evict old entries via the CLI.
    """
    logger.debug("[NODE] No per-session delete API — checking if bulk cleanup needed for %s", session_key)
    try:
        import aiohttp
        from app.orchestrator.config import GATEWAY_URL, GATEWAY_TOKEN, GATEWAY_SESSION_KEY
        async with aiohttp.ClientSession() as http:
            resp = await http.post(
                f"{GATEWAY_URL}/tools/invoke",
                headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                json={"tool": "sessions_list", "sessionKey": GATEWAY_SESSION_KEY, "args": {"limit": 1}},
                timeout=aiohttp.ClientTimeout(total=5),
            )
            data = await resp.json()
        count = 0
        if data.get("ok"):
            result_text = data.get("result", {}).get("content", [{}])[0].get("text", "{}")
            import json as _json
            result_data = _json.loads(result_text)
            count = result_data.get("count", 0)
        
        if count > 100:
            import asyncio, subprocess
            await asyncio.to_thread(
                subprocess.run,
                ["openclaw", "sessions", "cleanup", "--enforce"],
                timeout=15, capture_output=True, text=True,
            )
            logger.info("[NODE] Ran openclaw sessions cleanup (count was %d)", count)
    except Exception as exc:
        logger.debug("[NODE] Session cleanup attempt failed (non-fatal): %s", exc)
```

**Tradeoff:** Doesn't delete the specific session — runs maintenance on the whole store. Acceptable because:
- Session is idle after worker completes
- Bulk cleanup runs on-demand when >100 sessions accumulate
- No infinite growth if tasks complete regularly

---

## What We're NOT Changing

**ModelChooser architecture** — Already reads tier→model lists from DB settings. Already has explicit tier short-circuit. No hardcoding. No changes needed beyond the race fix above which ensures model_tier is populated.

**`classify_model_tier` workflow nodes** — None exist in `workflow_seeds.py` (confirmed by grep). No removals needed.

**`spawn_agent` fallback to classify_tier** — Still useful safety net for tasks created before this fix. Leave it in place.

---

## Testing Strategy

### Fix 1 (Synchronous Tier)
- `POST /api/tasks` with no `model_tier` field
- Immediately `GET /api/tasks/{id}`
- Assert: `model_tier` is one of `small|standard|strong` (not `null`)
- With LM Studio down: assert `model_tier == "standard"`
- With `model_tier` set in request: assert it's preserved unchanged

### Fix 2 (session_ref paths)
- Unit test: call `_resolve_context_path(context, "write_code.output.childSessionKey")` with synthetic context containing `write_code.output.childSessionKey = "agent:xxx"`; assert it resolves correctly
- Integration: verify `fix_tests` / `fix_lint` nodes don't fail with "Session ref not found"

### Fix 3 (Session cleanup)
- Call `delete_session("any-key")` and verify no exception
- Verify `openclaw sessions cleanup --dry-run` works on the session store

---

## Files to Modify

| File | Change |
|------|--------|
| `app/routers/tasks.py` | Make tier classification synchronous; remove `_classify_and_update` background task |
| `app/orchestrator/workflow_seeds.py` | Fix `session_ref` paths in `fix_tests` and `fix_lint` nodes |
| `app/orchestrator/workflow_nodes.py` | Implement `delete_session()` with bulk cleanup logic |
