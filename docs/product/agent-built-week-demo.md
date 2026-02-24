# Agent-Built Week — Public Demo Narrative

**Last updated:** 2026-02-24  
**Audience:** Prospects, pilot customers, partners, technical evaluators  
**Core claim:** In one inspectable 7-day window, agent-assisted execution reclaimed founder time, accelerated decisions, and increased parallel output.

---

## 1) Executive narrative (publish-ready)

From **Feb 17–23, 2026**, Lobs ran an “Agent-Built Week” across backend engineering, reliability improvements, and go-to-market packaging.

This is not an AI highlight reel. It is an operating-week proof package with artifact-level evidence (commits, docs, CI, and implementation traces).

### Topline outcomes (conservative, estimated)
- **~7–9 founder hours reclaimed** in one week
- **Decision latency improved** from mostly next-day to mostly same-day
- **Parallel execution increased** from ~2–3 to ~5–7 active workstreams

---

## 2) Demo scope and why this week

**Selected window:** **Mon Feb 17 → Sun Feb 23, 2026**

Why this week is a strong public proof window:
1. Clear cross-functional output (platform + reliability + GTM)
2. Multi-agent role coverage (`programmer`, `researcher`, `writer`, `reviewer`, `architect`)
3. Inspectable evidence for each claim

---

## 3) Before/after operating model

### Before (founder-led baseline)
- Founder acts as scheduling + context bottleneck
- Frequent context switching across repo/chat/docs
- Many key decisions slip to the next day

### After (Agent-Built Week workflow)
- Scoped tasks run in parallel with explicit handoffs
- Founder remains approver/editor instead of micro-executor
- Outputs become reusable assets (code/docs/scripts), not transient chat output

---

## 4) 7-day timeline: tasks → outcomes → evidence

> Replace screenshot placeholders before external publishing.

| Day | Task cluster | Business outcome | Evidence anchor | Screenshot placeholder |
|---|---|---|---|---|
| **Mon (Feb 17)** | Tier-based model routing configurability (`5179d39`, `e332a95`, `5531a94`) | Better reliability and cost-fit by workload type | Commit history + routing config diffs | `[SS-01: commit graph + routing diff]` |
| **Tue (Feb 18)** | Orchestrator loop + reflection flow improvements (`5bf4c0a`, `f66ac91`, `e087e4d`, `401f8c6`, `9c25237`) | Faster execution/feedback loop | Orchestrator module diffs + architecture references | `[SS-02: orchestrator timeline + module view]` |
| **Wed (Feb 19)** | Reflection reliability fix (`778388b`) | Lower failure/retry drag | Commit + run/log evidence | `[SS-03: reliability fix proof]` |
| **Thu (Feb 20)** | Documentation restructuring (`6edf48a`) | Faster onboarding and lower coordination overhead | Docs tree before/after | `[SS-04: docs structure comparison]` |
| **Fri–Sat (Feb 21–22)** | Batch commit + worker completion persistence (`325554a`, `300682a`) | More predictable delegated-task closure | Completion-state flow + code diffs | `[SS-05: completion persistence flow]` |
| **Sun (Feb 23)** | CI/schema/docs/metrics advances (`224af0b`, `e5c606e`, `9364e12`, `37292e9`, `b32a3c5`) | Higher shipping confidence and visibility | CI status + schema/docs/metrics artifacts | `[SS-06: CI green + artifact panel]` |

**Packaging continuation (Feb 24):**
- `docs/product/landing-page-v1.md`
- `docs/product/elevator-pitch-v3.md`
- `docs/product/objection-handling.md`
- `docs/product/agent-built-week-demo.md` (this file)

---

## 5) Quantified workload impact (before vs after)

> Keep “estimated” labels public until telemetry export is attached.

### 5.1 Founder workload (hours/week)

| Work category | Before | Agent-Built Week | Change |
|---|---:|---:|---:|
| Manual coordination (assign, nudge, track) | 6.0–7.0 | 2.0–2.5 | **-4.0 to -4.5** |
| Context stitching (repo + chat + notes synthesis) | 3.0–4.0 | 1.0–1.5 | **-2.0 to -2.5** |
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

### 5.3 Decisions accelerated in this window

