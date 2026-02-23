# Failure Modes Research

This directory contains research on failure patterns in autonomous multi-agent systems, with specific analysis of lobs-server logs.

## Documents

### [taxonomy.md](taxonomy.md)

Comprehensive taxonomy of 27 failure modes across 6 categories:

1. **Control Flow Failures:** Infinite loops, deadlocks, livelocks
2. **Resource Exhaustion:** Context thrashing, token budget exhaustion, rate limiting
3. **State Management Failures:** Transaction deadlocks, stale reads, lost updates
4. **Communication Failures:** Message loss, duplication, out-of-order delivery
5. **Logic and Reasoning Failures:** Prompt injection, hallucination, tool misuse
6. **Infrastructure Failures:** Gateway unavailability, provider failures, network partitions

Each failure mode includes:
- Description and manifestations
- Detection criteria (with code examples)
- Mitigation strategies
- Examples from lobs-server logs (where observed)

**Critical findings:**
- Transaction deadlocks (75+ occurrences) causing cascading failures
- Circuit breaker cascades (130+ occurrences) from infrastructure issues
- Type mismatches from test mocks leaking into production

### [log-analysis.md](log-analysis.md)

Detailed analysis of 5,775 error log entries from lobs-server (2026-02-19 to 2026-02-22):

**Five major patterns identified:**

1. **Transaction Deadlock Storm** (75 occurrences)
   - Root cause: Concurrent `db.commit()` calls on shared session
   - Impact: Cascading failures across provider health, escalation, worker status
   - Fix: Session-per-request pattern

2. **Circuit Breaker Cascade** (130 occurrences)
   - Root causes: Gateway auth failures, session lock contention, missing API keys
   - Impact: 60-second system pauses
   - Status: Circuit breakers working correctly, need monitoring improvements

3. **Type Mismatch Errors** (25+ occurrences)
   - Root causes: Missing `await`, test mocks leaking, missing `None` checks
   - Impact: Core orchestration crashes
   - Fix: Add type hints, defensive coding

4. **Value Unpacking Errors** (10+ occurrences)
   - Root cause: Inconsistent return types from functions
   - Fix: Use TypedDict/Pydantic for return types

5. **Gateway Communication Timeouts** (28 occurrences)
   - Root cause: Gateway overload or network latency
   - Impact: Auto-assign and task spawning failures
   - Fix: Adaptive timeouts, better monitoring

**Immediate action items:**
1. Fix transaction deadlocks (Priority 1)
2. Investigate gateway auth failures (Priority 1)
3. Fix type errors (Priority 1)

## How to Use This Research

### For Programmers

1. **Before fixing a bug:** Check taxonomy.md for the failure mode category
2. **When designing error handling:** Reference detection criteria and mitigation strategies
3. **When adding new features:** Consider which failure modes could be introduced

### For Architects

1. **System design:** Apply patterns from multi-agent-patterns research
2. **Monitoring:** Implement detection infrastructure from taxonomy.md
3. **Reliability:** Address Priority 1 issues from log-analysis.md

### For Operators

1. **Incident response:** Use log-analysis.md patterns to diagnose live issues
2. **Monitoring dashboards:** Track failure modes from taxonomy.md
3. **Runbooks:** Create runbooks for each major failure pattern

## Related Documentation

- **Orchestration Patterns:** See [../multi-agent-patterns/](../multi-agent-patterns/)
- **Architecture:** See [../../ARCHITECTURE.md](../../ARCHITECTURE.md)
- **Known Issues:** See [../../docs/KNOWN_ISSUES.md](../../docs/KNOWN_ISSUES.md)

## Sources

- **Log data:** 5,775 error entries from `/Users/lobs/lobs-server/logs/error.log` (2026-02-19 to 2026-02-22)
- **Framework research:** CrewAI, LangGraph, AutoGen, MetaGPT documentation
- **Academic:** CAP theorem, distributed systems patterns
- **Empirical:** lobs-server codebase analysis

**Research Date:** 2026-02-22  
**Next Review:** After implementing Priority 1 fixes
