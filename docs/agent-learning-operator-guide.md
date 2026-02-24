# Agent Learning System — Operator Guide

**Date:** 2026-02-24  
**Status:** Implementation pending — guide reflects the system once built  
**Audience:** Human operators managing agent behavior  
**Design spec:** [learning-loop-mvp-design.md](learning-loop-mvp-design.md)

---

## What This Is

The agent learning system automatically tracks whether agent tasks succeed or fail, finds patterns in failures, and surfaces lesson suggestions for you to approve. Once you approve a lesson, it gets injected into matching agent prompts before the next run.

**The flow in one line:** Agent runs → outcome logged → patterns aggregated nightly → lessons suggested → you approve → agents improve.

---

## The Learning Cycle

```
1. Agent completes a task
         ↓
2. Outcome logged automatically
   (success / failure / user-corrected)
         ↓
3. Daily at 2am ET: batch job groups failures by pattern
   e.g. "programmer had 8 missing_tests failures in 14 days"
         ↓
4. Batch creates a lesson suggestion (inactive, pending approval)
   e.g. "Always include unit tests..."
         ↓
5. You review and approve/reject via dashboard or API
         ↓
6. Active lessons injected into matching prompts pre-spawn
   (80% of tasks — 20% are control group for measuring impact)
         ↓
7. Next summary shows A/B lift: did the lesson help?
```

---

## Daily Workflow

### Morning Review

Check the summary each morning for:
1. **Pending suggestions** — new lessons awaiting your approval
2. **A/B lift** — are active lessons improving outcomes?
3. **Top failure patterns** — where agents are struggling

```bash
# Via curl
curl -H "Authorization: Bearer $API_TOKEN" \
  http://localhost:8000/api/agent-learning/summary
```

Or in the dashboard: **Agent Learning** tab → Summary view.

### Approving a Lesson

When the batch job surfaces a new lesson, you'll see it in:
- The **ops brief** (daily summary, 8am)  
- The **inbox** (as a suggestion item)
- `GET /api/agent-learning/summary` → `pending_suggestions`

To approve via API:
```bash
curl -X PATCH \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "approve"}' \
  http://localhost:8000/api/agent-learning/learnings/<id>
```

To edit before approving (improve the lesson text):
```bash
curl -X PATCH \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "edit", "lesson_text": "Always write tests in a separate test_<module>.py file with at least one test per public function."}' \
  http://localhost:8000/api/agent-learning/learnings/<id>

# Then approve
curl -X PATCH \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "approve"}' \
  http://localhost:8000/api/agent-learning/learnings/<id>
```

### Tagging an Outcome Manually

When you review a task and find the agent made a specific mistake, tag it:

```bash
curl -X POST \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "A1B2C3D4",
    "outcome": "user-corrected",
    "human_feedback": "Code worked but had no error handling for the API call.",
    "reason_tags": ["missing_error_handling"]
  }' \
  http://localhost:8000/api/agent-learning/outcomes
```

Manual tags are higher quality than auto-detected patterns — they feed the next batch run directly.

---

## Understanding the Summary

### A/B Lift

The system runs a 20% control group: these tasks receive **no** learning injection. The other 80% get the lessons. The `lift` field shows relative improvement:

```
lift = (treatment_success_rate - control_success_rate) / control_success_rate
```

- **Positive lift** (e.g., `0.20`) — lessons are helping. 20% improvement.
- **Near zero** — lessons aren't making a difference. Review what they say.
- **Negative** — lessons may be harmful or confusing. Reject or rewrite them.

A lift of 0.10+ (10%) is the target for each active learning.

### Confidence Score

Each lesson has a `confidence` score (0.0–1.0):

| Score | Meaning |
|-------|---------|
| 0.5 | Just approved — starting confidence |
| 0.6–0.9 | Performing well — incremented on each success |
| < 0.3 | Struggling — auto-deactivated if failure_count ≥ 3 |
| 1.0 | Maximum — excellent track record |

When a task using a lesson succeeds, confidence goes up (+0.1). When it fails, it goes down (-0.15). A lesson with confidence < 0.3 and ≥ 3 failures is automatically deactivated and flagged for review.

### Failure Patterns

Standard reason tags and what they mean:

