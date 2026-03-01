# Design: Task Tier Classification + Model Routing Overhaul

**Date:** 2026-03-01  
**Status:** Ready for implementation  
**Task ID:** FAD05A1F-5D54-4B27-A89B-BA876BF4AA4C

---

## Problem Statement

Three separate issues that compound into one: tasks often enter the workflow with `model_tier=None`, causing ModelChooser to do complex classification mid-execution; session cleanup has been silently broken (no-op); and workflows spawn agents without guaranteed tier context.

Specifically:
1. `task.model_tier` is null when the orchestrator picks it up — the tier gets classified mid-workflow by `_resolve_model_tier()` on every spawn, not once at creation time.
2. ModelChooser is complex: Ollama/LM Studio auto-discovery, budget guards, health ranking — all evaluated per task execution. The task assignment asks for a "clean tier-based routing" reading from DB settings directly.
3. Session cleanup `delete_session()` is already a confirmed no-op (OpenClaw doesn't expose a REST session-delete endpoint accessible from HTTP `/tools/invoke`). The `session_refs` wiring in workflow_seeds is already complete for most workflows. The cleanup issue is architectural, not code-missing.

---

## Current State (what exists)

### Task creation (app/routers/tasks.py)
- `create_task` saves `TaskModel` with whatever `model_tier` is in the payload (often null)
- No post-save classification happens
- `task.model_tier` field exists in DB already

### ModelChooser (app/orchestrator/model_chooser.py)
- Full-featured: Ollama/LM Studio discovery, budget guardrails, health ranking, cost scoring
- Reads tier to model lists from DB settings: MODEL_ROUTER_TIER_{MICRO,SMALL,MEDIUM,STANDARD,STRONG}
- The `decide_models()` function in model_router.py maps tier to candidates
- Complexity is justified but the entry point needs to be tier-driven, not agent-type heuristics

### Session cleanup (app/orchestrator/workflow_nodes.py)
- `delete_session()` is a confirmed no-op — OpenClaw auto-archives completed sessions after archiveAfterMinutes=5
- `_exec_cleanup` node already supports `session_ref` and `session_refs` config keys
- task-router workflow already wires all spawn nodes' childSessionKeys in done.config.session_refs
- programmer-task and research-task workflows already have `session_refs` in cleanup nodes

### Gateway session deletion
- The Gateway exposes `sessions.delete` only via WebSocket RPC protocol (not REST)
- `/tools/invoke` does NOT expose `sessions_delete` as a callable tool
- Recommendation: Keep no-op. Auto-archive at 5 min is acceptable. When OpenClaw exposes a REST endpoint or `/tools/invoke` tool for session deletion, wire it in `delete_session()`.

---

## Proposed Solution

### Change 1: Tier Classification at Task Creation

After `db_task` is flushed and refreshed in `create_task`, run tier classification:

```
1. If task.model_tier already set -> skip (caller override)
2. Project hard minimums:
   - project_id contains 'lobs-server', 'lobs-mission-control', 'lobs-mobile' -> minimum 'standard'
3. LLM classification using local qwen via LM Studio (title + notes + project + agent -> small|standard|strong)
   - If LM Studio unavailable -> default 'standard'
4. Default: 'standard'
```

Implementation location: new service `app/services/task_tier.py` with function `classify_task_tier(task: TaskModel, db: AsyncSession) -> str`.

Call site: In `create_task()` in `app/routers/tasks.py`, after `await db.flush()` and `await db.refresh(db_task)`, call classify, update `db_task.model_tier`, then commit.

Also accept on input: `TaskCreate` schema should accept `model_tier` as an optional field. If present and non-null, skip classification.

LLM call for classification: Use LM Studio's OpenAI-compatible endpoint (http://localhost:1234/v1/chat/completions) with whatever model is loaded. If unreachable or times out (2s), default to `standard`. Do NOT use ModelChooser for this — it's a chicken-and-egg problem.

Prompt:
```
Classify this software task into one tier:
- small: boilerplate, docs, config, experiments, scripts, simple bug fixes
- standard: production features, multi-file changes, API endpoints, refactors, research
- strong: architecture, security, complex debugging, system design, DB migrations

Task title: {title}
Project: {project_id}
Agent: {agent}
Notes (first 500 chars): {notes[:500]}

Reply with exactly one word: small, standard, or strong.
```

### Change 2: ModelChooser tier short-circuit

No major rewrite needed. The current ModelChooser is correct and already reads tier->model lists from DB. The issue: `decide_models()` in model_router.py uses agent type + task complexity heuristics to pick a tier instead of reading `task.model_tier` directly.

Fix: In `ModelChooser.choose()`, check `task.get("model_tier")` first. If set, use it as the tier — skip `decide_models()` heuristics. Fall through to health ranking, budget guards, and cost scoring as normal.

```python
# In ModelChooser.choose(), before calling decide_models():
explicit_tier = task.get("model_tier")
if explicit_tier and explicit_tier in TIER_ORDER:
    tier_candidates = tiers.get(explicit_tier) or []
    if tier_candidates:
        candidates = tier_candidates[:]
        # skip decide_models, continue with budget guards + health ranking
    else:
        decision = decide_models(...)  # fallback: tier list empty in DB
else:
    decision = decide_models(...)  # fallback: no tier set
```

Keep all downstream processing: budget guards, health ranking, routing policy. Add `audit["tier_source"]` = `"explicit"` or `"heuristic"` for observability.

### Change 3: Session cleanup — verify config, no code change needed

The session_refs are already wired in workflow_seeds.py. `delete_session()` is already a documented no-op. OpenClaw auto-archives at 5 min.

If sessions ARE accumulating, check openclaw.json config:
- `agents.defaults.subagents.archiveAfterMinutes` should be <= 10
- `agents.defaults.subagents.runTimeoutSeconds` should be <= 1800

The `delete_session()` placeholder is sufficient. No code change needed there.

---

## Implementation Plan

### Task 1 (Programmer): Tier classification at task creation
- Create `app/services/task_tier.py`: `classify_task_tier(task, db) -> str`
- Implement LM Studio call with 2s timeout, fallback to 'standard'
- Project hard minimums dict: lobs-server, lobs-mission-control, lobs-mobile -> standard minimum
- Hook into `create_task()` after flush/refresh
- Accept `model_tier` on `TaskCreate` schema as caller override (add if missing)
- Tests: unit test classification logic, mock LM Studio calls

### Task 2 (Programmer): ModelChooser explicit tier short-circuit
- In `ModelChooser.choose()`: if `task.model_tier` is set and in TIER_ORDER, bypass `decide_models()`
- Pull candidates directly from `tiers[task.model_tier]` (already loaded from DB)
- Keep all downstream: budget guards, health ranking, routing policy
- Add `audit["tier_source"]` for observability
- Tests: verify explicit tier bypasses decide_models, verify fallback when tier list empty

---

## Testing Strategy

Tier classification:
- POST /api/tasks with no model_tier -> task saved with non-null model_tier
- POST /api/tasks with model_tier="strong" -> not overwritten
- Task with project_id containing 'lobs-server' -> always >= 'standard'
- LM Studio down -> defaults to 'standard', no error thrown

ModelChooser short-circuit:
- Task with model_tier="small" -> chooser returns model from MODEL_ROUTER_TIER_SMALL list
- Task with model_tier="strong" -> chooser returns model from MODEL_ROUTER_TIER_STRONG list
- Empty tier list in DB -> falls back to decide_models() behavior

Session cleanup (verification):
- Run a task cycle, check session count via sessions_list before and after
- If sessions accumulate, check openclaw config archiveAfterMinutes

---

## Tradeoffs

LLM call at task creation: Adds ~200ms to task creation when LM Studio is available. Fire-and-forget as background task to keep API response fast. Default to 'standard' on any failure.

No ModelChooser rewrite: Keeps existing sophistication (budget guards, health ranking). Adds a clean short-circuit. Preserves all existing tests.

Session cleanup as no-op: Auto-archive at 5 min is acceptable. If accumulation is observed, check openclaw config first.

## Gateway Session Delete — Research Finding

Confirmed via Gateway control UI source (`/dist/control-ui/assets/index-C_C6XOMD.js`):
- Gateway supports `sessions.delete` via WebSocket RPC only
- REST `/tools/invoke` does NOT list `sessions_delete` as a tool
- DELETE `/api/sessions/{key}` returns "Method Not Allowed"
- The WebSocket call is: `client.request("sessions.delete", {key: sessionKey, deleteTranscript: true})`

To implement actual deletion, lobs-server would need to add a WebSocket Gateway client. This is not worth the complexity when auto-archive handles cleanup. Revisit if Gateway adds an HTTP endpoint.
