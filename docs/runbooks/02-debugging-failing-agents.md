# How to Debug a Failing Agent

**Purpose:** Use observability system to diagnose and fix agent failures

**Related:** [ADR 0005: Observability Architecture](../decisions/0005-observability-architecture.md), [Observability Implementation](../architecture/observability-implementation-design.md)

---

## Quick Reference

```bash
# Find task by ID
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/task-abc123

# Get trace ID from task
trace_id=$(curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/task-abc123 | jq -r '.trace_id')

# Find all logs for trace
jq "select(.trace_id == \"$trace_id\")" logs/server.log

# Get worker transcript
cat ~/lobs-control/state/transcripts/task-abc123.json

# Check orchestrator status
curl http://localhost:8000/api/orchestrator/status
```

---

## Failure Types and Symptoms

| Symptom | Likely Cause | Where to Look |
|---------|--------------|---------------|
| Task stuck in "running" forever | Worker timeout, agent hung | Worker logs, session status |
| Task failed immediately | Worker spawn error, bad prompt | Engine logs, spawn errors |
| Task completed but wrong result | Agent misunderstood prompt | Transcript, prompt template |
| Handoff not created | Agent didn't write handoff JSON | Transcript, workspace files |
| Escalation loop | Reflection not fixing issue | Escalation chain, reflection prompts |

---

## Step-by-Step Debugging

### Step 1: Identify the Failed Task

```bash
# List recent failed tasks
curl -H "Authorization: Bearer $TOKEN" \
  'http://localhost:8000/api/tasks?work_state=failed&limit=10'

# Or check specific task
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/task-abc123
```

**Look for:**
- `work_state` — Should be "failed"
- `error_message` — High-level error description
- `trace_id` — Correlation ID for logs
- `worker_id` — OpenClaw session ID
- `updated_at` — When it failed

### Step 2: Get the Trace ID

Every task has a `trace_id` that links all related logs, metrics, and events.

```bash
# Extract trace_id from task
TRACE_ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/task-abc123 | jq -r '.trace_id')

echo "Trace ID: $TRACE_ID"
```

### Step 3: Find All Related Logs

**JSON logs (production):**
```bash
# All events for this trace
jq "select(.trace_id == \"$TRACE_ID\")" logs/server.log

# Just errors
jq "select(.trace_id == \"$TRACE_ID\" and .level == \"ERROR\")" logs/server.log

# Timeline view (sorted by timestamp)
jq "select(.trace_id == \"$TRACE_ID\") | [.timestamp, .event_type, .message]" logs/server.log | jq -s 'sort_by(.[0])'
```

**Console logs (development):**
```bash
# Grep for task ID
grep "task-abc123" logs/console.log

# Grep for trace ID
grep "$TRACE_ID" logs/console.log
```

### Step 4: Inspect Worker Transcript

If the worker spawned, check what the agent actually did:

```bash
# Find transcript
TASK_ID_SHORT=$(echo "task-abc123" | cut -d'-' -f2)
TRANSCRIPT=~/lobs-control/state/transcripts/task-$TASK_ID_SHORT.json

# View full transcript
cat $TRANSCRIPT | jq .

# Extract just agent messages
cat $TRANSCRIPT | jq '.transcript[] | select(.role == "assistant")'

# Check tool calls
cat $TRANSCRIPT | jq '.transcript[] | select(.type == "tool_call")'
```

**What to look for:**
- Did agent understand the prompt?
- Did agent make progress before failing?
- What was the last tool call?
- Is there an error message in the transcript?

### Step 5: Check Orchestrator State

```bash
# Orchestrator status
curl http://localhost:8000/api/orchestrator/status

# Active workers
curl http://localhost:8000/api/orchestrator/workers

# Recent failures
curl http://localhost:8000/api/orchestrator/failures?limit=10
```

**Key metrics:**
- `active_workers` — Currently running workers
- `failed_last_hour` — Recent failure rate
- `circuit_breaker_state` — Are providers healthy?

---

## Common Failure Scenarios

### Scenario 1: Worker Spawn Failed

**Symptoms:**
- Task failed immediately (< 5 seconds)
- No transcript file
- Error: "Failed to spawn worker"

**Debug steps:**

1. **Check gateway logs:**
```bash
grep "spawn" logs/server.log | grep "$TRACE_ID"
```

2. **Look for spawn error:**
```json
{
  "event_type": "worker.spawn_failed",
  "error": "Gateway timeout",
  "trace_id": "..."
}
```

3. **Common causes:**
   - Gateway not running: `openclaw gateway status`
   - Model tier unavailable: Check `model_tier` in logs
   - Rate limit: Check `circuit_breaker_state`
   - Invalid config: Check `agents_config.json`

**Fix:**
```bash
# Restart gateway
openclaw gateway restart

# Check model availability
curl http://localhost:8000/api/orchestrator/models

# Retry task
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/task-abc123/retry
```

### Scenario 2: Agent Timeout

