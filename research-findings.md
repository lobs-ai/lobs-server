# Reliability Experiment: Prompt Variant Trials for First-Response SLA

**Task ID:** ddeca4a4-a21d-4050-95c4-16e05094a02f  
**Date:** 2026-02-25  
**Researcher:** researcher agent  
**Source Initiative:** a9384722-3e82-42e4-b028-603c553a2121  
**Source Reflection:** 32ba1e25-aad4-459e-8c03-00ae76c6ebce

---

## Executive Summary

Analysis of 9,661 worker runs reveals that **~47% of failures occur in the 5–10 minute window** — exactly where the 10-minute first-response SLA lives. The dominant failure mechanisms are `exit_code_-1` (no meaningful agent output, 45% of failures) and `worker_stuck_no_heartbeat_for_5min` (20% of failures), both consistent with an agent that spawns but never emits a first response within the SLA.

The system already has an 80/20 A/B testing infrastructure in `worker.py` (the learning control-group split). This experiment proposes a **second orthogonal A/B split specifically for prompt structure** — Variant A keeps the current layout, Variant B front-loads the task and uses a minimal preamble — measured against three metrics: SLA hit rate, `RVC_SLA_BREACH` rate, and completion validity.

The experiment is low-risk, reversible, and requires a programmer to implement the variant selector and instrument `WorkerRun.task_log` with the variant label.

---

## 1. Background: The SLA Contract

The Run Validity Contract (`app/orchestrator/run_validity.py`) enforces:

| Requirement | Code | SLA |
|---|---|---|
| Lifecycle event (started_at set) | `RVC_MISSING_LIFECYCLE` | Always |
| First assistant response present | `RVC_NO_FIRST_RESPONSE` | Always |
| First response within SLA | `RVC_SLA_BREACH` | **600 seconds (10 min)** |
| Transcript durable | `RVC_NO_TRANSCRIPT` | Always |
| Evidence bundle (summary or files) | `RVC_NO_EVIDENCE` | Always |

When any of these fail, the task is **fail-closed**: work_state reverts to `not_started`, failure_reason records the violation codes, and the task is eligible for retry.

The first-response timestamp is read from the JSONL transcript's first `role==assistant` entry. If none is found (session ran but produced no output), or if the delta from `started_at` > 600s, the contract is violated.

---

## 2. Current Failure Landscape

### Worker Run Outcomes (all-time)

| Outcome | Count | % |
|---|---|---|
| Succeeded | 8,751 | 90.6% |
| Failed | 910 | 9.4% |

### Failed Runs — Duration Distribution

| Duration Band | Count | % of Failures | Interpretation |
|---|---|---|---|
| 5–10 minutes | **432** | **47.5%** | Running but stuck — SLA zone |
| 30s–2 min | 231 | 25.4% | Fast failures (init, spawn, protocol) |
| <30s | 158 | 17.4% | Immediate failures (spawn_failed) |
| 2–5 min | 76 | 8.4% | Mid-range failures |
| >10 min | 13 | 1.4% | Near or past kill timeout |

### Failed Runs — Timeout Reason Breakdown

| Reason | Count | % | What It Means |
|---|---|---|---|
| `exit_code_-1` | 413 | 45.4% | Session ended without usable output |
| `finalize_exception` | 200 | 22.0% | Exception during completion handling |
| `worker_stuck_no_heartbeat_for_5min` | 180 | 19.8% | Worker spawned, no first response in 5min |
| `spawn_failed` | 34 | 3.7% | Never got off the ground |
| `finalize_failed` | 32 | 3.5% | Completion handling crashed |
| Other | 51 | 5.6% | Shutdown, exit_code_1, etc. |

### Task-Level Failure Reasons (most common)

| Reason | Count |
|---|---|
| No assistant response in deleted transcript | 21 |
| Stuck - no progress for 15 minutes | 19 |
| Orchestrator shutdown | 18 |
| Session not found | 16 |

**Key insight:** The two biggest SLA-relevant failure modes are:

1. **`worker_stuck_no_heartbeat_for_5min`** (180 runs) — the agent spawns and never emits any response at all. No transcript evidence, no first response. These all fail the `RVC_NO_FIRST_RESPONSE` check.

