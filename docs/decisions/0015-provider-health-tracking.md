# 15. Provider Health Tracking and Cooldown Management

**Date:** 2026-02-22  
**Status:** Accepted  
**Deciders:** System architect, product owner

## Context

lobs-server orchestrator spawns AI agent workers that call multiple LLM providers (Anthropic, OpenAI, Ollama, etc.). These providers can fail in various ways:

- **Rate limits** — 429 Too Many Requests, need to back off
- **Quota exceeded** — Account limit hit, unavailable for hours/days
- **Authentication errors** — Invalid API key, needs manual fix
- **Timeouts** — Provider slow or unresponsive
- **Server errors** — 500/502/503, temporary infrastructure issues

**Problems without health tracking:**

1. **Wasted retries** — Agent hits rate limit, retries immediately, fails again
2. **Cascade failures** — All workers hit same unhealthy provider, all fail
3. **No learning** — System doesn't remember which providers are problematic
4. **Poor user experience** — Tasks fail repeatedly instead of routing around bad providers
5. **Cost waste** — Paying for failed API calls that were doomed to fail

We needed a system to:
- Track provider/model health based on recent errors
- Apply smart cooldown periods per error type
- Route traffic away from unhealthy providers
- Recover automatically when providers come back online
- Provide observability into provider reliability

## Decision

We implement a **provider health tracking system** with error-type-specific cooldown policies and automatic recovery.

### Architecture

**Two-level health tracking:**
1. **Provider-level** — Tracks health of entire provider (e.g., "anthropic")
2. **Model-level** — Tracks health of specific models (e.g., "anthropic/claude-sonnet-4")

Both levels maintain:
- **Success/failure history** — Rolling window of last 50 attempts
- **Active cooldowns** — Per error type, with exponential backoff
- **Health score** — 0.0 to 1.0, used for routing decisions
- **Disabled state** — Manual or automatic disable until intervention

### Error Types and Cooldown Policies

Each error type has different cooldown characteristics:

| Error Type | Initial Cooldown | Max Cooldown | Multiplier | Reasoning |
|------------|------------------|--------------|------------|-----------|
| `rate_limit` | 60s | 15min | 2.0 | Provider may reset hourly/daily |
| `auth_error` | 24h | 24h | 1.0 | Requires manual API key fix |
| `quota_exceeded` | 24h | 24h | 1.0 | Simulates billing period reset |
| `timeout` | 30s | 5min | 1.5 | Temporary network/server issue |
| `server_error` | 120s | 30min | 2.0 | Infrastructure problems |
| `unknown` | 60s | 10min | 1.5 | Conservative default |

**Exponential backoff:**
- First failure → initial cooldown
- Second failure (consecutive) → cooldown × multiplier
- Third failure → cooldown × multiplier² (capped at max)
- Success resets cooldown and consecutive failure count

**Example:**
```
Attempt 1: Rate limit → 60s cooldown
Attempt 2 (after cooldown): Rate limit → 120s cooldown
Attempt 3: Rate limit → 240s cooldown
Attempt 4: Rate limit → 480s cooldown
Attempt 5: Rate limit → 900s (capped at max 15min)
Attempt 6: Success → cooldown reset to 60s
```

### Health Score Calculation

Health score (0.0 to 1.0) combines two factors:

**Success rate (70% weight):**
- Based on last 50 attempts in rolling window
- New/untested providers start at 1.0 (optimistic)
- Formula: `successes / total_attempts`

**Cooldown penalty (30% weight):**
- Active cooldown reduces health score
- More severe errors = larger penalty
- Penalty weights:
  - `auth_error`: -0.30
  - `quota_exceeded`: -0.30
  - `rate_limit`: -0.25
  - `server_error`: -0.20
  - `timeout`: -0.15
  - `unknown`: -0.10

**Final formula:**
```python
health = (success_rate * 0.7) + ((1.0 - max_cooldown_penalty) * 0.3)
```

**Disabled providers:**
- Manual disable → health = 0.0
- Auth errors → auto-disable until manual reset

### Integration with Model Routing

Health tracking integrates with the five-tier model routing system (ADR-0004):

**Before spawning worker:**
1. Check health of all models in fallback chain
2. Filter out models with active cooldowns
3. Sort remaining models by health score (descending)
4. Try healthiest model first

