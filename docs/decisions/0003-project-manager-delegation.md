# 3. Project Manager Agent for Task Routing

**Date:** 2026-02-22  
**Status:** Accepted  
**Deciders:** System architect, product owner

## Context

The task orchestrator needed a way to decide which agent should handle each task. The system had to:
- Route tasks to appropriate agents (programmer, researcher, writer, specialist)
- Consider project context, task type, and required capabilities
- Handle edge cases and ambiguous tasks gracefully
- Evolve routing logic without code changes
- Provide explanations for routing decisions

Initial approaches used hardcoded rules (e.g., "if task contains 'implement' → programmer"), but this became brittle and hard to maintain.

## Decision

We delegate all task routing decisions to a **project-manager agent** — a specialized AI agent that acts as intelligent router and coordinator.

**How it works:**
1. Orchestrator scanner finds eligible tasks
2. Orchestrator router calls project-manager agent via OpenClaw
3. Project-manager receives task context (title, description, project, tags)
4. Project-manager decides: which agent, what capability, task priority
5. Project-manager returns routing decision with reasoning
6. Orchestrator worker spawns the chosen agent

The project-manager agent:
- Has access to agent registry (capabilities, constraints, specializations)
- Sees project context and history
- Can request human approval for ambiguous cases
- Provides reasoning for its routing decisions
- Can adapt routing logic based on past outcomes

## Consequences

### Positive

- **Flexible routing logic** — No hardcoded rules, adapts to new task types
- **Context-aware decisions** — Considers full project context, not just keywords
- **Handles ambiguity** — Can escalate unclear tasks or split them
- **Explainable** — Every routing decision includes reasoning
- **Easy to improve** — Refine agent prompt, no code changes
- **Multi-factor routing** — Considers capabilities, workload, past performance
- **Graceful degradation** — Can fall back to safe defaults if uncertain

### Negative

- **LLM latency** — Routing takes 1-3 seconds vs. instant rule matching
- **LLM cost** — Every routed task incurs API cost (~$0.001-0.01)
- **Non-deterministic** — Same task might route differently (usually not a problem)
- **Debugging complexity** — Harder to predict routing behavior
- **Dependency on OpenClaw** — If OpenClaw gateway is down, routing fails

### Neutral

- Routing decisions are logged to database for audit trail
- Project-manager can be interrupted/overridden by explicit task assignment

## Alternatives Considered

### Option 1: Rule-Based Routing

- **Pros:**
  - Instant, deterministic
  - No LLM cost
  - Easy to debug
  - No external dependencies

- **Cons:**
  - Brittle (breaks on edge cases)
  - Hard to maintain (rules proliferate)
  - No context awareness
  - Requires code changes for new patterns
  - Doesn't handle ambiguity well

- **Why rejected:** We tried this first. It worked for simple cases but became unmaintainable as task types grew. Every edge case required a new rule.

### Option 2: Capability Registry + Keyword Matching

- **Pros:**
  - More structured than pure rules
  - Declarative agent capabilities
  - Still fast and deterministic

- **Cons:**
  - Still keyword-based (synonyms, phrasing variations break it)
  - Doesn't understand task intent
  - Can't reason about multi-step workflows
  - No natural language understanding

- **Why rejected:** Better than pure rules, but still too rigid. Couldn't handle "Research SQLite performance tuning for task queue" — is that research or programming?

### Option 3: ML Classifier (Train on Past Tasks)

- **Pros:**
  - Fast inference
  - Learns from data
  - Deterministic after training

- **Cons:**
  - Requires labeled training data (don't have enough)
  - Can't explain decisions
  - Requires ML infrastructure (training, deployment, monitoring)
  - Doesn't generalize to new task types
  - Overkill for current task volume

- **Why rejected:** Would need 1000+ labeled examples for decent accuracy. Current task volume is ~50/week, mostly unique.

### Option 4: Hybrid (Rules + LLM Fallback)

- **Pros:**
  - Fast path for common cases
  - LLM for edge cases
  - Deterministic where possible

- **Cons:**
  - More complex (two systems to maintain)
  - Rules still become brittle over time
  - Hard to know when to use which path

- **Why rejected:** Premature optimization. LLM routing is fast enough (1-3s). We can add rule-based fast path later if needed, but we haven't needed it yet.

## Performance Characteristics

**Routing latency (measured):**
- Rule-based: ~1ms
- Project-manager agent: ~1500ms (median), ~3000ms (p95)

**For current workload (10-50 tasks/day), 1-3s routing latency is acceptable.** Tasks are typically long-running (minutes to hours), so routing overhead is <1% of total execution time.

**Cost:**
- ~$0.002 per routing decision (using small/medium tier models)
- At 50 tasks/day × 30 days = 1500 tasks/month = $3/month

This is well within acceptable cost for the intelligence gained.

## Escalation and Fallbacks

If project-manager agent fails or is unavailable:
1. Check task for explicit `assigned_agent` field
2. Fall back to capability registry keyword matching
3. If still unclear, assign to "researcher" (safest default)
4. Log routing failure for manual review

## Future Enhancements

Possible improvements (not yet implemented):
- **Learn from outcomes** — Track which routing decisions led to successful task completion
- **Workload balancing** — Route away from busy agents
- **Skill matching** — Remember which agents handled similar tasks well
- **Multi-agent tasks** — Split complex tasks across multiple agents

These can be added by updating the project-manager prompt without changing orchestrator code.

## References

- `app/orchestrator/router.py` — Calls project-manager agent for routing
- `app/orchestrator/registry.py` — Agent capability definitions
- `app/orchestrator/prompter.py` — Builds routing prompt for project-manager
- [Initiative #52] — Orchestrator improvements
- ADR-0001 — Embedded orchestrator architecture

## Notes

This decision reflects a **bet on LLM capabilities over traditional programming**.

Traditional approach: Write code to encode all possible routing logic.  
Our approach: Describe the problem to an AI and let it reason.

As LLMs get faster and cheaper, this tradeoff becomes even more favorable.

If routing latency becomes an issue (e.g., thousands of tasks/day), we can:
- Cache routing decisions by task signature
- Add rule-based fast path for common patterns
- Use streaming LLM calls for lower latency
- Pre-route tasks in batch

None of these optimizations have been needed yet.

---

*Based on Michael Nygard's ADR format*
