# Orchestrator Model Router (First Pass)

This document describes the initial tier-based model-routing policy in `app/orchestrator/model_router.py` and how it is used by `WorkerManager`.

## Runtime updates (no restart)

Model tiers can be changed while the server is running via:
- `GET /api/orchestrator/model-router`
- `PUT /api/orchestrator/model-router`

`PUT` payload:
```json
{
  "tiers": {
    "cheap": ["anthropic/claude-haiku-4-5"],
    "standard": ["openai-codex/gpt-5.3-codex"],
    "strong": ["anthropic/claude-opus-4-6", "openai-codex/gpt-5.3-codex"]
  },
  "available_models": ["openai-codex/gpt-5.3-codex", "anthropic/claude-opus-4-6"]
}
```

Set a field to `null` to clear that DB override and fall back to env/defaults.

## Goals

- Classify tasks with deterministic heuristics (no LLM in classifier).
- Route by **tiers** (`cheap`, `standard`, `strong`) instead of hard-coded fixed providers.
- Resolve tiers from configurable model pools.
- Try an ordered fallback chain when provider/model calls fail.
- Record model selection + fallback attempts in worker run logs.

## Configuration

Routing config precedence is:
1. **DB runtime overrides** (`orchestrator_settings` table)
2. environment variables
3. in-code defaults

Environment variables:
- `LOBS_MODEL_TIER_CHEAP` (CSV)
- `LOBS_MODEL_TIER_STANDARD` (CSV)
- `LOBS_MODEL_TIER_STRONG` (CSV)
- `LOBS_AVAILABLE_MODELS` (optional CSV allow-list)

If no DB/env settings are present, router uses safe defaults from code.

### Example

```bash
export LOBS_MODEL_TIER_CHEAP="anthropic/claude-haiku-4-5,google-gemini-cli/gemini-3-pro-preview"
export LOBS_MODEL_TIER_STANDARD="openai-codex/gpt-5.3-codex,anthropic/claude-sonnet-4-5"
export LOBS_MODEL_TIER_STRONG="anthropic/claude-opus-4-6,openai-codex/gpt-5.2"

# Optional: limit to currently available models
export LOBS_AVAILABLE_MODELS="openai-codex/gpt-5.3-codex,anthropic/claude-haiku-4-5,google-gemini-cli/gemini-3-pro-preview"
```

## Routing Policy (v1)

### Programming tasks
- tier plan: `standard -> strong`

### Light inbox tasks
(when `task.status == "inbox"` for non-programmer specialist agents)
- tier plan: `cheap -> standard -> strong`

### Non-programming default
- tier plan: `standard`
- append `strong` when classified as `very_complex`

### High criticality override
- always include `strong` tier

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
- router decision payload (`policy`, `complexity`, `criticality`, tier plan)
- resolved tier model pools and optional availability allow-list
- fallback attempt list
- chosen model
- whether fallback was used and reason

Persisted in worker history (`WorkerRun.task_log.model_router`) and worker `model` field.

## Tests

`tests/test_model_router.py` covers:
- programmer standard/strong routing
- light inbox cheap-tier-first routing
- availability allow-list filtering
- high-criticality strong-tier inclusion
- fallback attempt behavior in `WorkerManager` (first candidate fails, next succeeds)
