# Agent-Built Week — Public Demo Narrative Package

**Last updated:** 2026-02-24  
**Audience:** Prospects, pilot customers, partners, technical evaluators  
**Purpose:** Prove real-world value with one inspectable 7-day operating window.

---

## 1) Core Narrative (Publishable)

From **Feb 17–23, 2026**, Lobs used its own multi-agent workflow to execute engineering, reliability, and go-to-market work in parallel.  
The outcome was not “interesting prompts,” but durable deliverables: merged code, operational improvements, and customer-facing messaging assets.

**Headline proof points:**
- ~**7–9 founder hours reclaimed** in one week
- **Decision latency reduced** from next-day to same-day in key flows
- **Parallel work capacity expanded** from ~2–3 to ~5–7 active streams

---

## 2) Demo Window (Fixed)

**Window selected:** **Mon Feb 17 → Sun Feb 23, 2026**

Why this week works publicly:
- Balanced cross-functional output (platform, reliability, GTM)
- Multiple agent roles active (`programmer`, `researcher`, `writer`, `reviewer`, `architect`)
- Claims can be grounded in artifacts (commits, docs, CI results, generated files)

---

## 3) Before vs After (Human + System)

### Before (founder-led baseline)
- Founder is execution bottleneck for triage, synthesis, and follow-through
- Decisions wait on manual context stitching across repos/notes/chats
- Throughput constrained by context-switching overhead

### After (Agent-Built Week)
- Agents execute scoped tasks in parallel with explicit handoffs
- Human remains final approver, but no longer the micro-execution bottleneck
- Outputs are reusable assets (code, docs, CI, scripts), not ephemeral chat

---

## 4) 7-Day Timeline: Task → Outcome → Proof

> Replace placeholders with screenshots before external publishing.

| Day | Work shipped (examples) | Outcome | Evidence anchor | Screenshot placeholder |
|---|---|---|---|---|
| **Mon (Feb 17)** | Tier-based model routing runtime configurability (`5179d39`, `e332a95`, `5531a94`) | Better reliability/cost tuning by workload | Commit log + routing config diffs | `[INSERT SS-01: model-routing commit history + diff]` |
| **Tue (Feb 18)** | Orchestrator loop + reflection flow improvements (`5bf4c0a`, `f66ac91`, `e087e4d`, `401f8c6`, `9c25237`) | Faster execution feedback cycle | Orchestrator code diffs + architecture refs | `[INSERT SS-02: orchestrator timeline + module view]` |
| **Wed (Feb 19)** | Reflection reliability fix (`778388b`) | Lower failure/retry drag in learning loop | Commit + run/log evidence | `[INSERT SS-03: reliability fix proof]` |
| **Thu (Feb 20)** | Documentation restructuring (`6edf48a`) | Faster onboarding + reduced coordination friction | Docs tree before/after | `[INSERT SS-04: docs structure comparison]` |
| **Fri–Sat (Feb 21–22)** | Batch commit + worker completion persistence (`325554a`, `300682a`) | More predictable closure of delegated work | Completion state flow + diffs | `[INSERT SS-05: completion persistence flow]` |
| **Sun (Feb 23)** | CI/schema/docs/metrics advances (`224af0b`, `e5c606e`, `9364e12`, `37292e9`, `b32a3c5`) | Higher shipping confidence + visibility | CI status + docs artifacts + metrics endpoint proof | `[INSERT SS-06: CI green + metrics/docs artifacts]` |

**Packaging follow-up (Feb 24):** GTM narrative assets + demo package under `docs/product/*`.

---

## 5) Before/After Workload Metrics

> **Note:** These are conservative estimate bands until telemetry export is attached.

### 5.1 Founder workload (hours/week)

