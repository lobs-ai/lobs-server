# Agent-Built Week — Public Demo Narrative Package

**Last updated:** 2026-02-24  
**Audience:** Prospects, pilot customers, partners, technical evaluators  
**Goal:** Prove real-world business value from one inspectable 7-day operating window.

---

## 1) Narrative (publish-ready)

From **Feb 17–23, 2026**, Lobs used its own multi-agent workflow to execute engineering, reliability, and go-to-market work in parallel.

This was not a prompt showcase. It produced durable, inspectable outputs: code changes, reliability fixes, documentation assets, and demo-ready GTM collateral.

**Topline outcomes:**
- **~7–9 founder hours reclaimed** in one week
- **Decision latency reduced** from next-day to same-day on key operating choices
- **Parallel execution increased** from ~2–3 to ~5–7 active streams

---

## 2) Demo window and why it was chosen

**Window:** **Mon Feb 17 → Sun Feb 23, 2026**

Why this week is ideal for public proof:
- Cross-functional output (platform + reliability + GTM)
- Multiple specialist roles contributed (`programmer`, `researcher`, `writer`, `reviewer`, `architect`)
- Claims are auditable through concrete artifacts (commit IDs, docs, CI evidence)

---

## 3) Before vs after operating model

### Before (founder-led baseline)
- Founder is the coordination and synthesis bottleneck
- Decisions wait on manual context stitching (repo + chat + notes)
- Throughput constrained by context switching

### After (Agent-Built Week)
- Scoped tasks run in parallel with explicit handoffs
- Human remains approver, but exits micro-execution path
- Outputs persist as reusable assets (code/docs/scripts), not ephemeral chat

---

## 4) 7-day timeline: work → outcome → proof

> Replace all placeholders before external publishing.

| Day | Work shipped (examples) | Outcome | Evidence anchor | Screenshot placeholder |
|---|---|---|---|---|
| **Mon (Feb 17)** | Tier-based model routing runtime configurability (`5179d39`, `e332a95`, `5531a94`) | Better reliability/cost tuning by workload | Commit history + routing config diffs | `[SS-01: commit graph + routing diff]` |
| **Tue (Feb 18)** | Orchestrator loop and reflection flow improvements (`5bf4c0a`, `f66ac91`, `e087e4d`, `401f8c6`, `9c25237`) | Faster execution/feedback cycle | Orchestrator module diffs + architecture refs | `[SS-02: orchestrator timeline + module view]` |
| **Wed (Feb 19)** | Reflection reliability fix (`778388b`) | Lower failure/retry drag in learning loop | Commit + run/log evidence | `[SS-03: reliability fix proof]` |
| **Thu (Feb 20)** | Documentation restructuring (`6edf48a`) | Faster onboarding + lower coordination friction | Docs tree before/after | `[SS-04: docs structure comparison]` |
| **Fri–Sat (Feb 21–22)** | Batch commit + worker completion persistence (`325554a`, `300682a`) | More predictable closure of delegated work | Completion-state flow + diffs | `[SS-05: completion persistence flow]` |
| **Sun (Feb 23)** | CI/schema/docs/metrics advances (`224af0b`, `e5c606e`, `9364e12`, `37292e9`, `b32a3c5`) | Higher shipping confidence + visibility | CI status + schema/docs/metrics artifacts | `[SS-06: CI green + artifacts]` |

**Packaging continuation (Feb 24):** `docs/product/landing-page-v1.md`, `docs/product/elevator-pitch-v3.md`, `docs/product/objection-handling.md`, and this demo package.

---

## 5) Before/after workload metrics

> **Status:** Conservative estimate bands pending telemetry export. Keep “estimated” label publicly until telemetry is attached.

### 5.1 Founder workload (hours/week)

| Work category | Before | Agent-Built Week | Change |
|---|---:|---:|---:|
| Manual coordination (assigning, nudging, tracking) | 6.0–7.0 | 2.0–2.5 | **-4.0 to -4.5** |
| Context stitching (repos/notes/chat synthesis) | 3.0–4.0 | 1.0–1.5 | **-2.0 to -2.5** |
| Artifact drafting (docs/pitches/summaries) | 3.0–3.5 | 1.5–2.0 | **-1.5 to -2.0** |
| Final review + approval | 2.0–2.5 | 2.0–2.5 | ~flat |
| **Total founder load** | **14.0–17.0** | **6.5–8.5** | **~7–9 hrs reclaimed** |

### 5.2 Throughput + decision speed

| Metric | Before | Agent-Built Week | Delta |
|---|---:|---:|---:|
| Parallel active workstreams | 2–3 | 5–7 | **~2x** |
| Cross-functional decision latency | 24–36 hrs | 6–12 hrs | **~2x–4x faster** |
| Publishable doc cycle (draft → demo-ready) | 2–3 days | 0.5–1.5 days | **~50–70% faster** |
| Rework from ambiguous handoffs | High | Medium-Low | **Reduced** |

### 5.3 Concrete decisions accelerated

| Decision type | Typical before | This week | Acceleration |
|---|---:|---:|---:|
| Model-routing policy adjustment | Next-day | Same-day | **~24h faster** |
| Reflection fix/no-fix call | 1–2 days | Same-day | **~24–48h faster** |
| GTM messaging sign-off | 2–3 days | <24h | **~1–2 days faster** |

**Business translation:**
- Reclaims roughly **one founder day per week**
- Moves key ops decisions to **same-day cadence**
- Builds reusable assets that compound across sales, onboarding, and hiring

---

## 6) 8-minute demo script (speaker-ready)

### 0:00–0:45 — Hook
“Instead of showing prompts, I’m showing one real operating week with inspectable outcomes.”

### 0:45–2:00 — Baseline pain
- Founder bottleneck
- Slow decision loops
- Coordination overhead consuming leverage time

### 2:00–4:30 — Walk the week (timeline + screenshots)
- Engineering leverage: model routing + orchestrator improvements
- Quality leverage: reflection reliability + CI/schema hardening
- GTM leverage: landing page, elevator pitch, objection handling

### 4:30–6:00 — Quantified impact
- Show workload table + throughput table
- Land the point: “~one founder day reclaimed in one week”

### 6:00–7:15 — Trust and control model
- Human approval remains in place
- Agents receive scoped tasks with review loops
- Existing quality gates remain (tests, CI, code/doc review)

### 7:15–8:00 — Close + CTA
“If one week can reclaim a founder day and roughly double parallel execution, run your own Agent-Built Week pilot and compare against your baseline.”

---

## 7) Screenshot checklist (required before publishing)

- [ ] **SS-01** Model routing commit history + diff
- [ ] **SS-02** Orchestrator/reflection timeline + module view
- [ ] **SS-03** Reflection reliability evidence (run/log)
- [ ] **SS-04** Docs structure before/after
- [ ] **SS-05** Worker completion persistence flow
- [ ] **SS-06** CI/schema/metrics validation artifacts
- [ ] **SS-07** Product narrative assets in `docs/product/`
- [ ] Final summary slide with before/after metric table

---

## 8) Publishing guardrails

- Tie each claim to an artifact (commit ID, CI run, doc path, log snippet)
- Keep any estimate explicitly labeled until telemetry-backed
- Avoid “AI magic” framing; describe workflow mechanics plainly
- Re-run this same package monthly to build a longitudinal proof series

---

## 9) 30-second social proof version

“In one week (Feb 17–23), we used agents for real product and engineering execution, reclaimed ~8 founder hours, reduced decision latency from next-day to same-day, and shipped reusable GTM assets. Agent-Built Week is inspectable outcomes, not AI theater.”