**After worker completes:**
1. Extract success/failure from worker result
2. Classify error type (if failed)
3. Record outcome in provider health registry
4. Update health scores and cooldowns
5. Persist state to database (every 5 minutes)

**Example routing decision:**
```yaml
Tier: medium
Fallback chain:
  - anthropic/claude-sonnet-4  (health: 0.95, no cooldown)
  - openai/gpt-4o             (health: 0.60, rate_limit cooldown 2min left)
  - anthropic/claude-sonnet-3  (health: 0.85, no cooldown)

Routing order:
  1. anthropic/claude-sonnet-4  ← healthiest
  2. anthropic/claude-sonnet-3  ← second healthiest
  3. openai/gpt-4o             ← skip (cooldown active)
```

### State Persistence

Health state is maintained in-memory for fast access, with periodic DB persistence:

**In-memory:**
- `ProviderHealthRegistry` singleton
- Fast lookups via `is_available(provider_or_model)`
- Stats per provider/model

**Database:**
- Stored in `orchestrator_settings` table
- Key: `provider_health.config`
- Persisted every 5 minutes
- Contains:
  - Disabled providers/models (manual list)
  - Cooldown state (for recovery across restarts)
  - Recent history (last 50 events per provider)

**On restart:**
- Load persisted state from database
- Cooldowns continue where they left off
- History window restored

## Consequences

### Positive

- **Intelligent retry avoidance** — Don't retry providers in cooldown
- **Automatic recovery** — Cooldowns expire automatically, provider becomes available again
- **Reduced wasted API calls** — Skip known-bad providers, save money
- **Better task success rate** — Route around unhealthy providers instead of failing
- **Observable** — Health scores visible in orchestrator status API
- **Error-specific handling** — Rate limits treated differently than auth errors
- **Graceful degradation** — System adapts to provider outages without manual intervention
- **Multi-provider resilience** — If Anthropic is down, fallback to OpenAI works seamlessly

### Negative

- **State management complexity** — In-memory + DB persistence, sync challenges
- **Calibration needed** — Cooldown times are heuristic, may need tuning
- **Cold start optimism** — New providers get benefit of doubt (health=1.0), might fail first try
- **Persistence lag** — Up to 5 minutes of health state can be lost on crash
- **Memory overhead** — Tracking history for every provider/model
- **No cross-instance coordination** — If running multiple orchestrator instances, health state doesn't sync

### Neutral

- Manual disable/enable API available for emergency intervention
- Cooldown state visible in logs for debugging
- Health scores could be exposed to Mission Control UI (not yet implemented)

## Alternatives Considered

### Option 1: Simple Retry with Fixed Backoff

- **Pros:**
  - Dead simple
  - No state to maintain
  - Works for transient errors

- **Cons:**
  - Wastes retries on persistent failures
  - No learning across tasks
  - No provider-specific logic
  - Doesn't handle quota limits well

- **Why rejected:** Too naive. Agent tasks take minutes/hours — we can't afford to waste 3-5 retries on a provider that's been down for an hour.

### Option 2: Circuit Breaker Only (No Health Scores)

- **Pros:**
  - Well-known pattern
  - Clear open/closed/half-open states
  - Simple to reason about

- **Cons:**
  - Binary (available or not), no gradation
  - Same logic for all error types
  - No ordering by reliability
  - Doesn't inform routing decisions

- **Why rejected:** Too coarse. We wanted to prefer healthy providers over marginal ones, not just filter out completely broken ones.

### Option 3: External Health Monitoring Service

- **Pros:**
  - Centralized health data
  - Can monitor providers proactively (synthetic checks)
  - Shared across all instances
  - Rich metrics and alerting

- **Cons:**
  - Additional infrastructure
  - Network dependency
  - Latency on health checks
  - Overkill for single-instance system

- **Why rejected:** We're running single orchestrator instance. In-process health tracking is fast and simple. Can migrate to external service if we ever run distributed orchestrators.

### Option 4: Provider-Reported Health (Use Provider Status Pages)

- **Pros:**
  - Authoritative source
  - Proactive knowledge of outages
  - No need to detect failures ourselves

- **Cons:**
  - Requires scraping status pages or calling APIs
  - Not all providers expose health APIs
  - Status pages may lag real issues
  - Regional availability not captured
  - Adds external dependencies