| Work category | Before | Agent-Built Week | Change |
|---|---:|---:|---:|
| Manual coordination (assigning, nudging, tracking) | 6.0–7.0 | 2.0–2.5 | **-4.0 to -4.5** |
| Context stitching (repos/notes/chat synthesis) | 3.0–4.0 | 1.0–1.5 | **-2.0 to -2.5** |
| Artifact drafting (docs/pitches/summaries) | 3.0–3.5 | 1.5–2.0 | **-1.5 to -2.0** |
| Final review + approval | 2.0–2.5 | 2.0–2.5 | ~flat |
| **Total founder load** | **14.0–17.0** | **6.5–8.5** | **~7–9 hrs reclaimed** |

### 5.2 Throughput + decision velocity

| Metric | Before | Agent-Built Week | Delta |
|---|---:|---:|---:|
| Parallel active workstreams | 2–3 | 5–7 | **~2x** |
| Cross-functional decision latency | 24–36 hrs | 6–12 hrs | **~2x–4x faster** |
| Publishable doc cycle (draft → demo-ready) | 2–3 days | 0.5–1.5 days | **~50–70% faster** |
| Rework from ambiguous handoffs | High | Medium-Low | **Reduced** |

### 5.3 Example decisions accelerated

| Decision type | Typical before | This week | Acceleration |
|---|---:|---:|---:|
| Model-routing policy adjustment | Next-day | Same-day | **~24h faster** |
| Reflection fix/no-fix call | 1–2 days | Same-day | **~24–48h faster** |
| GTM messaging sign-off | 2–3 days | <24h | **~1–2 days faster** |

**Business translation:**
- Reclaims approximately **one founder day/week**
- Converts key operating decisions to **same-day cadence**
- Creates compounding reusable assets for sales, onboarding, and hiring

---

## 6) 8-Minute Live Demo Script (Speaker Notes)

### 0:00–0:45 — Hook
“Rather than demoing prompts, I’ll show one real week where agents shipped inspectable outcomes.”

### 0:45–2:00 — Baseline pain
- Founder bottleneck
- Slow decision loops
- Coordination overhead consuming high-leverage time

### 2:00–4:30 — Walk the week
- Show Feb 17–23 timeline
- Emphasize three proof classes:
  1) **Engineering leverage** (routing + orchestrator improvements)
  2) **Quality leverage** (CI/schema/reliability hardening)
  3) **GTM leverage** (`landing-page-v1.md`, `elevator-pitch-v3.md`, `objection-handling.md`)

### 4:30–6:00 — Quantified impact
- Present workload table + throughput table
- Land on: “~one founder day reclaimed in one week”

### 6:00–7:15 — Trust and control
- Human approval stays in place
- Agents operate with scoped tasks + review loops
- Existing quality gates remain (tests, CI, review)

### 7:15–8:00 — Close + CTA
“If one week can reclaim a founder day and roughly double parallel execution, run your own Agent-Built Week pilot and benchmark against your current baseline.”

---

## 7) Screenshot Capture Checklist (for final deck)

- [ ] **SS-01** Model routing commits + diff
- [ ] **SS-02** Orchestrator/reflection timeline
- [ ] **SS-03** Reflection reliability evidence
- [ ] **SS-04** Docs structure before/after
- [ ] **SS-05** Task completion persistence flow
- [ ] **SS-06** CI/schema/metrics validation
- [ ] **SS-07** Product narrative assets in `docs/product/`
- [ ] Final slide: before/after metrics summary

---

## 8) Publishing Guardrails

- Tie every claim to an artifact (commit IDs, docs, CI runs, logs)
- Keep “estimated” label until telemetry-backed exports are included
- Avoid “AI magic” framing; describe operational changes in plain language
- Reuse this exact format monthly to build a credible proof series

---

## 9) 30-Second Social Version

“In one week (Feb 17–23), we used agents for real product and engineering execution, reclaimed ~8 founder hours, cut decision latency from next-day to same-day, and shipped reusable GTM assets. Agent-Built Week is outcomes you can inspect, not AI theater.”
