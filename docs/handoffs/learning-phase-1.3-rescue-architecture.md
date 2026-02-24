# Phase 1.3 Rescue Architecture — Prompt Enhancement & Learning Injection

**Initiative:** agent-learning-system  
**Task:** 498C8166-D5AA-4BF1-BFCD-54CE726F2707  
**Author:** Architect  
**Date:** 2026-02-23

---

## 1) Problem Statement

Phase 1.3 has retried multiple times and failed. The current implementation environment differs from the original handoff assumptions:

- `app/orchestrator/prompter.py` is currently **synchronous** and called from at least two places (`worker.py`, `worker_manager.py`).
- `PromptEnhancer`, `OutcomeTracker`, and `LessonExtractor` services are not present in `app/orchestrator/` yet.
- Learning models (`TaskOutcome`, `OutcomeLearning`) and `/api/learning` router exist, so there is enough schema surface to proceed.

Main failure mode risk: introducing a breaking async API change to `Prompter.build_task_prompt()` in one pass will cascade across callers and destabilize worker spawning.

---

## 2) Proposed Solution (Incremental, Non-Breaking)

### Decision
Implement Phase 1.3 in a **compatibility-first** way:

1. Add new async API: `Prompter.build_task_prompt_enhanced(...) -> tuple[str, list[str]]`
2. Keep existing sync API: `Prompter.build_task_prompt(...) -> str` unchanged
3. Introduce `app/orchestrator/prompt_enhancer.py` with best-effort behavior and hard failure isolation
4. Integrate enhancement only in `worker.py` first (primary runtime path)
5. Defer `worker_manager.py` integration to a follow-up once stable

### Why this is best
- Avoids system-wide breakage
- Preserves existing contracts for non-learning paths
- Enables production rollout behind feature flags
- Gives clean observability for enhancement impact

---

## 3) Detailed Architecture

### 3.1 PromptEnhancer service (`app/orchestrator/prompt_enhancer.py`)

Implement:

- `enhance_prompt(db, base_prompt, task, agent_type, learning_disabled=False) -> tuple[str, list[str]]`
- `_query_relevant_learnings(...)`
- `_select_learnings(...)`
- `_inject_learnings(...)`

Config:
- `LEARNING_INJECTION_ENABLED` (default `true`)
- `MAX_LEARNINGS_PER_PROMPT` (default `3`)
- `MIN_CONFIDENCE_THRESHOLD` (default `0.3`)
- `LEARNING_CONTROL_GROUP_PCT` (default `0.2`)

Hard requirements:
- Never throw to caller (return base prompt, `[]` on any error)
- Always log with `[LEARNING]` prefix
- Query constraints: `agent_type`, `is_active`, confidence threshold, optional category/complexity match

### 3.2 Prompter integration (`app/orchestrator/prompter.py`)

Add:
- `async def build_task_prompt_enhanced(db, item, project_path, agent_type=None, rules="", learning_disabled=False) -> tuple[str, list[str]]`

Flow:
1. Build base prompt using existing sync `build_task_prompt(...)`
2. If no db session, return `(base_prompt, [])`
3. Materialize minimal task context object from `item`
4. Call `PromptEnhancer.enhance_prompt(...)`
5. Return `(enhanced_prompt, learning_ids)`

Do **not** change current `build_task_prompt` signature.

### 3.3 Worker integration (`app/orchestrator/worker.py`)

At prompt build site:
- Sample control group with `LEARNING_CONTROL_GROUP_PCT`
- Use `await Prompter.build_task_prompt_enhanced(...)`
- Persist resulting prompt text as today
- Best-effort write-back of `applied_learnings` + `learning_disabled` to most recent `TaskOutcome` for `task_id`
  - If no outcome row exists yet, log debug and continue

This keeps task execution robust even if phase 1.1 flow is partial.

### 3.4 Observability

Add structured logs:
- candidates found
- selected count
- injected learning IDs
- control vs treatment decision
- enhancement latency ms

Target:
- `<200ms` p95 enhancement overhead (query + inject)

---

## 4) Tradeoffs

### Chosen: add async enhanced API, keep sync legacy API
**Pros:** safe rollout, low blast radius, no forced refactor of all callers  
**Cons:** temporary duplicate API surface

### Rejected: hard-replace `build_task_prompt` with async tuple API now
**Why rejected:** too risky during retries; touches multiple codepaths and tests at once.

### Chosen: best-effort outcome linkage in worker
**Pros:** resilient to partial dependency state  
**Cons:** if outcome record not yet created, applied learnings may be missed for that run.

---

## 5) Implementation Plan (Ordered)

