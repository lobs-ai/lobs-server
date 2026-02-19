# Agent Operations Playbook

**Last Updated:** 2026-02-18  
**Audience:** Operators and contributors adding/managing agent types

This playbook is the procedural companion to `docs/agent-lifecycle-architecture.md`.

---

## 1) Recruit a New Agent Type (End-to-End)

## Step 1: Create the agent definition folder

Create `agents/<agent_type>/` with:

- `AGENTS.md`
- `SOUL.md`
- `TOOLS.md`
- `IDENTITY.md`
- `USER.md`

`IDENTITY.md` must include parseable bullets for model/capabilities.

## Step 2: Verify registry discovery

`AgentRegistry.available_types()` (in `app/orchestrator/registry.py`) should include the new folder name.

## Step 3: Initialize agent workspaces (optional but recommended)

Use:

- `POST /api/agents/setup`

This runs `bin/setup-agents` and returns discovered setup entries.

## Step 4: Set/update operational status record

Use:

- `PUT /api/agents/{agent_type}`

to create/update `agent_status` state fields (`status`, `activity`, etc.).

## Step 5: Let capability sync populate routing metadata

Capability sync runs hourly in orchestrator (`CapabilityRegistrySync`). It ingests `IDENTITY.md` capabilities into `agent_capabilities`.

## Step 6: Assign a task to the new agent

Create/update a task with:

- `tasks.agent = <agent_type>`
- `work_state = not_started|ready`

Engine then spawns worker runs as capacity/locks permit.

## Step 7: Confirm execution + control-loop participation

Validate via:

- `GET /api/worker/activity`
- `GET /api/orchestrator/status`
- `GET /api/orchestrator/intelligence/summary`

New execution agents are automatically included in reflection/compression/sweep loops unless listed in `CONTROL_PLANE_AGENTS`.

---

## 2) Manage Existing Agents

Common operations:

1. **Read current statuses:** `GET /api/agents`
2. **Inspect single status:** `GET /api/agents/{agent_type}`
3. **Patch operational state:** `PUT /api/agents/{agent_type}`
4. **Read prompt/memory files:** `GET /api/agents/{agent_type}/files/{filename}`
5. **Update prompt/memory files:** `PUT /api/agents/{agent_type}/files/{filename}`

Safety note: file endpoints reject path traversal patterns.

---

## 3) Reflection / Compression Operations

## Strategic reflections

- Triggered by orchestrator at runtime interval (`orchestrator.interval.reflection_seconds`, default 6h).
- One reflection row per execution agent per cycle: `agent_reflections` with `reflection_type="strategic"`.
- Worker completion path persists structured JSON output and initiative candidates.

## Reactive diagnostics

- Triggered every diagnostic interval (default 10m) when trigger criteria hit.
- Written as `reflection_type="diagnostic"` rows.

## Initiative sweep arbitration

- Runs every 15m.
- Applies policy, budget, dedupe, and quality gates.
- Produces approvals/deferred/review/rejections via `agent_initiatives` + `inbox_items` + `system_sweeps`.

## Daily compression

- Once/day guard after configured UTC hour.
- Compresses last 24h strategic/diagnostic reflections into new `agent_identity_versions` row(s).

---

## 4) Runtime Control Knobs

Use orchestrator APIs (see contracts doc):

- Pause/resume orchestrator
- Update loop intervals (`PUT /api/orchestrator/runtime/intervals`)
- Change daily compression hour (`daily_compression_hour_utc`)
- Update model policy and router tiers
- Adjust per-agent autonomy budgets

All runtime overrides persist in `orchestrator_settings`.

---

## 5) Troubleshooting Checklist

## New agent not appearing

- Check directory exists under `agents/`.
- Ensure all required files are present.
- Ensure `IDENTITY.md` has `Model` + `Capabilities` bullets with expected markdown format.

## Agent discovered but not getting work

- Confirm task has explicit `agent` field.
- Confirm task `work_state` is `not_started` or `ready`.
- Check orchestrator paused state: `GET /api/orchestrator/status`.
- Check project/circuit-breaker and worker capacity constraints.

## Capabilities not synced

- Wait for hourly sync or restart orchestrator.
- Verify parseable `Capabilities` list in `IDENTITY.md`.

## Reflection records missing

- Confirm orchestrator running and OpenClaw available.
- Check reflection interval settings in `/api/orchestrator/runtime`.
- Check worker spawn errors in logs and reflection rows with `status=failed`.

---

## 6) Change Management Requirement

When lifecycle behavior changes:

1. Update this playbook and architecture/contracts docs.
2. Add an entry in `CHANGELOG.md` under `[Unreleased]`.
3. Include endpoint/model impact in the changelog note.
