# Agent-Built Week — Public Demo Narrative Package

**Last Updated:** 2026-02-24  
**Audience:** Prospects, design partners, investors, technical evaluators  
**Purpose:** Prove real-world value with one inspectable 7-day execution window.

---

## 1) One-Line Story

In one week (**Feb 17–23, 2026**), agents shipped production-adjacent engineering and GTM artifacts that reclaimed roughly a founder day of execution time and cut decision latency from next-day to same-day.

---

## 2) Why This Week Was Chosen

This 7-day window shows a credible mix of outcomes:
- Core platform improvements (routing/orchestrator/reliability)
- Quality + safety hardening (CI/schema/security scanning)
- Reusable GTM collateral (pitch/landing/objection docs)

It is evidence-based, not narrative-only: commit history, docs artifacts, and CI proof exist.

---

## 3) Before vs After (Operating Model)

### Before (founder as bottleneck)
- Founder owned triage, execution routing, and synthesis
- Decisions routinely waited 24–36 hours
- Parallel active workstreams capped at ~2–3

### After (Agent-Built Week model)
- Work is delegated in scoped packages with explicit acceptance criteria
- Human remains final decision-maker at key checkpoints
- Outputs arrive as reviewable artifacts (commits/docs/tests), reducing rework

---

## 4) 7-Day Timeline: Task → Outcome → Evidence

> **Publishing note:** Replace screenshot placeholders with final captures before external release.

| Day | What shipped | Immediate outcome | Value created | Evidence | Screenshot placeholder |
|---|---|---|---|---|---|
| **Mon (Feb 17)** | Tier-based model routing configurability (`5179d39`, `e332a95`, `5531a94`) | Routing tunable by workload tier | Better quality/cost control | Commit diffs + config changes | `[SS-01: Routing commits + config diff]` |
| **Tue (Feb 18)** | Orchestrator control-loop + reflection improvements (`5bf4c0a`, `f66ac91`, `e087e4d`, `401f8c6`, `9c25237`) | Faster execution feedback loops | Lower stall risk, faster interventions | Orchestrator module diffs | `[SS-02: Orchestrator timeline/modules]` |
| **Wed (Feb 19)** | Reflection reliability fix (`778388b`) | Reflection pipeline stabilized | Higher trust in autonomous loop | Commit + run/log excerpt | `[SS-03: Reliability fix proof]` |
| **Thu (Feb 20)** | Docs information architecture restructure (`6edf48a`) | Navigation + onboarding improved | Lower coordination overhead | Docs tree before/after | `[SS-04: Docs before/after]` |
| **Fri–Sat (Feb 21–22)** | Worker completion persistence + batch reliability (`325554a`, `300682a`) | More predictable completion behavior | Less manual cleanup/follow-up | Completion persistence diffs | `[SS-05: Completion persistence flow]` |
| **Sun (Feb 23)** | CI/security/schema/metrics improvements (`224af0b`, `e5c606e`, `9364e12`, `37292e9`, `b32a3c5`) | Stronger quality + visibility gates | Higher shipping confidence | CI artifacts + docs outputs | `[SS-06: CI green + artifacts]` |

**Packaging continuation (Feb 24):** GTM collateral built in `docs/product/` (`landing-page-v1.md`, `elevator-pitch-v3.md`, `objection-handling.md`).

---

## 5) Before/After Metrics (External-Facing)

> Keep these labeled as **conservative estimates** until telemetry snapshots are attached.

| Metric | Before | During Agent-Built Week | Delta |
|---|---:|---:|---:|
| Founder coordination/orchestration time | 12–14 hrs/week | 5–6 hrs/week | **~7–8 hrs reclaimed** |
| Decision latency (question → recorded decision) | 24–36 hrs | 6–12 hrs | **~2x–4x faster** |
| Publishable doc cycle time | 2–3 days | 0.5–1.5 days | **~50–70% faster** |
| Parallel active workstreams | 2–3 | 5–7 | **~2x capacity** |
| Rework from ambiguous handoffs | High | Medium–Low | **Meaningful reduction** |

### Business Translation
- **Capacity reclaimed:** about one founder day/week
- **Speed:** more same-day decisions, less backlog drift
- **Compounding effect:** each week creates reusable proof assets for sales, onboarding, and operations

---

## 6) Measurement Method (How We Quantified “Decisions Accelerated”)

- **Decision latency definition:** time from first explicit decision request to decision logged in task/docs/chat artifact
- **Sampling method:** compare Feb 17–23 sample set against recent baseline weeks
- **Conservatism:** ranges shown instead of single-point best-case claims
- **Next upgrade:** replace estimates with telemetry medians + P75 in future versions

---

## 7) 8-Minute Demo Script (Speaker Ready)

### 0:00–0:45 — Hook
“Instead of AI architecture slides, here’s one real week where agents shipped inspectable outcomes.”

### 0:45–1:45 — Baseline pain
- Founder was routing bottleneck
- Decisions drifted into next-day queues
- Parallel initiatives constrained by coordination overhead

### 1:45–4:15 — Walk the 7-day timeline
- Move day-by-day through the table
- Emphasize three leverage buckets:
  1. **Engineering leverage** (routing + orchestrator)
  2. **Reliability leverage** (reflection + completion persistence)
  3. **GTM leverage** (packaging docs for external conversations)

### 4:15–5:45 — Show before/after metrics
- Highlight reclaimed founder day + faster decision turnaround
- Stress conservative ranges and inspectable evidence

### 5:45–6:45 — Explain control model
- Agents execute scoped packages
- Human owns final approval
- Existing quality gates (tests/CI/review) remain intact

### 6:45–8:00 — Close + CTA
“If one week can reclaim a founder day and roughly double parallel throughput, run your own Agent-Built Week with your own baseline.”

---

## 8) Screenshot + Slide Checklist

- [ ] **SS-01** Routing commits + config diff
- [ ] **SS-02** Orchestrator/reflection timeline view
- [ ] **SS-03** Reflection reliability evidence
- [ ] **SS-04** Docs structure before/after
- [ ] **SS-05** Worker completion persistence evidence
- [ ] **SS-06** CI/security/schema/metrics proof
- [ ] **SS-07** Product collateral in `docs/product/`
- [ ] Final “Results” slide with 3 headline metrics

---

## 9) Publishing Guardrails

- Every claim must map to inspectable artifacts (commit IDs, docs, CI, logs)
- Keep estimate labels until telemetry-backed snapshots are attached
- Avoid “AI magic” language; frame as workflow + operating design
- Re-run monthly in same format to show trendline consistency

---

## 10) 30-Second Social Version

“In one week (Feb 17–23), agents helped us ship real engineering + GTM work, reclaim ~8 founder hours, and reduce decision latency from next-day to same-day. Agent-Built Week is outcomes you can inspect—not AI theater.”
