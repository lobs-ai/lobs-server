# Model Routing & Provider Health

**Last Updated:** 2026-02-20

How lobs-server selects AI models for tasks, manages provider reliability, and handles failures.

---

## Overview

The orchestrator uses a multi-tier routing system to select the right AI model for each task:

1. **Task Classification** - Complexity and criticality analysis
2. **Tier Selection** - Map task attributes to model tier (cheap/standard/strong)
3. **Health-Aware Ranking** - Filter and rank candidates by provider health
4. **Fallback Chain** - Try models in order until one succeeds
5. **Health Recording** - Track outcomes to improve future routing

```
┌────────────┐
│    Task    │
└─────┬──────┘
      │
      ▼
┌────────────────────┐
│ Classify Task      │
│ (complexity +      │
│  criticality)      │
└─────┬──────────────┘
      │
      ▼
┌────────────────────┐
│ Select Tier        │
│ cheap/standard/    │
│ strong             │
└─────┬──────────────┘
      │
      ▼
┌────────────────────┐
│ ModelChooser       │
│ (health-aware      │
│  ranking)          │
└─────┬──────────────┘
      │
      ▼
┌────────────────────┐
│ Try Models         │
│ (fallback chain)   │
└─────┬──────────────┘
      │
      ▼
┌────────────────────┐
│ Record Outcome     │
│ (update health)    │
└────────────────────┘
```

---

## Task Classification

**Implemented in:** `app/orchestrator/model_router.py`

### Complexity Levels

- **light** - Simple, routine tasks
- **standard** - Typical development work
- **very_complex** - Complex, multi-step, or large-scope tasks

**Heuristics:**
- Word count in task description
- Keywords: "complex", "architecture", "design", "refactor", "migrate"
- Inbox and coordination tasks bias toward "light"

### Criticality Levels

- **low** - Normal work
- **normal** - Important but not urgent
- **high** - Security, production issues, outages, auth, payments

**Keywords for high criticality:**
- incident, outage, security, production, prod, auth, authentication, payment, billing

---

## Model Tiers

**Default tier configuration:**

```python
DEFAULT_TIER_MODELS = {
    "cheap": (
        "google-gemini-cli/gemini-3-pro-preview",  # Free/OAuth
        "anthropic/claude-haiku-4-5",
    ),
    "standard": (
        "openai-codex/gpt-5.3-codex",  # Main programming model
        "anthropic/claude-sonnet-4-5",
    ),
    "strong": (
        "openai-codex/gpt-5.3-codex",  # Codex preferred for strong
        "anthropic/claude-opus-4-6",
    ),
}
```

**Routing rules:**
- **Programmer tasks:** Always use `anthropic/claude-sonnet-4-5` (primary) or `anthropic/claude-opus-4` (fallback)
- **Light inbox tasks:** Use "cheap" tier with fallback chain
- **Other tasks:** Use "standard" tier, upgrade to "strong" for very_complex or high criticality

---

## Provider Health Tracking

**Implemented in:** `app/orchestrator/provider_health.py`

The system tracks provider/model reliability and applies intelligent cooldowns based on error patterns.

### Health Score (0.0 - 1.0)

**Calculation:**
- **70%** - Recent success rate (rolling window of last 50 attempts)
- **30%** - Cooldown penalty (based on error severity)
- **0.0** - Manually disabled or auto-disabled (auth/quota errors)

### Error Types & Cooldowns

| Error Type | Initial Cooldown | Max Cooldown | Multiplier |
|------------|------------------|--------------|------------|
| `rate_limit` | 60s | 15min | 2.0x |
| `auth_error` | 24h | 24h | 1.0x (manual reset) |
| `quota_exceeded` | 24h | 24h | 1.0x |
| `timeout` | 30s | 5min | 1.5x |
| `server_error` | 120s | 30min | 2.0x |
| `unknown` | 60s | 10min | 1.5x |

**Cooldown behavior:**
- Exponential backoff per error type
- Cooldown resets on first success after error
- Manual intervention possible for auth/quota errors

### Availability Checks

**Fast path:** `is_available(provider_or_model)`
- Returns `False` if in cooldown period
- Returns `False` if manually disabled
- Returns `True` otherwise (does not wait for health score calculation)

**Used by:** `ModelChooser` filters candidates before ranking

### Manual Controls

**API endpoints** (planned):
- `POST /api/orchestrator/providers/{provider}/reset` - Clear cooldown, reset health
- `POST /api/orchestrator/providers/{provider}/toggle` - Enable/disable provider
- `GET /api/orchestrator/providers/health` - Get health report for all providers

**CLI:**
```python
# In orchestrator engine or admin script
from app.orchestrator.provider_health import provider_health

await provider_health.reset_provider("anthropic")  # Clear errors, reset cooldown
await provider_health.toggle_provider("google", enabled=False)  # Disable provider
report = await provider_health.get_health_report()  # Full status
```

---

## Model Selection Flow

**Implemented in:** `app/orchestrator/model_chooser.py`

### 1. Get Tier Candidates