- **Why rejected:** Provider status is useful context, but our own health tracking reflects **actual experience** from our API keys, rate limits, and usage patterns. Complementary, not a replacement.

## Implementation Details

**Core components:**

1. **`ProviderHealthStats`** — Dataclass holding health state for one provider/model
   - `recent_history: deque[bool]` — Last 50 success/failure outcomes
   - `cooldowns: dict[ErrorType, CooldownState]` — Active cooldowns per error type
   - `disabled: bool` — Manual disable flag
   - `get_health_score() -> float` — Calculate 0.0-1.0 score

2. **`ProviderHealthRegistry`** — Singleton managing all health state
   - `provider_health: dict[str, ProviderHealthStats]` — Provider-level tracking
   - `model_health: dict[str, ProviderHealthStats]` — Model-level tracking
   - `is_available(provider_or_model) -> bool` — Fast availability check
   - `record_outcome()` — Update health after attempt
   - `persist_to_db()` — Save state to database

3. **`CooldownState`** — Dataclass for one active cooldown
   - `error_type: ErrorType`
   - `started_at: float` — Unix timestamp
   - `duration: float` — Current cooldown length (grows with backoff)
   - `consecutive_failures: int` — For exponential backoff

**Error classification:**

Errors are classified by inspecting API response:
- HTTP 429 → `rate_limit`
- HTTP 401/403 → `auth_error`
- HTTP 402 (or billing-related message) → `quota_exceeded`
- Timeout exception → `timeout`
- HTTP 500/502/503 → `server_error`
- Other → `unknown`

**Worker integration:**

```python
# Before spawning worker
registry = ProviderHealthRegistry(db)
available_models = [
    m for m in tier_fallback_chain 
    if registry.is_available(m)
]
sorted_by_health = sorted(
    available_models, 
    key=lambda m: registry.get_health_score(m),
    reverse=True
)
chosen_model = sorted_by_health[0]

# After worker completes
success = worker.result.success
error_type = classify_error(worker.result.error)
registry.record_outcome(
    model=chosen_model,
    success=success,
    error_type=error_type if not success else None
)
await registry.persist_to_db()
```

## Monitoring and Observability

Health registry exposes metrics for monitoring:

**Via `/api/orchestrator/status` endpoint:**
```json
{
  "provider_health": {
    "anthropic": {
      "health_score": 0.95,
      "success_rate": 0.98,
      "total_attempts": 150,
      "active_cooldowns": [],
      "disabled": false
    },
    "openai": {
      "health_score": 0.65,
      "success_rate": 0.70,
      "total_attempts": 80,
      "active_cooldowns": [
        {"error_type": "rate_limit", "remaining_sec": 120}
      ],
      "disabled": false
    }
  }
}
```

**Logs:**
- `[PROVIDER_HEALTH] Cooldown started: anthropic/claude-sonnet-4, error=rate_limit, duration=60s`
- `[PROVIDER_HEALTH] Cooldown expired: openai/gpt-4o, error=timeout`
- `[PROVIDER_HEALTH] Provider disabled: ollama (manual)`

## Future Enhancements

Possible improvements (not yet implemented):

1. **Proactive health checks** — Ping provider APIs periodically to detect issues before tasks fail
2. **Cross-instance state sharing** — Sync health via Redis for distributed orchestrators
3. **Adaptive cooldown tuning** — Learn optimal cooldown periods from historical data
4. **Provider preference learning** — Remember which providers work best for which task types
5. **Cost-aware routing** — Factor in provider pricing, not just health
6. **Alert on provider degradation** — Notify human when health drops below threshold

## References

- `app/orchestrator/provider_health.py` — Health tracking implementation
- `app/orchestrator/worker.py` — Worker integration
- ADR-0004 — Five-tier model routing (uses health scores)
- ADR-0007 — State management and consistency (persistence strategy)

## Notes

This decision reflects a **reliability-first philosophy**: the system should adapt to failures automatically rather than requiring manual intervention every time a provider has issues.

By tracking health and applying smart cooldowns, we make the orchestrator resilient to:
- Provider outages
- Rate limit storms
- Transient network issues
- Account quota problems

The key insight: **past behavior predicts future reliability**. If a provider failed 10 times in the last hour, it's likely to fail again soon — route around it.

---

*Based on Michael Nygard's ADR format*
