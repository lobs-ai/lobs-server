# Model Selection Guide

**Last Updated:** 2026-02-22

How to choose the right AI model tier for your tasks — balancing cost, quality, and speed.

---

## Quick Reference

| Tier | Use When | Example Tasks | Cost | Speed |
|------|----------|---------------|------|-------|
| **micro** | Trivial operations | JSON formatting, yes/no answers, template filling | Free (local) | ⚡⚡⚡ |
| **small** | Simple structured work | Code review checklist, basic analysis, drafts | Free (local) | ⚡⚡ |
| **medium** | Standard complexity | Feature implementation, research, documentation | Low ($) | ⚡ |
| **standard** | Complex reasoning | Architecture, debugging, multi-step planning | Medium ($$) | ⚡ |
| **strong** | Hardest problems | Novel research, critical decisions, complex design | High ($$$) | ⚡ |

**Rule of thumb:** Start with the smallest tier that can handle the job. The system falls back to stronger models if needed.

---

## The Five-Tier System

lobs-server uses a **five-tier model routing system** that automatically selects the right model based on task complexity. This balances cost efficiency (using free local models when possible) with quality (escalating to cloud models for complex work).

### Tier Definitions

#### 1. Micro (≤15B params)
**Models:** Qwen 7B, Llama 3.2 3B, Phi-4 (local Ollama models)

**Best for:**
- JSON formatting and parsing
- Simple text transformations
- Quick yes/no questions
- Template filling
- Basic classification

**Not suitable for:**
- Code generation
- Creative writing
- Complex reasoning
- Anything requiring deep context

**Cost:** Free (runs locally via Ollama)  
**Speed:** ~500-1000 tokens/sec  
**Quality:** Good for mechanical tasks, poor for nuanced work

#### 2. Small (≤40B params)
**Models:** Qwen 30B, Mistral 22B, Llama 3.2 7B (local Ollama models)

**Best for:**
- Code review following a checklist
- Basic data analysis
- Straightforward code implementations
- Documentation writing (first drafts)
- Summaries and reports

**Not suitable for:**
- Complex architecture decisions
- Novel problem solving
- High-stakes code changes
- Critical business logic

**Cost:** Free (runs locally via Ollama)  
**Speed:** ~200-400 tokens/sec  
**Quality:** Solid for well-defined tasks with clear instructions

#### 3. Medium (≤80B params + cheap cloud)
**Models:** Llama 70B (local), Claude Haiku, Gemini Pro (cloud)

**Best for:**
- Feature implementation (new code)
- Research tasks with clear scope
- Content writing (articles, docs)
- Refactoring existing code
- Test writing

**Not suitable for:**
- System architecture
- Complex debugging (race conditions, distributed systems)
- Security-critical code
- Novel algorithm design

**Cost:** Free (local) or $0.001-0.002/1K tokens (cloud)  
**Speed:** ~100-200 tokens/sec (local), ~300-500 tokens/sec (cloud)  
**Quality:** Good general-purpose capability

#### 4. Standard (Cloud-quality reasoning)
**Models:** Claude Sonnet 4.5, GPT-5.3 Codex, GPT-4o

**Best for:**
- System architecture and design
- Complex debugging (multi-component issues)
- Multi-step planning and strategy
- Design documents
- Code review (deep analysis)

**Not suitable for:**
- Simple tasks (wasteful)
- Highly experimental research (use strong tier)

**Cost:** $0.003-0.015/1K tokens  
**Speed:** ~150-300 tokens/sec  
**Quality:** High-quality reasoning, strong code understanding

**This is the default tier for most production work.**

#### 5. Strong (Maximum capability)
**Models:** Claude Opus 4.6, GPT-5.3 Codex, O1-Pro

**Best for:**
- Novel research (no known solution)
- Critical architecture decisions
- Complex system design
- Previously unsolved problems
- Maximum quality requirements

**Not suitable for:**
- Routine tasks (very wasteful)
- High-frequency operations
- Well-understood problems

**Cost:** $0.015-0.060/1K tokens  
**Speed:** ~50-150 tokens/sec  
**Quality:** Best available

**Use sparingly. Strong tier is for when you need the absolute best.**

---

## Cost vs. Quality Tradeoffs

### Real-World Cost Comparison

Typical task: "Add user authentication to API"

