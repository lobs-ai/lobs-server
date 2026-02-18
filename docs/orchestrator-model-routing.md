# Orchestrator Model Routing (First Pass)

Introduced in Feb 2026 as a lightweight, reversible policy engine for worker model selection.

## Goals

- Deterministic task classification (complexity + criticality)
- Default model routing by task type
- Provider failure fallback chain
- Inspectable audit trail for model decisions

## Policy Summary

Implemented in `app/orchestrator/model_router.py` via `decide_models(agent_type, task)`.

### Classification

- **Complexity**: `light` / `standard` / `very_complex`
  - Keyword and word-count heuristics
  - Inbox + coordination-style tasks are biased to `light`
- **Criticality**: `low` / `normal` / `high`
  - `high` keywords (incident/outage/security/prod/auth/payment/etc)

### Model Routing Rules

- **Programmer tasks**
  - Primary: `anthropic/claude-sonnet-4-5`
  - Fallback: `anthropic/claude-opus-4`
- **Light inbox tasks**
  - Primary tier: `anthropic/claude-haiku-4-5`
  - Secondary tier: `google/gemini-2.5-flash`
  - Then `sonnet`, then `opus`
- **Other tasks**
  - Primary: `sonnet`
  - Add `opus` fallback when task is very complex or high criticality

## Provider Failure Handling

`WorkerManager.spawn_worker()` now attempts models in order until one `sessions_spawn` succeeds.

- Each attempt records:
  - candidate model
  - success/failure
  - provider error text (if any)
- If all models fail, worker spawn returns `False` and emits structured logs.

## Audit Logging

Audit metadata is emitted and persisted:

- Runtime log events include `extra={"model_router": ...}`
- Worker run history (`WorkerRun.task_log`) stores:
  - classifier result
  - selected policy
  - ordered model list
  - all spawn attempts
  - chosen model
  - fallback usage + reason

This keeps routing decisions inspectable and reversible without changing task payload schemas.

## Reversibility

The policy is isolated to:

- `app/orchestrator/model_router.py` (decision logic)
- `app/orchestrator/worker.py` (attempt loop + audit capture)

To roll back, revert these files (and related tests/docs) without affecting unrelated orchestration flow.
