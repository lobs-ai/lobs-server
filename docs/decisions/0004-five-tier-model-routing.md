# 4. Five-Tier Model Routing with Fallback Chains

**Date:** 2026-02-22  
**Status:** Accepted  
**Deciders:** System architect, product owner

## Context

OpenClaw agents need to select AI models for different task types. Requirements:
- **Cost efficiency** — Use smallest capable model for each task
- **Capability matching** — Complex reasoning needs strong models, simple tasks don't
- **Reliability** — Graceful degradation when models are unavailable
- **Local-first** — Prefer free local models (Ollama) over paid APIs when possible
- **Performance** — Fast inference for interactive tasks

Initial system used 3 tiers (small/medium/large), but this was too coarse. Many tasks landed in "medium" when they could use smaller/local models.

Recent ARCHITECTURE.md updates mention "5-tier model routing" and "Ollama auto-discovery" as a solved architectural change (Feb 21-22).

## Decision

We implement a **five-tier model routing system** with automatic fallback chains:

### Tiers

1. **micro** — Trivial tasks (formatting, simple extraction, quick responses)
2. **small** — Structured tasks with clear instructions (code review, basic analysis)
3. **medium** — Moderate complexity (feature implementation, research, writing)
4. **standard** — Complex reasoning (architecture, multi-step planning, debugging)
5. **strong** — Hardest problems (novel research, complex design, critical decisions)

### Model Assignment (Example Configuration)

```yaml
micro:
  - ollama/qwen2.5:3b       # Local, fast, free
  - ollama/llama3.2:3b      # Fallback local
  - anthropic/claude-haiku  # Fallback cloud

small:
  - ollama/qwen2.5:7b
  - ollama/llama3.2:7b
  - anthropic/claude-haiku

medium:
  - ollama/qwen2.5:14b
  - anthropic/claude-sonnet-4
  - openai/gpt-4o-mini

standard:
  - anthropic/claude-sonnet-4-5
  - openai/gpt-4o
  - anthropic/claude-sonnet-4

strong:
  - anthropic/claude-opus-4
  - openai/o1-pro
  - anthropic/claude-sonnet-4-5
```

### Fallback Behavior

When a model is unavailable (rate limit, downtime, timeout):
1. Try next model in tier's fallback chain
2. If entire chain fails, escalate to next tier
3. If all tiers fail, retry with exponential backoff
4. After N failures, pause agent and alert human

### Ollama Auto-Discovery

On startup, the system:
1. Queries Ollama API for available models
2. Matches them to tier definitions
3. Adds discovered models to fallback chains
4. Updates routing config dynamically

This allows zero-config local model usage when Ollama is running.

## Consequences

### Positive

- **Cost reduction** — Simple tasks use free local models (~70% of tasks can use micro/small)
- **Better matching** — Five tiers provide finer granularity than three
- **Resilience** — Multi-model fallback chains prevent single point of failure
- **Local-first** — Ollama models tried first, cloud APIs only when needed
- **Performance** — Small models are faster (3B/7B params vs 70B+)
- **Transparency** — Each task logs which model tier and specific model was used
- **Easy tuning** — Can adjust tier thresholds and model assignments without code changes

### Negative

- **Configuration complexity** — More tiers = more config to maintain
- **Model sprawl** — Need to track many model names, versions, capabilities
- **Inconsistent quality** — Same task might use different models on retries
- **Local dependency** — Relies on Ollama being installed and running for best cost savings
- **Debugging harder** — "It worked on micro but failed on small" issues

### Neutral

- Model selection is logged per task for audit/debugging
- Tier assignment is agent-configurable (agents can override default tier)
- Fallback chains are retried with exponential backoff

## Alternatives Considered

### Option 1: Single Model for All Tasks

- **Pros:**
  - Simple, predictable
  - Consistent quality
  - Easy to debug

- **Cons:**
  - Expensive (use best model for everything)
  - OR low quality (use cheap model for everything)
  - Slow for simple tasks if using large model

- **Why rejected:** Wasteful. Using Claude Opus for "format this JSON" is like hiring a surgeon to put on a band-aid.

### Option 2: Three-Tier System (Small/Medium/Large)

- **Pros:**
  - Simpler than five tiers
  - Still provides some optimization
  - Less config to maintain