| Tier | Model | Est. Tokens | Cost | Quality | Success Rate |
|------|-------|-------------|------|---------|--------------|
| micro | Qwen 7B | ~8K | $0 | Low | ~40% |
| small | Qwen 30B | ~10K | $0 | Medium | ~70% |
| medium | Llama 70B | ~12K | $0 | Good | ~85% |
| standard | Sonnet 4.5 | ~15K | ~$0.05 | Excellent | ~95% |
| strong | Opus 4.6 | ~18K | ~$0.30 | Best | ~98% |

**Key insight:** Standard tier hits the sweet spot for most work — high success rate at reasonable cost.

### Estimated Monthly Costs by Usage Pattern

**Assumptions:** 100 tasks/month, 10K avg tokens/task

| Pattern | Tier Mix | Monthly Cost |
|---------|----------|--------------|
| **Cost-optimized** | 40% micro, 30% small, 20% medium, 10% standard | ~$10-15 |
| **Balanced** | 10% small, 40% medium, 45% standard, 5% strong | ~$30-50 |
| **Quality-first** | 20% medium, 50% standard, 30% strong | ~$80-120 |

**Reality check:** Most teams use 60-70% standard tier, saving micro/small for simple tasks and strong for critical work.

---

## Per-Agent Recommendations

Different agent types have different complexity profiles. Here's how each agent typically uses model tiers:

### Programmer
**Primary tier:** Standard (Codex 5.3)  
**Fallbacks:** Medium → Strong

**Rationale:** Code needs high accuracy. Wrong code is worse than no code. Use standard tier by default.

**Tier usage:**
- Micro: Never (too risky for code)
- Small: Simple refactors, test writing (with review)
- Medium: Straightforward implementations (CRUD, utilities)
- Standard: Most code work (features, fixes, integrations)
- Strong: Complex algorithms, security-critical code

**Override setting:** `strict_coding_tier=true` forces programmer to use first model in chain (no automatic fallback to cheaper models).

### Researcher
**Primary tier:** Standard  
**Fallbacks:** Medium → Strong

**Rationale:** Research needs depth and accuracy. Bad research leads to bad decisions.

**Tier usage:**
- Micro: Quick fact checks
- Small: Basic summaries
- Medium: Literature review, comparative analysis
- Standard: Deep investigation, technical evaluation
- Strong: Novel research, strategic analysis

### Architect
**Primary tier:** Standard  
**Fallbacks:** Strong

**Rationale:** Architecture is high-leverage. A good design saves months; a bad one costs them.

**Tier usage:**
- Micro: Never
- Small: Never
- Medium: Minor design updates
- Standard: System design, technical strategy
- Strong: Foundational architecture, critical decisions

**Note:** Architect rarely uses micro/small. Design work is too important to skimp.

### Writer
**Primary tier:** Standard  
**Fallbacks:** Medium → Standard

**Rationale:** Writing quality matters, but tolerance for revision is higher than code.

**Tier usage:**
- Micro: Template filling
- Small: First drafts, simple docs
- Medium: Documentation, guides, reports
- Standard: Technical write-ups, design docs, polished content
- Strong: Critical communications, public-facing content

### Reviewer
**Primary tier:** Standard  
**Fallbacks:** Medium → Standard

**Rationale:** Reviews need high accuracy to catch issues, but can tolerate false positives.

**Tier usage:**
- Micro: Never
- Small: Basic checklist reviews
- Medium: Code review (style, obvious bugs)
- Standard: Deep review (logic, security, design)
- Strong: Audit of critical systems

---

## How Model Routing Works

The orchestrator automatically selects models using this algorithm:

### 1. Classify Task Complexity

The system analyzes task title and notes for keywords:

**Very Complex** (→ standard or strong tier):
- Keywords: "orchestrator", "policy engine", "migration", "refactor", "distributed", "database", "schema", "performance", "security"
- Examples: "Refactor auth system", "Design distributed cache"

**High Criticality** (→ tier +1):
- Keywords: "incident", "urgent", "security", "vulnerability", "prod", "production", "auth", "payment"
- Examples: "Fix production auth bypass", "Urgent: payment API down"

**Light** (→ tier -1):
- Keywords: "typo", "copy edit", "quick reply", "small cleanup"
- Examples: "Fix typo in README", "Quick reply to issue #123"

### 2. Map to Tier Plan

Based on complexity and criticality, the system generates a fallback chain:

```python
# Example: Standard complexity, normal criticality
plan = ["standard", "strong"]  # Try standard first, escalate to strong if needed

# Example: Very complex, high criticality
plan = ["strong"]  # Skip straight to strong tier

# Example: Light complexity, low criticality
plan = ["medium", "standard"]  # Try medium, fall back to standard
```

### 3. Resolve Tiers to Models

Each tier maps to a list of models:

```python
tier_map = {
    "micro": ["ollama/qwen2.5:7b", "ollama/llama3.2:3b"],
    "small": ["ollama/qwen2.5:30b", "anthropic/claude-haiku"],
    "medium": ["ollama/llama3.2:70b", "google/gemini-pro"],
    "standard": ["anthropic/claude-sonnet-4-5", "openai-codex/gpt-5.3-codex"],
    "strong": ["anthropic/claude-opus-4-6", "openai-codex/gpt-5.3-codex"],
}
```

**Ollama models are auto-discovered** at runtime and prepended to tier lists (preferred because free).

### 4. Apply Routing Policy

Models are reordered based on preferences:

```python
routing_policy = {
    "fallback_chains": {
        "default": ["subscription", "anthropic", "openai", "google"]
    },
    "subscription_models": ["openai-codex/gpt-5.3-codex"],
    "quality_preference": ["anthropic", "openai", "google"]
}
```

**Subscription models** (unlimited usage plans) are preferred over pay-per-token APIs.

### 5. Apply Budget Guards

Models are filtered by provider spending:

```python
budgets = {
    "per_provider_monthly_usd": {
        "anthropic": 100.0,  # Cap Anthropic at $100/month
        "openai": 200.0      # Cap OpenAI at $200/month
    }
}
```

If a provider has exceeded its monthly budget, all models from that provider are removed from the candidate list.

### 6. Rank by Health and Cost

Final ranking considers:
1. **Health score** (0-1, based on recent success rate)
2. **Error rate** (last 30 minutes)
3. **Provider preference** (from quality_preference)
4. **Cost** (unit price per 1M tokens)

**Example ranking:**
```
1. anthropic/claude-sonnet-4-5  (health: 0.98, err: 0.02, cost: $3.00)
2. openai-codex/gpt-5.3-codex   (health: 0.95, err: 0.03, cost: $5.00)
3. anthropic/claude-haiku       (health: 0.92, err: 0.05, cost: $1.00)
```

### 7. Execute with Fallback

Worker tries models in order until one succeeds:

```python
for model in candidate_models:
    try:
        result = await run_task(model)
        log_success(model)
        return result
    except ModelUnavailable:
        log_failure(model)
        continue  # Try next model

# If all models fail, escalate to next tier
```

---

## Practical Examples

### Example 1: Simple Documentation Update

**Task:** "Fix typo in README.md"

**Classification:**
- Complexity: Light (keyword: "typo")
- Criticality: Low

**Tier plan:** `["small", "medium"]`

**Resolved models:** `["ollama/qwen2.5:30b", "ollama/llama3.2:70b", "anthropic/claude-haiku"]`

**Selected:** `ollama/qwen2.5:30b` (local, free)

**Cost:** $0

---

### Example 2: Feature Implementation

**Task:** "Add pagination to /api/users endpoint"

**Classification:**
- Complexity: Standard (no special keywords)
- Criticality: Normal

**Tier plan:** `["standard", "strong"]`

**Resolved models:** `["openai-codex/gpt-5.3-codex", "anthropic/claude-sonnet-4-5", "anthropic/claude-opus-4-6"]`

**Selected:** `openai-codex/gpt-5.3-codex` (subscription model, preferred)

**Cost:** $0 (subscription)

---

### Example 3: Production Security Issue

**Task:** "URGENT: Fix authentication bypass in /api/admin"

**Classification:**
- Complexity: Very Complex (keywords: "security", "auth")
- Criticality: High (keywords: "urgent", "prod")

**Tier plan:** `["strong"]` (skip straight to strongest)

**Resolved models:** `["anthropic/claude-opus-4-6", "openai-codex/gpt-5.3-codex"]`

**Selected:** `anthropic/claude-opus-4-6`

**Cost:** ~$0.50-1.00 (worth it for security)

---

### Example 4: Research Task

**Task:** "Research SQLite vs PostgreSQL for multi-tenant architecture"

**Classification:**
- Complexity: Standard
- Criticality: Normal

**Tier plan:** `["standard", "strong"]`

**Resolved models:** `["anthropic/claude-sonnet-4-5", "openai-codex/gpt-5.3-codex", "anthropic/claude-opus-4-6"]`

