# lobs-server Documentation

Index of design documents, implementation notes, and research findings.

## Getting Started

### Core Documentation
- **[ARCHITECTURE.md](../ARCHITECTURE.md)** — System architecture, data flow, components (comprehensive overview)
- **[AGENTS.md](../AGENTS.md)** — API reference, development guide, agent integration
- **[README.md](../README.md)** — Project overview, quick start, setup

### Development Guides
- **[TESTING.md](TESTING.md)** — Complete testing guide (setup, running tests, adding new tests)
- **[KNOWN_ISSUES.md](KNOWN_ISSUES.md)** — Known issues, limitations, and technical debt

## Design Documents

### Agent Coordination & Orchestration
- **[project-manager-agent.md](project-manager-agent.md)** — Project manager agent design (task routing, delegation, approval workflows)
- **[tiered-approval-system.md](tiered-approval-system.md)** — Three-tier approval workflow (auto-approve, human review, escalate)

### Topics & Knowledge System
- **[TOPICS_IMPLEMENTATION.md](TOPICS_IMPLEMENTATION.md)** — Topics feature implementation (auto-creation, researcher integration)
- **[TOPIC_MIGRATION_VERIFICATION.md](TOPIC_MIGRATION_VERIFICATION.md)** — Topics migration validation and testing
- **[researcher-topic-creation-design.md](researcher-topic-creation-design.md)** — Autonomous topic creation design for researcher agent
- **[document-lifecycle-design.md](document-lifecycle-design.md)** — Document lifecycle and state management
- **[work-dump-analysis-design.md](work-dump-analysis-design.md)** — Work dump analysis and processing design

### System Architecture
- **[cron-timeout-investigation.md](cron-timeout-investigation.md)** — Cron job timeout debugging and fixes

### Research
- **[../research-findings.md](../research-findings.md)** — WebSocket reconnection handling research (best practices, exponential backoff, implementation examples)

## Quick Reference

| Topic | Document |
|-------|----------|
| **System architecture** | [ARCHITECTURE.md](../ARCHITECTURE.md) |
| **API reference** | [AGENTS.md](../AGENTS.md) |
| **Testing** | [TESTING.md](TESTING.md) |
| **Known issues & limitations** | [KNOWN_ISSUES.md](KNOWN_ISSUES.md) |
| Project manager workflow | [project-manager-agent.md](project-manager-agent.md) |
| Approval system overview | [tiered-approval-system.md](tiered-approval-system.md) |
| Creating topics from agents | [researcher-topic-creation-design.md](researcher-topic-creation-design.md) |
| Topics system overview | [TOPICS_IMPLEMENTATION.md](TOPICS_IMPLEMENTATION.md) |
| Document management | [document-lifecycle-design.md](document-lifecycle-design.md) |
| WebSocket best practices | [../research-findings.md](../research-findings.md) |
| Debugging cron issues | [cron-timeout-investigation.md](cron-timeout-investigation.md) |

## Document Types

- **Development Guides** — Testing, known issues, troubleshooting
- **Design** — Feature designs and architectural decisions
- **Implementation** — Implementation notes and validation
- **Research** — Research findings and best practices
- **Fixes** — Bug investigations and solutions (in `fixes/`)

## Recent Additions

- 2026-02-14: **Architecture documentation (ARCHITECTURE.md)** — Comprehensive system architecture overview
- 2026-02-14: Testing guide (TESTING.md)
- 2026-02-14: Known issues documentation (KNOWN_ISSUES.md)
- 2026-02-14: Project manager agent documentation
- 2026-02-14: Tiered approval system documentation
- 2026-02-14: WebSocket reconnection research
- 2026-02-13: Topics implementation and migration
- 2026-02-13: Document lifecycle design
- 2026-02-13: Work dump analysis design
- 2026-02-13: Cron timeout investigation

## Contributing

When adding documentation:
1. **Use descriptive filenames** — `feature-name-design.md` or `issue-investigation.md`
2. **Update this index** — Add your doc to the appropriate section
3. **Include status & dates** — Help readers know currency
4. **Cross-reference** — Link to code files, tests, related docs
5. **Keep AGENTS.md updated** — API changes documented there too
