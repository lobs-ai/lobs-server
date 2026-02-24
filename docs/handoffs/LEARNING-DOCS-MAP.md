# Learning System — Doc Map

**Date:** 2026-02-24  
**Purpose:** Orient new readers; clarify which docs are current and which are stale.

There are 10+ learning-related docs in this directory after multiple implementation attempts. This map tells you what to read and what to skip.

---

## ✅ Current Docs — Read These

| Doc | When to Read |
|-----|-------------|
| [`learning-loop-mvp-implementation-guide.md`](learning-loop-mvp-implementation-guide.md) | **START HERE** — step-by-step build instructions, code snippets, build order |
| [`learning-loop-mvp-status.md`](learning-loop-mvp-status.md) | What's done, what's missing, precise file/line references |
| [`learning-loop-mvp-handoff.json`](learning-loop-mvp-handoff.json) | Machine-readable acceptance criteria |
| [`../learning-loop-mvp-design.md`](../learning-loop-mvp-design.md) | Full spec — event schema, API contracts, confidence model, lesson templates |
| [`../LEARNING_SYSTEM_QUICKSTART.md`](../LEARNING_SYSTEM_QUICKSTART.md) | Operator view — how to use the system once built |
| [`../agent-learning-operator-guide.md`](../agent-learning-operator-guide.md) | Day-to-day operator workflow |

---

## ⚠️ Stale Docs — Do Not Follow

These are from previous failed implementation attempts. They describe a phased approach (1.1 → 1.2 → 1.3 → 1.4) that was **abandoned** in favor of the consolidated MVP approach documented above.

| Doc | Why It's Stale |
|-----|---------------|
| `learning-phase-1.1-database-tracking.md` | Old Phase 1.1 handoff — superseded by MVP design |
| `learning-phase-1.2-pattern-extraction.md` | Old Phase 1.2 handoff — superseded |
| `learning-phase-1.3-prompt-enhancement.md` | Old Phase 1.3 handoff — PromptEnhancer is already built |
| `learning-phase-1.3-rescue-architecture.md` | Rescue attempt design — superseded by learning-loop-mvp-* docs |
| `learning-phase-1.4-metrics-validation.md` | Old Phase 1.4 handoff — metrics are now part of `/api/agent-learning/summary` |
| `learning-phase-1-consolidated-rescue.md` | Early rescue; superseded by current MVP docs |
| `learning-handoffs.json` | Old multi-phase handoff; superseded by `learning-loop-mvp-handoff.json` |
| `learning-mvp-consolidated.json` | Earlier consolidated spec; `learning-loop-mvp-handoff.json` is canonical |

---

## What to Build (Summary)

See `learning-loop-mvp-implementation-guide.md` for details. The 7-step build order:

1. **`app/orchestrator/outcome_tracker.py`** — `OutcomeTracker.track_completion()` + `record_feedback()` (critical)
2. **`app/orchestrator/worker.py`** — Hook 2: call `OutcomeTracker.track_completion()` after task completion; add `applied_learning_ids` + `learning_disabled` to `WorkerInfo`
3. **`app/routers/agent_learning.py`** — `POST /api/agent-learning/outcomes`, `GET /api/agent-learning/summary`, `PATCH /api/agent-learning/learnings/{id}`
4. **`app/main.py`** — Register `agent_learning` router at prefix `/api/agent-learning`
5. **`app/orchestrator/learning_batch.py`** — Daily 2am ET batch job (pattern extraction + lesson suggestions)
6. **`app/orchestrator/engine.py`** — 2am ET timer integration
7. **`tests/test_agent_learning.py`** — 12+ test cases

Steps 1–4 give a working ledger and API surface. Stop there if time-boxed.

---

## Do Not Touch

- `app/routers/learning.py` — personal learning plans (different system, different prefix `/api/learning`)
- `app/models.py` — `TaskOutcome` and `OutcomeLearning` models are already correct
- `app/orchestrator/prompt_enhancer.py` — fully implemented, no changes needed

---

## History (Why So Many Docs?)

The learning system went through 5+ failed implementation attempts using a phased approach (1.1 → 1.2 → 1.3 → 1.4). Each phase was blocked by the previous. The rescue analysis (`PHASE_1.3_RESCUE_FINDINGS.md`) identified the root cause and produced the consolidated MVP spec, which implements all pieces together in the correct order.

The old phase docs remain for historical context but should **not** be used as implementation guides.
