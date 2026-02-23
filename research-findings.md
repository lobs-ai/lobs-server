# Observability Gaps Analysis & Recommendations

**Date:** 2026-02-23  
**Author:** researcher agent  
**Task ID:** 93df3aa5-b8d0-476f-b03f-07a2db6118f7

---

## Executive Summary

This document audits current agent observability in lobs-server, identifies critical gaps, and recommends solutions based on industry patterns from LangSmith, Phoenix, and other agent frameworks.

**Key Findings:**
- ✅ **Good foundation**: Token usage tracking, structured logging, model routing audit trails, failure escalation tracking
- ❌ **Critical gaps**: No distributed tracing, limited reasoning visibility, no real-time dashboards
- 🎯 **Top priority**: Implement OpenTelemetry-based distributed tracing with span hierarchy

---

## Current State: What's Being Tracked

### 1. Worker Lifecycle Events ✅
**Location:** `app/orchestrator/worker.py`, `app/orchestrator/engine.py`

**What's logged:**
- Worker spawn events with model selection audit trail
- Worker completion/failure events
- Runtime duration and exit codes
- Stuck worker detection (15min timeout)

**Data persisted:**
- `WorkerRun` table: worker_id, task_id, model, tokens, cost, commit_shas, files_modified, summary
- `WorkerStatus` singleton: current task, heartbeat timestamp, activity state

**Example log:**
```python
logger.info(
    f"[WORKER] Spawned worker {worker_id} for task {task_id_short} "
    f"(project={project_id}, agent={agent_type}, model={chosen_model}, runId={run_id})",
    extra={"model_router": worker_info.model_audit},
)
```

### 2. Token Usage & Cost Tracking ✅
**Location:** `app/orchestrator/token_extractor.py`

**What's tracked:**
- Input/output tokens per message
- Cache read/write tokens (for prompt caching)
- Estimated cost per session
- Provider and model used

**Source:** Extracted from OpenClaw session JSONL transcripts post-completion

**Example data:**
```python
SessionTokenUsage(
    input_tokens=1250,
    output_tokens=890,
    cache_read_tokens=450,
    estimated_cost_usd=0.0245,
    message_count=3,
    model="claude-sonnet-4-5",
    provider="anthropic"
)
```

### 3. Failure Tracking & Escalation ✅
**Location:** `app/orchestrator/escalation_enhanced.py`, `app/orchestrator/circuit_breaker.py`

**What's tracked:**
- Escalation tier (0-4: none → retry → agent_switch → diagnostic → human)
- Retry count per task
- Failure reason text
- Circuit breaker state (tracks infrastructure vs task failures)
- Provider health (error types: rate_limit, auth_error, quota_exceeded, timeout, server_error)

**Stored in:** `Task.escalation_tier`, `Task.retry_count`, `Task.failure_reason`

### 4. Logging Infrastructure ✅
**Location:** `app/logging_config.py`

**Implementation:**
- Structured JSON logging with `JSONFormatter`
- Console output with colored formatting
- Rotating file handlers (10MB chunks, 5 backups)
- Separate error log stream
- Module-level log level control

**Example output:**
```json
{
  "timestamp": "2026-02-23T20:00:15.123Z",
  "level": "INFO",
  "logger": "app.orchestrator.worker",
  "message": "[WORKER] Spawned worker...",
  "extra": {
    "model_router": {...}
  }
}
```

---

## Gap #1: No Distributed Tracing 🔴 CRITICAL

### What's Missing

**No span hierarchy** — Operations are logged as flat events with no parent-child relationships. Can't answer:
- "Which LLM calls happened during this retrieval step?"
- "How long did the tool selection phase take vs. execution?"
- "What was the full call stack when this error occurred?"

**No trace context propagation** — When a task spawns sub-agents or makes external calls, there's no trace ID linking them together.

**No timing breakdown** — Worker runtime is tracked as a single duration. Can't identify which operation was slow (prompt building, model inference, tool execution, result parsing).

### Industry Standard: OpenTelemetry Tracing