```python
candidates = DEFAULT_TIER_MODELS.get(tier, DEFAULT_TIER_MODELS["standard"])
# Returns tuple of model strings, e.g.:
# ("openai-codex/gpt-5.3-codex", "anthropic/claude-sonnet-4-5")
```

### 2. Filter by Availability

```python
available = [
    c for c in candidates 
    if provider_health.is_available(c) and 
       provider_health.is_available(infer_provider(c))
]
```

Checks both model-level and provider-level health.

### 3. Rank by Health & Cost

**Ranking priority** (in order):
1. **High error rate penalty** (legacy, for backwards compat)
2. **Health score** (primary signal)
3. **Recent error rate** (secondary)
4. **Provider preference** (from routing policy)
5. **Candidate order** (from tier list)
6. **Cost/spend** (tie-breaker only)

Lower score = higher priority.

### 4. Fallback Handling

If all candidates are unavailable:
- Return full candidate list anyway
- Let worker spawn attempt all models
- Worker will fail gracefully and log structured errors

---

## Worker Spawn Flow

**Implemented in:** `app/orchestrator/worker.py`

### 1. Attempt Each Model

```python
for model in models:
    success, session_id, error_type = await _spawn_session(
        task, agent_type, model, ...
    )
    
    if success:
        await provider_health.record_outcome(
            provider=infer_provider(model),
            model=model,
            success=True
        )
        return True  # Done
    
    # Record failure
    await provider_health.record_outcome(
        provider=infer_provider(model),
        model=model,
        success=False,
        error_type=error_type
    )
```

### 2. Error Classification

**Pattern matching on error messages:**
- `rate_limit` - "rate limit", "429", "too many requests"
- `auth_error` - "401", "403", "unauthorized", "invalid api key"
- `quota_exceeded` - "quota", "insufficient credit", "billing"
- `timeout` - "timeout", "timed out", "504"
- `server_error` - "500", "502", "503", "internal server error"
- `unknown` - Everything else

**Reuses patterns from:** `app/orchestrator/circuit_breaker.py`

### 3. Auto-Disable on Critical Errors

- **auth_error** → Auto-disable provider (24h cooldown, requires manual reset)
- **quota_exceeded** → Auto-disable provider (24h cooldown)

---

## Health Persistence

**Storage:** `orchestrator_settings` table (JSON blob)
- Key: `provider_health_state`
- Value: Serialized state (success counts, cooldowns, manual overrides)

**Update frequency:** Every 5 minutes (in-memory state persists to DB)

**On startup:** Load last-known state from DB, continue tracking

---

## Monitoring & Debugging

### Health Report Structure

```python
{
    "providers": {
        "anthropic": {
            "health_score": 0.95,
            "is_available": True,
            "recent_success_rate": 0.98,
            "total_attempts": 150,
            "cooldown_until": None,
            "manually_disabled": False,
            "models": {
                "anthropic/claude-sonnet-4-5": {
                    "health_score": 0.96,
                    "is_available": True,
                    "recent_success_rate": 0.98
                }
            }
        },
        "openai": {
            "health_score": 0.0,
            "is_available": False,
            "cooldown_until": "2026-02-20T14:30:00Z",
            "last_error_type": "rate_limit",
            "manually_disabled": False
        }
    }
}
```

### Logs

**Key log messages:**
- `"Provider health check: {provider} is_available={available}"` (DEBUG)
- `"Recorded outcome for {provider}/{model}: success={success}, error_type={error_type}"` (INFO)
- `"Provider {provider} auto-disabled due to {error_type}"` (WARNING)
- `"All model candidates unavailable for tier={tier}, returning full list for graceful degradation"` (WARNING)

---

## Configuration

### Environment Variables

None required - uses hardcoded tier definitions.

**Future:** Could add env vars for:
- `MODEL_TIER_CHEAP` - Override cheap tier models
- `MODEL_TIER_STANDARD` - Override standard tier models
- `MODEL_TIER_STRONG` - Override strong tier models

### Registry Integration

Model tiers can be overridden per agent in `registry.json`:

```json
{
    "programmer": {
        "routing_policy": {
            "default_models": [
                "anthropic/claude-sonnet-4-5",
                "anthropic/claude-opus-4"
            ]
        }
    }
}
```

**Priority:** Registry models override tier-based selection.

---

## Future Enhancements

### Planned
- [ ] API endpoints for health management
- [ ] UI dashboard for provider health visualization
- [ ] Configurable error classification patterns
- [ ] Per-task-type routing policies
- [ ] Cost-aware routing (prefer cheaper when quality is similar)

### Under Consideration
- [ ] A/B testing for model performance
- [ ] Automatic tier promotion for stuck tasks
- [ ] Provider SLA tracking
- [ ] Alert on repeated provider failures

---

## See Also

- **[PROVIDER_HEALTH.md](PROVIDER_HEALTH.md)** - Implementation details and technical summary
- **[orchestrator-model-routing.md](orchestrator-model-routing.md)** - First-pass design document
- **[ARCHITECTURE.md](../ARCHITECTURE.md#orchestrator)** - Orchestrator overview
- **[BEST_PRACTICES.md](BEST_PRACTICES.md)** - General development patterns
