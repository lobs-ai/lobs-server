# Agent Lifecycle Architecture

**Last Updated:** 2026-02-18  
**Status:** Canonical

This document describes how agent types are defined, recruited, executed, observed, and evolved in `lobs-server`.

---

## 1) System Model

`lobs-server` treats agents as a **decoupled, extensible registry** loaded from disk (`agents/<agent_type>/`) plus runtime state in SQLite.

Core layers:

1. **Definition layer (filesystem):** `app/orchestrator/registry.py` loads agent prompts/identity.
2. **Execution layer (orchestrator):** `app/orchestrator/engine.py` and `worker.py` spawn sessions.
3. **Control-loop layer:** reflection, diagnostics, sweep arbitration, and daily compression.
4. **State/API layer:** SQLAlchemy models in `app/models.py` + `/api/agents`, `/api/orchestrator`, `/api/worker`.

---

## 2) Baseline Agent Set (Current)

Execution-agent directories currently present in `agents/`:

- `architect`
- `programmer`
- `researcher`
- `reviewer`
- `writer`

Control-plane identities (not treated as execution workers):

- `lobs`
- `project-manager`
- `sink`

Source of truth for control-plane exclusions: `app/orchestrator/config.py` (`CONTROL_PLANE_AGENTS`).

---

## 3) Agent Definition Contract

Each agent type must have a directory at `agents/<type>/` containing:

- `AGENTS.md`
- `SOUL.md`
- `TOOLS.md`
- `IDENTITY.md`
- `USER.md`

Loader: `AgentRegistry` in `app/orchestrator/registry.py`.

`IDENTITY.md` must provide bullets parseable as:

- `**Model:** ...`
- `**Capabilities:** cap1, cap2, ...`
- `**Proactive:** ...` (optional)

If required files/fields are missing, registry load fails for that type.

---

## 4) End-to-End Agent Lifecycle

## A. Recruit / Create

1. Add `agents/<new_type>/` with required files.
2. Define capabilities in `IDENTITY.md`.
3. (Optional ops bootstrap) run `POST /api/agents/setup` to configure workspaces via `bin/setup-agents`.

## B. Register Runtime Metadata

- `AgentRegistry.available_types()` discovers the new type automatically.
- Hourly capability sync (`CapabilityRegistrySync` in engine) writes capabilities to `agent_capabilities`.

## C. Receive Work

- Task assigned with `tasks.agent = <type>`.
- Scanner/engine picks eligible task.
- Worker spawned via OpenClaw Gateway and tracked in `worker_runs` / `worker_status`.

## D. Publish Outcomes

- Worker writes run summary (`worker_runs.summary`).
- Task/workflow state updates occur through DB-backed orchestrator flow.

## E. Reflect and Evolve

- Strategic reflections (default every 6h) persist to `agent_reflections`.
- Initiative extraction creates `agent_initiatives`.
- Daily compression writes `agent_identity_versions` snapshots.
- System sweeps are logged in `system_sweeps`.

---

## 5) Control Loops and Cadence

Implemented in `app/orchestrator/engine.py` + runtime overrides (`orchestrator_settings`).

Default cadences (`app/orchestrator/runtime_settings.py`):

- Strategic reflection: every `21600s` (6h)
- Sweep arbitration: every `900s` (15m)
- Diagnostic triggers: every `600s` (10m)
- GitHub sync: every `120s`
- OpenClaw model catalog sync: every `900s`
- Daily compression: once/day at `08:00 UTC` guard (`orchestrator.daily_compression.hour_utc`)

Reflection and compression managers: `app/orchestrator/reflection_cycle.py`.

Sweep arbitration: `app/orchestrator/sweep_arbitrator.py`.

Diagnostic spawning: `app/orchestrator/diagnostic_triggers.py`.

---

## 6) DB-Mediated Inter-Agent Communication

The system avoids direct agent-to-agent RPC. Agents coordinate through persisted shared state:

- **Task handoff:** `tasks` table (`agent`, `status`, `work_state`, `review_state`)
- **Execution outcomes:** `worker_runs`
- **Operational visibility:** `agent_status`
- **Strategic outputs:** `agent_reflections`
- **Proposals/governance:** `agent_initiatives`
- **Identity memory snapshots:** `agent_identity_versions`
- **Global decisions and batch state:** `system_sweeps`
- **Human-in-the-loop control:** `inbox_items`

Cross-agent context is assembled from DB by `ContextPacketBuilder` (`app/orchestrator/context_packets.py`), including other-agent activity summaries and backlog/performance signals.

---

## 7) Key Tables (Agent Lifecycle)

- `agent_status`
- `agent_capabilities`
- `worker_status`
- `worker_runs`
- `agent_reflections`
- `agent_initiatives`
- `agent_identity_versions`
- `system_sweeps`
- `orchestrator_settings`

All defined in `app/models.py`.

---

## 8) Related Docs

- `docs/agent-operations-playbook.md`
- `docs/agent-api-contracts.md`
- `docs/ADAPTIVE_MULTI_AGENT_CONTROL_LOOP.md`
- `ARCHITECTURE.md`