**Symptoms:**
- Task ran for timeout duration (30-60 minutes)
- Transcript exists but incomplete
- Error: "Worker timeout"

**Debug steps:**

1. **Check timeout setting:**
```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/task-abc123 | jq '.timeout_minutes'
```

2. **Check transcript progress:**
```bash
# Last agent message
cat $TRANSCRIPT | jq '.transcript[-1]'

# Count tool calls
cat $TRANSCRIPT | jq '[.transcript[] | select(.type == "tool_call")] | length'
```

3. **Determine if agent was stuck or just slow:**
   - **Stuck:** Same message repeated, no progress
   - **Slow:** Making progress but task too complex

**Fix:**

```bash
# If stuck: review prompt, may need clearer instructions
# If slow: increase timeout
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"timeout_minutes": 90}' \
  http://localhost:8000/api/tasks/task-abc123

# Retry
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/task-abc123/retry
```

### Scenario 3: Agent Misunderstood Task

**Symptoms:**
- Task completed successfully
- Result is wrong or incomplete
- Handoffs missing or incorrect

**Debug steps:**

1. **Review the prompt:**
```bash
# Find prompt in logs
jq "select(.trace_id == \"$TRACE_ID\" and .event_type == \"worker.prompt_generated\")" logs/server.log
```

2. **Compare prompt to agent output:**
   - Did agent address acceptance criteria?
   - Did agent follow instructions?
   - Was prompt ambiguous?

3. **Check agent understanding:**
```bash
# First agent message shows understanding
cat $TRANSCRIPT | jq '.transcript[1]'  # Agent's first response
```

**Fix:**

- **Improve prompt clarity:** Edit `app/orchestrator/prompter.py`
- **Add examples:** Include example output in prompt
- **Adjust acceptance criteria:** Make success criteria more specific

### Scenario 4: Handoff Not Created

**Symptoms:**
- Original task completed
- No follow-up task created
- Agent mentioned handoff in transcript

**Debug steps:**

1. **Check handoff in transcript:**
```bash
cat $TRANSCRIPT | jq '.result.handoff'
```

2. **Check worker result parsing:**
```bash
jq "select(.trace_id == \"$TRACE_ID\" and .event_type == \"worker.result_parsed\")" logs/server.log
```

3. **Check handoff creation:**
```bash
jq "select(.trace_id == \"$TRACE_ID\" and .event_type == \"task.handoff_created\")" logs/server.log
```

**Common causes:**
- Agent wrote handoff to wrong location (not in result JSON)
- Handoff parser failed (invalid JSON)
- Router didn't process handoff (see router logs)

**Fix:**

```bash
# Manually create handoff task
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Follow-up task",
    "description": "Continued from task-abc123",
    "agent_type": "target-agent",
    "work_state": "queued",
    "parent_task_id": "task-abc123"
  }' \
  http://localhost:8000/api/tasks
```

### Scenario 5: Escalation Loop

**Symptoms:**
- Task fails repeatedly
- Multiple escalation tasks created
- Reflection doesn't fix the issue

**Debug steps:**

1. **Check escalation chain:**
```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/task-abc123/escalations
```

2. **Review reflection prompts and results:**
```bash
# Find reflection task
REFLECTION_TASK=$(curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/task-abc123/escalations | jq -r '.[0].id')

# Check reflection transcript
cat ~/lobs-control/state/transcripts/task-$REFLECTION_TASK.json
```

3. **Look for root cause:**
   - Is the task impossible to complete?
   - Is the agent lacking required context?
   - Is there a system/environment issue?

**Fix:**

- **Stop escalation loop:** Mark task as "blocked" manually
- **Add context:** Update task description with missing info
- **Change agent:** Delegate to different agent type
- **Fix environment:** Address system issue (missing file, API down, etc.)

```bash
# Mark as blocked
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"work_state": "blocked", "blocked_reason": "Missing required API key"}' \
  http://localhost:8000/api/tasks/task-abc123
```

---

## Using Observability Metrics

### Check System Health

```bash
# Prometheus metrics endpoint
curl http://localhost:8000/metrics | grep lobs_

# Task metrics
curl http://localhost:8000/metrics | grep lobs_tasks_total
curl http://localhost:8000/metrics | grep lobs_task_duration

# Worker metrics
curl http://localhost:8000/metrics | grep lobs_worker
curl http://localhost:8000/metrics | grep lobs_active_workers

# Circuit breaker status
curl http://localhost:8000/metrics | grep lobs_circuit_breaker
```

### Visualize with Grafana (if configured)

```bash
# Start Prometheus + Grafana (if not running)
docker-compose -f ops/docker-compose.yml up -d

# Access Grafana
open http://localhost:3000

# Import dashboard
# → Import → Upload docs/grafana/lobs-server-dashboard.json
```

---

## Advanced Debugging

### Trace Full Lifecycle

**Generate trace diagram:**

