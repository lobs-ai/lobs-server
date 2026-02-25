# Diagnostic Review — Model Spend Guardrails + Auto-Downgrade Policy

**Task ID:** `124b5b33-2a6e-4db5-8293-5529d224ae82`  
**Diagnostic ID:** `diag_124b5b33-2a6e-4db5-8293-5529d224ae82_1771975866`  
**Date:** 2026-02-25  
**Reviewer:** reviewer agent

---

## TL;DR

**The task is actually completed.** The feature is fully implemented in production code and all 106 budget-related tests pass. The repeated agent failures were **not caused by broken code** — they were caused by agents running into context/timeout limits (evident from the error log: *"No assistant response in deleted transcript"*). Each retry spawned a new agent that re-implemented the feature rather than recognizing the prior work was already done.

---

## Root Cause of Failures

### Primary: Context Exhaustion / Transcript Deletion

The error log for every failed attempt contains:
```
No assistant response in deleted transcript
```

This is **not a code bug**. It means the agent's conversation transcript was deleted before it could produce a response — most likely because the context window was exhausted. The programmer was likely reading/re-reading large files (model_chooser.py, worker.py, models.py) to understand the codebase, running out of tokens, and the orchestrator detected a missing response.

### Secondary: Multiple Agents, Redundant Implementations

Because each retry spawned a fresh agent with no context of prior work, multiple versions of the budget guardrail were written across 10+ commits:
- `app/orchestrator/budget_guard.py` — second, cleaner implementation (BudgetGuard class with DB-backed policy)
- `app/orchestrator/budget_guardrails.py` — first implementation (standalone functions + daily report)
- `app/orchestrator/model_chooser.py` — imports from **both** files

This dual-implementation is not a bug but creates maintenance overhead.

---

## Implementation Status: ✅ COMPLETE

All required features are implemented and tested:

### Features Delivered

| Feature | Status | Location |
|---|---|---|
| Budget lane classification (critical/standard/background) | ✅ | `budget_guard.py:classify_task_lane()` |
| Per-lane daily spend caps with DB-backed config | ✅ | `budget_guard.py:BudgetGuard` |
| Model tier downgrade when cap exceeded | ✅ | `budget_guard.py:_filter_to_tier_and_below()` |
| Global daily hard cap (last-resort guardrail) | ✅ | `budget_guardrails.py:apply_daily_hard_cap()` |
| BudgetGuard wired into ModelChooser.choose() | ✅ | `model_chooser.py` (layer 2 of 3-layer guard) |
| Override log (rolling, persisted to DB) | ✅ | `budget_guard.py:_append_override_log()` |
| `GET /api/usage/budget-lanes` | ✅ | `routers/usage.py:552` |
| `PATCH /api/usage/budget-lanes` | ✅ | `routers/usage.py:583` |
| `GET /api/usage/daily-report` | ✅ | `routers/usage.py:633` |
| `ModelUsageEvent.budget_lane` column | ✅ | `models.py:750` |
| budget_lane tagged at worker spawn time | ✅ | `worker.py:297,421,430` |

### Test Results

```
106 passed, 966 deselected, 3 warnings in 19.07s
```

All budget-related tests pass. Test files:
- `tests/test_budget_guard.py` — 20+ unit + integration tests
- `tests/test_budget_guardrails.py` — 35+ tests covering endpoints, model chooser integration, hard cap

---

## Issues Found

### 🟡 Important: Dual Implementation Creates Maintenance Risk

Two files implement overlapping budget guardrail concerns:
- `budget_guard.py` — Primary class-based implementation (`BudgetGuard`)
- `budget_guardrails.py` — Older function-based implementation (some used in `model_chooser.py`)

`model_chooser.py` imports from both:
```python
from app.orchestrator.budget_guard import BudgetGuard
from app.orchestrator.budget_guardrails import (
    apply_daily_hard_cap,
    get_today_total_spend,
    append_override_log,
)
```

This works correctly but creates confusion: where does the logic live? Future maintainers (or agents) may modify one and not the other.

**Recommendation:** Consolidate `apply_daily_hard_cap`, `get_today_total_spend`, and `append_override_log` into `budget_guard.py`. Deprecate or remove `budget_guardrails.py` after migrating the daily report builder.

**Priority:** Important (not critical — current code is correct).

### 🟡 Important: Lane Spend Fallback Uses Double-Counting Heuristics

In `budget_guard.py:today_lane_spend()`, the legacy fallback for events without `budget_lane` column sums per-keyword independently for critical lane:

```python
for kw in _CRITICAL_KEYWORDS:
    r = await self.db.execute(...)
    fallback_total += float(r.scalar() or 0.0)
```

If a model name matches multiple critical keywords (e.g., "gpt-5-turbo-opus"), the spend would be double-counted in the critical lane. This is acknowledged in a comment as "an overestimate" but could lead to false positives (triggering downgrade when under actual budget).

**Recommendation:** Use `OR` filtering in a single query with `func.or_()` instead of summing per keyword separately.

### 🔵 Suggestion: `budget_guardrails.py` Lane Classification Disagrees with `budget_guard.py`

`budget_guardrails.py:classify_task_lane()` classifies `programmer` agents as `critical`:
```python
if is_high_criticality or (agent_type == "programmer" and purpose == "execution"):
    return "critical"
```

But `budget_guard.py:classify_task_lane()` classifies programmer as `standard` unless criticality is explicitly "high":
```python
if criticality == "high" or explicit_tier == "strong":
    return LANE_CRITICAL
```

The `ModelChooser` uses `budget_guard.py`'s version (via `BudgetGuard.apply()`), so in practice programmers are `standard` lane, not `critical`. This inconsistency is confusing but currently harmless since `budget_guardrails.py:classify_task_lane()` is not called from hot paths.

---

## Recommendation

**Mark this task as COMPLETE.** The implementation is done, tests pass, and endpoints are live.

The orchestration failure was a systemic issue (context exhaustion causing transcript deletion) that caused the escalation cascade. The underlying code never actually failed — agents just couldn't report success.

### Suggested Systemic Fix

For complex feature tasks that touch many files:
1. **Agents should check git log first** before starting work — "Did a previous agent commit anything?" This prevents re-implementing already-completed work.
2. **Context budget awareness** — Large codebase reads should be chunked. The programmer likely ran out of context reading files like `model_chooser.py` (800+ lines) and `worker.py` (1400+ lines) in full.

### Optional Follow-up Handoff

A cleanup handoff to merge `budget_guardrails.py` into `budget_guard.py` would reduce maintenance confusion, but is **not urgent**.
