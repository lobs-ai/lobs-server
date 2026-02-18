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

### Engine integration
- Reflection cycle every 6 hours.
- Daily compression guard once per day.
- Sweep record creation in `system_sweeps`.

### Worker integration
- Reflection session outputs parsed as JSON.
- Derived initiatives persisted with policy-tiered status.

### API visibility
- `GET /api/orchestrator/intelligence/summary`
- `GET /api/orchestrator/intelligence/initiatives`

---

## Remaining work (next passes)

1. **Capability-based routing execution path**
   - Route tasks by capability registry scoring before regex fallback.

2. **Sweep arbitration engine**
   - Deduplicate initiative overlap.
   - Detect contradictions.
   - Emit approved initiatives into tasks/inbox based on policy.

3. **Reactive diagnostic triggers**
   - Stalled task / repeated failures / idle drift should spawn lightweight diagnostic packets.

4. **Identity rewrite quality upgrades**
   - Replace placeholder compression text with structured synthesis from reflection payloads + run metrics.

5. **Writer/researcher standing mandate budgets**
   - Daily effort budgets and auto-execution quotas per agent.

---

## Operating Principle

- Deterministic server logic decides *when* and *what context*.
- LLM calls decide *how to reason* within that bounded packet.
- Lobs retains final global governance authority.
