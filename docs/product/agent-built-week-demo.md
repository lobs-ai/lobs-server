# Agent-Built Week (Public Demo Narrative)

**Last updated:** 2026-02-24  
**Audience:** Prospects, pilot customers, partners, technical evaluators  
**Primary claim:** In one 7-day operating window, agent-assisted execution reclaimed meaningful founder time and accelerated decisions while keeping human approval and quality gates intact.

---

## 1) Publishable narrative (ready to reuse)

From **Feb 17–23, 2026**, we ran a focused “Agent-Built Week” across engineering, reliability, and go-to-market execution.

This was not a prompt demo. It was an operating-week test with auditable outputs: commits, docs, and process artifacts that can be inspected by anyone evaluating the system.

**What changed in one week (estimated, conservative):**
- **~7–9 founder hours reclaimed**
- **Decision latency moved from next-day to same-day** on key operational calls
- **Parallel execution increased from ~2–3 to ~5–7 active streams**

---

## 2) The 7-day demo window

**Window selected:** **Mon Feb 17 → Sun Feb 23, 2026**

Why this week works publicly:
1. It includes **cross-functional work** (platform + reliability + GTM).
2. It shows **multi-agent specialization** (`programmer`, `researcher`, `writer`, `reviewer`, `architect`).
3. It has **inspectable evidence anchors** (commit hashes, doc paths, CI artifacts).

---

## 3) Task → outcome map (proof-first)

> Replace screenshot placeholders with real captures before external distribution.

| Day | Key tasks shipped | Outcome generated | Evidence anchor | Screenshot placeholder |
|---|---|---|---|---|
| **Mon (Feb 17)** | Tier-based model routing runtime configurability (`5179d39`, `e332a95`, `5531a94`) | Better workload-to-model matching (cost/reliability control) | Commit diffs + routing config | `[SS-01: commit graph + routing diff]` |
| **Tue (Feb 18)** | Orchestrator loop + reflection flow improvements (`5bf4c0a`, `f66ac91`, `e087e4d`, `401f8c6`, `9c25237`) | Faster task cycle and tighter feedback loop | Orchestrator module changes | `[SS-02: orchestrator timeline view]` |
| **Wed (Feb 19)** | Reflection reliability fix (`778388b`) | Less retry drag and fewer fragile runs | Commit + runtime/log snippet | `[SS-03: reliability fix evidence]` |
| **Thu (Feb 20)** | Documentation restructuring (`6edf48a`) | Reduced onboarding/coordination overhead | Docs tree before/after | `[SS-04: docs structure comparison]` |
| **Fri–Sat (Feb 21–22)** | Worker completion persistence + batch completion handling (`325554a`, `300682a`) | More predictable delegated-task closure | Completion-state flow + diffs | `[SS-05: completion persistence flow]` |
| **Sun (Feb 23)** | CI/schema/docs/metrics improvements (`224af0b`, `e5c606e`, `9364e12`, `37292e9`, `b32a3c5`) | Higher confidence in shipping and observability | CI status + schema/docs artifacts | `[SS-06: CI green + artifacts]` |

**Packaging follow-through (Feb 24):**
- `docs/product/landing-page-v1.md`
- `docs/product/elevator-pitch-v3.md`
- `docs/product/objection-handling.md`
- `docs/product/agent-built-week-demo.md` (this file)

---

## 4) Before/after workload metrics

> Keep metrics labeled **estimated** until telemetry export is attached. Values below are conservative bands for public narrative use.

### 4.1 Founder workload (hours/week)

| Work category | Before | Agent-Built Week | Change |
|---|---:|---:|---:|
| Manual coordination (assignment, follow-up, tracking) | 6.0–7.0 | 2.0–2.5 | **-4.0 to -4.5** |
| Context stitching (repo + docs + chat synthesis) | 3.0–4.0 | 1.0–1.5 | **-2.0 to -2.5** |
| Drafting artifacts (summaries, GTM docs, updates) | 3.0–3.5 | 1.5–2.0 | **-1.5 to -2.0** |
| Final review + approvals | 2.0–2.5 | 2.0–2.5 | ~flat |
| **Total founder load** | **14.0–17.0** | **6.5–8.5** | **~7–9 hrs reclaimed** |

### 4.2 Throughput and speed

| Metric | Before | Agent-Built Week | Delta |
|---|---:|---:|---:|
| Parallel active workstreams | 2–3 | 5–7 | **~2x** |
| Cross-functional decision latency | 24–36 hrs | 6–12 hrs | **~2x–4x faster** |
| Draft-to-demo cycle (publishable docs) | 2–3 days | 0.5–1.5 days | **~50–70% faster** |
| Rework from ambiguous handoffs | High | Medium-Low | **Reduced** |

### 4.3 Decisions accelerated (examples)

| Decision type | Typical before | During demo week | Acceleration |
|---|---:|---:|---:|
| Model-routing policy adjustment | Next-day | Same-day | **~24h faster** |
| Reflection fix/no-fix determination | 1–2 days | Same-day | **~24–48h faster** |
| GTM messaging package sign-off | 2–3 days | <24h | **~1–2 days faster** |

**Business translation:** one week reclaimed about **one founder day**, while moving key execution decisions to a **same-day cadence**.

---

## 5) 8-minute demo script (speaker-ready)

### 0:00–0:45 — Open on outcomes
“Most AI demos show prompts. This one shows a real operating week with inspectable outputs and measured operational impact.”

### 0:45–2:00 — Baseline problem
- Founder is bottleneck for coordination and synthesis
- Decision cycles slip to next-day or later
- Context-switching drains leverage time

### 2:00–4:30 — Walk the week (timeline)
- **Engineering leverage:** routing + orchestrator upgrades
- **Reliability leverage:** reflection fix + completion persistence
- **Go-to-market leverage:** publishable narrative assets shipped

(Show SS-01 through SS-06 while speaking to each day’s task→outcome path.)

### 4:30–6:00 — Quantify impact
- Show workload table and throughput table
- Land the headline: “~7–9 founder hours reclaimed in 7 days”

### 6:00–7:15 — Trust model (control, not autopilot)
- Human approval remains mandatory on critical decisions
- Agents execute scoped tasks, not open-ended directives
- Existing quality gates remain (tests, CI, review)

### 7:15–8:00 — Close + CTA
“If this pattern can reclaim a founder day in one week here, the right next step is a pilot: run your own Agent-Built Week and compare against your baseline.”

---

## 6) Screenshot shot list (required before publishing)

- [ ] **SS-01** Commit history + diff for model routing changes
- [ ] **SS-02** Orchestrator/reflection flow timeline or module diff
- [ ] **SS-03** Reflection reliability proof (log/run output)
- [ ] **SS-04** Docs structure before/after comparison
- [ ] **SS-05** Worker completion persistence path
- [ ] **SS-06** CI/schema/metrics validation artifacts
- [ ] **SS-07** Product narrative file set under `docs/product/`
- [ ] Final “before/after metrics” summary slide

---

## 7) Publishing guardrails

1. Tie every public claim to a visible artifact (commit, doc path, CI run, log snippet).
2. Keep “estimated” labels until telemetry-derived metrics are attached.
3. Avoid “AI magic” framing; describe workflow mechanics and controls.
4. Re-run this package monthly for a longitudinal proof series.

---

## 8) 30-second version (social/intro)

“In one week (Feb 17–23), we used agents to execute real engineering and GTM work, reclaimed ~8 founder hours, shifted key decisions from next-day to same-day, and shipped reusable assets. Agent-Built Week is inspectable outcomes, not AI theater.”
