# Design: Task Tier Classification + ModelChooser Simplification + Session Cleanup Fix

**Task ID:** FAD05A1F-5D54-4B27-A89B-BA876BF4AA4C  
**Status:** Approved for implementation  
**Date:** 2026-03-01

---

## Problem Statement

Three interconnected issues with the orchestrator workflow:

1. **Task model_tier is not set at creation** — tier classification happens mid-workflow via a `classify_model_tier` node, but this node is NOT wired into workflow_seeds.py (not present in any workflow). So tasks run without model_tier set at all — ModelChooser uses complex fallback logic every time.

2. **ModelChooser is over-complex** — doesn't cleanly use task.model_tier as the primary signal. Intent is: tier → model list (from DB settings) → pick first healthy one. The current code buries this.

3. **Session cleanup paths are wrong** — `session_ref` paths in workflow_seeds.py use `.output.childSessionKey` or `.session_key` which both resolve to None. Cleanup has never worked. Sessions accumulate.

---

## Root Cause Analysis

### Session ref path bug

`_resolve_context_path(context, "write_code.output.childSessionKey")` fails because:
- `context["write_code"]` = `{"runId": ..., "childSessionKey": ...}` (output stored directly)
- There is NO nested "output" key inside the output dict
- `.output.` in the path returns None

Similarly, `"write_code.session_key"` fails:
- context["write_code"]["session_key"] = None (key is "childSessionKey", not "session_key")

**Correct path:** `"write_code.childSessionKey"` — traverses context["write_code"]["childSessionKey"] correctly.

### delete_session status

Already a no-op placeholder. Sessions auto-archive after archiveAfterMinutes=5. No change needed.

### ModelChooser

task.model_tier is in DB schema but not populated at creation. Once populated, ModelChooser needs a fast-path that uses it directly.

---

## Proposed Solution

### 1. Tier classification at task creation

**New service:** `app/services/tier_classifier.py`

Logic (first match wins):
1. If task.model_tier already set → return it (caller override)
2. Project hard minimums (lobs-server, lobs-mission-control, lobs-mobile → min "standard")
3. LLM classification via local LM Studio (title + notes + project_id → small|standard|strong)
   - 3s timeout, fallback to "standard" on error/unavailability
4. Default: "standard"

Tier definitions for LLM prompt:
- small: boilerplate, docs, simple CRUD, config, scripts, drafts, obvious trivial bugs
- standard: production features, API endpoints, real bug fixes, research, integrations
- strong: architecture, security, complex debugging, system design, prod DB migrations

**Hook in task creation** (app/routers/tasks.py):
After db.flush(), before db.commit(), call classifier and write back to db_task.model_tier.

### 2. ModelChooser — tier fast-path

Add fast-path at top of `choose()`:

```python
tier = (task or {}).get("model_tier", "").strip().lower()
if tier in {"micro", "small", "medium", "standard", "strong"}:
    candidates = await self._tier_to_models(tier)
    if candidates:
        # Apply project local policy, health ranking
        candidates = await self._rank_by_health_and_cost(candidates, ...)
        local_policy = await self._get_project_local_policy(...)
        # Apply local policy filtering
        return ModelChoice(model=candidates[0], ...)
# Fallback: existing complex logic unchanged
```

`_tier_to_models(tier)` reads directly from DB settings (MODEL_ROUTER_TIER_SMALL_KEY etc.).
Reads same keys as existing `_load_runtime_config()` — no new DB queries.

Keep existing complex path as fallback. Don't remove it.

### 3. Fix session_ref paths in workflow_seeds.py

All session_ref paths that use `.output.childSessionKey` must be changed to `.childSessionKey`.
Paths that use `.session_key` for session targeting must also change to `.childSessionKey`.

Affected paths:
- "write_code.output.childSessionKey" → "write_code.childSessionKey"
- "write_code.session_key" (in send_to_session) → "write_code.childSessionKey"  
- "research.output.childSessionKey" → "research.childSessionKey"
- "spawn_programmer.output.childSessionKey" → "spawn_programmer.childSessionKey"
- "spawn_programmer_fix_1.output.childSessionKey" → "spawn_programmer_fix_1.childSessionKey"
- "spawn_programmer_fix_2.output.childSessionKey" → "spawn_programmer_fix_2.childSessionKey"
- "spawn_researcher.output.childSessionKey" → "spawn_researcher.childSessionKey"
- "spawn_writer.output.childSessionKey" → "spawn_writer.childSessionKey"
- "spawn_architect.output.childSessionKey" → "spawn_architect.childSessionKey"
- "spawn_reviewer.output.childSessionKey" → "spawn_reviewer.childSessionKey"
- "spawn_inbox.output.childSessionKey" → "spawn_inbox.childSessionKey"

Also fix the docstring in cleanup node in workflow_nodes.py.

---

## Implementation Handoffs

### Handoff 1: Tier Classifier Service + Task Creation Hook

File: app/services/tier_classifier.py (new)
File: app/routers/tasks.py (modify create_task)

Acceptance criteria:
- POST /api/tasks with no model_tier → response has model_tier populated
- POST /api/tasks with model_tier="strong" → preserved unchanged
- Tasks for lobs-server project → tier is "standard" or "strong", never "small"
- LLM unavailable → defaults to "standard" without error

### Handoff 2: ModelChooser Tier Fast-Path

File: app/orchestrator/model_chooser.py

Acceptance criteria:
- task with model_tier="small" → choose() returns model from MODEL_ROUTER_TIER_SMALL_KEY list
- task with model_tier="strong" → choose() returns model from MODEL_ROUTER_TIER_STRONG_KEY list
- task with no model_tier → existing logic applies (no regression)
- No LLM calls in model selection path

### Handoff 3: Fix Session Ref Paths

File: app/orchestrator/workflow_seeds.py
File: app/orchestrator/workflow_nodes.py (docstring fix)

Acceptance criteria:
- All session_ref values use .childSessionKey (not .output.childSessionKey or .session_key)
- python -c "from app.orchestrator.workflow_seeds import WORKFLOW_SEEDS; print('OK')" passes
- After a programmer task completes, cleanup node logs deleted_sessions with actual key (not empty)

---

## Testing Strategy

### Tier classification
- Unit: classify_task_tier with "fix typo in README" → "small"
- Unit: classify_task_tier with lobs-server project → >= "standard"
- Integration: POST /api/tasks → verify model_tier in response

### ModelChooser fast-path
- Unit: task with model_tier="small" → returns small-tier model
- Unit: task with model_tier="strong" → returns strong-tier model

### Session cleanup
- After fix: context["write_code"]["childSessionKey"] resolves correctly
- Cleanup node logs actual session key, not empty list

---

## Tradeoffs

**Keep existing ModelChooser** vs rewrite: Keeping. Budget guards and health ranking work. Fast-path is additive. Rewrite risk is too high.

**LLM at task creation**: Adds ~1-2s latency. Acceptable because classification is deterministic-first. 3s timeout prevents blocking.

**No Gateway session deletion**: auto-archive at 5min is sufficient.

---

## Files Affected

- app/services/tier_classifier.py — NEW
- app/routers/tasks.py — add classification after flush
- app/orchestrator/model_chooser.py — add tier fast-path
- app/orchestrator/workflow_seeds.py — fix all session_ref paths
- app/orchestrator/workflow_nodes.py — fix cleanup docstring