**Selected:** `anthropic/claude-sonnet-4-5`

**Cost:** ~$0.10-0.20

---

## Configuration

### Environment Variables

Override tier defaults via environment variables:

```bash
# Tier-specific model lists (comma-separated)
export LOBS_MODEL_TIER_MICRO="ollama/qwen2.5:7b,ollama/llama3.2:3b"
export LOBS_MODEL_TIER_SMALL="ollama/qwen2.5:30b,anthropic/claude-haiku"
export LOBS_MODEL_TIER_MEDIUM="ollama/llama3.2:70b,google/gemini-pro"
export LOBS_MODEL_TIER_STANDARD="openai-codex/gpt-5.3-codex,anthropic/claude-sonnet-4-5"
export LOBS_MODEL_TIER_STRONG="anthropic/claude-opus-4-6,openai-codex/gpt-5.3-codex"

# Allowlist of available models (optional)
export LOBS_AVAILABLE_MODELS="openai-codex/gpt-5.3-codex,anthropic/claude-sonnet-4-5"
```

### Database Settings

Runtime settings stored in `orchestrator_settings` table override env vars:

```sql
-- Set tier models
INSERT INTO orchestrator_settings (key, value) VALUES
  ('model_router.tier.standard', '["openai-codex/gpt-5.3-codex"]'),
  ('model_router.tier.strong', '["anthropic/claude-opus-4-6"]');

-- Set routing policy
INSERT INTO orchestrator_settings (key, value) VALUES
  ('usage.routing_policy', '{
    "fallback_chains": {
      "default": ["subscription", "anthropic", "openai"]
    },
    "subscription_models": ["openai-codex/gpt-5.3-codex"],
    "quality_preference": ["anthropic", "openai"]
  }');

-- Set budget caps
INSERT INTO orchestrator_settings (key, value) VALUES
  ('usage.budgets', '{
    "per_provider_monthly_usd": {
      "anthropic": 100.0,
      "openai": 200.0
    }
  }');

-- Disable automatic tier degradation on quota
UPDATE orchestrator_settings SET value = false WHERE key = 'model_router.degrade_on_quota';

-- Strict coding tier (programmer uses only first model in list)
UPDATE orchestrator_settings SET value = true WHERE key = 'model_router.strict_coding_tier';
```

### Precedence

Settings are applied in this order (last wins):

1. **Code defaults** (`DEFAULT_TIER_MODELS` in `model_router.py`)
2. **Environment variables** (`LOBS_MODEL_TIER_*`)
3. **Database settings** (`orchestrator_settings` table)
4. **Ollama auto-discovery** (prepends local models to tiers)

---

## Monitoring and Observability

### What Gets Logged

Every task execution logs:
- **Model tier used** (micro/small/medium/standard/strong)
- **Specific model** (e.g., "anthropic/claude-sonnet-4-5")
- **Fallback attempts** (how many models tried before success)
- **Token usage** (input, output, total)
- **Cost estimate** (USD)
- **Success/failure**

### Key Metrics to Track

**Cost metrics:**
```sql
-- Monthly cost by provider
SELECT provider, SUM(estimated_cost_usd) as total_cost
FROM model_usage_events
WHERE timestamp >= DATE('now', 'start of month')
GROUP BY provider;

-- Cost per tier
SELECT metadata->>'tier' as tier, SUM(estimated_cost_usd) as total_cost
FROM model_usage_events
WHERE timestamp >= DATE('now', 'start of month')
GROUP BY tier;
```

**Quality metrics:**
```sql
-- Success rate by model
SELECT model, 
       COUNT(*) as attempts,
       SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successes,
       ROUND(100.0 * SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) / COUNT(*), 2) as success_rate
FROM model_usage_events
WHERE timestamp >= DATE('now', '-7 days')
GROUP BY model
ORDER BY attempts DESC;

-- Fallback frequency (how often does primary model fail?)
SELECT metadata->>'fallback_count' as fallback_count,
       COUNT(*) as occurrences
FROM model_usage_events
WHERE timestamp >= DATE('now', '-7 days')
GROUP BY fallback_count;
```

**Performance metrics:**
```sql
-- P50/P95 latency by model
SELECT model,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) as p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) as p95_ms
FROM model_usage_events
WHERE timestamp >= DATE('now', '-7 days')
GROUP BY model;
```

### Alerts to Set Up

