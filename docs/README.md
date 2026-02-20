# lobs-server Documentation

Index of design documents, implementation notes, and research findings.

## Getting Started

### Core Documentation
- **[QUICKSTART.md](../QUICKSTART.md)** — Get started in 5 minutes (Added 2026-02-14)
- **[ARCHITECTURE.md](../ARCHITECTURE.md)** — System architecture, data flow, components (Updated 2026-02-14)
- **[AGENTS.md](../AGENTS.md)** — API reference, development guide, agent integration
- **[CHANGELOG.md](../CHANGELOG.md)** — API changes and version history (Added 2026-02-14)
- **[README.md](../README.md)** — Project overview, quick start, setup
- **[.env.example](../.env.example)** — Environment configuration reference (Added 2026-02-14)

### Development Guides
- **[coding-standards.md](coding-standards.md)** — Code quality, testing, and review standards (Added 2026-02-20)
- **[git-workflow.md](git-workflow.md)** — Branch strategy, commit conventions, PR process (Added 2026-02-20)
- **[BEST_PRACTICES.md](BEST_PRACTICES.md)** — N+1 prevention, SQLite optimization, Pydantic v2 patterns (Added 2026-02-14)
- **[TESTING.md](TESTING.md)** — Complete testing guide (setup, running tests, adding new tests)
- **[KNOWN_ISSUES.md](KNOWN_ISSUES.md)** — Known issues, limitations, and technical debt
- **[task-improvements-roadmap-implementation.md](task-improvements-roadmap-implementation.md)** — Phase 0.5→4 implementation (Added 2026-02-18)

## Design Documents

### Agent Coordination & Orchestration
- **[agent-lifecycle-architecture.md](agent-lifecycle-architecture.md)** — Canonical lifecycle architecture (Added 2026-02-18)
- **[agent-operations-playbook.md](agent-operations-playbook.md)** — Operator runbook for agent management (Added 2026-02-18)
- **[agent-api-contracts.md](agent-api-contracts.md)** — API and model contracts for agent lifecycle (Added 2026-02-18)
- **[model-routing.md](model-routing.md)** — Model tier system, provider health tracking, fallback chains (Added 2026-02-20)
- **[PROVIDER_HEALTH.md](PROVIDER_HEALTH.md)** — Provider health implementation details (Added 2026-02-20)
- **[orchestrator-model-routing.md](orchestrator-model-routing.md)** — First-pass model router design (Added 2026-02-14)
- **[project-manager-agent.md](project-manager-agent.md)** — Project manager agent design
- **[tiered-approval-system.md](tiered-approval-system.md)** — Three-tier approval workflow

### Topics & Knowledge System
- **[TOPICS_IMPLEMENTATION.md](TOPICS_IMPLEMENTATION.md)** — Topics feature implementation (auto-creation, researcher integration)
- **[TOPIC_MIGRATION_VERIFICATION.md](TOPIC_MIGRATION_VERIFICATION.md)** — Topics migration validation and testing
- **[researcher-topic-creation-design.md](researcher-topic-creation-design.md)** — Autonomous topic creation design for researcher agent
- **[document-lifecycle-design.md](document-lifecycle-design.md)** — Document lifecycle and state management
- **[work-dump-analysis-design.md](work-dump-analysis-design.md)** — Work dump analysis and processing design

### System Architecture
- **[cron-timeout-investigation.md](cron-timeout-investigation.md)** — Cron job timeout debugging and fixes

### Work Tracking & Analysis
- **[work-dump-analysis-design.md](work-dump-analysis-design.md)** — Work dump analysis and proactive task creation design

### Research
- **[../research-findings.md](../research-findings.md)** — WebSocket reconnection handling research (best practices, exponential backoff, implementation examples)

## Quick Reference