| Decision type | Typical before | During this week | Acceleration |
|---|---:|---:|---:|
| Model-routing policy adjustment | Next-day | Same-day | **~24h faster** |
| Reflection fix/no-fix decision | 1–2 days | Same-day | **~24–48h faster** |
| GTM narrative sign-off | 2–3 days | <24h | **~1–2 days faster** |

**Business translation:**
- Reclaims approximately **one founder day per week**
- Pulls key operating decisions into a **same-day loop**
- Produces durable assets that compound across sales, onboarding, and recruiting

---

## 6) 8-minute live demo script (stage-ready)

### Slide 1 (0:00–0:45) — Hook
“Today is not an AI prompt demo. It is one inspectable operating week showing exactly what shipped and what changed for the business.”

### Slide 2 (0:45–2:00) — Baseline pain
- Founder as throughput bottleneck
- Slow decisions due to fragmented context
- Coordination overhead consuming leverage time

### Slides 3–4 (2:00–4:30) — Walk the week
- Show **SS-01 → SS-06** in chronological order
- Narrate each day as: **task cluster → business outcome → artifact proof**
- Highlight cross-functional breadth (engineering + reliability + GTM)

### Slide 5 (4:30–6:00) — Quantified impact
- Present workload and decision-speed tables
- Landing line: “This week reclaimed about one founder day and roughly doubled parallel execution.”

### Slide 6 (6:00–7:15) — Control + trust model
- Human stays in approval loop
- Agents are scoped to explicit tasks
- Existing quality gates remain (review, tests, CI, docs)

### Slide 7 (7:15–8:00) — Close + CTA
“If you want proof, run your own Agent-Built Week. Baseline your current throughput, then compare against a 7-day artifact-backed window.”

---

## 7) Screenshot production checklist

- [ ] **SS-01** Model routing commit history + config diff
- [ ] **SS-02** Orchestrator/reflection timeline + module view
- [ ] **SS-03** Reflection reliability evidence (run/log)
- [ ] **SS-04** Docs structure before/after
- [ ] **SS-05** Worker completion persistence flow
- [ ] **SS-06** CI/schema/metrics validation artifacts
- [ ] **SS-07** Product narrative asset folder (`docs/product/`)
- [ ] Final summary slide with before/after metrics

---

## 8) Publishing guardrails

- Anchor each major claim to a specific artifact pointer
- Mark non-telemetry numbers as **estimated**
- Avoid “AI magic” framing; explain workflow mechanics plainly
- Re-run monthly with the same template for longitudinal proof

---

## 9) 30-second social proof version

“In one week (Feb 17–23), we used agents for real engineering and GTM execution, reclaimed ~8 founder hours, moved key decisions from next-day to same-day, and shipped reusable assets. Agent-Built Week is inspectable outcomes—not AI theater.”

---

## 10) Repurposing map (narrative package)

Use this file as source-of-truth and repurpose into:
- **Website proof page:** Sections 1, 4, 5, 9
- **Sales one-pager:** Sections 1, 5, 6
- **Founder demo deck (8 min):** Sections 4, 5, 6, 7

### Copy-ready title options
- **Agent-Built Week: 7 Days, One Founder Day Reclaimed**
- **From AI Demos to Operating Proof: Our Agent-Built Week**
- **What We Actually Shipped in 7 Days with Agents**

### Footer evidence note
“All claims in this demo map to inspectable repository artifacts and dated documentation. Metrics are labeled estimated unless telemetry-backed exports are attached.”

---

## 11) Evidence ledger template (fill before publishing)

Use this mini-ledger to bind every screenshot and metric to a concrete artifact.

| Claim ID | Claim | Artifact type | Pointer (commit/PR/doc/log) | Owner | Verification status |
|---|---|---|---|---|---|
| C-01 | Routing became tier-aware | Commit diff | `<hash>` | Eng | [ ] |
| C-02 | Reflection reliability improved | Run log + commit | `<log path>`, `<hash>` | Eng | [ ] |
| C-03 | Docs onboarding improved | Docs tree diff | `<before/after paths>` | PM | [ ] |
| C-04 | Founder hours reclaimed (~7–9h) | Time log estimate sheet | `<sheet/doc link>` | Founder | [ ] |
| C-05 | Decision latency reduced | Decision log timestamps | `<log/export>` | Ops | [ ] |

**Rule for public release:** no unlinked claim survives review. If a claim cannot be tied to an artifact pointer, remove or relabel it.