1. **Cost spike:** Monthly spend exceeds budget by 20%
2. **High error rate:** Any provider/model with >15% error rate in last hour
3. **Fallback storm:** >50% of tasks require fallback in last 15 minutes
4. **Budget exhaustion:** Provider hits 90% of monthly budget

---

## Tuning Tips

### Optimize for Cost

1. **Install Ollama** and pull models:
   ```bash
   ollama pull qwen2.5:7b
   ollama pull qwen2.5:30b
   ollama pull llama3.2:70b
   ```

2. **Review task classifications** — many tasks marked "standard" can run on "medium":
   ```sql
   SELECT title, metadata->>'tier' as tier, metadata->>'model' as model
   FROM tasks
   WHERE status = 'completed'
     AND metadata->>'tier' = 'standard'
   ORDER BY completed_at DESC
   LIMIT 50;
   ```

3. **Tune tier thresholds** — adjust keywords in `model_router.py`

4. **Use subscription models** — unlimited usage beats pay-per-token for high volume

### Optimize for Quality

1. **Raise minimum tier:**
   ```bash
   export LOBS_MODEL_TIER_MEDIUM="anthropic/claude-sonnet-4-5"  # Skip cheap models
   ```

2. **Enable strict coding tier:**
   ```sql
   UPDATE orchestrator_settings SET value = true WHERE key = 'model_router.strict_coding_tier';
   ```

3. **Increase strong tier usage** — manually override tier for important tasks

4. **Add quality preference:**
   ```sql
   UPDATE orchestrator_settings 
   SET value = '{"quality_preference": ["anthropic", "openai"]}'
   WHERE key = 'usage.routing_policy';
   ```

### Optimize for Speed

1. **Use local models** (micro/small/medium tiers) — 2-10x faster than cloud APIs

2. **Prefer Haiku over Sonnet** for medium tier:
   ```bash
   export LOBS_MODEL_TIER_MEDIUM="anthropic/claude-haiku,google/gemini-pro"
   ```

3. **Reduce fallback chains** — shorter chains = faster failures

4. **Cache common operations** — use micro tier for frequently repeated tasks

---

## Common Questions

### Q: Can I force a specific model for a task?

**A:** Yes, via task metadata:

```python
task = {
    "title": "Critical auth fix",
    "agent_type": "programmer",
    "metadata": {
        "model_override": "anthropic/claude-opus-4-6"
    }
}
```

This skips tier routing and uses the specified model directly.

### Q: What happens if all models in a tier fail?

**A:** The system escalates to the next tier in the plan. If all tiers fail, the task is marked as failed and retried later with exponential backoff.

### Q: Can different agents use different tier configurations?

**A:** Not currently. All agents share the same tier configuration. However, you can set agent-specific model overrides in task metadata.

### Q: How do I add a new model?

**A:** Add it to the appropriate tier list:

```bash
# Via environment
export LOBS_MODEL_TIER_STANDARD="my-new-model,anthropic/claude-sonnet-4-5"

# Or via database
INSERT INTO orchestrator_settings (key, value) VALUES
  ('model_router.tier.standard', '["my-new-model", "anthropic/claude-sonnet-4-5"]');
```

Also add pricing data to `model_pricing` table for cost tracking.

### Q: Why does my local Ollama model not appear?

**A:** Check:
1. Ollama is running (`ollama list`)
2. Model is pulled (`ollama pull qwen2.5:7b`)
3. Model is discoverable via API (`curl http://localhost:11434/api/tags`)
4. Cache hasn't expired (auto-discovery runs every 60 seconds)

### Q: Can I disable Ollama auto-discovery?

**A:** Yes, explicitly set tier lists without Ollama models:

```sql
UPDATE orchestrator_settings SET value = '["anthropic/claude-haiku"]' WHERE key = 'model_router.tier.micro';
```

Explicit tier configs override auto-discovery.

---

## Related Documentation

- [ADR-0004: Five-Tier Model Routing](../decisions/0004-five-tier-model-routing.md) — Design decisions and rationale
- [ADR-0008: Agent Specialization Model](../decisions/0008-agent-specialization-model.md) — Agent types and capabilities
- [ARCHITECTURE.md](../../ARCHITECTURE.md) — System overview
- `app/orchestrator/model_chooser.py` — Model selection implementation
- `app/orchestrator/model_router.py` — Tier routing policy engine

---

## Revision History

- **2026-02-22:** Initial version documenting 5-tier system
