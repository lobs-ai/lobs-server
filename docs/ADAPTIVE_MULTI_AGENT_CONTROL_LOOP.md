# Adaptive Multi-Agent Control Loop (Lobs-Governed)

Status: **in progress (scaffolding merged)**
Owner: lobs-server orchestrator

## Goal

Replace hard-coded PM-centric routing with a Lobs-governed adaptive loop:

- Agents reason locally and propose initiatives.
- Lobs performs global arbitration/sweep.
- Server owns deterministic scheduling, context packet construction, and trigger detection.
- Reflection/identity routines run continuously and compound over time.

---

## Control Loops

### 1) Event Loop (continuous)
- Trigger: task events, failures, stalls, idleness, regressions.
- Action: route task to best-fit capability/agent and execute with narrow context.

### 2) Strategic Reflection Loop (every 6 hours)
- Trigger: scheduler interval (`21600s`).
- Action:
  1. Build per-agent `AgentContextPacket` server-side.
  2. Spawn reflection session with strict JSON output schema.
  3. Persist `AgentReflection` + derived `AgentInitiative` rows.

### 3) Lobs Sweep (after reflection batch)
- Trigger: end of batch.
- Action:
  - Aggregate reflections.
  - Detect overlap/conflicts/gaps.
  - Gate proposals by policy tiers.
  - Emit approved initiatives/tasks.

### 4) Daily Compression (3am local target)
- Trigger: once per day guard.
- Action:
  - Gather last 24h reflections.
  - Create versioned identity snapshot per agent.
  - Archive in `AgentIdentityVersion`.

---

## New Data Model (implemented)

- `agent_capabilities` — capability registry for dynamic routing.
- `agent_reflections` — strategic/diagnostic outputs.
- `agent_initiatives` — proposal lifecycle from reflection outputs.
- `agent_identity_versions` — versioned daily compression output.
- `system_sweeps` — global Lobs sweep logs.

---

## Policy Tiers (implemented)

Policy engine assigns autonomy mode:

- **Tier A / auto**: low-risk recurring maintenance (`docs_sync`, `test_hygiene`, etc.)
- **Tier B / soft_gate**: moderate impact (`automation_proposal`, reprioritization)
- **Tier C / hard_gate**: architecture/destructive/cross-project/recruitment

This resolves "does writer/researcher need approval every time?" with standing mandates + bounded autonomy.

---

## Context Packet Schema (implemented)

`AgentContextPacket` currently includes:

- recent task summaries
- active backlog summary
- performance metrics
- other-agent recent activity
- placeholders for initiatives + repo change summary

Built server-side in `app/orchestrator/context_packets.py`.

---

## Current Implementation Footprint

### New modules
- `app/orchestrator/policy_engine.py`
- `app/orchestrator/context_packets.py`
- `app/orchestrator/reflection_cycle.py`
- `app/orchestrator/capability_registry.py`
- `app/orchestrator/capability_router.py`
- `app/orchestrator/sweep_arbitrator.py`
- `app/orchestrator/diagnostic_triggers.py`

### Engine integration
- Capability registry sync every hour.
- Reflection cycle every 6 hours.
- Daily compression guard once per day.
- Initiative sweep/arbitration every 15 minutes.
- Reactive diagnostic trigger scan every 10 minutes.

### Worker integration
- Reflection and diagnostic session outputs parsed as JSON.
- Strategic reflections derive initiatives with policy metadata.
- Diagnostics persist structured results to `agent_reflections`.

### API visibility
- `GET /api/orchestrator/intelligence/summary`
- `GET /api/orchestrator/intelligence/initiatives`

---

## Remaining work (next passes)

1. **Sweep contradiction detection upgrade**
   - Current sweep dedupes exact overlaps and applies policy/budgets.
   - Add contradiction detection across competing initiatives and auto-merge proposals.

2. **Initiative execution lifecycle depth**
   - Add explicit transitions: proposed → approved → active → completed/rejected.
   - Track outcome quality to improve future proposal scoring.

3. **Identity rewrite quality upgrades**
   - Replace placeholder compression text with structured synthesis from reflection payloads + run metrics.

4. **Capability confidence learning**
   - Adjust capability confidence using measured task outcomes.

5. **Scheduler timezone precision**
   - Daily compression currently uses UTC guard aligned for ET baseline.
   - Move to explicit timezone-aware cron for DST-safe 3am local execution.

---

## Operating Principle

- Deterministic server logic decides *when* and *what context*.
- LLM calls decide *how to reason* within that bounded packet.
- **All initiatives now route through Lobs decision authority** (`status=lobs_review`) with a recommendation, instead of auto-executing directly.
