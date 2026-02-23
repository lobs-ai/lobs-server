# 14. Risk-Based Initiative Approval System

**Date:** 2026-02-22  
**Status:** Accepted  
**Deciders:** System architect, product owner

## Context

AI agents in lobs-server autonomously propose improvements, features, and refactors through a **strategic reflection cycle** (every 6 hours). Without governance, this creates several problems:

- **Proposal overload** — Agents could flood the inbox with suggestions
- **No quality filter** — Bad ideas mix with good ones
- **No prioritization** — All proposals appear equal
- **No learning loop** — Agents don't know what gets approved/rejected
- **Human bottleneck** — Reviewing 10-20 proposals daily becomes tedious

We needed a system to:
1. Categorize proposals by risk/scope
2. Enable efficient batch review
3. Feed decisions back to agents so they learn
4. Track decision rationale for future reference
5. Prevent duplicate or vague proposals

## Decision

We implement a **risk-based initiative approval system** with four-tier classification and structured feedback loops.

### Risk Tiers

Every agent initiative is assigned a risk tier based on scope, complexity, and potential impact:

- **A (Low Risk)** — Small, safe changes
  - Examples: Documentation updates, adding tests, minor refactors
  - Auto-approval candidate for trusted agents
  
- **B (Medium Risk)** — Moderate scope
  - Examples: New feature (scoped), automation tool, schema changes
  - Requires human review, typically approved quickly
  
- **C (High Risk)** — Significant effort or system impact
  - Examples: Architecture changes, cross-project work, data model changes
  - Deep review required, may need scoping discussion
  
- **D (Critical Risk)** — Major undertakings or risky operations
  - Examples: Data migrations, new projects, breaking changes
  - Always requires careful review and planning

### Initiative Categories

Initiatives are also categorized by type of work:

**Low-Risk Maintenance:**
- `docs_sync` — Update documentation to match code
- `test_hygiene` — Add missing tests, fix flaky tests
- `stale_triage` — Clean up old tasks, backlog items
- `light_research` — Investigate a question or technology
- `backlog_reprioritization` — Re-evaluate task priorities

**Medium-Risk Improvements:**
- `automation_proposal` — Build tools or scripts to automate work
- `moderate_refactor` — Restructure code without changing behavior
- `feature_proposal` — New user-facing capability (scoped small)
- `architecture_change` — Small structural improvements

**High-Risk Changes:**
- `destructive_operation` — Deletes or migrates data
- `cross_project_migration` — Changes that span multiple repos/systems
- `agent_recruitment` — Propose new agent types or capabilities
- `new_project` — Standalone new system/tool

### Workflow

```
Agent Reflection Cycle (every 6 hours)
           ↓
   Proposes initiatives
           ↓
   Stored in agent_initiatives table
           ↓
   Status: proposed → pending review
           ↓
Human reviews via Intelligence UI
           ↓
     ┌─────┴──────┬──────────┐
     ▼            ▼          ▼
  Approve      Defer      Reject
     ↓            ↓          ↓
Create task   Postpone   Record reason
     ↓
Decision recorded in initiative_decision_records
     ↓
Feedback sent to agent in next reflection prompt
```

### Batch Review Support

To handle multiple proposals efficiently:
- **UI batch selection** — Select multiple initiatives, apply same decision
- **Batch API endpoint** — `POST /api/orchestrator/intelligence/initiatives/batch-decide`
- **Mixed decisions** — Can approve some, reject others in one call
- **Deduplication** — System highlights overlapping/duplicate proposals

### Feedback Loop

Every decision (approve/defer/reject) is fed back to the proposing agent:

**In next reflection prompt:**
```markdown
## Your Recent Initiative Decision History (Last 7 Days)

### ✅ Approved (3)
- **Add retry logic to worker.py** [automation_proposal, Tier B]
  Why approved: Solves real Gateway timeout issue

### ❌ Rejected (2)  
- **Improve monitoring** [architecture_change, Tier C]
  Why rejected: Too vague, no specific metrics named

### 🎯 Patterns to Avoid
- Vague, speculative proposals without concrete steps
- Work already covered by existing tasks
```

This teaches agents what types of proposals get approved/rejected.

## Consequences

### Positive

- **Efficient triage** — Risk tiers let you prioritize review (A/B first, C/D when you have time)
- **Better proposals** — Agents learn from feedback and improve proposal quality over time
- **Batch processing** — Review 10 proposals in 5 minutes instead of one-by-one
- **Decision auditability** — Every approve/reject recorded with reasoning
- **Clear expectations** — Agents know categories and risk levels expected
- **Reduced noise** — Agents stop re-proposing rejected ideas
- **Transparency** — Color-coded UI makes risk obvious at a glance

