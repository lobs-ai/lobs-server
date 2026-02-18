# Orchestrator Model Router (First Pass)

This document describes the initial model-routing policy in `app/orchestrator/model_router.py` and how it is used by `WorkerManager`.

## Goals

- Classify tasks with deterministic heuristics (no LLM in classifier).
- Route by task type/complexity/criticality.
- Try an ordered fallback chain when provider/model calls fail.
- Record model selection + fallback attempts in worker run logs.

## Routing Policy (v1)

### Programming tasks
- Primary: `anthropic/claude-sonnet-4-5`
- Fallback: `anthropic/claude-opus-4-6`

### Light inbox tasks
(when `task.status == "inbox"` for non-programmer specialist agents)
- Primary: `anthropic/claude-haiku-4-5`
- Secondary: `google-gemini-cli/gemini-3-pro-preview`
- Then: `anthropic/claude-sonnet-4-5`
- Last resort: `anthropic/claude-opus-4-6`

### Non-programming default
- Primary: `anthropic/claude-sonnet-4-5`
- Add `anthropic/claude-opus-4-6` fallback when classified as `very_complex`.

### High criticality override
- Ensure `anthropic/claude-opus-4-6` is present in fallback chain.

## Classification Inputs

Heuristic signals include:
- title + notes text
- keyword checks for complexity and criticality
- word-count thresholds
- task status (`inbox` fast path)

Output:
- `complexity`: `light | standard | very_complex`
- `criticality`: `low | normal | high`
- ordered model list + policy tag + audit payload

## Failure Handling

`WorkerManager.spawn_worker` now:
1. gets an ordered model list from `decide_models(...)`
2. attempts `sessions_spawn` with each candidate in order
3. stops on first accepted run
4. records per-attempt metadata (`ok`, `error`, index, model)

If all attempts fail, worker spawn returns `False` and logs structured audit data.

## Audit Logging

The following are emitted/recorded:
- router decision payload (`policy`, `complexity`, `criticality`, candidate list)
- fallback attempt list
- chosen model
- whether fallback was used and reason

Persisted in worker history (`WorkerRun.task_log.model_router`) and worker `model` field.

## Tests

`tests/test_model_router.py` covers:
- programmer default routing
- light inbox cheap-tier routing
- very-complex routing with opus fallback
- high-criticality fallback strengthening
- fallback attempt behavior in `WorkerManager` (first candidate fails, next succeeds)