```python
# bin/trace_visualizer.py
import json
import sys

trace_id = sys.argv[1]

# Load all logs for trace
with open("logs/server.log") as f:
    events = [
        json.loads(line)
        for line in f
        if json.loads(line).get("trace_id") == trace_id
    ]

# Build timeline
for event in sorted(events, key=lambda e: e["timestamp"]):
    print(f"{event['timestamp']} | {event['event_type']:30s} | {event['message']}")
```

**Run:**
```bash
python bin/trace_visualizer.py $TRACE_ID
```

### Query Database Directly

```bash
# Open database
sqlite3 lobs.db

# Find task
sqlite> SELECT id, title, work_state, error_message, trace_id 
        FROM tasks 
        WHERE id = 'task-abc123';

# Find all tasks in trace
sqlite> SELECT id, title, work_state, parent_task_id
        FROM tasks
        WHERE trace_id = 'a1b2c3d4-...';

# Find escalations
sqlite> SELECT * FROM escalations WHERE task_id = 'task-abc123';
```

### Compare with Similar Successful Task

```bash
# Find recent successful tasks for same agent
curl -H "Authorization: Bearer $TOKEN" \
  'http://localhost:8000/api/tasks?agent_type=programmer&work_state=completed&limit=5'

# Compare transcripts
diff <(cat ~/lobs-control/state/transcripts/task-success.json | jq .) \
     <(cat ~/lobs-control/state/transcripts/task-failed.json | jq .)
```

---

## Preventive Measures

### Add Better Logging

```python
# app/orchestrator/worker.py
from app.observability.logging import log_worker_event

log_worker_event(
    logger, logging.INFO, "spawn_started",
    worker_id=worker_id,
    task_id=task["id"],
    agent_type=agent_type,
    model=model,
)
```

### Add Metrics

```python
from app.observability.metrics import observe_worker_spawned

observe_worker_spawned(agent_type, model_tier, spawn_duration)
```

### Write Regression Test

```python
# tests/test_regression_abc123.py
async def test_task_abc123_scenario():
    """Regression test for task-abc123 failure."""
    # Recreate conditions that caused failure
    task = {...}
    result = await process_task(task)
    
    # Verify fix works
    assert result.status == "completed"
```

---

## Escalation Path

If you can't resolve the failure:

1. **Collect debug bundle:**
```bash
# Create debug archive
tar czf debug-task-abc123.tar.gz \
  ~/lobs-control/state/transcripts/task-abc123.json \
  <(jq "select(.trace_id == \"$TRACE_ID\")" logs/server.log) \
  <(curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/tasks/task-abc123)
```

2. **File issue:**
   - Include debug bundle
   - Include trace ID
   - Include reproduction steps
   - Tag as `bug` / `agent-failure`

3. **Temporary workaround:**
   - Mark task as blocked
   - Create manual task for human intervention
   - Document in task notes

---

## Checklist

When debugging agent failure:

- [ ] Got trace_id from task
- [ ] Found all logs for trace
- [ ] Checked worker transcript (if exists)
- [ ] Identified failure type
- [ ] Checked circuit breaker status
- [ ] Reviewed prompt clarity
- [ ] Checked for missing context
- [ ] Verified escalation chain
- [ ] Attempted retry with fixes
- [ ] Documented root cause
- [ ] Added regression test (if applicable)

---

## Examples

### Example 1: Timeout Due to Large Refactor

**Symptoms:** Task "Refactor authentication" timed out after 60 minutes.

**Investigation:**
```bash
# Check transcript
cat ~/lobs-control/state/transcripts/task-auth-refactor.json | jq '.transcript | length'
# → 847 messages (agent made lots of progress)

# Last message
cat ~/lobs-control/state/transcripts/task-auth-refactor.json | jq '.transcript[-1]'
# → Agent was still working, not stuck
```

**Root cause:** Task too large, needed 90+ minutes.

**Fix:**
```bash
# Increase timeout and retry
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -d '{"timeout_minutes": 120}' \
  http://localhost:8000/api/tasks/task-auth-refactor

curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/task-auth-refactor/retry
```

### Example 2: Spawn Failure Due to Model Unavailable

**Symptoms:** Task failed immediately with "Model not available."

**Investigation:**
```bash
# Check circuit breaker
curl http://localhost:8000/metrics | grep circuit_breaker_state
# → lobs_circuit_breaker_state{provider="ollama"} 1  (open)
```

**Root cause:** Ollama service down.

**Fix:**
```bash
# Restart Ollama
ollama serve &

# Wait for circuit breaker to close (or manually close)
# Then retry
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/task-xyz/retry
```

---

## Related Runbooks

- **[Reading Observability Metrics](03-reading-observability-metrics.md)** — Understanding metrics
- **[Handoff Failure Debugging](04-handoff-failure-debugging.md)** — Handoff-specific issues
- **[Memory Search Troubleshooting](05-memory-search-troubleshooting.md)** — Memory system issues
