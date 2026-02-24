# Learning System Quick Start

**Last Updated:** 2026-02-24  
**Status:** MVP in progress — tracking + API endpoints not yet implemented  
**Spec:** [docs/learning-loop-mvp-design.md](learning-loop-mvp-design.md)

---

## Overview

The agent learning system tracks task outcomes and uses them to improve agent prompts over time. It has two layers:

1. **Outcome Ledger** — logs every agent run with task intent, prompt version, and outcome (success/failure/user-corrected)
2. **Prompt Enhancement** — injects relevant lessons from past failures into future prompts before task execution

The `PromptEnhancer` is fully implemented and live. The `OutcomeTracker` and API endpoints are the remaining MVP work.

---

## Current State

### Live Today
- `PromptEnhancer` — active in `app/orchestrator/prompt_enhancer.py`
- Worker Hook 1 (pre-spawn enhancement) — live in `worker.py:~241`
- A/B control group (20%) — live in `worker.py:~234`
- DB tables: `task_outcomes`, `outcome_learnings`

### Not Yet Built
- `OutcomeTracker` — nothing writes outcomes on completion yet
- `/api/agent-learning/*` endpoints
- Daily batch job

See [learning-loop-mvp-status.md](handoffs/learning-loop-mvp-status.md) for precise implementation status.

---

## API Reference (once built)

> **Note:** All learning endpoints use the prefix `/api/agent-learning`, not `/api/learning`.  
> `/api/learning` belongs to personal learning plans — a different system.

### Submit or update an outcome

```bash
curl -X POST "http://localhost:8000/api/agent-learning/outcomes" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "uuid",
    "outcome": "failure",
    "human_feedback": "Missing tests and no error handling",
    "reason_tags": ["missing_tests", "missing_error_handling"]
  }'
```

Outcome values: `success`, `failure`, `user-corrected`.

### Get learning summary

```bash
curl "http://localhost:8000/api/agent-learning/summary?since_days=30&agent_type=programmer" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Response includes: success rate, A/B lift (treatment vs. control), top failure patterns, active learnings, and pending suggestions awaiting approval.

### Approve or reject a pending lesson

```bash
curl -X PATCH "http://localhost:8000/api/agent-learning/learnings/{id}" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "approve"}'
```

Actions: `approve` (sets active + confidence=0.5), `reject`, `edit` (with `lesson_text`).

---

## What to Watch For

### Healthy System
- ✅ Recent outcomes being created (24h)
- ✅ Learnings extracted and active
- ✅ A/B split ~20% control group
- ✅ Confidence ≥0.5 on active learnings
- ✅ Positive A/B lift (treatment group outperforms control)

### Warning Signs
- ⚠️ No recent outcomes → `OutcomeTracker` may not be wired up
- ⚠️ No active learnings → Batch job hasn't run or all suggestions are pending approval
- ⚠️ A/B split far from 20% → Check `LEARNING_CONTROL_GROUP_PCT` env var
- ⚠️ Negative lift → Active learnings may be hurting; audit and deactivate
- ⚠️ Many low-confidence learnings → Review quality; batch will auto-deactivate if confidence < 0.3 and failure_count ≥ 3

---

## Database Schema

### task_outcomes

Records every agent task completion.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `task_id` | UUID | FK to tasks |
| `agent_type` | str | programmer, researcher, writer, etc. |
| `success` | bool | True if task completed without rejection |
| `learning_disabled` | bool | True = A/B control group (no learnings injected) |
| `applied_learnings` | JSON | IDs of OutcomeLearning rows that were injected |
| `human_feedback` | text | Manual feedback from review |
| `context_hash` | str | SHA-256 prefix grouping similar tasks |
| `created_at` | timestamp | |

### outcome_learnings

Extracted lessons from failure patterns.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | Primary key |
| `agent_type` | str | Which agent this applies to |
| `pattern_name` | str | e.g. `missing_tests`, `missing_error_handling` |
| `lesson_text` | text | What gets injected into the prompt |
| `confidence` | float | 0.0–1.0 based on success/failure ratio |
| `success_count` | int | Times applied and task succeeded |
| `failure_count` | int | Times applied and task failed |
| `is_active` | bool | If false, not injected |

---

## Environment Variables

| Variable | Default | Effect |
|----------|---------|--------|
| `LEARNING_ENABLED` | `true` | Master switch |
| `LEARNING_INJECTION_ENABLED` | `true` | Prompt injection only |
| `LEARNING_CONTROL_GROUP_PCT` | `0.2` | A/B control group fraction |
| `MAX_LEARNINGS_PER_PROMPT` | `3` | Max lessons per prompt |
| `MIN_CONFIDENCE_THRESHOLD` | `0.3` | Minimum confidence to inject |
| `LEARNING_BATCH_ENABLED` | `true` | Daily batch |

---

## A/B Lift

The system computes lift as:

```
lift = (treatment_success_rate - control_success_rate) / control_success_rate
```

Positive lift confirms the system is working. The `GET /api/agent-learning/summary` response includes both success rates and the computed lift.

The control group (20% of runs) receives no learning injection. This is controlled by `LEARNING_CONTROL_GROUP_PCT` and is live today in `worker.py`.

---

## Troubleshooting

**No outcomes after tasks run:**
- `OutcomeTracker.track_completion()` is not yet wired into `worker.py` — see [implementation guide](handoffs/learning-loop-mvp-implementation-guide.md)

**No learnings showing in summary:**
- The daily batch job hasn't run yet (scheduled 2am ET)
- Or all suggestions are pending human approval (check `pending_suggestions` in the summary response)

**Summary endpoint returns 404:**
- Router not registered — ensure `app/main.py` includes the `agent_learning` router at prefix `/api/agent-learning`

---

## Reference

- [Learning Loop MVP Design](learning-loop-mvp-design.md) — full specification
- [Implementation Guide](handoffs/learning-loop-mvp-implementation-guide.md) — step-by-step build instructions
- [Current Status](handoffs/learning-loop-mvp-status.md) — what's built vs. missing
- [Handoff JSON](handoffs/learning-loop-mvp-handoff.json) — acceptance criteria
