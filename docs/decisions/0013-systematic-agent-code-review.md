# 13. Systematic Agent Code Review

**Date:** 2026-02-22  
**Status:** Proposed  
**Deciders:** Architect, Project Manager

## Context

The reviewer agent is currently invoked ad-hoc — someone manually decides "this needs review." This means high-risk changes sometimes slip through without review, while low-risk changes sometimes get unnecessary review. We need systematic triggers so the quality gate is consistent and predictable.

## Decision

Implement automatic review triggers in the orchestrator. When an agent completes a task, the orchestrator analyzes the resulting changes and creates a reviewer task if any trigger matches:

1. **Large refactors** — >500 lines changed
2. **New API endpoints** — Any new route added
3. **State management** — Changes to models, migrations, or DB schema
4. **Security-sensitive code** — Auth, tokens, permissions, crypto
5. **Large test additions** — >20 new tests

Each trigger category has a dedicated review checklist. The original task remains in `review` status until the reviewer approves.

See [Agent Code Review Workflow](../guides/agent-code-review-workflow.md) for full details, checklists, and handoff templates.

## Consequences

### Positive

- Consistent quality gates — no high-risk changes skip review
- Reviewer agent has clear scope and checklists per category
- Reduces human oversight burden for routine quality checks
- Creates audit trail of what was reviewed and why

### Negative

- Adds latency to task completion for triggered changes
- Requires orchestrator changes to detect triggers (diff analysis)
- May over-trigger initially; thresholds need tuning

### Neutral

- Non-triggered changes proceed as before (no review overhead)
- Thresholds are configurable and should be adjusted based on experience

## Alternatives Considered

### Option 1: Review Everything
- Pros: Maximum coverage
- Cons: Reviewer becomes bottleneck; most changes are low-risk
- Why rejected: Doesn't scale with agent throughput

### Option 2: Human-Only Review
- Pros: Highest quality judgment
- Cons: Human becomes bottleneck; defeats purpose of agent automation
- Why rejected: Agents can handle checklist-based review effectively

### Option 3: Risk Scoring Model
- Pros: More nuanced than hard thresholds
- Cons: Complex to build; hard to explain why something was/wasn't reviewed
- Why rejected: Premature optimization; start with simple triggers, evolve later

## References

- [Agent Code Review Workflow](../guides/agent-code-review-workflow.md)
- [ADR-0008: Agent Specialization Model](0008-agent-specialization-model.md)
- [ADR-0011: Handoff Protocol](0011-handoff-protocol.md)

---

*Based on Michael Nygard's ADR format*
