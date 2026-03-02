# Design: Task Tier Classification + Session Cleanup + Model Routing

**Status:** Implementation complete (verified 2026-03-01). Programmer needs to verify functional correctness.  
**Task ID:** FAD05A1F-5D54-4B27-A89B-BA876BF4AA4C

---

## Problem Statement

Three interconnected issues:
1. Tasks were starting without a `model_tier` set, forcing mid-workflow LLM classification
2. `ModelChooser` had complex heuristic routing instead of using explicit tier
3. Session cleanup not working — wrong API / missing session_refs in some workflows

---

## What Has Been Implemented

### 1. Task Tier Classification at Creation (✅ Complete)

**Files:** `app/services/task_tier.py`, `app/routers/tasks.py`

`classify_task_tier()` runs synchronously after task save in `POST /api/tasks`. Logic:
1. If `model_tier` already set by caller → keep it (caller override)
2. Call LM Studio (2s timeout) with title+notes+project+agent → small|standard|strong
3. Apply project hard minimums (lobs-server/lobs-mission-control/lobs-mobile → minimum standard)
4. Default: standard on any failure

### 2. ModelChooser — DB-Backed Tier Routing (✅ Complete)

**File:** `app/orchestrator/model_chooser.py`

When `task.model_tier` is set, ModelChooser uses explicit tier short-circuit:
- Reads tier→model lists from DB settings (MODEL_ROUTER_TIER_* keys)
- No LLM calls mid-workflow, no fuzzy heuristics
- Still runs budget guards, health ranking, project local policy

### 3. Session Cleanup — Gateway WebSocket API (✅ Complete)

**File:** `app/orchestrator/worker_gateway.py` — `delete_session()`

Uses WebSocket `sessions.delete` JSON-RPC method (NOT sessions_kill). Returns True on success or not_found (idempotent).

**File:** `app/orchestrator/workflow_nodes.py` — `_exec_cleanup()`

Supports session_ref (single), session_refs (list), delete_session (fallback).

**File:** `app/orchestrator/workflow_seeds.py`

All spawn workflows updated with session_refs:
- task-router: 8 spawn nodes all covered
- code-task: write_code.childSessionKey
- research-task: research.childSessionKey
- doc-upkeep: spawn_writer.childSessionKey
- Non-spawn workflows: delete_session: False (correct)

---

## Programmer Verification Needed

### 1. Verify delete_session works end-to-end

Test against live Gateway:
1. Run a task cycle
2. Check `openclaw sessions list` before and after
3. Look for `[GATEWAY] Deleted session` in logs

If WebSocket sessions.delete is rejected, try: `DELETE http://localhost:18789/api/sessions/{key}`

### 2. Verify tier classification fires at task creation

After `POST /api/tasks`, response should include `model_tier`. If LM Studio unreachable → defaults to `standard`.

### 3. Quick tests

```bash
# Task gets tier assigned
curl -s -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title": "Write unit tests for auth", "project_id": "lobs-server"}' \
  | python3 -m json.tool | grep model_tier
# Should show "model_tier": "standard" (hard minimum for lobs-server)

# Caller override respected
curl -s -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title": "Draft README", "model_tier": "small"}' \
  | python3 -m json.tool | grep model_tier
# Should show "model_tier": "small"

# After task completion, session count drops
openclaw sessions list
```

---

## Tradeoffs

**Synchronous classification** — adds 50-2000ms to POST /api/tasks. Worth it: task is ready to run immediately when orchestrator picks it up.

**LM Studio for classification** — 2s timeout + standard fallback makes it safe when local model is offline.

**DB settings for tier→model** — admin updates models without code deploy. The TIER_MODELS dict in the task spec was illustrative; actual values live in OrchestratorSetting rows.
