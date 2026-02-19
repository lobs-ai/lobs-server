# Agent API Contracts

**Last Updated:** 2026-02-18  
**Scope:** Implemented server contracts for agent lifecycle, control loops, and operations

All routes below are mounted under `/api` and require Bearer auth.

---

## 1) Agent Management API (`app/routers/agents.py`)

## `GET /api/agents`
List agent status rows (`agent_status`).

## `GET /api/agents/{agent_type}`
Get one agent status row; `404` if missing.

## `PUT /api/agents/{agent_type}`
Create/update agent status row (upsert behavior).

## `GET /api/agents/{agent_type}/files/{filename}`
Read agent file from `AGENT_FILES_DIR/<agent_type>/<filename>`.

- Rejects path traversal (`..`, `/`, `\\`) with `400`.

## `PUT /api/agents/{agent_type}/files/{filename}`
Write/update file in `AGENT_FILES_DIR` with same traversal protections.

## `POST /api/agents/setup`
Runs `bin/setup-agents` and returns parsed list of configured agents.

---

## 2) Worker Runtime API (`app/routers/worker.py`)

## `GET /api/worker/status`
Reads singleton `worker_status` row (creates default row if absent).

## `PUT /api/worker/status`
Updates singleton worker status.

## `GET /api/worker/history`
Returns recent `worker_runs` rows (descending id).

## `GET /api/worker/activity`
Returns run activity plus joined task context (`task_title`, `project_id`, `agent`).

## `POST /api/worker/history`
Creates a `worker_runs` entry.

---

## 3) Orchestrator Control + Intelligence API (`app/routers/orchestrator.py`)

## Core control

- `GET /api/orchestrator/status`
- `POST /api/orchestrator/pause`
- `POST /api/orchestrator/resume`
- `GET /api/orchestrator/workers`
- `GET /api/orchestrator/health`

## Model router/runtime settings

- `GET /api/orchestrator/model-router`
- `PUT /api/orchestrator/model-router`
- `GET /api/orchestrator/runtime`
- `PUT /api/orchestrator/runtime/intervals`
- `PUT /api/orchestrator/runtime/model-policy`

`PUT /runtime/intervals` bounds:

- reflection/sweep/diagnostic/openclaw-model-sync: min 60s
- github-sync: min 30s
- daily compression hour: clamped `0..23`

## Intelligence/initiative surface

- `GET /api/orchestrator/intelligence/summary`
- `GET /api/orchestrator/intelligence/initiatives`
- `POST /api/orchestrator/intelligence/initiatives/{initiative_id}/decide`
- `GET /api/orchestrator/intelligence/budgets`
- `PUT /api/orchestrator/intelligence/budgets`

---

## 4) Persistence Contracts (Models)

Primary tables used by these APIs/jobs (`app/models.py`):

- `agent_status`
- `agent_capabilities`
- `worker_status`
- `worker_runs`
- `orchestrator_settings`
- `agent_reflections`
- `agent_initiatives`
- `agent_identity_versions`
- `system_sweeps`
- `tasks` (agent assignment and state)
- `inbox_items` (human review/escalation)

---

## 5) Runtime Setting Keys (Stored in `orchestrator_settings`)

From `app/orchestrator/runtime_settings.py`:

- `orchestrator.interval.reflection_seconds`
- `orchestrator.interval.sweep_seconds`
- `orchestrator.interval.diagnostic_seconds`
- `orchestrator.interval.github_sync_seconds`
- `orchestrator.interval.openclaw_model_sync_seconds`
- `orchestrator.reflection.last_run_at`
- `orchestrator.daily_compression.hour_utc`
- `model_router.strict_coding_tier`
- `model_router.degrade_on_quota`

Additional operational keys used in code paths:

- `autonomy_budget.daily`
- model-router tier keys (`model_router.tier.cheap|standard|strong`) and available models key

---

## 6) Control-Loop Job Contracts (Code-Level)

## Reflection cycle (`ReflectionCycleManager`)

- Writes `agent_reflections` (strategic, pending->completed/failed)
- Emits `system_sweeps` (`reflection_batch`)
- Strategic reflection payload may yield `agent_initiatives`

## Daily compression

- Reads last 24h strategic/diagnostic reflections
- Writes new `agent_identity_versions`
- Emits `system_sweeps` (`daily_cleanup`)

## Sweep arbitration (`SweepArbitrator`)

- Reads pending initiatives
- Applies governance decisions
- Writes initiative status/rationale, optional task conversion, and inbox items
- Emits `system_sweeps` (`initiative_sweep`)

---

## 7) Operator Notes

- Engine cadence and defaults are in `app/orchestrator/engine.py` and runtime settings.
- `CONTROL_PLANE_AGENTS` are excluded from execution reflection/compression logic.
- API routes are canonical for operators; direct DB edits should be avoided except emergency recovery.