- **Cons:**
  - Not granular enough
  - Too many tasks fall into "medium"
  - Misses opportunity for local model usage
  - "Small" tier often over-specified for trivial tasks

- **Why rejected:** We tried this. ~60% of tasks landed in "medium" because small felt too weak and large too expensive. Adding micro and standard tiers better distributes the load.

### Option 3: Dynamic Model Selection (LLM Chooses Model)

- **Pros:**
  - Perfectly matched to task complexity
  - No manual tier assignment needed
  - Adaptive to new model releases

- **Cons:**
  - Requires meta-LLM call (adds latency and cost)
  - Circular dependency (which model decides which model to use?)
  - Hard to predict costs
  - Can't use free local models (meta-LLM needs to be cloud)

- **Why rejected:** Too clever. Adds complexity and cost without clear benefit. Static tier assignment works well enough.

### Option 4: Cost-Based Routing (Cheapest Available Model)

- **Pros:**
  - Minimizes cost automatically
  - Simple decision logic

- **Cons:**
  - No quality guarantees
  - Would always pick smallest model regardless of task complexity
  - Poor user experience (failures on complex tasks)

- **Why rejected:** Optimizing purely for cost sacrifices reliability. We want cost-effective, not cost-minimized.

### Option 5: Manual Model Selection Per Task

- **Pros:**
  - Perfect control
  - No abstraction layer

- **Cons:**
  - Tedious for users
  - Requires model expertise
  - Doesn't scale (100s of tasks)

- **Why rejected:** We want the system to be smart by default. Expert users can still override tier selection.

## Tier Assignment Guidelines

**Micro:**
- JSON formatting
- Simple text transformations
- Quick yes/no answers
- Template filling

**Small:**
- Code review (following checklist)
- Basic data analysis
- Straightforward implementations
- Documentation writing

**Medium:**
- Feature implementation (new code)
- Research tasks
- Content writing
- Refactoring

**Standard:**
- System architecture
- Complex debugging
- Multi-step planning
- Design documents

**Strong:**
- Novel research (no clear answer)
- Critical architecture decisions
- Solving previously unsolved problems
- Maximum quality requirements

## Model Selection Algorithm

```python
def select_model(tier: str, task_context: dict) -> str:
    """
    1. Get tier config (e.g., tier='medium' -> [ollama/qwen2.5:14b, claude-sonnet-4, ...])
    2. Discover available Ollama models
    3. Build fallback chain: [local models...] + [cloud models...]
    4. Try each model in order until one succeeds
    5. Log which model was used for this task
    6. Return model identifier
    """
```

## Observability

System tracks:
- **Model usage per tier** — Which models are actually being used
- **Fallback frequency** — How often does primary model fail
- **Cost per tier** — Running total of inference costs
- **Latency per model** — P50/P95/P99 inference times
- **Success rate per model** — Which models complete tasks vs. fail

This data informs tier threshold adjustments and model selection updates.

## Migration Notes

**From 3-tier to 5-tier:**
- `small` → now `small` or `micro` (review tasks individually)
- `medium` → now `medium` or `small` (default: keep as medium)
- `large` → now `standard` or `strong` (most should be standard)

Agents default to `medium` tier unless overridden. Gradually tune down to smaller tiers as confidence grows.

## Future Enhancements

Possible improvements:
- **Adaptive tiers** — Learn which tasks can be downgraded after successful completion
- **Model performance tracking** — Automatically remove poorly performing models from chains
- **Custom chains per agent** — Programmer agent might have different preferences than writer
- **Streaming tier escalation** — Start with micro, escalate mid-task if struggling
- **User feedback** — "This response was too slow/expensive/low-quality" → adjust tier

## References

- ARCHITECTURE.md — Recent changes section (Feb 21-22)
- `app/orchestrator/registry.py` — Agent config with model tiers
- OpenClaw documentation — Model routing and fallback chains
- [Initiative #52] — Model routing improvements

## Notes

This decision is informed by **real usage patterns**:
- ~40% of tasks are simple enough for micro tier
- ~25% work well with small tier (local models)
- ~25% need medium tier
- ~8% need standard tier
- ~2% need strong tier

By routing intelligently, estimated cost savings: **~60% vs. using medium tier for everything**.

The system is designed to **degrade gracefully**: if all local models fail, cloud models catch it. If preferred cloud model is down, fallback chain continues.

---

*Based on Michael Nygard's ADR format*
