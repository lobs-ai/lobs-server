# Architecture Decision Records (ADRs)

This directory contains Architecture Decision Records for lobs-server, documenting key architectural choices, their context, and tradeoffs.

## Format

We use [Michael Nygard's ADR format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions):

- **Context** — What problem are we solving?
- **Decision** — What did we decide to do?
- **Consequences** — What are the positive, negative, and neutral outcomes?
- **Alternatives** — What else did we consider and why did we reject it?

See [0000-template.md](0000-template.md) for the full template.

## Index

| # | Title | Status | Date |
|---|-------|--------|------|
| [0000](0000-template.md) | Template | - | - |
| [0001](0001-embedded-orchestrator.md) | Embedded Task Orchestrator | Accepted | 2026-02-22 |
| [0002](0002-sqlite-for-primary-database.md) | SQLite for Primary Database | Accepted | 2026-02-22 |
| [0003](0003-project-manager-delegation.md) | Project Manager Agent for Task Routing | Accepted | 2026-02-22 |
| [0004](0004-five-tier-model-routing.md) | Five-Tier Model Routing with Fallback Chains | Accepted | 2026-02-22 |
| [0005](0005-observability-architecture.md) | Observability Architecture | Accepted | 2026-02-22 |
| [0006](0006-distributed-testing-architecture.md) | Distributed Testing Architecture | Accepted | 2026-02-22 |
| [0007](0007-state-management-and-consistency.md) | State Management and Consistency Model | Accepted | 2026-02-22 |
| [0008](0008-agent-specialization-model.md) | Agent Types and Specialization Model | Accepted | 2026-02-22 |
| [0009](0009-workspace-isolation-strategy.md) | Workspace Isolation Strategy | Accepted | 2026-02-22 |
| [0010](0010-agent-memory-architecture.md) | Agent Memory Architecture (MEMORY.md vs memory/) | Accepted | 2026-02-22 |
| [0011](0011-handoff-protocol.md) | Handoff Protocol and Task Assignment | Accepted | 2026-02-22 |
| [0012](0012-tool-access-policies.md) | Tool Access Policies | Accepted | 2026-02-22 |
| [0013](0013-systematic-agent-code-review.md) | Systematic Agent Code Review | Accepted | 2026-02-22 |
| [0014](0014-risk-based-initiative-approval.md) | Risk-Based Initiative Approval System | Accepted | 2026-02-22 |
| [0015](0015-provider-health-tracking.md) | Provider Health Tracking and Cooldown Management | Accepted | 2026-02-22 |

## Status Definitions

- **Proposed** — Under consideration, not yet implemented
- **Accepted** — Decision made and implemented
- **Deprecated** — No longer relevant, but kept for historical context
- **Superseded** — Replaced by a newer ADR (link to replacement)

## When to Write an ADR

Create an ADR when you make a decision that:
- Affects system architecture or infrastructure
- Has significant tradeoffs or alternatives
- Would be hard to reverse
- Needs to be understood by future developers/agents
- Resolves a long-standing debate

**Examples of ADR-worthy decisions:**
- Choice of database (SQLite vs Postgres)
- Task orchestration architecture (embedded vs external)
- Authentication strategy (JWT vs sessions)
- Deployment model (monolith vs microservices)

**Not ADR-worthy:**
- Library choices (unless highly controversial)
- Code style decisions (use linter config)
- UI layout choices (use design docs)
- Bug fixes (use git commits)

## Creating a New ADR

1. Copy `0000-template.md` to `NNNN-short-title.md` (next number in sequence)
2. Fill in all sections (especially Context, Decision, Consequences, Alternatives)
3. Update this README index
4. Commit with message: `docs: ADR-NNNN <title>`

## Updating Existing ADRs

ADRs are **immutable** once accepted. If a decision changes:
1. Create a new ADR superseding the old one
2. Update old ADR's status to "Superseded by ADR-NNNN"
3. Update index

This preserves decision history and reasoning.

## References

- [Architecture Decision Records by Michael Nygard](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
- [ADR GitHub Organization](https://adr.github.io/)
- [When to Write an ADR](https://github.com/joelparkerhenderson/architecture-decision-record#when-to-write-an-adr)

---

*Documenting decisions preserves institutional knowledge and prevents revisiting solved problems.*