| Tag | Meaning |
|-----|---------|
| `missing_tests` | Task delivered code with no tests |
| `missing_error_handling` | No try/except around I/O, external calls, or failure paths |
| `unclear_names` | Variables, functions, or files had confusing/abbreviated names |
| `missing_docs` | No docstrings, no inline comments on complex logic |
| `missing_validation` | Input wasn't validated before use |
| `wrong_approach` | Technically correct but wrong solution to the actual problem |
| `incomplete` | Task didn't satisfy all requirements |
| `scope_creep` | Task expanded beyond what was asked |
| `hallucinated_api` | Agent invented APIs or methods that don't exist |

---

## When Things Go Wrong

### "Lift is near zero" — learnings aren't helping

1. Read the active lessons. Are they specific enough? Vague lessons ("Write good code") don't help.
2. Check which agent type and task category — maybe the lesson applies broadly but only matters for one context.
3. Reject weak lessons and manually write a better one via inbox proposal.

### "Summary shows no data" — nothing is being tracked

Possible causes:
- The `OutcomeTracker` integration in `worker.py` isn't running — check server logs for `[LEARNING]` entries.
- `LEARNING_ENABLED` env var is `false`.
- No tasks have completed since the feature was deployed.

```bash
# Check server logs for learning activity
grep "\[LEARNING\]" /path/to/server.log | tail -20
```

### "Batch created no suggestions"

The batch requires ≥3 failures with the same reason tag in the last 14 days before creating a suggestion. If failures are spread across different tags or are fewer than 3, no suggestion is created. You can trigger suggestions manually by tagging outcomes via `POST /api/agent-learning/outcomes`.

### "A lesson was auto-deactivated"

Check the ops brief — it logs deactivations with reason. An auto-deactivated lesson means the advice was making things worse. You can:
1. Read the lesson and the failure tasks it was applied to
2. Rewrite the lesson to be more precise
3. Re-approve the updated version

---

## Feature Flags

Control learning behavior via environment variables:

| Variable | Default | Effect |
|----------|---------|--------|
| `LEARNING_ENABLED` | `true` | Master switch — turn off entirely |
| `LEARNING_INJECTION_ENABLED` | `true` | Disable prompt injection, keep tracking |
| `LEARNING_AB_TEST_ENABLED` | `true` | Disable A/B split (100% get learnings) |
| `LEARNING_CONTROL_GROUP_PCT` | `0.20` | Control group size (0.0–1.0) |
| `MAX_LEARNINGS_PER_PROMPT` | `3` | Max lessons per prompt |
| `MIN_CONFIDENCE_THRESHOLD` | `0.3` | Minimum confidence to inject |
| `LEARNING_BATCH_ENABLED` | `true` | Daily batch pattern extraction |

Set in `.env` or as shell environment variables before starting the server.

---

## Editing Lessons Directly

If a lesson needs a minor text fix, use `edit` then `approve`:

```bash
# Step 1: edit the lesson text
curl -X PATCH \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "edit", "lesson_text": "Your improved lesson here."}' \
  http://localhost:8000/api/agent-learning/learnings/<id>

# Step 2: approve it (sets is_active=true, confidence=0.5)
curl -X PATCH \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "approve"}' \
  http://localhost:8000/api/agent-learning/learnings/<id>
```

Write lessons as **direct instructions to the agent**, not observations about past failures:
- ❌ "Agents often forget to add tests"
- ✅ "Always create a test file alongside your implementation. Name it `test_<module>.py` and include at least one test per public function."

---

## Related Docs

| Document | Purpose |
|----------|---------|
| [learning-loop-mvp-design.md](learning-loop-mvp-design.md) | Full technical spec |
| [handoffs/learning-loop-mvp-status.md](handoffs/learning-loop-mvp-status.md) | Implementation status |
| [handoffs/learning-loop-mvp-implementation-guide.md](handoffs/learning-loop-mvp-implementation-guide.md) | Programmer build guide |
| [ARCHITECTURE.md](../ARCHITECTURE.md#8-agent-learning-system) | System overview |
| [AGENTS.md](../AGENTS.md#agent-learning-api-endpoints) | API endpoint reference |

---

*The goal is a 10%+ improvement in programmer code review acceptance rate within 4 weeks of the first lessons being approved.*