2. **"No assistant response in deleted transcript"** — the transcript exists but by the time it's read, it contains zero assistant messages. Session ran but the agent was silent.

Both patterns suggest the agent received the prompt but **spent too long deliberating before first output** — consistent with a prompt that front-loads heavy context that must be processed before the agent can begin.

---

## 3. Root Cause Hypothesis

### Prompt Structure Today

The current prompt structure (`app/orchestrator/prompter.py`, `build_task_prompt()`) is:

```
1. === Lessons from Past Tasks === (prepended by PromptEnhancer — up to 3 learnings)
2. ## Agent Context (AGENTS.md + SOUL.md) — up to 12,000 chars
3. # Work Assignment
4. ## Agent Mode: [type] — mode-specific framing + code file listing
5. ## Engineering Rules
6. ## Product Context (README + ARCHITECTURE + PRODUCT — up to ~15,000 chars)
7. ## Your Task (the actual task title + notes)
8. ## When You're Done
9. Begin.
```

**The task content is buried at position 7 of 9**, after potentially 20,000+ characters of context. For an agent starting cold:

- Steps 1–6 require reading and loading large context blocks
- First token of actual task appears very late
- Agent may begin planning/thinking about context before even reading the task
- In models with extended thinking or careful reading modes, this multiplies startup delay

### Why This Explains the Data

- The `5–10 minute` failure band (432 runs, 47%) aligns precisely with an agent that reads ~15–20k characters of preamble, then either: (a) times out the allocated thinking window, or (b) produces a planning message so late it breaches the SLA
- The `worker_stuck_no_heartbeat_for_5min` (180 runs) suggests the agent issues no tool calls or messages during the first 5 minutes — consistent with a "reading and thinking" phase on a long context prompt
- Models under heavy cache-read load (the token_usage data shows cache_read_tokens in the hundreds of thousands for successful long runs) may have latency spikes when warming cache

---

## 4. Proposed Experiment Design

### Hypothesis

**H₀** (null): Prompt structure has no effect on first-response SLA hit rate.  
**H₁** (alternative): Task-first prompt structure reduces SLA misses by ≥15% vs. baseline.

### Variant Definitions

#### Variant A — Baseline (current)
Current prompt structure exactly as built by `Prompter.build_task_prompt()`:
- Lessons prepended (if PromptEnhancer enabled)
- Agent context → engineering rules → product context → task details
- 20,000–30,000 chars typical for programmer tasks

#### Variant B — Action-First (treatment)
Restructured prompt with task content moved to position 1:

```
1. # Your Task (task title + notes — immediately actionable)
2. ## When You're Done (exit contract — brief)
3. ## Agent Context (SOUL.md only — stripped AGENTS.md to essentials ~1,000 chars)
4. ## Workspace (path and task ID)
5. ## Engineering Rules (if any — collapsed)
6. ## Key Context (condensed product context: README summary only, 500 chars max)
7. Begin. (explicit action trigger)
```