| Topic | Document |
|-------|----------|
| **Quick start** | [QUICKSTART.md](../QUICKSTART.md) |
| **System architecture** | [ARCHITECTURE.md](../ARCHITECTURE.md) |
| **API reference** | [AGENTS.md](../AGENTS.md) |
| **API changelog** | [CHANGELOG.md](../CHANGELOG.md) |
| **Environment config** | [.env.example](../.env.example) |
| **Coding standards** | [coding-standards.md](coding-standards.md) |
| **Git workflow** | [git-workflow.md](git-workflow.md) |
| **Best practices** | [BEST_PRACTICES.md](BEST_PRACTICES.md) |
| **Model routing & provider health** | [model-routing.md](model-routing.md) |
| **Testing** | [TESTING.md](TESTING.md) |
| **Known issues & limitations** | [KNOWN_ISSUES.md](KNOWN_ISSUES.md) |
| Agent lifecycle architecture | [agent-lifecycle-architecture.md](agent-lifecycle-architecture.md) |
| Agent operations playbook | [agent-operations-playbook.md](agent-operations-playbook.md) |
| Agent API contracts | [agent-api-contracts.md](agent-api-contracts.md) |
| Provider health implementation | [PROVIDER_HEALTH.md](PROVIDER_HEALTH.md) |
| Project manager workflow | [project-manager-agent.md](project-manager-agent.md) |
| Approval system overview | [tiered-approval-system.md](tiered-approval-system.md) |
| Creating topics from agents | [researcher-topic-creation-design.md](researcher-topic-creation-design.md) |
| Topics system overview | [TOPICS_IMPLEMENTATION.md](TOPICS_IMPLEMENTATION.md) |
| Document management | [document-lifecycle-design.md](document-lifecycle-design.md) |
| Work dump analysis | [work-dump-analysis-design.md](work-dump-analysis-design.md) |
| WebSocket best practices | [../research-findings.md](../research-findings.md) |
| Debugging cron issues | [cron-timeout-investigation.md](cron-timeout-investigation.md) |

## Document Types

- **Development Guides** — Testing, known issues, troubleshooting
- **Design** — Feature designs and architectural decisions
- **Implementation** — Implementation notes and validation
- **Research** — Research findings and best practices
- **Fixes** — Bug investigations and solutions (in `fixes/`)

## Recent Additions & Updates

### 2026-02-20
- ✅ **coding-standards.md** — Code quality, testing, and review standards
- ✅ **git-workflow.md** — Branch strategy, commit conventions, PR process
- ✅ **model-routing.md** — Model tier system, provider health tracking, fallback chains
- ✅ **PROVIDER_HEALTH.md** — Provider health implementation details (moved from root to docs/)
- ✅ Documentation cleanup — Removed "NEW" labels, standardized to "Added <date>" format

### 2026-02-18
- ✅ **Agent lifecycle docs set** — Canonical architecture, operations playbook, API contracts
- ✅ **Task improvements roadmap** — Phase 0.5→4 implementation documentation

### 2026-02-14
- ✅ **BEST_PRACTICES.md** — N+1 prevention, SQLite optimization, Pydantic v2 patterns
- ✅ **QUICKSTART.md** — 5-minute getting started guide
- ✅ **CHANGELOG.md** — API version history
- ✅ **ARCHITECTURE.md** — Comprehensive system architecture overview
- ✅ Worker activity endpoint — New `/api/worker/activity` API for activity feeds
- ✅ Task state documentation — work_state vs. review_state vs. status
- ✅ inbox-responder removal — Project-manager now handles all inbox processing

### 2026-02-13
- ✅ Topics implementation and migration
- ✅ Document lifecycle design
- ✅ Work dump analysis design
- ✅ Cron timeout investigation

## Contributing

When adding documentation:
1. **Use descriptive filenames** — `feature-name-design.md` or `issue-investigation.md`
2. **Update this index** — Add your doc to the appropriate section
3. **Include status & dates** — Help readers know currency
4. **Cross-reference** — Link to code files, tests, related docs
5. **Keep AGENTS.md updated** — API changes documented there too