### Negative

- **Human still required** — Can't fully automate approval (by design)
- **Categorization overhead** — Agents must assign category and risk tier
- **Risk tier calibration** — What's "high risk" evolves over time
- **Feedback lag** — Agents only see feedback after next reflection cycle (6 hours)
- **UI complexity** — Intelligence view is more complex than simple inbox

### Neutral

- Initiatives can be deferred indefinitely (not auto-archived)
- Decision history is agent-specific (programmer sees their own approval patterns)

## Alternatives Considered

### Option 1: Flat Inbox (No Risk Tiers)

- **Pros:**
  - Simple
  - No categorization needed
  - Agents just write proposals

- **Cons:**
  - Can't prioritize review
  - High-effort and low-effort proposals look the same
  - No learning signal for agents
  - Review becomes overwhelming at scale

- **Why rejected:** We tried this first. After 50+ proposals accumulated, reviewing them became tedious and we had no way to triage by importance.

### Option 2: Auto-Approve Low-Risk Changes

- **Pros:**
  - Reduces human review burden
  - Fast execution for safe changes
  - Trusts agents for simple work

- **Cons:**
  - Risky — even "safe" changes can break things
  - Hard to define "safe" precisely
  - Loses human oversight
  - Agents might game the system (mark risky things as safe)

- **Why rejected:** Premature automation. We want to see proposal quality improve first. May revisit for specific agents/categories with proven track record.

### Option 3: Voting/Consensus System

- **Pros:**
  - Multiple agents review each other's proposals
  - Democratic decision-making
  - Distributes review workload

- **Cons:**
  - Agents can't objectively evaluate proposals outside their domain
  - Creates agent politics ("I'll vote for yours if you vote for mine")
  - Still needs human tie-breaker
  - Complex coordination logic

- **Why rejected:** Agents aren't peers — they have different specializations. Architect shouldn't vote on documentation proposals, writer shouldn't vote on refactoring.

### Option 4: AI-Assisted Triage

- **Pros:**
  - LLM reviews proposals and flags issues
  - Suggests approval/rejection with reasoning
  - Reduces human effort

- **Cons:**
  - LLM judging LLM work is circular
  - Loses human judgment on product priorities
  - Adds LLM cost for every proposal
  - Hard to tune (what criteria should AI use?)

- **Why rejected:** Could be useful as a pre-filter ("flag vague proposals"), but final decision should be human. May add as enhancement later.

## Implementation Details

**Database schema:**
```sql
-- Initiatives table
agent_initiatives:
  - category (string, indexed)
  - risk_tier (string, A/B/C/D, indexed)
  - status (proposed/approved/rejected/deferred, indexed)
  - proposed_by_agent (string, indexed)

-- Decision audit trail
initiative_decision_records:
  - initiative_id (FK)
  - decision (approve/defer/reject)
  - decided_by (default: "lobs")
  - decision_summary (text, optional reasoning)
  - overlap_with_ids (JSON, list of duplicate initiative IDs)
```

**Risk tier assignment:**
Agents assign risk tier based on prompt guidance:
- Estimate scope (hours/days)
- Consider reversibility (easy to undo = lower risk)
- Assess blast radius (affects one file vs. entire system)
- Default to higher tier if uncertain

**UI indicators:**
- A: Green
- B: Yellow
- C: Orange
- D: Red

## Future Enhancements

Possible improvements (not yet implemented):

1. **Auto-approval for Tier A from trusted agents** — After agent demonstrates good judgment over 50+ proposals
2. **Proposal templates** — Structured forms for common categories
3. **Overlap detection** — Automatically flag duplicate/similar proposals
4. **Effort estimation** — Agents estimate task size, tracked against actual
5. **Approval analytics** — Show trends (which agents get most approvals, which categories most common)

## References

- `app/models.py` — `AgentInitiative`, `InitiativeDecisionRecord` schemas
- `app/routers/orchestrator.py` — Batch decision endpoint
- `docs/intelligence-workflow.md` — User guide for Intelligence view
- ADR-0003 — Project Manager delegation (related governance)

## Notes

This system embodies a core philosophy: **AI proposes, human decides.**

Agents are creative and tireless, but they lack product judgment and user empathy. The risk-based system lets them contribute ideas while keeping humans in control of what actually ships.

Over time, as agents learn what gets approved, proposal quality should improve and review burden should decrease — but we never eliminate human oversight entirely.

---

*Based on Michael Nygard's ADR format*
