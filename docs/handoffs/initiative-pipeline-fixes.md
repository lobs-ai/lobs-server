# Initiative Pipeline Fixes — Handoff Spec

**Task ID:** 5936172E-5625-40AC-9383-B98E4524D1C9
**Priority:** High
**Agent:** programmer

## Context

Rafe wants the initiative suggestion pipeline to be smarter. Currently Lobs (the PM agent) makes binary approve/reject/defer decisions. 44 initiatives were rejected, 27 deferred — many of these had good ideas but were scoped wrong or needed prerequisites. Nothing is reaching Rafe for review despite 27 risk-C items.

## Three Fixes Required

### Fix 1: Rafe Escalation (BROKEN)

**Problem:** `sweep_arbitrator._create_rafe_inbox_item()` exists but zero Rafe inbox items were ever created. The sweep creates them at sweep-time for `proposed` initiatives, but the initiatives are already processed by the LLM before the sweep runs.

**Fix:** Move Rafe notification to DECISION TIME in `initiative_decisions.py`:

```python
# In InitiativeDecisionEngine.decide(), after setting status:
if initiative.risk_tier == "C":
    from app.orchestrator.policy_engine import PolicyEngine
    policy = PolicyEngine().decide(initiative.category, estimated_effort=...)
    if policy.escalate_to_rafe:
        # Create Rafe inbox item with the decision context
        self._create_rafe_notification(initiative, decision, decision_summary)
```

The notification should say what Lobs decided and why, so Rafe can override if needed.

### Fix 2: PM Rescoping Ability

**Problem:** The LLM prompt for initiative review (in `worker_monitor._process_sweep_review_results` or wherever the review prompt is constructed) only asks for approve/reject/defer. It doesn't encourage the PM to reshape ideas.

**Fix:** Update the LLM review prompt to include:
1. Encourage rescoping: "If an idea is directionally good but poorly scoped, revise the title and description to make it practical"
2. Prerequisite awareness: Include active tasks in context so PM can see what's in progress
3. Task creation: "If a suggestion needs prerequisite work first, create that prerequisite as a new_task and defer the original"
4. The JSON schema already supports `revised_title`, `revised_description`, `task_notes` — make sure the prompt tells the LLM to use them

Example decision with rescoping:
```json
{
  "initiative_id": "abc123",
  "decision": "approve",
  "revised_title": "Add basic retry logic to worker spawning",
  "task_notes": "Scoped down from full failure recovery system. Just add 1-retry with exponential backoff to worker_gateway.spawn_session(). ~2 hours.",
  "owner_agent": "programmer",
  "reason": "Original scope was too broad. Rescoped to minimal viable retry."
}
```

### Fix 3: Smarter PM Decision-Making

The PM prompt should understand:
- **Build foundations first:** If agents propose features that need infrastructure, create the infrastructure task instead of rejecting the feature
- **Practical scoping:** Large C-tier ideas should be broken into A/B-tier pieces
- **Context awareness:** What tasks are already in progress? What was recently completed? Don't approve duplicates but DO approve natural next steps
- **Rejection is the last resort:** Only reject things that are truly irrelevant, not things that are just premature

## Files to Modify

1. **`app/orchestrator/initiative_decisions.py`**
   - Add `_create_rafe_notification()` method
   - Call it in `decide()` for risk-C items

2. **`app/orchestrator/worker_monitor.py`** (lines ~690-770)
   - Find where the LLM prompt for sweep review is constructed
   - Add rescoping instructions to the prompt
   - Ensure `revised_title`/`revised_description` fields are passed through

3. **`app/orchestrator/sweep_arbitrator.py`**
   - Keep `_create_rafe_inbox_item` as backup
   - Verify the sweep commit actually succeeds (add logging)

4. **`app/orchestrator/context_packets.py`** (if exists)
   - Ensure initiative review context includes active task list

## Testing

- After deploying, trigger a reflection cycle and verify:
  1. New initiatives get reviewed
  2. At least one gets rescoped (revised title/description)
  3. Risk-C decisions create Rafe inbox items
  4. `GET /api/inbox` returns items with 🚨 prefix
