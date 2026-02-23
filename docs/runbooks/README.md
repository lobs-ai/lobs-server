# Operational Runbooks

Practical guides for daily development, debugging, and system operations.

**Last Updated:** 2026-02-22

---

## Quick Navigation

### Testing
- **[How to Add a Test](01-how-to-add-a-test.md)** — Writing unit, contract, integration, and chaos tests

### Debugging
- **[How to Debug a Failing Agent](02-debugging-failing-agents.md)** — Using observability to debug agent failures
- **[How to Read Observability Metrics](03-reading-observability-metrics.md)** — Understanding logs, traces, and metrics
- **[Multi-Agent Operations](04-multi-agent-operations.md)** — Handoffs, memory, permissions, models, reflection, worker recovery

---

## When to Use These Runbooks

| Situation | Runbook |
|-----------|---------|
| "I need to add test coverage for my feature" | [01-how-to-add-a-test](01-how-to-add-a-test.md) |
| "An agent task failed in production" | [02-debugging-failing-agents](02-debugging-failing-agents.md) |
| "What do these metric values mean?" | [03-reading-observability-metrics](03-reading-observability-metrics.md) |
| "Handoff didn't create follow-up task" | [04-multi-agent-operations § Handoff Failures](04-multi-agent-operations.md#handoff-failures) |
| "Memory search returns wrong results" | [04-multi-agent-operations § Memory Issues](04-multi-agent-operations.md#memory-issues) |
| "Agent got permission denied error" | [04-multi-agent-operations § Permission Errors](04-multi-agent-operations.md#permission-errors) |
| "Wrong model tier selected (too expensive)" | [04-multi-agent-operations § Model Selection](04-multi-agent-operations.md#model-selection-problems) |
| "Worker stuck or crashed (OOM)" | [04-multi-agent-operations § Worker Recovery](04-multi-agent-operations.md#worker-stuckoom-recovery) |
| "Circuit breaker is open, tasks won't spawn" | [04-multi-agent-operations § Circuit Breaker](04-multi-agent-operations.md#circuit-breaker-management) |
| "Task keeps failing and retrying in loop" | [04-multi-agent-operations § Escalation Loop](04-multi-agent-operations.md#escalation-loop-prevention) |

---

## Philosophy

These runbooks are:
- **Practical** — Step-by-step procedures, not theory
- **Example-driven** — Show real commands, real output
- **Actionable** — Get from problem → solution quickly
- **Living documents** — Update as system evolves

Not architecture docs (see `docs/decisions/`) or design docs (see `docs/architecture/`). This is operational knowledge.

---

## Contributing

Found a gap or improvement? Update the runbook and note the date.

**Template structure:**
```markdown
# Runbook Title

**Purpose:** One-line description

---

## Quick Reference
- Common command or pattern
- Common command or pattern

## When to Use This
Describe the situation

## Step-by-Step
1. Do this
2. Then this
3. Finally this

## Troubleshooting
- If X happens → do Y
- If Z happens → see [other runbook]

## Examples
Real examples with real output
```

---

## Related Documentation

- **[docs/decisions/](../decisions/)** — Architecture decision records
- **[docs/architecture/](../architecture/)** — System design documents
- **[docs/guides/](../guides/)** — Implementation guides
- **[TESTING.md](../../TESTING.md)** — High-level testing strategy (if exists)
- **[CONTRIBUTING.md](../../CONTRIBUTING.md)** — Development workflow
