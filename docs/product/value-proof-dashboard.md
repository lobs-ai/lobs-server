# PAW Value Proof Dashboard — Copy & Rendering Spec

**Last Updated:** 2026-02-25  
**Status:** Draft — ready for programmer implementation  
**Data Contract:** [`schemas/value_proof_event.json`](../../schemas/value_proof_event.json)

---

## Purpose

Answer one question in ≤ 60 seconds: **"What did my agents do for me today?"**

This dashboard card gives Rafe immediate ROI visibility — making it easy to trust automation, prioritize agent work, and justify the system's existence day-to-day. Without it, value delivered is invisible and confidence erodes.

---

## The One-Minute Value Proof Card

This is the **primary UI surface**. It renders at the top of Mission Control's dashboard as a summary card. Clicking it opens a detail drawer.

### Card Copy Template

```
╔══════════════════════════════════════════════════════╗
║  🤖 Your agents saved you  [TIME_SAVED] today       ║
║                                                      ║
║  ✅ [N] tasks completed    🧠 [N] decisions needed  ║
║  🛡️  [N] risks prevented                             ║
║                                                      ║
║  [View details →]                            [date] ║
╚══════════════════════════════════════════════════════╝
```

**Filled example:**

```
🤖 Your agents saved you  2h 15m  today

✅ 7 tasks completed    🧠 1 decision needed
🛡️  2 risks prevented

[View details →]                    Feb 25, 2026
```

### Copy Blocks

| Slot | Copy | Notes |
|------|------|-------|
| **Card headline** | `Your agents saved you {TIME_SAVED} today` | TIME_SAVED formatted as `Xh Ym` (e.g. `2h 15m`); if < 60 min, show `45m` |
| **Zero-state headline** | `Agents are working — check back soon` | Shown when `minutes_saved.total == 0` and tasks still in progress |
| **Empty-state headline** | `Nothing ran today` | Shown when no tasks completed and no events |
| **Tasks completed label** | `{N} task completed` / `{N} tasks completed` | Singular/plural |
| **Decisions label (pending > 0)** | `{N} decision needed` / `{N} decisions needed` | Use **bold** or accent color when `pending > 0` |
| **Decisions label (all resolved)** | `All caught up` | When `decisions_required.pending == 0` and `count > 0` |
| **Risks label** | `{N} risk prevented` / `{N} risks prevented` | Singular/plural; 0 = omit this line entirely |
| **Risks label (zero)** | *(hidden)* | Don't show `0 risks prevented` — it reads as neutral noise |
| **Detail drawer title** | `Today's Agent Activity` | |
| **Period label** | `Today · {date}` / `Yesterday · {date}` / `{date}` | Relative label for recent periods |

---

## Metric Definitions

These are the four required fields in `ValueProofEvent`. Every display value traces back to one of these.

### `minutes_saved`

**What it measures:** Estimated human time that agents handled autonomously — time you didn't spend doing it yourself.

**How it's calculated (default: `task_baseline`):**

Each completed task type has a baseline estimate of the equivalent human effort:

| Task type | Baseline |
|-----------|----------|
| Writing a doc/summary | 30 min |
| Research & synthesis | 45 min |
| Code review pass | 20 min |
| Scheduling / calendar triage | 10 min |
| Inbox / communication triage | 15 min |
| Planning / task decomposition | 20 min |

Baselines are summed across `tasks_completed.task_ids`. The `estimation_method` field declares which method produced the value.

**Display format:**
- `135 min` → display as `2h 15m`
- `45 min` → display as `45m`
- `60 min` → display as `1h`
- `0 min` → show zero-state copy (see above)

**Honesty note:** Time savings are estimates, not measurements. The UI should never present them as exact. Consider a subtle tooltip: *"Estimated based on typical task effort."*

---

### `tasks_completed`

**What it measures:** Tasks brought to a fully finished state by agents during the period — no further human action needed.

**Inclusion criteria:**
- Task `status == "done"`
- Completed within `period_start` → `period_end`
- Completed by an agent worker (not manually marked done by user)

**Exclusion:**
- Tasks blocked or cancelled — not counted
- Tasks partially done or in review — not counted

**Display:**
- Primary number: `tasks_completed.count`
- Detail drawer: per-agent breakdown from `tasks_completed.by_agent` (e.g., "Writer: 3, Programmer: 2")

---

### `decisions_required`

**What it measures:** Moments where agent automation reached a boundary and correctly surfaced a decision to the human, rather than proceeding autonomously into a risky action.

**Framing:** This is not a failure metric. It's a **trust signal** — the system knew when to stop and ask. High decisions-required with high tasks-completed = system is working exactly right.

