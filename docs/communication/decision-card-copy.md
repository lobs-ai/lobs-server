# Decision Card Copy — Sample Text Variants

**Status:** Reference document  
**Created:** 2026-02-25  
**Author:** architect  
**Purpose:** 5 real examples of decision cards drawn from recent lobs-server remediation scenarios. Use as reference when writing new cards.

Each example follows the canonical format from [decision-card-spec.md](decision-card-spec.md).

---

## Example 1 — Budget Lane Exhausted Mid-Task

**Scenario:** The `standard` budget lane hit its daily cap while an agent was mid-implementation on a medium-priority task. The task is paused; the agent cannot spawn any more model calls.

```markdown
## 🃏 Decision Required — Override Daily Budget Cap for Active Task

**Task:** Implement pattern extraction for learning loop (task-8a3d1e)  
**Deadline:** 2026-02-25T18:00:00-05:00 (6 hours from now)  
**Urgency:** 🔴 Critical  
**If no response by deadline:** Task is cancelled, rolled back to queued. Will retry tomorrow when budget resets.

---

### What Happened
The `standard` budget lane hit its $8.00 daily cap at 11:47 AM while the programmer agent was 60% through implementing the pattern extractor. The agent made 14 model calls totaling $7.98 before hitting the limit. No further progress is possible until the cap resets at midnight or is overridden.

### Your Options

| # | Option | Consequence |
|---|--------|-------------|
| A | Override cap — allow up to $4 more for this task today | Task completes today. Total spend: ~$12. Cap override is logged; daily report will flag it. |
| B | Cancel task, retry tomorrow | Work already done is preserved in commit. Task resumes tomorrow with fresh budget. Delay: ~16 hours. |
| C | Downgrade to `background` lane | Task continues with cheaper micro/small models. Likely quality drop; may require additional retry. |

### Recommendation
**→ Option B** — The task is 60% complete, committed work is preserved, and nothing is time-sensitive. Waiting 16 hours costs less than a $4 override on a non-critical task. Override budget if the task were blocking a deploy or deadline.

### To Approve
Reply with your choice (A, B, or C) in chat, or run:
`lobs task task-8a3d1e set-option B`
```

---

## Example 2 — Agent Stuck at Escalation Tier 3

**Scenario:** A programmer agent has failed 3 times on the same task. Each failure was a different root cause (timeout, wrong file, test failure). The orchestrator has escalated to tier 3 and paused auto-retry pending human review.

```markdown
## 🃏 Decision Required — Repeated Failure on Task, 3 Retries Exhausted

**Task:** Add `cancel_reason` validation to PATCH /api/tasks/{id}/status (task-9f2b44)  
**Deadline:** 2026-02-25T20:00:00-05:00 (8 hours from now)  
**Urgency:** 🔴 Critical  
**If no response by deadline:** Task is placed in `blocked` status. Will appear in tomorrow's brief for triage.

---

### What Happened
The programmer agent attempted this task 3 times and failed each time: (1) timeout at 45 min before writing any code, (2) wrote changes to wrong file (`app/routers/inbox.py` instead of `tasks.py`), (3) test suite failed — `test_task_status_transitions` rejected the new 422 response. The task was correctly scoped and the schema exists. Each failure had a different root cause.

### Your Options

| # | Option | Consequence |
|---|--------|-------------|
| A | Retry with architect-level context injection | Add a correction note to the task with the exact file location and expected test changes. One more auto-retry with that context. Cost: ~$0.80. |
| B | Assign to a senior model tier (strong) | Force `claude-sonnet` or equivalent for next run. Higher cost (~$2.50) but much higher first-pass success rate. |
| C | Cancel this task | Remove from backlog. The `cancel_reason` validation can be skipped for now, reopened if needed. Tradeoff: silent task cancellations remain unflagged. |

### Recommendation
**→ Option A** — The failures are context failures, not logic failures. The task is straightforward (one route, one validation rule). Add the file path and test name as explicit hints before retrying. If it fails again, escalate to Option B.

### To Approve
Reply "A", "B", or "C" in chat.  
If Option A: paste the correction context you want injected, or say "use defaults" and the system will add the most recent error trace.
```

---

## Example 3 — Stuck Inbox Remediation (Approved, Never Started)

**Scenario:** An inbox item was approved 5 days ago and converted to a task. The task has been in `queued` status for 5 days without pickup. The daily ops brief stuck-remediations check surfaced it.

```markdown
## 🃏 Decision Required — Approved Task Stuck in Queue 5 Days

**Task:** Set up ADR system in lobs-shared-memory (task-3c7a90)  
**Deadline:** 2026-02-26T08:00:00-05:00 (tomorrow morning)  
**Urgency:** 🟡 Standard  
**If no response by deadline:** Escalates to 🔴 Critical in tomorrow's brief. System will attempt force-assign to next available architect agent.

---

### What Happened
This task was approved from inbox on 2026-02-20 and has been in `queued` status for 5 days. The architect agent has been running (completed 7 other tasks in this window) but this task was never picked up. Root cause: it was in the general queue without explicit agent assignment. No blocking dependency.

### Your Options

| # | Option | Consequence |
|---|--------|-------------|
| A | Force-assign to architect agent now | Jumps the queue. Architect picks it up on next cycle (1–3 hours). |
| B | Deprioritize — move to backlog | Removes from active queue. ADR system stays undone for now. Re-request when ready. |
| C | Cancel — scope has changed | If the original need is no longer relevant, clean closure. Requires a cancel reason. |

### Recommendation
**→ Option A** — This was already approved. The work is well-scoped. The only reason it stalled is queue position. Force-assign costs nothing and unblocks a system-level improvement that affects all agents.

### To Approve
Reply "A", "B", or "C". For C, add a one-line reason (e.g., "superseded by lobs-shared-memory overhaul").
```

