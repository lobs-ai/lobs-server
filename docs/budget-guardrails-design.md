# Budget Guardrails + Auto-Downgrade Policy

**Status:** Implemented and tested  
**Date:** 2026-02-25

## Problem

As integration-triggered workloads increase, unbounded model spend on background/standard tasks
threatens sustainability. We need to enforce per-day spend caps by task criticality and gracefully
downgrade model tier when caps are breached — without breaking SLA for high-criticality work.

## Solution

A 3-layer budget enforcement chain wired into `ModelChooser.choose()`:

```
ModelChooser.choose()
  │
  ├─ 1. Per-provider monthly caps (_apply_budget_guards)
  │     Source: usage.budgets.per_provider_monthly_usd
  │
  ├─ 2. Per-lane daily spend caps (_apply_lane_budget_guard → BudgetGuard)
  │     Source: budget_guard.lane_policy (OrchestratorSetting)
  │     Lanes: critical / standard / background
  │
  └─ 3. Global daily hard cap (_apply_global_daily_cap)
        Source: usage.budgets.daily_hard_cap_usd
        Fallback: restrict to micro/small tier
```

### Lane Classification

Tasks are classified into one of three lanes before model selection:

| Lane | Criteria | Default Daily Cap | Downgrade Tier |
|------|----------|-------------------|----------------|
| `critical` | High-criticality keywords or explicit `strong` tier | None (uncapped) | None (SLA preserved) |
| `standard` | Default programmer/researcher/architect work | $8/day | ≤ medium |
| `background` | Writer/reviewer light tasks, inbox, reflections | $3/day | ≤ small |

### Downgrade Mechanism

When a lane's daily cap is reached:
1. `BudgetGuard.apply()` filters candidates to those in the allowed tier and below
2. If filtering removes all candidates, original list is kept (safety fallback)
3. Downgrade event is logged to `budget_guard.override_log` (rolling 500-entry ring buffer)
4. Chosen lane is written to `ModelUsageEvent.budget_lane` for accurate spend tracking

### Data Flow

```
task + agent_type
      │
      ▼
classify_task_lane()  → lane (critical|standard|background)
      │
      ▼
BudgetGuard.today_lane_spend()  → spent_usd (from ModelUsageEvent.budget_lane column)
      │
      ▼
over cap? → filter candidates to downgrade_tier and below
      │
      ▼
effective candidates → ModelChooser continues to health/cost ranking
      │
      ▼
ModelChoice.budget_lane set → tagged on ModelUsageEvent at worker spawn
```

## Key Files

| File | Purpose |
|------|---------|
| `app/orchestrator/budget_guard.py` | Core `BudgetGuard` class; lane classification; override log |
| `app/orchestrator/budget_guardrails.py` | Daily report builder; global hard cap utility; legacy override log |
| `app/orchestrator/model_chooser.py` | Wires budget guards into model selection (`choose()`) |
| `app/routers/usage.py` | `GET/PATCH /api/usage/budget-lanes`, `GET /api/usage/daily-report` |
| `tests/test_budget_guard.py` | Unit tests for BudgetGuard and lane classification |
| `tests/test_budget_guardrails.py` | Integration tests: endpoints, ModelChooser, hard cap |

## API Endpoints

### `GET /api/usage/budget-lanes`
Returns current lane policy with today's spend per lane and override log.

### `PATCH /api/usage/budget-lanes`
Update lane caps and downgrade tiers. Partial updates supported.

```json
{
  "standard": {"daily_cap_usd": 10.0, "downgrade_tier": "medium"},
  "background": {"daily_cap_usd": 5.0, "downgrade_tier": "small"}
}
```

### `GET /api/usage/daily-report`
Full daily cost-vs-quality report including:
- Total spend vs daily hard cap
- Per-provider spend breakdown
- Per-lane: spend, cap, utilization%, at-cap flag, downgrade tier
- Recent override events (auto-downgrades)
- Budget alerts (≥80% utilization or cap exceeded)

## Tradeoffs

**Why two files (budget_guard.py + budget_guardrails.py)?**  
`budget_guard.py` was added as a focused replacement for the broader utility functions in `budget_guardrails.py`. 
Both coexist: `budget_guard.py` owns the `BudgetGuard` class and per-lane cap logic (used by ModelChooser); 
`budget_guardrails.py` owns the daily report builder and global hard cap utility. They complement rather 
than duplicate each other.

**Why `OrchestratorSetting` for the override log?**  
Lightweight. No new table needed. The rolling 500-entry ring buffer is sufficient for observability 
without creating a long-term DB footprint. Full history lives in `ModelUsageEvent`.

**Critical lane is uncapped by default.**  
SLA preservation for urgent/prod/security tasks takes priority over cost. Operators can configure a 
cap if needed via `PATCH /api/usage/budget-lanes`.

## Testing

All 114 tests pass. Coverage includes:
- Lane classification correctness
- Spend queries with explicit `budget_lane` and fallback heuristics
- Downgrade triggering and filtering
- Safety fallback when downgrade removes all candidates
- Override log persistence
- API endpoints (budget-lanes GET/PATCH, daily-report)
- ModelChooser integration (lane guard + hard cap)