Key changes:
- Task title and notes appear in the **first 10 lines**
- Product context condensed to README summary only (vs. full README + ARCHITECTURE + PRODUCT)
- AGENTS.md dropped (SOUL.md provides the personality; AGENTS.md provides tooling reference the agent shouldn't need for first response)
- Lessons from PromptEnhancer still injected, but **after** the task statement (not before)
- Total prompt length target: <5,000 chars for typical tasks

### Randomization

Use the existing A/B infrastructure in `worker.py:spawn_worker()`. The learning control-group split uses `random.random() < control_group_pct`. Add a **separate** variant selector:

```python
# Prompt variant trial (orthogonal to learning A/B split)
prompt_variant = "A" if random.random() < 0.5 else "B"
```

Store `prompt_variant` in `WorkerRun.task_log` so it can be correlated with outcomes.

### Metrics

| Metric | Measurement Point | Success Direction |
|---|---|---|
| **First-response SLA hit rate** | `RunValidityResult.sla_ok` | Higher is better for Variant B |
| **RVC_SLA_BREACH rate** | `RVC_SLA_BREACH` in `failure_reason` | Lower is better for Variant B |
| **RVC_NO_FIRST_RESPONSE rate** | `RVC_NO_FIRST_RESPONSE` in `failure_reason` | Lower is better for Variant B |
| **Completion validity** | `RunValidityResult.passed` overall | Must not degrade for Variant B |
| **Output quality** | Human review spot check (10%) | Must not degrade for Variant B |

The primary signal is **SLA hit rate** (proportion of runs where `first_response_time_seconds` ≤ 600s). Secondary: overall validity pass rate (compound metric that includes evidence quality).

### Sample Size

Ballpark power calculation:
- Current failure rate: ~9.4% (910/9661)
- SLA-related failures (5–10min band): ~4.5% of total runs
- Target: detect a 15% relative reduction in SLA misses (4.5% → ~3.8%)
- Required sample per arm: ~400 runs (at α=0.05, power=80%, two-tailed)
- **Total sample size: 800 runs** (~400 per variant)

At the current run rate (the system runs hundreds of workers per day based on DB data), this should take **3–7 days** to collect with 50/50 randomization across non-internal tasks.

Exclude from sample: reflection workers, sweep workers, inbox workers, diagnostic workers (these are internal tasks with different prompt requirements).

### Feature Flag

The variant selector should check an `OrchestratorSetting`:

```python
PROMPT_VARIANT_TRIAL_ENABLED = "prompt_variant_trial_enabled"
PROMPT_VARIANT_B_PCT = "prompt_variant_b_pct"  # 0.0 to 1.0, default 0.5
```

This allows:
- Enabling/disabling the trial without code change
- Adjusting the B fraction (e.g., ramp to 80% if early results are strong)
- Instant rollback (set to 0.0 to stop using Variant B)

### Promotion Criteria

After 800 total runs (400 per variant):
- If Variant B shows **≥15% reduction in SLA misses** (p < 0.05): promote to 100%
- If Variant B shows **no degradation in completion validity** (p > 0.1 on quality loss): prerequisite for promotion
- If Variant B shows **any increase in RVC violations** (other than SLA-related): abort trial

---

## 5. Implementation Plan

This is a light code change. Estimated programmer effort: **2–4 hours**.

### Files to Modify

1. **`app/orchestrator/prompter.py`** — add `build_task_prompt_variant_b()` method implementing the action-first structure, and `build_task_prompt_for_trial()` dispatcher that takes `variant: str`

2. **`app/orchestrator/worker.py`** (`spawn_worker()`) — add variant selection logic:
   ```python
   # After line ~234 (learning A/B split)
   prompt_variant = await _select_prompt_variant(db)  # "A" or "B"
   # Pass variant to Prompter
   prompt_content, applied_learning_ids = await Prompter.build_task_prompt_for_trial(
       db=self.db,
       item=task,
       project_path=repo_path,
       agent_type=agent_type,
       rules=global_rules,
       learning_disabled=learning_disabled,
       prompt_variant=prompt_variant,
   )
   ```

3. **`app/orchestrator/worker.py`** (`_record_worker_run()`) — add `prompt_variant` to `task_log`:
   ```python
   task_log["prompt_variant"] = prompt_variant
   ```

4. **`app/orchestrator/runtime_settings.py`** — add `PROMPT_VARIANT_TRIAL_ENABLED` and `PROMPT_VARIANT_B_PCT` settings with defaults

5. **`app/services/analytics.py`** (or new file) — query to compute SLA hit rate by variant from `worker_runs.task_log`

### No Schema Migration Needed

`task_log` is a JSON column that already exists on `worker_runs`. Variant label is just another key stored there.

---

## 6. Variant B Prompt Template

Below is the concrete proposed structure for Variant B:

```
# Your Task

**{task_title}**

{task_notes}

---

## When You're Done

Just stop. The orchestrator handles git commits and state updates.

Write 1-2 lines to `.work-summary` when finished:
```
echo "Summary here" > .work-summary
```
If blocked: `echo "BLOCKED: reason" > .work-summary && exit 1`

---

## Context

**Agent:** {agent_type}  
**Project:** {project_id}  
**Workspace:** `{project_path}`  
**Task ID:** {task_id}

### Your Identity
{soul_md[:800]}

### Project Overview
{readme_first_paragraph_or_500_chars}

{engineering_rules_if_any}

---

Begin.
```

The key structural difference: the task is the **first thing the agent reads**. Context follows. The agent forms intent before reading context, not after.

---

## 7. Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Variant B agents lack enough context and produce lower-quality output | Medium | Spot-check 10% of Variant B completions; abort if quality drops |
| Stripping AGENTS.md causes agents to miss project conventions | Medium | Include key conventions in condensed section or from `ENGINEERING_RULES.md` |
| 50/50 split dilutes quality on important tasks | Low | Can reduce B fraction to 20% and extend trial; filter to low-stakes tasks only |
| Random assignment produces skewed task types | Low | Log task category (from PromptEnhancer's `_infer_task_category`) per run |
| Prompt variant affects learning A/B split validity | Low | Store both flags in task_log; analyze jointly |
| First-response timing measurement is imprecise (transcript timestamps) | Medium | Document measurement methodology; same issue affects both variants equally |

---

## 8. Alternative Approaches Considered

### Alt 1: Tune the SLA threshold upward (600s → 900s)
- **Against:** Doesn't fix latency, just hides it. The 5–10 minute failure band shifts to 8–15 minutes. Trust signal degrades more slowly but still degrades.

### Alt 2: Warm the model by sending a "start" ping before the real prompt
- **Against:** Gateway API doesn't support this. Session spawning is atomic.

### Alt 3: Stream first-response detection (don't wait for full transcript)
- **For:** Would detect first response immediately, not post-hoc. Could catch SLA breaches in real time.
- **Against:** Large infrastructure change; requires streaming transcript reader in the engine loop. Good follow-up after this experiment but not the fastest path to improvement.

### Alt 4: Pre-summarize large context files before injecting
- **For:** Reduces prompt length; could be combined with Variant B.
- **Against:** Pre-summarization adds latency at spawn time and costs tokens. Adds complexity.

**Recommendation:** Start with Variant B (structural reorder, no new infrastructure). If results are strong, combine with Alt 4 (pre-summarized context) in a Phase 2 trial.

---

## 9. What to Do Next

**Immediate action (programmer task):**

1. Implement `Prompter.build_task_prompt_variant_b()` per the template in §6
2. Add variant selector + `OrchestratorSetting` feature flag in `worker.py`
3. Add `prompt_variant` to `WorkerRun.task_log`
4. Add a simple `/api/usage/prompt-variant-report` endpoint that queries hit rates by variant from `worker_runs`

**After 3–7 days of data collection:**

5. Query `worker_runs` for variant comparison: SLA hit rate, overall validity rate
6. Run chi-squared test on SLA pass/fail contingency table
7. If H₁ supported: merge Variant B as the new default behind `PROMPT_VARIANT_B_PCT=1.0`

**Parallel investigation (optional):**

- Investigate the `worker_stuck_no_heartbeat_for_5min` pattern more closely — are these all specific agent types? Specific models? The data shows `exit_code_-1` (413) and `worker_stuck` (180) together account for 65% of failures. Understanding whether these cluster by model or agent type would help prioritize whether prompt changes are the right lever, or whether model routing is the primary fix.

---

## Sources

- `app/orchestrator/run_validity.py` — SLA contract, violation codes, `FIRST_RESPONSE_SLA_SECONDS=600`
- `app/orchestrator/worker.py` — spawn flow, A/B control-group split, `_handle_worker_completion` (lines ~234, ~370–450)
- `app/orchestrator/prompter.py` — current prompt structure, `build_task_prompt()`, section ordering
- `app/orchestrator/prompt_enhancer.py` — learning injection, position of `Lessons from Past Tasks` block
- `data/lobs.db:worker_runs` — 9,661 runs: 8,751 succeeded (90.6%), 910 failed (9.4%)
- `data/lobs.db:tasks.failure_reason` — top reason: "No assistant response in deleted transcript" (21 tasks)
- Worker run duration analysis: 47% of failures fall in 5–10 min SLA window
- `app/orchestrator/config.py` / `app/orchestrator/runtime_settings.py` — settings infrastructure