### Task A — PromptEnhancer service (small/medium)
**Owner:** programmer  
**Acceptance:**
- New file exists with required methods
- Feature flags/env vars implemented
- Failure isolation implemented (no unhandled exceptions)
- Unit tests for query/select/inject + feature flag + control group behavior

### Task B — Prompter compatibility API (small)
**Owner:** programmer  
**Acceptance:**
- Existing sync API unchanged
- New async `build_task_prompt_enhanced` added
- Returns tuple with learning IDs
- Falls back to no-op enhancement when db missing/errors

### Task C — Worker runtime integration (medium)
**Owner:** programmer  
**Acceptance:**
- `worker.py` uses enhanced API
- control/treatment sampling implemented via env-configured percent
- best-effort persistence to `TaskOutcome.applied_learnings` and `.learning_disabled`
- worker spawn remains functional when enhancement fails

### Task D — Tests + performance guardrails (medium)
**Owner:** programmer  
**Acceptance:**
- `tests/test_prompt_enhancer.py` added
- `tests/test_prompter_learning_integration.py` added
- worker-level integration test for fallback path added
- simple latency test/assertion for enhancement path under local DB fixture

---

## 6) Testing Strategy

1. **Unit tests**
   - learning query filters
   - top-N selection behavior
   - prompt injection formatting
   - env feature flag disable path
   - control-group skip path

2. **Integration tests**
   - end-to-end: outcome learning exists -> prompt contains lesson text
   - no-learning path returns original prompt + empty IDs
   - exception in enhancer does not block worker prompt generation

3. **Regression tests**
   - existing callers of `build_task_prompt()` still pass unchanged

4. **Performance**
   - capture enhancement elapsed ms in logs; assert not pathological in tests

---

## 7) Risks & Mitigations

- **Risk:** Prompt bloat from long lessons  
  **Mitigation:** cap number of learnings; truncate lesson text per item.

- **Risk:** Missing outcome row at injection time  
  **Mitigation:** best-effort update + log; do not fail execution.

- **Risk:** Query quality (irrelevant lessons)  
  **Mitigation:** strict agent match + confidence threshold + category/complexity filters.

---

## 8) Programmer Handoffs

```json
[
  {
    "to": "programmer",
    "initiative": "agent-learning-system",
    "title": "Implement PromptEnhancer with fail-safe behavior",
    "context": "Create app/orchestrator/prompt_enhancer.py per rescue architecture. Keep all failures non-fatal and log with [LEARNING].",
    "acceptance": "Enhancer methods implemented, env flags honored, and unit tests cover query/select/inject + disabled paths.",
    "files": ["docs/handoffs/learning-phase-1.3-rescue-architecture.md", "docs/handoffs/learning-phase-1.3-prompt-enhancement.md"]
  },
  {
    "to": "programmer",
    "initiative": "agent-learning-system",
    "title": "Add non-breaking enhanced prompter API",
    "context": "Add async build_task_prompt_enhanced returning (prompt, learning_ids), keep existing sync build_task_prompt unchanged.",
    "acceptance": "All existing sync call sites continue working; enhanced API returns tuple and gracefully degrades on enhancer errors.",
    "files": ["docs/handoffs/learning-phase-1.3-rescue-architecture.md", "app/orchestrator/prompter.py"]
  },
  {
    "to": "programmer",
    "initiative": "agent-learning-system",
    "title": "Integrate learning injection into worker.py only (phase 1 rollout)",
    "context": "Use enhanced prompter API in worker.py, apply control-group sampling, and best-effort persist applied learning metadata to TaskOutcome.",
    "acceptance": "Worker spawning remains stable; treatment tasks get enhanced prompts; control tasks do not; no task fails due to learning pipeline errors.",
    "files": ["docs/handoffs/learning-phase-1.3-rescue-architecture.md", "app/orchestrator/worker.py", "app/models.py"]
  },
  {
    "to": "programmer",
    "initiative": "agent-learning-system",
    "title": "Add tests and latency instrumentation for phase 1.3",
    "context": "Add/extend tests for enhancer and prompter integration, including fallback and overhead behavior.",
    "acceptance": "New tests pass locally and validate both happy path and graceful degradation; enhancement latency is observable in logs.",
    "files": ["docs/handoffs/learning-phase-1.3-rescue-architecture.md", "tests/"]
  }
]
```

---

## 9) Done Definition

Phase 1.3 is considered complete when:
- prompts can be enhanced with relevant learnings,
- rollout is controlled via flags/A-B sampling,
- no regression occurs in worker prompt generation,
- applied learning metadata is captured when available,
- test coverage includes failure isolation and integration paths.