---

## Example 4 — Scope Ambiguity Before Significant Implementation

**Scenario:** Before starting a 3-day implementation, the programmer agent hit a fork: two valid interpretations of the spec with different tradeoffs. The task spec doesn't specify, and getting it wrong would require a rewrite.

```markdown
## 🃏 Decision Required — Architecture Fork in Learning Loop Implementation

**Task:** Implement outcome tracking hook for agent learning system (task-5e2d78)  
**Deadline:** 2026-02-26T12:00:00-05:00 (tomorrow noon)  
**Urgency:** 🟡 Standard  
**If no response by deadline:** Programmer uses Option A (simpler approach) and proceeds. You can request a rewrite if wrong.

---

### What Happened
The programmer agent paused before implementing `Hook 2` (outcome tracking) in the agent learning system. The spec says "track task outcomes" but is ambiguous on what counts as a signal: option A treats any `succeeded = True` worker run as positive; option B requires a separate outcome evaluation step. The implementation effort differs by ~4 hours and the data quality differs significantly.

### Your Options

| # | Option | Consequence |
|---|--------|-------------|
| A | Use worker run success as the signal | Simpler. Implemented in ~2 hours. Less precise — some succeeded tasks may be low quality (e.g., passed tests but wrong approach). |
| B | Add outcome evaluation step (separate worker run) | More accurate signal. +4 hours to implement. Requires a new worker type. Better training data long-term. |

### Recommendation
**→ Option A** — We're in MVP phase. Getting the loop running with imperfect signals is more valuable than perfect signals with a delayed start. We can replace signal quality in v2 once we see what patterns emerge. This is a reversible decision.

### To Approve
Reply "A" or "B". No other info needed — the programmer will proceed within the hour.
```

---

## Example 5 — Production Deploy Gating on Failing Test

**Scenario:** A CI test is failing and the automated deploy is paused. The failure is in a flaky test that has failed before, but it could also be a real regression. Human must decide: bypass or block.

```markdown
## 🃏 Decision Required — Deploy Blocked on Potentially Flaky Test Failure

**Task:** Deploy failure-explainer API to production (task-7b9c12)  
**Deadline:** 2026-02-25T16:00:00-05:00 (2 hours from now)  
**Urgency:** 🔴 Critical  
**If no response by deadline:** Deploy is held. Task status set to `blocked`. Will retry in tonight's deploy window (10 PM ET).

---

### What Happened
The production deploy for the failure-explainer API is blocked on one failing test: `test_failure_explainer_budget_lane_exhausted`. This test has failed flakily 4 times in the past 30 days (all false positives). However, this deploy includes a budget_guard change that touches the code path the test covers. The failure could be real.

### Your Options

| # | Option | Consequence |
|---|--------|-------------|
| A | Skip this test and deploy now | Ships today. Risk: ~25% chance this is a real regression (based on test history + code diff). If broken, requires a hotfix deploy. |
| B | Block deploy, re-run test suite first | 20-minute delay. If test passes on re-run → deploy proceeds. If fails again → confirms real issue. Safest path. |
| C | Revert the budget_guard change, deploy without it | Ships the failure-explainer today without the guardrail fix. Budget guardrail delayed ~1 day. No risk of regression. |

### Recommendation
**→ Option B** — The test has a real code-path overlap with this change. A 20-minute re-run costs very little compared to a hotfix deploy. If it passes, deploy proceeds. This is the cheapest uncertainty resolution available.

### To Approve
Reply "A", "B", or "C". If B: re-run is triggered automatically. Result reported back within 25 minutes.
```

---

## Writing Tips for New Cards

**Title: make the decision explicit, not the situation.**  
❌ "Budget problem on task 8a3d1e"  
✅ "Override Daily Budget Cap for Active Task"

**What Happened: lead with the agent, then the event, then what it tried.**  
"The programmer agent attempted X, hit Y, and could not Z." Under 40 words.

**Options: write the consequence before you know the choice.**  
Draft the consequence column first (what does each path cost/gain?) then name the option. This forces you to be concrete.

**Recommendation: commit. Never hedge.**  
"→ Option B — because X" is correct.  
"Either A or B could work" is wrong. Pick one.

**Deadline: if it truly doesn't matter, say so — don't leave it blank.**  
`Deadline: none` is valid for advisory items. But you must state it.

**If no response: the safe default is always "do nothing / preserve state".**  
Acceptable: "Task is paused." "System retries tomorrow." "Proceeds with current approach."  
Not acceptable: "System deletes the task." "Deploys to production anyway." "Charges the credit card."

---

*See [decision-card-spec.md](decision-card-spec.md) for the full format specification and urgency threshold table.*