**LangSmith approach** (source: https://docs.smith.langchain.com/observability):
- Every operation creates a **span** (e.g., "llm_call", "retriever", "tool_use", "chain")
- Spans have:
  - `trace_id`: Groups all operations in a request
  - `span_id`: Unique identifier for this operation
  - `parent_span_id`: Links to parent operation
  - Start/end timestamps
  - Attributes: model, tokens, cost, error message, etc.
  - Events: Sub-events within a span (e.g., "cache_hit", "retry_attempt")

**Phoenix approach** (source: https://docs.arize.com/phoenix/tracing/llm-traces):
- Uses **OpenTelemetry (OTLP)** protocol standard
- Auto-instrumentation for popular frameworks (LlamaIndex, LangChain, OpenAI SDK)
- Captures:
  - Model calls with prompt/response
  - Retrieval steps with documents and scores
  - Tool invocations with inputs/outputs
  - Custom logic with timing

**Example trace hierarchy:**
```
Task: "Research auth libraries"  [trace_id=abc123, duration=145s]
  ├─ Span: agent.plan  [15s]
  ├─ Span: tool.web_search [query="oauth libraries python"]  [8s]
  ├─ Span: llm.call [model=gpt-4, tokens=450/230]  [3s]
  │   └─ Event: cache_hit [saved 120 tokens]
  ├─ Span: tool.web_fetch [url="..."]  [12s]
  └─ Span: agent.synthesize  [25s]
      └─ Span: llm.call [model=claude-sonnet, tokens=2100/890]  [22s]
```

### Recommendation

**Implement OpenTelemetry-based tracing:**

1. **Add tracing instrumentation to worker spawning:**
   ```python
   # In worker.py
   from opentelemetry import trace
   
   tracer = trace.get_tracer(__name__)
   
   async def spawn_worker(self, task, project_id, agent_type):
       with tracer.start_as_current_span(
           "worker.spawn",
           attributes={
               "task.id": task["id"],
               "project.id": project_id,
               "agent.type": agent_type,
           }
       ) as span:
           # ... existing spawn logic ...
           span.set_attribute("worker.id", worker_id)
           span.set_attribute("model.selected", chosen_model)
   ```

2. **Instrument OpenClaw Gateway calls:**
   - Propagate trace context via HTTP headers (`traceparent`)
   - Extract span IDs from session transcripts
   - Link worker spans to OpenClaw internal spans

3. **Add custom spans for key operations:**
   - Task scanning and routing
   - Model selection fallback chain
   - Prompt building
   - Result extraction
   - Git commit/push

4. **Export traces to a backend:**
   - **Short-term:** File-based OTLP exporter (JSON files in `logs/traces/`)
   - **Medium-term:** Phoenix (open-source, self-hosted)
   - **Long-term:** LangSmith or Arize (managed SaaS)

**Effort:** Medium (2-3 days)  
**Impact:** High — Unlocks detailed performance analysis and debugging

---

## Gap #2: Limited Agent Reasoning Visibility 🟡 HIGH

### What's Missing

**No decision path tracking** — Can't see:
- Why agent chose tool A over tool B
- What prompted a retry vs. giving up
- How the agent interpreted ambiguous instructions
- Which examples from memory influenced behavior

**No intermediate outputs** — Currently only track:
- Final summary (from `.work-summary` or session transcript)
- Commit SHAs and modified files
- Binary success/failure

**Missing:**
- Agent's plan before execution
- Tool selection rationale
- Partial results from failed attempts
- Self-correction reasoning ("I tried X, it failed, so now trying Y")

### Industry Standard: Structured Outputs & Metadata

**LangSmith approach:**
- Captures **full prompt** and **full response** for every LLM call
- Stores **tool inputs/outputs** as structured data
- Allows **annotations** on spans (human feedback, scores, tags)
- Supports **custom metadata** (e.g., `reasoning_type: "chain_of_thought"`)

**Phoenix approach:**
- **Embeddings visualization** — See which examples were retrieved
- **Prompt templates** — View the actual template + variables
- **LLM function calls** — Inspect tool selection and arguments
- **Evaluation scores** — Attach quality metrics to traces

### Example: What Good Reasoning Capture Looks Like

```json
{
  "trace_id": "task-abc123",
  "spans": [
    {
      "name": "agent.plan",
      "attributes": {
        "agent.goal": "Research auth libraries and recommend one",
        "agent.plan": [
          "Search for popular Python auth libraries",
          "Compare OAuth 2.0 support",
          "Check maintenance status",
          "Evaluate security track record"
        ],
        "reasoning": "User needs production-ready auth. Prioritizing security over ease-of-use."
      }
    },
    {
      "name": "tool.select",
      "attributes": {
        "tools.available": ["web_search", "web_fetch", "code_read"],
        "tool.selected": "web_search",
        "selection.reasoning": "Need broad overview before diving into specific library docs",
        "confidence": 0.85
      }
    },
    {
      "name": "agent.self_correct",
      "attributes": {
        "error": "Search returned outdated results",
        "correction": "Retry with date filter 'after:2024'",
        "reasoning": "Initial results from 2020, need current info"
      }
    }
  ]
}
```

### Recommendation

**Phase 1: Capture reasoning in structured metadata (low-hanging fruit)**

1. **Modify agent prompts to output reasoning:**
   ```python
   # In prompter.py
   system_prompt += """
   
   When making decisions, output your reasoning in this format:
   <reasoning>
   - Goal: [what you're trying to achieve]
   - Options: [available choices]
   - Selection: [your choice and why]
   </reasoning>
   """
   ```

2. **Extract reasoning from assistant responses:**
   ```python
   # New module: app/orchestrator/reasoning_extractor.py
   def extract_reasoning(assistant_text: str) -> dict:
       """Parse <reasoning> blocks from agent responses."""
       import re
       match = re.search(r'<reasoning>(.*?)</reasoning>', assistant_text, re.DOTALL)
       if match:
           return {"raw": match.group(1), "parsed": parse_reasoning_lines(match.group(1))}
       return {}
   ```

3. **Store reasoning in WorkerRun.task_log:**
   ```python
   task_log = {
       "model_router": model_audit,
       "reasoning": {
           "plan": extracted_plan,
           "tool_selections": extracted_tool_choices,
           "self_corrections": extracted_corrections
       }
   }
   ```

**Phase 2: Capture full tool I/O (requires OpenClaw changes)**

- Modify OpenClaw Gateway to expose tool calls in transcript
- Store tool name, arguments, result, and duration
- Link to parent span in distributed trace

**Effort:** Phase 1: Low (1 day), Phase 2: Medium (3-4 days, requires OpenClaw changes)  
**Impact:** High — Dramatically improves debugging failed tasks

---

## Gap #3: No Real-Time Observability Dashboard 🟡 HIGH

### What's Missing

**No visual monitoring** — Logs and DB tables exist, but no way to:
- See active workers at a glance
- Monitor queue depth over time
- Track success/failure rates by agent type
- Identify performance regressions (e.g., "researcher agent 20% slower this week")

**No alerting** — System creates inbox alerts for stuck tasks, but:
- No Slack/email notifications
- No anomaly detection (e.g., "failure rate jumped 3x")
- No SLO tracking (e.g., "95% of tasks complete within 5 minutes")

**No historical analysis** — Can query DB, but no:
- Time-series charts
- Aggregated metrics (p50/p95/p99 latency)
- Correlation analysis (e.g., "failures spike when using model X on project Y")

### Industry Standard: Observability Platforms

**LangSmith:**
- **Trace search & filtering** — Query by metadata, error status, latency, cost
- **Dashboards** — Custom charts for throughput, latency, cost, errors
- **Alerts** — Trigger on conditions (e.g., "error rate > 5%")
- **Comparison view** — Side-by-side trace comparison for debugging regressions

**Phoenix:**
- **Real-time trace viewer** — Live feed of incoming traces
- **Metrics page** — Latency histograms, token usage trends, error rates
- **Projects & sessions** — Group traces by application and conversation
- **Annotations** — Tag traces with human feedback for evaluation

**Grafana + Prometheus (self-hosted alternative):**
- Export OpenTelemetry metrics to Prometheus
- Build dashboards with Grafana
- Set up alerting rules
- Integrate with PagerDuty/Slack

### Recommendation

**Phase 1: Extend `/api/orchestrator/status` endpoint (quick win)**

Add detailed metrics to existing status endpoint:

```python
# In app/routers/orchestrator.py
@router.get("/status/detailed")
async def get_detailed_status(db: AsyncSession = Depends(get_db)):
    """Extended status with performance metrics."""
    
    # Current metrics (already tracked)
    active_workers = await get_active_worker_count(db)
    queue_depth = await get_eligible_task_count(db)
    
    # New metrics to add
    last_hour = datetime.now(timezone.utc) - timedelta(hours=1)
    recent_runs = await db.execute(
        select(WorkerRun).where(WorkerRun.ended_at >= last_hour)
    )
    runs = recent_runs.scalars().all()
    
    return {
        "active_workers": active_workers,
        "queue_depth": queue_depth,
        "last_hour": {
            "total_tasks": len(runs),
            "success_rate": sum(1 for r in runs if r.succeeded) / len(runs) if runs else 0,
            "avg_duration_seconds": sum((r.ended_at - r.started_at).total_seconds() for r in runs) / len(runs) if runs else 0,
            "total_cost_usd": sum(r.total_cost_usd or 0 for r in runs),
            "by_agent": _group_by_agent(runs),
            "by_model": _group_by_model(runs),
        },
        "provider_health": await get_provider_health_summary(db),
    }
```

**Phase 2: Build a simple dashboard (static HTML + Chart.js)**

Create `app/static/dashboard.html` served at `/dashboard`:

- Live metrics (refreshes every 10s via `/api/orchestrator/status/detailed`)
- Charts: Task throughput, success rate, avg duration, cost over time
- Active workers table with real-time progress
- Recent failures list with links to logs

**Effort:** Low-Medium (1-2 days per phase)

**Phase 3: Integrate with Phoenix (long-term)**

1. Deploy Phoenix (Docker container or self-hosted)
2. Export OpenTelemetry traces to Phoenix
3. Use Phoenix UI for trace visualization and analysis
4. Build custom dashboards in Phoenix for lobs-specific metrics

**Effort:** Medium (2-3 days setup + ongoing maintenance)  
**Impact:** High — Enables proactive monitoring and faster incident response

---

## Summary: Top 3 Gaps & Recommended Solutions

| Gap | Severity | Current Impact | Recommended Solution | Effort | ROI |
|-----|----------|----------------|---------------------|---------|-----|
| **1. No distributed tracing** | 🔴 Critical | Can't debug slow tasks or trace errors to root cause | Implement OpenTelemetry spans with trace_id/span_id hierarchy | Medium (2-3 days) | **Very High** — Unlocks detailed performance analysis |
| **2. Limited reasoning visibility** | 🟡 High | Can't understand why agents made specific decisions or failed | Extract reasoning blocks from responses + store tool I/O | Low-Med (1-4 days) | **High** — Dramatically improves debugging |
| **3. No real-time dashboard** | 🟡 High | Blind to system health, can't spot regressions quickly | Build `/dashboard` endpoint + Phoenix integration | Low-Med (1-5 days) | **High** — Enables proactive monitoring |

---

## Industry Patterns Summary

### Common Observability Features Across Platforms

| Feature | LangSmith | Phoenix | CrewAI | lobs-server |
|---------|-----------|---------|--------|-------------|
| **Distributed tracing** | ✅ Spans with parent-child | ✅ OpenTelemetry | ❌ | ❌ **Gap** |
| **Trace search & filtering** | ✅ Rich query UI | ✅ Metadata tags | ❌ | ❌ **Gap** |
| **Token usage tracking** | ✅ Per-span | ✅ Per-trace | ⚠️ Basic | ✅ **Good** |
| **Cost tracking** | ✅ Per-trace | ✅ Per-model | ❌ | ✅ **Good** |
| **Error traces** | ✅ Full context | ✅ Exception details | ⚠️ Basic | ⚠️ **Partial** |
| **Real-time dashboards** | ✅ | ✅ | ❌ | ❌ **Gap** |
| **Alerting** | ✅ Rules engine | ✅ Webhooks | ❌ | ⚠️ **Inbox only** |
| **Human feedback** | ✅ Annotations | ✅ Annotations | ❌ | ❌ **Gap** |
| **Session replay** | ✅ Step-by-step | ✅ Message history | ❌ | ⚠️ **Transcript only** |
| **Performance metrics** | ✅ p50/p95/p99 | ✅ Histograms | ❌ | ⚠️ **Basic avg only** |
| **Tool I/O capture** | ✅ Structured | ✅ Structured | ⚠️ Via logs | ❌ **Gap** |
| **Reasoning traces** | ⚠️ Via prompts | ⚠️ Via prompts | ❌ | ❌ **Gap** |

**Legend:**
- ✅ Full support
- ⚠️ Partial support or workarounds
- ❌ Not implemented

### Key Insights from Research

1. **OpenTelemetry is the industry standard** — Both LangSmith and Phoenix use OTLP as their trace protocol. This enables interoperability and vendor portability.

2. **Trace visualization is critical** — Flat logs aren't enough. Hierarchical span trees with timing breakdowns are essential for debugging complex agent behaviors.

3. **Metadata-driven filtering** — Successful platforms allow querying traces by custom tags (e.g., `agent_type=researcher`, `error=rate_limit`, `cost>$1.00`).

4. **Human feedback loops** — Production systems need a way to mark traces as "good" or "bad" and feed that signal back into evaluation pipelines.

5. **Cost is a first-class metric** — Token usage and cost tracking aren't optional — they're core observability signals for LLM applications.

---

## Next Steps

### Immediate Actions (Week 1)

1. ✅ **Document current state** (this document)
2. ⏭️ **Prototype OpenTelemetry integration**
   - Add `opentelemetry-api` and `opentelemetry-sdk` to `requirements.txt`
   - Instrument `worker.spawn_worker()` with a single span
   - Export traces to JSON files in `logs/traces/`
   - Validate trace structure

3. ⏭️ **Add reasoning extraction**
   - Update agent system prompts to include `<reasoning>` blocks
   - Implement `reasoning_extractor.py`
   - Store reasoning in `WorkerRun.task_log`

### Short-Term (Month 1)

4. ⏭️ **Build comprehensive tracing**
   - Instrument all major operations (task scanning, model selection, git operations)
   - Propagate trace context to OpenClaw Gateway
   - Link worker spans to sub-agent spans

5. ⏭️ **Deploy Phoenix**
   - Set up Phoenix Docker container
   - Configure OTLP exporter to send traces to Phoenix
   - Build initial dashboards

6. ⏭️ **Extend status API**
   - Add `/api/orchestrator/status/detailed` endpoint
   - Track performance metrics over time
   - Build simple HTML dashboard at `/dashboard`

### Long-Term (Quarter 1)

7. ⏭️ **Integrate human feedback**
   - Add UI for marking task outcomes as "good"/"bad"
   - Store feedback in `task_annotations` table
   - Use feedback for model selection and prompt tuning

8. ⏭️ **Advanced analytics**
   - Build custom Phoenix dashboards for lobs-specific metrics
   - Set up alerting for anomalies (failure spikes, cost overruns)
   - Implement SLO tracking (e.g., "95% of tasks complete within target duration")

---

## Appendix: Implementation Examples

### Example 1: OpenTelemetry Span Instrumentation

```python
# app/orchestrator/worker.py

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

tracer = trace.get_tracer("lobs.orchestrator.worker", version="1.0.0")

async def spawn_worker(self, task, project_id, agent_type):
    with tracer.start_as_current_span(
        "worker.spawn",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            "task.id": task["id"],
            "task.title": task.get("title", "")[:100],
            "project.id": project_id,
            "agent.type": agent_type,
        }
    ) as span:
        try:
            # Model selection
            with tracer.start_as_current_span("model.select") as model_span:
                choice = await chooser.choose(agent_type, task, purpose="execution")
                model_span.set_attribute("model.selected", choice.candidates[0])
                model_span.set_attribute("model.tier", choice.tier)
                model_span.set_attribute("fallback.available", len(choice.candidates))
            
            # Gateway spawn
            with tracer.start_as_current_span("gateway.spawn") as gateway_span:
                spawn_result = await self._spawn_session(...)
                gateway_span.set_attribute("gateway.run_id", spawn_result["runId"])
            
            span.set_attribute("worker.id", worker_id)
            span.set_attribute("spawn.success", True)
            span.set_status(Status(StatusCode.OK))
            
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise
```

### Example 2: Reasoning Extraction

```python
# app/orchestrator/reasoning_extractor.py

import re
from typing import Dict, List, Optional

def extract_reasoning(assistant_text: str) -> Dict[str, any]:
    """Extract structured reasoning from agent responses."""
    
    reasoning = {}
    
    # Extract plan
    plan_match = re.search(
        r'<plan>(.*?)</plan>',
        assistant_text,
        re.DOTALL | re.IGNORECASE
    )
    if plan_match:
        steps = [
            line.strip('- ').strip()
            for line in plan_match.group(1).strip().split('\n')
            if line.strip()
        ]
        reasoning['plan'] = steps
    
    # Extract tool selections
    tool_matches = re.finditer(
        r'<tool_select>(.*?)</tool_select>',
        assistant_text,
        re.DOTALL | re.IGNORECASE
    )
    tool_selections = []
    for match in tool_matches:
        lines = match.group(1).strip().split('\n')
        selection = {}
        for line in lines:
            if ':' in line:
                key, value = line.split(':', 1)
                selection[key.strip('- ').lower()] = value.strip()
        tool_selections.append(selection)
    if tool_selections:
        reasoning['tool_selections'] = tool_selections
    
    # Extract self-corrections
    correction_matches = re.finditer(
        r'<correction>(.*?)</correction>',
        assistant_text,
        re.DOTALL | re.IGNORECASE
    )
    corrections = [match.group(1).strip() for match in correction_matches]
    if corrections:
        reasoning['self_corrections'] = corrections
    
    return reasoning


# Usage in worker.py
def _handle_worker_completion(...):
    # ... existing code ...
    
    # Extract reasoning from session transcript
    if result_summary:
        from app.orchestrator.reasoning_extractor import extract_reasoning
        reasoning = extract_reasoning(result_summary)
        if reasoning:
            task_log["reasoning"] = reasoning
    
    # Store in WorkerRun
    run = WorkerRun(
        ...,
        task_log=task_log,
    )
```

### Example 3: Enhanced Status Endpoint

```python
# app/routers/orchestrator.py

from datetime import datetime, timedelta, timezone
from collections import defaultdict

@router.get("/status/metrics")
async def get_orchestrator_metrics(
    window_hours: int = 1,
    db: AsyncSession = Depends(get_db)
):
    """Get detailed performance metrics over a time window."""
    
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    
    # Fetch recent worker runs
    result = await db.execute(
        select(WorkerRun)
        .where(WorkerRun.ended_at >= cutoff)
        .order_by(WorkerRun.ended_at.desc())
    )
    runs = result.scalars().all()
    
    if not runs:
        return {"message": "No data in time window", "window_hours": window_hours}
    
    # Calculate metrics
    total = len(runs)
    succeeded = sum(1 for r in runs if r.succeeded)
    failed = total - succeeded
    
    durations = [
        (r.ended_at - r.started_at).total_seconds()
        for r in runs
        if r.started_at and r.ended_at
    ]
    durations.sort()
    
    costs = [r.total_cost_usd for r in runs if r.total_cost_usd]
    
    # Group by agent type
    by_agent = defaultdict(lambda: {"total": 0, "succeeded": 0, "failed": 0})
    for r in runs:
        task = await db.get(Task, r.task_id)
        if task and task.agent:
            by_agent[task.agent]["total"] += 1
            if r.succeeded:
                by_agent[task.agent]["succeeded"] += 1
            else:
                by_agent[task.agent]["failed"] += 1
    
    # Group by model
    by_model = defaultdict(lambda: {"count": 0, "cost": 0.0})
    for r in runs:
        if r.model:
            by_model[r.model]["count"] += 1
            by_model[r.model]["cost"] += r.total_cost_usd or 0.0
    
    return {
        "window_hours": window_hours,
        "window_start": cutoff.isoformat(),
        "window_end": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_tasks": total,
            "succeeded": succeeded,
            "failed": failed,
            "success_rate": round(succeeded / total * 100, 1) if total > 0 else 0,
        },
        "performance": {
            "duration_p50": durations[len(durations)//2] if durations else 0,
            "duration_p95": durations[int(len(durations)*0.95)] if durations else 0,
            "duration_p99": durations[int(len(durations)*0.99)] if durations else 0,
            "duration_avg": sum(durations) / len(durations) if durations else 0,
            "duration_max": max(durations) if durations else 0,
        },
        "cost": {
            "total_usd": sum(costs),
            "avg_per_task": sum(costs) / len(costs) if costs else 0,
            "max_per_task": max(costs) if costs else 0,
        },
        "by_agent": dict(by_agent),
        "by_model": dict(by_model),
    }
```

---

## Sources

- **LangSmith Observability Docs:** https://docs.smith.langchain.com/observability
- **Phoenix Tracing Overview:** https://docs.arize.com/phoenix/tracing/llm-traces
- **LangChain Agent Concepts:** https://docs.langchain.com/oss/python/langchain/overview
- **CrewAI Tools Documentation:** https://docs.crewai.com/concepts/tools
- **OpenTelemetry Specification:** https://opentelemetry.io/docs/specs/otel/
- **lobs-server codebase analysis:** `app/orchestrator/`, `app/models.py`, `app/logging_config.py`

---

**End of Report**
