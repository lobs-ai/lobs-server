# 6. Distributed Agent System Testing Architecture

**Date:** 2026-02-22  
**Status:** Accepted  
**Deciders:** System architect

## Context

The lobs-server orchestrator manages a distributed multi-agent system where:
- Multiple concurrent agents execute tasks across different projects
- Agents communicate via async handoffs (task creation, status updates, results)
- The orchestrator coordinates spawning, monitoring, escalation, and failure recovery
- Agent interfaces are implicit contracts (prompt format, result structure, state transitions)
- Failures can cascade (agent crash → task stuck → escalation → reflection → retry)
- Race conditions exist (concurrent task updates, project locks, worker spawning)

**Current testing:**
- Unit tests for individual components (router, scanner, escalation, circuit breaker)
- Mocked external dependencies (OpenClaw Gateway, agent responses)
- Isolated database fixtures

**What's missing:**
- **Integration tests** — Testing actual agent handoffs, multi-step workflows, end-to-end task lifecycle
- **Contract tests** — Verifying agent interface contracts (prompt → agent → result format)
- **Chaos tests** — Injecting failures to validate resilience (agent timeout, DB lock, API error)
- **Concurrency tests** — Validating behavior under parallel task execution
- **Observability validation** — Ensuring failures are detectable and traceable

Without these, we risk:
- Silent contract breakage when agent prompts/schemas change
- Undetected race conditions in concurrent operations
- Brittle failure recovery (escalation/circuit breaker untested under realistic conditions)
- Production incidents that could have been caught in testing

## Decision

We establish a **4-tier testing pyramid** for the distributed agent system:

```
        ┌─────────────────┐
        │  Manual E2E     │  (Exploratory, pre-release validation)
        └─────────────────┘
       ┌───────────────────┐
       │  Chaos Tests      │  (Failure injection, resilience validation)
       └───────────────────┘
      ┌─────────────────────┐
      │  Integration Tests  │  (Multi-component workflows, handoffs)
      └─────────────────────┘
     ┌────────────────────────┐
     │  Contract Tests        │  (Agent interface validation)
     └────────────────────────┘
    ┌──────────────────────────┐
    │  Unit Tests              │  (Component logic, isolated functions)
    └──────────────────────────┘
```

### Tier 1: Unit Tests (Existing)
- **Scope:** Individual functions and classes in isolation
- **Coverage:** Router, scanner, escalation, circuit breaker, model chooser
- **Mocks:** Database, HTTP clients, OpenClaw Gateway
- **Speed:** Fast (<1s per test)
- **Kept:** Current approach continues as-is

### Tier 2: Contract Tests (New)
- **Scope:** Agent input/output interfaces
- **What we validate:**
  - Agent prompt structure matches expected format
  - Agent result parsing works with real agent output samples
  - State transitions follow expected patterns
  - Error responses are parseable
- **Implementation:**
  - **Contract fixtures** — Versioned JSON schemas for agent I/O
  - **Snapshot tests** — Capture real agent responses, verify parsing continues to work
  - **Regression suite** — Historical responses must still parse correctly
- **Files:** `tests/contracts/` directory
- **Speed:** Fast (<5s per suite)

### Tier 3: Integration Tests (New)
- **Scope:** Multi-component workflows with real (but controlled) agent interactions
- **What we test:**
  - Full task lifecycle: queued → spawned → completed → result processed
  - Agent handoffs: programmer → tester → reviewer
  - Escalation chains: task fails → escalation created → reflection triggered
  - Concurrent workflows: multiple agents on different projects
  - Scheduler integration: scheduled events trigger tasks
- **Implementation:**
  - **Test harness** — Fake OpenClaw Gateway that responds with canned agent outputs
  - **Scenario tests** — Scripted multi-step workflows
  - **Async coordination** — Use pytest-asyncio to run concurrent operations
- **Files:** `tests/integration/` directory
- **Speed:** Medium (10-30s per scenario)

### Tier 4: Chaos Tests (New)
- **Scope:** Resilience under failure conditions
- **What we inject:**
  - **Agent failures:** timeout, crash, malformed output, infinite loop
  - **Database failures:** lock timeout, connection drop, constraint violation
  - **Network failures:** Gateway API timeout, 500 error, rate limit
  - **Resource exhaustion:** max workers reached, disk full simulation
- **Implementation:**
  - **Failure injection framework** — Decorators to inject faults into components
  - **Chaos scenarios** — Scripted failure sequences
  - **Observability validation** — Verify error detection, logging, metrics
- **Files:** `tests/chaos/` directory
- **Speed:** Slow (30-120s per scenario)

## Consequences

### Positive

- **Confident refactoring** — Can safely refactor orchestrator internals
- **Early detection** — Catch contract breakage before production
- **Documented behavior** — Tests serve as executable specifications
- **Resilience validation** — Know that failure recovery actually works
- **Faster debugging** — Integration test failures localize issues faster than production logs
- **Incremental adoption** — Can add tests as we fix bugs or add features