**Inclusion criteria:**
- Inbox items created during the period with `requires_decision = true`
- Decision cards issued (60-Second Decision Card format)

**Display:**
- Primary: `decisions_required.count`
- Badge/highlight: `decisions_required.pending` — highlight when > 0, because user action is needed
- Detail drawer: links to open inbox items

**Copy guidance:**
- Pending > 0: show as **action needed** in accent/warning color
- All resolved: show as "All caught up" with no special color

---

### `risk_prevented`

**What it measures:** Discrete risk events caught and neutralized by agent guardrails before they caused harm.

**Inclusion criteria — events that count:**
- Budget cap triggered (`BudgetGuard` prevented overspend)
- Failure cascade guardrail stopped dependent task spawn
- Diagnostic agent caught and escalated before silent failure
- Task decomposition validated and corrected malformed plan before commit

**Exclusion:**
- General task failures (that's `tasks_completed` negative space, not risk prevention)
- Retry-and-succeed without explicit guardrail involvement

**Display:**
- If `count == 0`: **hide this line entirely** (zero risks prevented is table stakes, not a value statement)
- If `count >= 1`: show with tooltip listing `risk_prevented.examples`
- Severity: high risks use distinct icon or color in the tooltip list

---

## Detail Drawer Sections

When the user clicks "View details →", a drawer or modal opens with:

```
Today's Agent Activity  ·  Feb 25, 2026
─────────────────────────────────────────

⏱ Time Saved: 2h 15m
   Writing: 1h  ·  Research: 45m  ·  Triage: 30m

✅ Tasks Completed: 7
   Writer: 3  ·  Programmer: 2  ·  Researcher: 2
   [View all tasks →]

🧠 Decisions: 3 total  ·  1 pending
   → [Approve new API endpoint design]   ⏳ Due in 22h
   → [Reviewed: Deploy to staging]       ✅ Resolved
   → [Reviewed: Skip low-priority task]  ✅ Resolved
   [Go to inbox →]

🛡 Risks Prevented: 2
   • Budget cap prevented $38 model overspend  (Medium)
   • Failure cascade guardrail stopped 4 dependent task spawns  (High)
```

---

## API Endpoint (to be implemented)

The dashboard card consumes:

```
GET /api/value-proof/today
```

Returns a `ValueProofEvent` for the current calendar day (ET timezone, same convention as daily ops brief).

Optional query params:
- `?date=YYYY-MM-DD` — fetch a specific day
- `?period=week` — 7-day rollup (future)

Response: JSON conforming to `schemas/value_proof_event.json`.

---

## Implementation Handoff Notes

### For the programmer:

**Step 1 — Telemetry mapping.** Map existing events to schema fields:

| Schema field | Source table/field |
|---|---|
| `tasks_completed.count` | `tasks` where `status='done'` and `updated_at` in period and `assigned_agent IS NOT NULL` |
| `tasks_completed.by_agent` | Group by `tasks.assigned_agent` |
| `minutes_saved.total` | Sum of per-task baselines (see definitions above); store baseline in `task_types` config or hardcode initial map |
| `decisions_required.count` | `inbox_items` where `created_at` in period and `requires_decision=true` |
| `decisions_required.pending` | Above + `status='pending'` |
| `risk_prevented.count` | `orchestrator_events` or `worker_runs` where event_type in `['budget_cap_hit', 'cascade_guardrail_triggered', 'diagnostic_escalation']` |

**Step 2 — Endpoint.** Build `GET /api/value-proof/today` that aggregates and returns a `ValueProofEvent`. Cache with 5-min TTL; invalidate on task completion or inbox resolution.

**Step 3 — Dashboard card.** One card component consuming the endpoint. Use the copy blocks and display rules defined above.

---

## Related Docs

- [Daily Ops Brief Design](../daily-ops-brief-design.md) — same time-window convention (ET calendar day)
- [Decision Card Spec](../communication/decision-card-spec.md) — how `decisions_required` items are formatted when delivered
- [Failure Explainer Design](../failure-explainer-design.md) — risk events that map to `risk_prevented`
- [Budget Guardrails Design](../budget-guardrails-design.md) — source of `budget_overrun` risk events
- [Elevator Pitch v3](elevator-pitch-v3.md) — product positioning context for copy tone

---

## Open Questions

1. **Time savings method** — should we start with `task_baseline` and ship, or wait for a model-estimate approach? Recommendation: ship `task_baseline` now, add `model_estimate` later.
2. **Timezone** — ET assumed (same as daily ops brief). Confirm this is right for Rafe's use.
3. **Weekly rollup** — keep `?period=week` out of v1 to keep scope tight.
