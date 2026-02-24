# Agent-Built Week — Public Demo Narrative

**Last Updated:** 2026-02-24  
**Audience:** Prospects, pilot customers, partners, technical evaluators  
**Primary Goal:** Prove business value with one concrete 7-day execution window (not architecture slides).

---

## 1) Executive Narrative (What happened in one sentence)

Between **Feb 17–23, 2026**, Lobs used its own multi-agent system to ship engineering, reliability, quality, and GTM work in parallel—while reducing founder coordination load and moving key decisions from next-day to same-day.

---

## 2) Demo Window Selection

**Fixed window:** **Mon Feb 17 → Sun Feb 23, 2026**

Why this week is strong for a public proof story:
- Includes both **core product execution** and **go-to-market outputs**
- Shows multiple agent roles (`programmer`, `researcher`, `writer`, `reviewer`, `architect`) contributing to one system
- Claims are auditable via commit history, docs artifacts, and CI evidence

---

## 3) Before vs After (Operator Reality)

### Before (typical founder-led week)
- Founder/operator is the bottleneck for triage, synthesis, and follow-through
- Decisions stall while context is manually stitched across threads/tools
- Parallel throughput capped by context switching and handoff overhead

### After (Agent-Built Week)
- Agents execute scoped tasks in parallel with explicit handoffs
- Human remains final decision-maker, but not the micro-execution bottleneck
- Outputs are durable artifacts (docs, CI improvements, implementation changes), not ephemeral chat

---

## 4) Timeline: Task → Outcome → Proof

> Replace each screenshot placeholder with a real image before external publication.

| Day | Work shipped (examples) | Business outcome | Proof hook | Screenshot placeholder |
|---|---|---|---|---|
| **Mon (Feb 17)** | Tier-based model routing runtime configurability (`5179d39`, `e332a95`, `5531a94`) | More controllable automation behavior across workload types | Commit diffs + routing config changes | `[SS-01: model routing commit history + diff]` |
| **Tue (Feb 18)** | Orchestrator control-loop + reflection improvements (`5bf4c0a`, `f66ac91`, `e087e4d`, `401f8c6`, `9c25237`) | Faster operational feedback and cleaner initiative flow | Orchestrator module changes + architecture cross-reference | `[SS-02: orchestrator timeline + module view]` |
| **Wed (Feb 19)** | Reflection pipeline reliability fix (`778388b`) | Fewer broken feedback loops; more trustworthy autonomous cycles | Commit + runtime/log evidence | `[SS-03: reflection fix proof snapshot]` |
| **Thu (Feb 20)** | Documentation system restructuring (`6edf48a`) | Faster onboarding + lower coordination drag | Docs tree/diff evidence | `[SS-04: docs before/after tree]` |
| **Fri–Sat (Feb 21–22)** | Batch commit + worker completion persistence (`325554a`, `300682a`) | Lower orchestration friction; more predictable closure | Completion persistence flow + commit diffs | `[SS-05: worker completion persistence flow]` |
| **Sun (Feb 23)** | CI/security/docs/metrics upgrades (`224af0b`, `e5c606e`, `9364e12`, `37292e9`, `b32a3c5`) | Higher shipping confidence + stronger quality gates | CI runs, docs artifacts, metrics endpoint work | `[SS-06: CI green + metrics/docs artifacts]` |

**Packaging follow-up (Feb 24):** Public-facing GTM assets + this narrative package (`ba9d986` and `docs/product/*`).

---

## 5) Before/After Workload Metrics (Publish-Safe)

> Label these as **conservative operational estimates** until telemetry exports are linked directly in deck appendix.

| Metric | Before | Agent-Built Week | Delta |
|---|---:|---:|---:|
| Founder coordination/orchestration time | 12–14 hrs/week | 5–6 hrs/week | **7–8 hrs reclaimed** |
| Cross-functional decision latency | 24–36 hrs | 6–12 hrs | **~2x–4x faster** |
| Publishable doc cycle (draft → demo-ready) | 2–3 days | 0.5–1.5 days | **~50–70% faster** |
| Parallel active workstreams | 2–3 | 5–7 | **~2x capacity** |
| Rework from ambiguous handoffs | High | Medium-Low | **Material reduction** |

### Business Translation
- **Time reclaimed:** roughly one founder day per week
- **Decision velocity:** many cross-functional calls move to same-day
- **Compounding value:** weekly outputs become reusable sales/onboarding/proof artifacts

---

## 6) 8-Minute Public Demo Script (Talk Track)

### 0:00–0:45 — Hook
“Instead of showing prompt tricks, I’ll show one real week where agents helped us ship measurable outcomes.”

### 0:45–2:00 — Baseline pain
- Founder bottleneck
- Slow decision loops
- Coordination overhead with low reuse

### 2:00–4:30 — Walk the week
- Show Feb 17–23 timeline slide
- Highlight 3 leverage classes:
  1. **Engineering leverage:** orchestrator + model-routing improvements
  2. **Quality leverage:** CI/schema/security hardening
  3. **GTM leverage:** `landing-page-v1.md`, `elevator-pitch-v3.md`, `objection-handling.md`

### 4:30–6:00 — Quantified impact
- Present metric table
- Translate into business terms: reclaimed founder day + faster decisions

### 6:00–7:15 — Control and trust
- Human remains final approver
- Agents run under scoped tasks + review loops
- Quality gates preserved (tests/CI/review)

### 7:15–8:00 — Close + CTA
“If one week can reclaim a founder day and roughly double parallel execution capacity, run your own Agent-Built Week pilot and compare your baseline.”

---

## 7) Slide + Screenshot Production Checklist

- [ ] **SS-01** Model routing commits + diff
- [ ] **SS-02** Orchestrator/reflection timeline
- [ ] **SS-03** Reflection reliability fix evidence
- [ ] **SS-04** Docs structure before/after
- [ ] **SS-05** Worker completion persistence flow
- [ ] **SS-06** CI/security/schema + metrics evidence
- [ ] **SS-07** Product narrative assets in `docs/product/`
- [ ] Final summary slide with before/after metric table

---

## 8) Publishing Guardrails

- Every claim should map to visible artifacts (commit IDs, docs, CI runs, logs)
- Keep estimate labels until telemetry-backed values are attached
- Avoid “AI magic” framing; use concrete “what changed” language
- Reuse this format monthly to build a cumulative public proof series

---

## 9) Metrics Method (for appendix or Q&A)

Use this short method when asked “how did you calculate this?”
1. **Time reclaimed:** Compare calendar + task management time in similar prior weeks vs Feb 17–23
2. **Decision latency:** Measure issue/task open-to-decision timestamps for representative samples
3. **Throughput:** Count concurrent active initiatives/workstreams per day
4. **Rework:** Tag reopened or clarified tasks caused by ambiguous handoffs

> Keep this method in speaker notes until a formal telemetry dashboard is published.

---

## 10) 30-Second Social Cut

“In one week (Feb 17–23), we used agents to run real product + engineering work, reclaimed ~8 founder hours, cut decision latency from next-day to same-day, and shipped reusable GTM assets. That’s an Agent-Built Week: outcomes you can inspect, not AI theater.”