### Negative

- **Higher test maintenance** — More tests to update when schemas change
- **Slower CI** — Integration and chaos tests add 5-10 minutes to test suite
- **Test data management** — Need realistic agent output samples
- **Complexity** — More sophisticated test harnesses required
- **Flakiness risk** — Async/concurrent tests can be flaky if not carefully designed

### Neutral

- Tests will live alongside code in `tests/` directory
- Pytest markers will allow running subsets (`pytest -m unit`, `pytest -m integration`)
- Contract fixtures require versioning and update process

## Alternatives Considered

### Option 1: Full E2E with Real Agents

- **Pros:**
  - Most realistic testing
  - Tests actual agent behavior
  - Catches integration issues with OpenClaw

- **Cons:**
  - Extremely slow (minutes per test)
  - Flaky due to LLM nondeterminism
  - Expensive (API costs)
  - Hard to debug failures
  - Can't test edge cases (agents don't reliably produce errors on demand)

- **Why rejected:** Too slow and flaky for CI. Reserved for manual pre-release validation.

### Option 2: Property-Based Testing (Hypothesis)

- **Pros:**
  - Finds edge cases automatically
  - Good for testing invariants (e.g., "task status transitions are always valid")

- **Cons:**
  - Doesn't help with agent contracts or handoffs
  - Hard to write for complex stateful systems
  - Test failures can be hard to reproduce

- **Why rejected:** Useful for specific components (e.g., state machines) but not a general solution. Can be added later for targeted use cases.

### Option 3: External Contract Testing Tool (Pact/Spring Cloud Contract)

- **Pros:**
  - Industry-standard contract testing
  - Provider-consumer workflow
  - Contract sharing between services

- **Cons:**
  - Overkill for our use case (agents aren't microservices)
  - Requires broker/registry infrastructure
  - Python tooling less mature than Java/Node

- **Why rejected:** Too heavyweight. Our agents don't have formal provider/consumer contracts. Custom solution is simpler.

### Option 4: Continuous Chaos (Chaos Monkey)

- **Pros:**
  - Tests resilience in production
  - Finds issues that tests miss

- **Cons:**
  - Requires production-like environment
  - Risk of actual user impact
  - Hard to correlate failures with root cause

- **Why rejected:** Good long-term goal, but we need deterministic chaos tests in CI first. Random production chaos without a baseline is premature.

## Implementation Plan

### Phase 1: Contract Testing (Week 1)
1. Create `tests/contracts/` directory structure
2. Define contract schemas for each agent type
3. Collect sample outputs from real agent runs
4. Implement contract validation helpers
5. Write contract tests for programmer, project-manager, researcher
6. Add CI step for contract tests

**Acceptance:** Contract tests run in CI, fail if agent output parsing breaks

### Phase 2: Integration Testing (Week 2)
1. Create `tests/integration/` directory
2. Build test harness (fake Gateway API with canned responses)
3. Implement scenario tests:
   - Happy path: task queued → completed
   - Handoff: task creates subtask
   - Escalation: task fails → escalation triggered
4. Add concurrent execution scenarios
5. Add CI step for integration tests (can be parallel with unit)

**Acceptance:** Integration tests cover top 5 orchestrator workflows

### Phase 3: Chaos Testing (Week 3)
1. Create `tests/chaos/` directory
2. Build failure injection framework
3. Implement failure scenarios:
   - Agent timeout
   - Database lock
   - Gateway API 500
   - Max workers reached
4. Add observability validation (logs, metrics, DB state)
5. Document chaos test writing guide

**Acceptance:** Chaos tests validate that each circuit breaker and escalation path works

### Phase 4: Documentation & CI Integration (Week 4)
1. Update TESTING.md with testing philosophy and examples
2. Add pytest markers for selective test running
3. Configure CI to run tiers in parallel
4. Create runbook for test failures
5. Add memory file about testing patterns learned

**Acceptance:** Team can run/write tests for all tiers; CI provides clear feedback

## References

- `tests/test_orchestrator_engine.py` — Existing unit tests
- `app/orchestrator/` — Components under test
- ARCHITECTURE.md — System overview
- Initiative #10 — Testing architecture initiative
- [Google Testing Blog: Test Sizes](https://testing.googleblog.com/2010/12/test-sizes.html)
- [Martin Fowler: Practical Test Pyramid](https://martinfowler.com/articles/practical-test-pyramid.html)

## Notes

This decision is **reversible and incremental**:
- Can start with contract tests only, add integration/chaos later
- Can switch to external tools (Pact, Testcontainers) if custom approach proves limiting
- Tests can evolve as system complexity grows

The goal is **confidence in changes**, not 100% coverage. Focus on high-risk paths first.

---

*Based on Michael Nygard's ADR format*
