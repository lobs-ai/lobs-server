# Multi-Agent Operations Runbook

**Purpose:** Operational guide for managing multi-agent task orchestration, covering common failure modes, recovery procedures, and system tuning.

**Last Updated:** 2026-02-22

---

## Quick Reference

```bash
# Check orchestrator status
curl http://localhost:8000/api/orchestrator/status

# List active workers
curl http://localhost:8000/api/orchestrator/workers

# View circuit breaker state
curl http://localhost:8000/metrics | grep circuit_breaker

# Check reflection cycle status
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/reflections?status=pending

# Manual memory sync
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/memories/sync

# Kill stuck worker
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/workers/{worker_id}/kill
```

---

## Table of Contents

1. [Handoff Failures](#handoff-failures)
2. [Memory Issues](#memory-issues)
3. [Permission Errors](#permission-errors)
4. [Model Selection Problems](#model-selection-problems)
5. [Reflection Cycle Issues](#reflection-cycle-issues)
6. [Worker Stuck/OOM Recovery](#worker-stuckoom-recovery)
7. [Circuit Breaker Management](#circuit-breaker-management)
8. [Escalation Loop Prevention](#escalation-loop-prevention)

---

## Handoff Failures

### Overview

Handoffs enable multi-step workflows (Researcher → Architect → Programmer). Failures occur when:
- Agent creates malformed handoff JSON
- Orchestrator fails to parse handoff
- Target agent doesn't exist
- Circular handoffs create infinite loops

### Symptoms

- Task completes but expected follow-up task never appears
- Orchestrator logs: `[HANDOFF] Invalid handoff in {project}`
- `.handoffs/*.json` files remain in project directory (not cleaned up)
- Inbox item created: "Handoff parsing failed"

### Diagnosis

**Step 1: Check for handoff files**

```bash
cd /path/to/project
ls -la .handoffs/
```

If files exist after task completion, orchestrator failed to process them.

**Step 2: Validate handoff JSON**

```bash
cat .handoffs/*.json | jq .
```

**Common errors:**
- Invalid JSON syntax (missing comma, unclosed bracket)
- Missing required fields (`to`, `initiative`, `title`)
- Invalid agent type (typo: `programer` instead of `programmer`)
- Invalid initiative format (must be kebab-case: `user-auth` not `User Auth`)

**Step 3: Check orchestrator logs**

```bash
grep "HANDOFF" logs/server.log | tail -20
```

Look for:
- `Invalid handoff in {project}` — parsing error
- `Unknown agent type: {agent}` — invalid target agent
- `Handoff validation failed` — schema validation error

### Recovery

**Option 1: Fix and re-process (if handoff still exists)**

```bash
# Fix the JSON
vim .handoffs/abc-123.json

# Trigger task completion webhook manually (if needed)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/{task_id}/complete
```

**Option 2: Manually create follow-up task**

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Implement feature X",
    "description": "Follow-up from task {parent_id}",
    "agent_type": "programmer",
    "work_state": "todo",
    "initiative_id": "{initiative_id}",
    "parent_task_id": "{parent_id}"
  }' \
  http://localhost:8000/api/tasks
```

**Option 3: Clean up and ignore**

```bash
# Remove invalid handoff
rm .handoffs/abc-123.json

# Document decision in parent task
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -d '{"notes": "Handoff failed, handled manually"}' \
  http://localhost:8000/api/tasks/{parent_id}
```

### Prevention

**Validate handoffs in agent prompt:**

Add to `app/orchestrator/prompter.py`:

```python
handoff_schema = {
  "to": "string (required) - programmer|researcher|architect|writer|reviewer",
  "initiative": "string (required) - kebab-case slug",
  "title": "string (required) - specific task title",
  "context": "string (optional) - background info",
  "acceptance": "string (optional) - definition of done",
  "files": "array (optional) - relevant file paths"
}
```

**Add schema validation:**

```python
# app/orchestrator/engine.py
from jsonschema import validate, ValidationError

HANDOFF_SCHEMA = {
    "type": "object",
    "required": ["to", "initiative", "title"],
    "properties": {
        "to": {"type": "string", "enum": VALID_AGENT_TYPES},
        "initiative": {"type": "string", "pattern": "^[a-z0-9-]+$"},
        "title": {"type": "string", "minLength": 5},
        "context": {"type": "string"},
        "acceptance": {"type": "string"},
        "files": {"type": "array", "items": {"type": "string"}}
    }
}

try:
    validate(handoff_data, HANDOFF_SCHEMA)
except ValidationError as e:
    logger.error(f"[HANDOFF] Validation failed: {e.message}")
    # Create inbox item for manual review
```

---

## Memory Issues

### Overview

The memory system has two layers:
- **MEMORY.md** — Curated knowledge (loaded into agent context)
- **memory/** — Daily logs and topic files (searchable, not auto-loaded)

Common issues:
- Memory sync failures (filesystem ↔ database out of sync)
- Search returns no/wrong results
- MEMORY.md bloat (>1000 lines)
- Concurrent write conflicts
- Curation loops (curator spawns repeatedly)

### Memory Sync Failures

**Symptoms:**
- Agent reports "can't find previous notes" but files exist on disk
- API search (`/api/memories/search`) returns no results
- Dashboard shows outdated memory content

**Diagnosis:**

```bash
# Check filesystem
ls ~/.openclaw/workspace-{agent}/memory/

# Check database
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/memories?agent={agent} | jq '.[] | {id, title, updated_at}'

# Compare timestamps
stat ~/.openclaw/workspace-{agent}/MEMORY.md
```

**Recovery:**

```bash
# Trigger manual sync
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -d '{"agent": "{agent}"}' \
  http://localhost:8000/api/memories/sync

# Verify sync
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/memories?agent={agent}&limit=5
```

**If sync still fails, check logs:**

```bash
grep "memory_sync" logs/server.log | tail -20
```

Common causes:
- File permissions (agent can't read workspace)
- Database locked (concurrent sync attempts)
- Invalid UTF-8 in memory files
- File too large (>1MB)

### Memory Search Returns Wrong Results

**Symptoms:**
- Agent searches for "authentication patterns" but gets unrelated results
- Search returns empty when relevant memories exist

**Diagnosis:**

```bash
# Test search manually
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/memories/search?query=authentication&agent=programmer"

# Check if memory files contain expected keywords
grep -r "authentication" ~/.openclaw/workspace-programmer/memory/
```

**Root causes:**

1. **Memory not synced** → See "Memory Sync Failures" above
2. **Poor memory quality** → Vague titles, no keywords, buried information
3. **Search is too literal** → Full-text search, not semantic (vector search planned)

**Improve searchability:**

Edit memory files to include:

```markdown
# Authentication Patterns

## Keywords
authentication, auth, JWT, token, middleware, permissions

## Problem
How to implement secure auth in FastAPI

## Solution
Use dependency injection for token validation...
```

### MEMORY.md Bloat

**Symptoms:**
- MEMORY.md exceeds 500-1000 lines
- Agent context window warnings
- Slow agent spawn times
- Redundant or stale information

**Diagnosis:**

```bash
wc -l ~/.openclaw/workspace-{agent}/MEMORY.md
# → 1234 lines (too large)

# Check for duplication
sort ~/.openclaw/workspace-{agent}/MEMORY.md | uniq -d
```

**Recovery:**

**Option 1: Trigger automatic curation**

System auto-curates when MEMORY.md > 500 lines during daily maintenance. Force it:

```bash
# Run memory maintenance manually
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/maintenance/memory
```

This spawns a curator worker to:
- Keep high-value information (patterns, decisions, gotchas)
- Remove verbose examples, stale info, duplicates
- Preserve "Shared Context" section
- Compress to ~300-500 lines

**Option 2: Manual curation**

```bash
cd ~/.openclaw/workspace-{agent}

# Backup
cp MEMORY.md MEMORY.md.backup

# Edit
vim MEMORY.md

# Remove:
# - Historical session logs (move to memory/YYYY-MM-DD.md)
# - Verbose tutorials (summarize to bullets)
# - Duplicate info (already in Shared Context)
# - Outdated/stale information

# Sync to database
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -d '{"agent": "{agent}"}' \
  http://localhost:8000/api/memories/sync
```

### Concurrent Write Conflicts

**Symptoms:**
- Error: "database locked" when syncing memories
- Lost memory updates (agent writes but changes don't persist)
- Corrupted MEMORY.md (mixed content from multiple writers)

**Diagnosis:**

```bash
# Check for concurrent memory operations
grep "memory_sync.*locked" logs/server.log

# Check for multiple agents running
curl http://localhost:8000/api/orchestrator/workers | jq '.[] | select(.agent_type == "{agent}")'
```

**Prevention:**

Memory sync uses file-based locking:

```python
# app/services/memory_sync.py
async with aiofiles.open("/tmp/memory_sync_{agent}.lock", "w") as f:
    await f.write(str(os.getpid()))
    # ... sync logic
```

If lock file exists, sync waits or fails. Check for stale locks:

```bash
ls -la /tmp/memory_sync_*.lock

# Remove stale locks (no process running)
rm /tmp/memory_sync_programmer.lock
```

### Curation Loops

**Symptoms:**
- Curator tasks spawn repeatedly (every few minutes)
- MEMORY.md keeps getting rewritten
- Curator makes minimal changes each run

**Diagnosis:**

```bash
# Check recent curator tasks
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/tasks?agent_type=curator&limit=10"

# Check MEMORY.md size
wc -l ~/.openclaw/workspace-{agent}/MEMORY.md
```

**Root cause:**
- Curator doesn't compress enough (output still >500 lines)
- Maintenance runs too frequently
- Curator adds new content instead of removing bloat

**Recovery:**

```bash
# Disable automatic curation temporarily
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -d '{"key": "memory_curation_enabled", "value": false}' \
  http://localhost:8000/api/orchestrator/settings

# Manually curate MEMORY.md to <300 lines
vim ~/.openclaw/workspace-{agent}/MEMORY.md

# Re-enable curation
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -d '{"key": "memory_curation_enabled", "value": true}' \
  http://localhost:8000/api/orchestrator/settings
```

**Fix curator prompt:**

Update `app/orchestrator/memory_maintenance.py` curator prompt:

```python
prompt = f"""
You are curating MEMORY.md for {agent} agent.

GOAL: Compress to <300 lines while keeping ALL genuinely important information.

KEEP:
- Architecture decisions affecting daily work
- Critical gotcas and lessons (prevent bugs)
- Active project context
- User preferences
- Patterns used regularly

REMOVE:
- Historical session logs → already in memory/YYYY-MM-DD.md
- Verbose examples → summarize, link to memory/ files
- Duplicate information → already in Shared Context
- Stale/outdated information

PRESERVE:
- "Shared Context" section EXACTLY as-is (do not edit)

Be AGGRESSIVE in removing low-signal content. Length matters.
"""
```

---

## Permission Errors

### Overview

Agents have role-based tool access (ADR-0012). Violations occur when:
- Agent tries to use forbidden tool (writer running `exec`)
- Agent writes to restricted path (researcher modifying code)
- File permissions prevent read/write
- Git operations attempted (only orchestrator allowed)

### Tool Access Violations

**Symptoms:**
- Error: "Tool 'exec' not available for agent type 'writer'"
- Error: "Permission denied: write access restricted"
- Agent task fails immediately with permission error

**Diagnosis:**

Check agent tool policy:

```python
# app/orchestrator/registry.py
TOOL_POLICIES = {
    "programmer": ["read", "write", "edit", "exec", "browser", "web_search", "web_fetch"],
    "researcher": ["read", "exec", "browser", "web_search", "web_fetch"],  # No write
    "architect": ["read", "write", "edit", "exec", "browser", "web_search", "web_fetch"],
    "writer": ["read", "write", "edit", "browser", "web_search", "web_fetch"],  # No exec
    "reviewer": ["read", "exec", "browser", "web_search", "web_fetch"],  # No write
}
```

Check worker logs:

```bash
grep "permission\|forbidden\|denied" ~/lobs-control/state/transcripts/task-{id}.json
```

**Recovery:**

**Option 1: Re-assign to correct agent**

```bash
# Task requires exec (tests) but assigned to writer
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -d '{"agent_type": "programmer", "work_state": "todo"}' \
  http://localhost:8000/api/tasks/{task_id}
```

**Option 2: Grant temporary access (use sparingly)**

```bash
# Allow writer to run read-only commands (ls, grep)
# Edit app/orchestrator/worker.py to add exec with constraints:

EXEC_READONLY_COMMANDS = ["ls", "grep", "rg", "find", "cat", "head", "tail", "tree", "wc"]

def validate_exec(agent_type, command):
    if agent_type == "writer":
        cmd = command.split()[0]
        if cmd not in EXEC_READONLY_COMMANDS:
            raise PermissionError(f"exec forbidden for {agent_type}")
```

**Option 3: Fix task prompt**

If task requires forbidden tool, the prompt is wrong:

```bash
# Original: "Write docs and run tests"  (writer can't run tests)
# Fixed: "Write docs for test suite"    (no execution needed)
```

### File Permission Errors

**Symptoms:**
- Error: "Permission denied: cannot write to /path/to/file"
- Error: "EACCES: permission denied, open '/file'"
- Agent can read but not write expected files

**Diagnosis:**

```bash
# Check file ownership
ls -la /path/to/file

# Check agent workspace ownership
ls -la ~/.openclaw/workspace-{agent}/

# Check project directory permissions
ls -la /path/to/project/
```

**Common causes:**
1. File owned by different user (root, another user)
2. Read-only file system mount
3. macOS/Linux permissions (chmod 644 vs 755)
4. SELinux/AppArmor restrictions

**Recovery:**

```bash
# Fix ownership
sudo chown -R $USER:$USER ~/.openclaw/workspace-{agent}

# Fix permissions
chmod -R u+rw ~/.openclaw/workspace-{agent}
chmod -R u+rw /path/to/project/

# Verify
ls -la ~/.openclaw/workspace-{agent}/
```

### Git Operation Attempts

**Symptoms:**
- Agent tries to run `git commit` or `git push`
- Error: "git operations forbidden for agents"

**Why forbidden:**
- Orchestrator handles all git operations (consistency)
- Agents work in isolation, orchestrator commits atomically
- Prevents accidental pushes to wrong branches

**Recovery:**

Agents should NEVER call git directly. If task requires git:

```bash
# Task: "Commit changes to branch feature-x"
# Fix: Orchestrator handles commits automatically after task completion
# Agent should just write files, orchestrator commits them
```

If git operation is truly needed (rare):

```bash
# Orchestrator can run git via engine.py
# See: app/orchestrator/git_manager.py
```

---

## Model Selection Problems

### Overview

5-tier model routing (micro/small/medium/standard/strong) with fallback chains. Issues:
- Wrong tier selected (too expensive or too weak)
- All models in tier unavailable (fallback exhausted)
- Ollama models not discovered (local-first fails)
- Cost spike (using strong tier for simple tasks)

### Wrong Model Tier Selected

**Symptoms:**
- Simple task uses expensive model (Claude Opus for code formatting)
- Complex task uses weak model (Ollama 3B for architecture design)
- Task fails with "model couldn't understand instructions"

**Diagnosis:**

```bash
# Check which model was used
grep "model.*selected" logs/server.log | grep {task_id}

# Check task tier assignment
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/{task_id} | jq '.model_tier'
```

**Model tier defaults:**

```python
# app/orchestrator/model_router.py
DEFAULT_TIERS = {
    "inbox_triage": "small",           # Simple classification
    "code_review": "medium",           # Moderate complexity
    "implementation": "medium",        # Feature work
    "architecture": "standard",        # Complex reasoning
    "research": "standard",            # Novel problems
    "debugging_critical": "strong",    # Hard problems
}
```

**Override tier for specific task:**

```bash
# Force specific tier
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -d '{"model_tier": "standard"}' \
  http://localhost:8000/api/tasks/{task_id}

# Retry with new tier
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/{task_id}/retry
```

**Tune tier selection:**

Edit `app/orchestrator/model_chooser.py`:

```python
def infer_tier(task: dict, agent_type: str) -> str:
    """Infer model tier from task characteristics."""
    
    # Simple tasks → micro/small
    if task.get("task_type") == "inbox_triage":
        return "small"
    
    # Complex reasoning → standard/strong
    if any(keyword in task.get("title", "").lower() 
           for keyword in ["architecture", "design", "debug", "investigate"]):
        return "standard"
    
    # Code tasks → medium (balance cost/capability)
    if agent_type == "programmer":
        return "medium"
    
    # Default
    return "medium"
```

### All Models Unavailable (Fallback Exhausted)

**Symptoms:**
- Error: "All models failed" or "FailoverError"
- Circuit breaker opens
- Tasks stuck in "not_started" state

**Diagnosis:**

```bash
# Check circuit breaker state
curl http://localhost:8000/metrics | grep circuit_breaker_state

# Check provider health
curl http://localhost:8000/api/orchestrator/providers

# Check model availability
curl http://localhost:8000/api/orchestrator/models
```

**Common causes:**

1. **API rate limits** — Too many requests, all providers rate-limited
2. **Ollama down** — Local models unavailable, cloud fallback also failing
3. **API key missing** — No valid credentials for any provider
4. **Network issues** — Can't reach model providers

**Recovery:**

```bash
# Check Ollama
curl http://localhost:11434/api/tags

# Start Ollama if down
ollama serve &

# Verify Ollama models
ollama list

# Check API keys
env | grep -E "ANTHROPIC_API_KEY|OPENAI_API_KEY"

# Wait for circuit breaker cooldown (5 minutes default)
# Or manually close circuit:
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/circuit-breaker/reset
```

### Ollama Models Not Discovered

**Symptoms:**
- System uses expensive cloud models despite Ollama running
- Cost higher than expected
- Logs: "Ollama not reachable — no local models"

**Diagnosis:**

```bash
# Check Ollama service
curl http://localhost:11434/api/tags

# Check discovered models
curl http://localhost:8000/api/orchestrator/models | jq '.ollama'

# Check model chooser cache
grep "OLLAMA.*Discovered" logs/server.log | tail -5
```

**Root causes:**

1. **Ollama on different port** — Default is 11434
2. **Ollama not running**
3. **No models pulled** — Ollama running but empty
4. **Wrong OLLAMA_HOST env var**

**Recovery:**

```bash
# Check OLLAMA_HOST
echo $OLLAMA_HOST
# Should be: http://localhost:11434

# Start Ollama
ollama serve &

# Pull models
ollama pull qwen2.5:7b
ollama pull llama3.2:3b

# Verify
ollama list

# Restart server to refresh cache
./bin/run restart

# Verify discovery
curl http://localhost:8000/api/orchestrator/models | jq '.ollama'
```

### Cost Spike Investigation

**Symptoms:**
- Unexpectedly high API bills
- Usage metrics show expensive model overuse
- Most tasks using strong tier when medium would suffice

**Diagnosis:**

```bash
# Check model usage by tier
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/usage/summary?days=7" | \
  jq '.by_tier'

# Check most expensive tasks
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/tasks?sort_by=cost&limit=20" | \
  jq '.[] | {id, title, model, cost}'

# Check tier distribution
grep "model_tier" logs/server.log | \
  grep "$(date +%Y-%m-%d)" | \
  awk '{print $NF}' | sort | uniq -c
```

**Cost reduction strategies:**

1. **Tune tier defaults** — Lower default tiers for routine tasks
2. **Prefer Ollama** — Pull larger local models (30B, 70B)
3. **Add tier overrides** — Explicit tier for known task types
4. **Review task prompts** — Simpler prompts can use smaller models

```python
# Example: Force inbox triage to use small tier (Haiku)
# app/orchestrator/model_chooser.py

TASK_TYPE_TIERS = {
    "inbox_triage": "small",      # $0.25/M tokens (Haiku)
    "daily_summary": "small",
    "code_formatting": "micro",   # Use local Ollama 3B
    "simple_docs": "small",
}
```

---

## Reflection Cycle Issues

### Overview

Reflection cycles are periodic strategic thinking runs where agents analyze recent work and compress identity. Two types:

1. **Strategic reflection** — Every 6 hours, agent reviews recent tasks
2. **Identity compression** — Daily, merge insights into long-term identity

Issues:
- Reflection workers spawn but produce no output
- Identity file grows unbounded
- Reflection fails to start (resource constraints)
- Reflection loops (same insights repeated)

### Reflection Workers Produce No Output

**Symptoms:**
- `AgentReflection` record shows `status="completed"` but `insights` is empty
- No identity updates despite reflection running
- Reflection transcript shows agent confused about task

**Diagnosis:**

```bash
# Check recent reflections
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/reflections?agent={agent}&limit=5" | \
  jq '.[] | {id, status, insights_count: (.insights | length)}'

# Get reflection transcript
cat ~/lobs-control/state/transcripts/reflection-{id}.json | jq .
```

**Root causes:**

1. **Vague prompt** — Agent doesn't understand what to reflect on
2. **No recent activity** — Window has zero tasks (nothing to reflect on)
3. **Context packet empty** — Failed to build context from recent work
4. **Model too weak** — Small model can't do strategic reasoning

**Recovery:**

**Fix prompt** (app/orchestrator/reflection_cycle.py):

```python
def _build_reflection_prompt(agent: str, context: dict, reflection_id: str) -> str:
    return f"""
You are the {agent} agent conducting strategic reflection.

GOAL: Analyze your recent work and extract actionable insights.

RECENT WORK SUMMARY:
- Tasks completed: {context.get('task_count', 0)}
- Key projects: {', '.join(context.get('projects', []))}
- Success rate: {context.get('success_rate', 0)}%
- Common patterns: {context.get('patterns', [])}

WHAT TO REFLECT ON:
1. What patterns did I notice? (e.g., "Tests often fail on async code")
2. What mistakes did I make? (e.g., "Forgot to await database calls 3 times")
3. What did I learn? (e.g., "Always run migrations with --dry-run first")
4. What should I remember for next time?

OUTPUT FORMAT:
Write 3-5 bullet points of actionable insights.

EXAMPLE:
- Pattern: Authentication tasks often need JWT validation examples
- Mistake: Forgot to handle token expiry in 2 implementations
- Learning: Always implement token refresh alongside validation
- Reminder: Check for existing auth middleware before creating new one

Your insights:
"""
```

**Use stronger model for reflection:**

```python
# app/orchestrator/reflection_cycle.py
choice = await chooser.choose(
    agent_type=agent,
    task={...},
    purpose="reflection",
    override_tier="standard",  # Force stronger model
)
```

### Identity File Grows Unbounded

**Symptoms:**
- `~/.openclaw/workspace-{agent}/identity.md` is 5000+ lines
- Reflection runs but doesn't compress
- Agent context window warnings

**Diagnosis:**

```bash
wc -l ~/.openclaw/workspace-{agent}/identity.md
# → 8234 lines (way too large)

# Check compression history
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/reflections?reflection_type=identity_compression&limit=5"
```

**Root cause:**
- Compression cycle not running (disabled or failing)
- Compression prompt adds instead of condensing
- No line limit enforcement

**Recovery:**

**Option 1: Manual compression**

```bash
cd ~/.openclaw/workspace-{agent}

# Backup
cp identity.md identity.md.backup

# Manually compress to ~300-500 lines
# Keep: core patterns, recent insights (last 30 days), critical lessons
# Remove: old dated entries, verbose examples, duplicate insights

vim identity.md
```

**Option 2: Trigger compression cycle**

```bash
# Force identity compression run
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/maintenance/identity
```

**Fix compression prompt:**

```python
# app/orchestrator/reflection_cycle.py
def _build_compression_prompt(agent: str, identity_content: str) -> str:
    return f"""
Compress the agent identity file to ≤500 lines while preserving all important information.

CURRENT SIZE: {len(identity_content.splitlines())} lines → TARGET: ≤500 lines

KEEP:
- Core patterns and principles (timeless)
- Recent insights (last 30 days)
- Critical lessons that prevent bugs
- High-value examples (max 1-2 per pattern)

REMOVE:
- Old dated entries (>90 days unless critical)
- Duplicate insights (same pattern stated multiple ways)
- Verbose examples (summarize to 1-2 lines)
- Low-signal observations

CONSOLIDATE:
- Merge related insights ("Always await DB calls" + "DB calls must be awaited" → one entry)
- Summarize patterns ("Saw X 3 times" → "Pattern: X is common")

BE AGGRESSIVE. Every line must earn its place.
"""
```

### Reflection Fails to Start

**Symptoms:**
- `POST /api/orchestrator/reflection/trigger` returns 500 error
- Scheduled reflection cycle doesn't spawn workers
- Error: "Failed to spawn reflection worker"

**Diagnosis:**

```bash
# Check orchestrator status
curl http://localhost:8000/api/orchestrator/status

# Check for reflection spawn errors
grep "reflection.*spawn.*failed" logs/server.log | tail -10

# Check resource constraints
curl http://localhost:8000/api/orchestrator/workers | jq 'length'
# If ≥10, may be at worker limit
```

**Common causes:**

1. **Worker limit reached** — Too many active workers
2. **Circuit breaker open** — Model provider down
3. **Gateway unavailable** — OpenClaw gateway not running
4. **Model unavailable** — No models in standard tier

**Recovery:**

```bash
# Wait for workers to complete
# Or kill stuck workers:
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/workers/{worker_id}/kill

# Check gateway
openclaw gateway status

# Restart gateway if needed
openclaw gateway restart

# Retry reflection
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/reflection/trigger
```

### Reflection Loops (Same Insights Repeated)

**Symptoms:**
- Every reflection produces identical insights
- Identity file contains duplicates
- No new learning despite new work

**Diagnosis:**

```bash
# Check recent reflections
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/reflections?limit=10" | \
  jq '.[] | .insights'

# Look for duplicates
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/reflections?limit=10" | \
  jq '.[] | .insights[]' | sort | uniq -d
```

**Root cause:**
- Reflection prompt doesn't reference existing identity
- Agent re-learns same lessons (no memory of previous insights)
- Context window too small (can't see past reflections)

**Fix:**

```python
# app/orchestrator/reflection_cycle.py
def _build_reflection_prompt(agent: str, context: dict, reflection_id: str) -> str:
    # Load existing identity
    identity_path = Path(f"~/.openclaw/workspace-{agent}/identity.md").expanduser()
    existing_identity = identity_path.read_text() if identity_path.exists() else ""
    
    return f"""
EXISTING IDENTITY (don't repeat these):
{existing_identity[:2000]}  # First 2000 chars
...

RECENT WORK:
{context}

INSTRUCTIONS:
Reflect on your recent work and identify NEW insights not already in your identity.
Focus on:
- New patterns not previously documented
- Mistakes not previously noted
- Learnings that UPDATE or REFINE existing identity
- Actionable reminders for future work

DO NOT repeat insights already in your identity.
"""
```

---

## Worker Stuck/OOM Recovery

### Overview

Workers can get stuck (hung agent) or crash (OOM, segfault). Detection and recovery:

- **Timeout detection** — Worker exceeds task timeout (30-60 min)
- **Heartbeat monitoring** — Worker stops sending updates
- **OOM detection** — Process killed by OS (exit code 137)
- **Graceful kill** — Send SIGTERM, wait, SIGKILL if needed

### Worker Timeout

**Symptoms:**
- Task stuck in "running" state for >60 minutes
- Worker still shows as active
- No recent activity in transcript

**Diagnosis:**

```bash
# Check worker runtime
curl http://localhost:8000/api/orchestrator/workers | \
  jq '.[] | {id, runtime: .runtime_seconds, task: .task_id}'

# Check task timeout setting
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/{task_id} | jq '.timeout_minutes'
```

**Automatic handling:**

Worker monitor (`app/orchestrator/worker_monitor.py`) checks every 30 seconds:

```python
if runtime > task_timeout * 60:
    logger.warning(f"Worker {worker_id} exceeded timeout")
    await kill_worker(worker_id)
    task.work_state = "failed"
    task.failure_reason = "Worker timeout"
```

**Manual kill:**

```bash
# Kill specific worker
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/workers/{worker_id}/kill

# Kill all workers for agent
curl http://localhost:8000/api/orchestrator/workers | \
  jq -r '.[] | select(.agent_type == "programmer") | .id' | \
  xargs -I {} curl -X POST -H "Authorization: Bearer $TOKEN" \
    http://localhost:8000/api/orchestrator/workers/{}/kill
```

**Adjust timeout:**

```bash
# Increase timeout for complex task
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -d '{"timeout_minutes": 120}' \
  http://localhost:8000/api/tasks/{task_id}
```

### Worker Hung (No Progress)

**Symptoms:**
- Worker within timeout but making no progress
- Transcript shows repeated failed tool calls
- Agent stuck in loop

**Diagnosis:**

```bash
# Check transcript tail
cat ~/lobs-control/state/transcripts/task-{id}.json | \
  jq '.transcript[-20:]' | \
  jq '.[] | {role, content: .content[0:100]}'

# Look for loops
cat ~/lobs-control/state/transcripts/task-{id}.json | \
  jq '.transcript[] | select(.type == "tool_call") | .name' | \
  tail -20 | sort | uniq -c
# → 15 read (agent reading same file repeatedly)
```

**Common loop patterns:**

1. **Read loop** — Agent reads file, forgets, reads again
2. **Error loop** — Tool fails, agent retries without fixing issue
3. **Planning loop** — Agent plans but never executes
4. **Search loop** — Agent searches but doesn't use results

**Recovery:**

```bash
# Kill and retry with better prompt
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/workers/{worker_id}/kill

# Update task with clearer instructions
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -d '{"notes": "IMPORTANT: After reading the file, immediately make the required changes. Do not re-read."}' \
  http://localhost:8000/api/tasks/{task_id}

# Retry
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/{task_id}/retry
```

### Out of Memory (OOM)

**Symptoms:**
- Worker exits with code 137 (SIGKILL by OS)
- Error: "Process killed (OOM)"
- System logs show OOM killer

**Diagnosis:**

```bash
# Check exit code in logs
grep "worker.*exit.*137" logs/server.log

# Check system OOM logs (Linux)
sudo dmesg | grep -i "out of memory"

# Check system memory
free -h

# Check worker memory usage (if still running)
ps aux | grep openclaw
```

**Root causes:**

1. **Large context** — Task context + MEMORY.md + transcript > 100k tokens
2. **Memory leak** — Worker process memory grows unbounded
3. **Concurrent workers** — Too many workers saturate RAM
4. **Large file processing** — Agent reading huge files into memory

**Recovery:**

**Option 1: Reduce context size**

```bash
# Trim MEMORY.md
wc -l ~/.openclaw/workspace-{agent}/MEMORY.md
# If >500 lines, trigger curation

# Split large task into smaller tasks
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -d '{
    "title": "Subtask 1: Refactor auth.py only",
    "agent_type": "programmer",
    "parent_task_id": "{original_task_id}"
  }' \
  http://localhost:8000/api/tasks
```

**Option 2: Increase system memory**

```bash
# For Docker deployments
docker update --memory 4g lobs-server

# For systemd services
# Edit /etc/systemd/system/lobs-server.service
MemoryLimit=4G
```

**Option 3: Limit concurrent workers**

```bash
# Edit orchestrator config
# app/orchestrator/config.py
MAX_CONCURRENT_WORKERS = 5  # Down from 10

# Restart server
./bin/run restart
```

### Worker Crash (Segfault, Panic)

**Symptoms:**
- Worker exits with non-zero code (not 0, not 137)
- Error: "Worker crashed unexpectedly"
- Core dump files generated

**Diagnosis:**

```bash
# Check exit code
grep "worker.*exit" logs/server.log | grep {worker_id}

# Check for core dumps
ls -la /cores/ /tmp/core.*

# Check worker stderr (if captured)
cat ~/lobs-control/state/logs/worker-{id}.stderr
```

**Common causes:**

1. **OpenClaw bug** — Crash in gateway code
2. **Model provider error** — HTTP 500 from API
3. **Filesystem issue** — Disk full, permissions
4. **Network timeout** — Long-running API call dropped

**Recovery:**

```bash
# Retry task (auto-escalation handles retries)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/{task_id}/retry

# Check gateway logs
openclaw gateway logs

# Update OpenClaw if bug suspected
openclaw update
```

---

## Circuit Breaker Management

### Overview

Circuit breaker prevents cascade failures by pausing spawning when infrastructure fails. States:

- **CLOSED** — Normal operation, tasks spawn freely
- **OPEN** — Infrastructure failure detected, spawning paused
- **HALF_OPEN** — Cooldown elapsed, allowing probe spawn

### Circuit Breaker Open

**Symptoms:**
- Tasks stuck in "not_started" state
- Error: "Circuit breaker open: {reason}"
- No workers spawning despite eligible tasks

**Diagnosis:**

```bash
# Check circuit state
curl http://localhost:8000/metrics | grep lobs_circuit_breaker_state
# → lobs_circuit_breaker_state{provider="ollama"} 1  (1 = open)

# Check reason
curl http://localhost:8000/api/orchestrator/circuit-breaker | \
  jq '.providers[] | {name, state, reason}'
```

**Common reasons:**

- `gateway_auth` — OpenClaw gateway auth failure
- `session_lock` — Session file locked
- `missing_api_key` — No API key for provider
- `service_unavailable` — Provider unreachable
- `rate_limited` — Provider rate limit hit
- `all_models_failed` — All fallback models failed

**Recovery:**

**1. Fix root cause**

```bash
# Gateway auth
openclaw gateway restart

# Missing API key
export ANTHROPIC_API_KEY=sk-...
./bin/run restart

# Rate limit
# Wait for reset (usually 1 minute - 1 hour)

# Service unavailable
# Check provider status page
# Or use alternative provider
```

**2. Wait for auto-recovery**

Circuit breaker auto-closes after cooldown (5 minutes default):

```bash
# Check when circuit will close
curl http://localhost:8000/api/orchestrator/circuit-breaker | \
  jq '.providers[] | select(.state == "open") | {name, opened_at, cooldown_seconds}'
```

**3. Manual reset (use sparingly)**

```bash
# Force circuit closed
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/circuit-breaker/reset

# Verify
curl http://localhost:8000/metrics | grep circuit_breaker_state
```

### Circuit Breaker Flapping

**Symptoms:**
- Circuit opens and closes repeatedly (every few minutes)
- Tasks spawn, immediately fail, circuit opens
- Logs: "Circuit breaker state changed: closed → open → closed"

**Diagnosis:**

```bash
# Count state changes
grep "circuit.*state changed" logs/server.log | wc -l
# → 47 changes in last hour (flapping)

# Identify pattern
grep "circuit.*state changed" logs/server.log | tail -20
```

**Root causes:**

1. **Intermittent failure** — Provider flaky (succeeds, fails, succeeds)
2. **Threshold too sensitive** — Opens after 3 failures (too low)
3. **Probe task fails** — Half-open state, probe fails, reopens

**Recovery:**

**Option 1: Increase threshold**

```python
# app/orchestrator/circuit_breaker.py
circuit_breaker = CircuitBreaker(
    db=db,
    threshold=5,  # Up from 3 (requires 5 consecutive failures)
    cooldown_seconds=600.0  # Up from 300 (wait 10 minutes)
)
```

**Option 2: Identify unstable provider, exclude temporarily**

```bash
# Check failure rate by provider
grep "model.*failed" logs/server.log | \
  awk '{print $NF}' | sort | uniq -c

# If one provider is flaky, remove from routing
# Edit app/orchestrator/model_router.py
MODEL_ROUTER_TIER_MEDIUM_KEY = [
    "anthropic/claude-sonnet-4",
    # "openai/gpt-4o-mini",  # Temporarily disabled (flaky)
]
```

**Option 3: Add provider-specific circuit**

```python
# Circuit per provider, not global
circuit_breaker.record_failure(
    task_id=task_id,
    project_id=project_id,
    agent_type=agent_type,
    error_log=error_log,
    provider="anthropic",  # Isolate to one provider
)
```

---

## Escalation Loop Prevention

### Overview

Task failures trigger 4-tier escalation:

1. **Auto-retry** (same agent, max 2 retries)
2. **Agent switch** (try different agent type)
3. **Diagnostic run** (reviewer analyzes failure)
4. **Human escalation** (inbox alert)

Loops occur when escalation doesn't resolve the issue.

### Escalation Loop Symptoms

**Symptoms:**
- Task retries 5+ times, still failing
- Multiple diagnostic tasks created
- Same error message across retries
- Escalation tier keeps increasing but doesn't reach human (tier 4)

**Diagnosis:**

```bash
# Check escalation history
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/{task_id}/escalations

# Check retry count
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks/{task_id} | \
  jq '{retry_count, escalation_tier, failure_reason}'

# Check for repeated failures
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/tasks?parent_task_id={task_id}" | \
  jq '.[] | {id, work_state, failure_reason}'
```

**Common patterns:**

1. **Impossible task** — Task can't be completed (missing requirements, conflicting constraints)
2. **Environment issue** — Missing file, broken dependency, API down
3. **Agent mismatch** — Task requires capability no agent has
4. **Escalation logic bug** — Doesn't advance to tier 4

### Breaking the Loop

**Option 1: Mark as blocked (stop escalation)**

```bash
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -d '{
    "work_state": "blocked",
    "blocked_reason": "Missing API credentials for service X"
  }' \
  http://localhost:8000/api/tasks/{task_id}
```

**Option 2: Fix environment, retry manually**

```bash
# Fix issue (install dependency, add API key, etc.)
npm install missing-package

# Reset escalation and retry
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -d '{"escalation_tier": 0, "retry_count": 0, "work_state": "todo"}' \
  http://localhost:8000/api/tasks/{task_id}
```

**Option 3: Rewrite task (clarify requirements)**

```bash
# Original task too vague: "Fix the auth bug"
# Rewrite with specifics:
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -d '{
    "title": "Fix JWT expiry validation in auth middleware",
    "description": "Bug: tokens with exp in the past are accepted. Fix: check token.exp < now(), return 401.",
    "notes": "See test case: tests/test_auth.py::test_expired_token_rejected"
  }' \
  http://localhost:8000/api/tasks/{task_id}
```

**Option 4: Force human escalation**

```bash
# Skip to tier 4 immediately
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -d '{"escalation_tier": 4}' \
  http://localhost:8000/api/tasks/{task_id}

# Create inbox alert manually
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -d '{
    "title": "Task {task_id} stuck in escalation loop",
    "body": "Failed {retry_count} times. Needs human review.",
    "type": "alert",
    "related_task_id": "{task_id}"
  }' \
  http://localhost:8000/api/inbox
```

### Prevent Future Loops

**1. Detect impossible tasks earlier**

```python
# app/orchestrator/scanner.py
def is_task_viable(task: Task) -> tuple[bool, str]:
    """Check if task can possibly succeed."""
    
    # Missing required info
    if not task.acceptance_criteria:
        return False, "No acceptance criteria defined"
    
    # Conflicting requirements
    if "don't change auth" in task.notes and "refactor auth" in task.title:
        return False, "Conflicting requirements"
    
    # No capable agent
    if task.required_capability and not has_capable_agent(task.required_capability):
        return False, f"No agent has capability: {task.required_capability}"
    
    return True, ""
```

**2. Cap escalation attempts**

```python
# app/orchestrator/escalation_enhanced.py
MAX_ESCALATION_ATTEMPTS = 8  # Total retries across all tiers

if task.retry_count >= MAX_ESCALATION_ATTEMPTS:
    # Force tier 4 (human)
    return await self._tier_4_human_escalation(task, agent_type, error_log)
```

**3. Improve diagnostic runs**

Diagnostic tasks (tier 3) should produce actionable fixes:

```python
# app/orchestrator/escalation_enhanced.py
diagnostic_prompt = f"""
Analyze why task {task_id} failed {retry_count} times.

FAILURE HISTORY:
{failure_history}

YOUR JOB:
1. Identify ROOT CAUSE (not symptoms)
2. Determine if task is VIABLE (can it succeed?)
3. Propose CONCRETE FIX (change task, fix env, or mark blocked)

OUTPUT:
- Root cause: [specific issue]
- Viable: [yes/no with reasoning]
- Recommended action: [specific next step]
"""
```

---

## Quick Troubleshooting Matrix

| Symptom | Most Likely Cause | First Action |
|---------|-------------------|--------------|
| Tasks stuck in "not_started" | Circuit breaker open | Check `curl .../circuit-breaker` |
| Worker times out | Task too complex or hung | Check transcript for loops, increase timeout |
| Memory sync fails | File permissions or concurrent writes | Check logs, remove stale locks |
| Wrong model used | Incorrect tier assignment | Check `model_tier` field, override if needed |
| Handoff not created | Invalid JSON or missing fields | Check `.handoffs/` files, validate JSON |
| Reflection produces no output | Vague prompt or no recent activity | Review context packet, improve prompt |
| Worker OOM crash | Large context or memory leak | Trim MEMORY.md, split task, increase RAM |
| Escalation loop | Impossible task or env issue | Mark blocked or fix environment |
| Cost spike | Wrong tier defaults | Review tier usage, add overrides |

---

## Monitoring Commands Cheat Sheet

```bash
# Overall system health
curl http://localhost:8000/api/orchestrator/status

# Active workers
curl http://localhost:8000/api/orchestrator/workers | jq 'length'

# Failed tasks (last hour)
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/tasks?work_state=failed&updated_after=$(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)" | \
  jq 'length'

# Circuit breaker state (0=closed, 1=open)
curl http://localhost:8000/metrics | grep circuit_breaker_state

# Memory sync status
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/memories?limit=1 | jq '.[0].updated_at'

# Model usage by tier (last 24h)
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/usage/summary?hours=24" | \
  jq '.by_tier'

# Reflection status
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/reflections?status=pending" | jq 'length'

# Escalation rate
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/tasks?escalation_tier__gte=1" | jq 'length'
```

---

## Related Documentation

- **[Debugging Failing Agents](02-debugging-failing-agents.md)** — Agent-specific debugging
- **[Reading Observability Metrics](03-reading-observability-metrics.md)** — Metrics interpretation
- **[ADR-0011: Handoff Protocol](../decisions/0011-handoff-protocol.md)** — Handoff design
- **[ADR-0010: Memory Architecture](../decisions/0010-agent-memory-architecture.md)** — Memory system
- **[ADR-0004: Model Routing](../decisions/0004-five-tier-model-routing.md)** — Model selection
- **[ADR-0012: Tool Access Policies](../decisions/0012-tool-access-policies.md)** — Permission model
- **[Memory Best Practices](../guides/memory-best-practices.md)** — Writing good memories

---

**Maintained by:** Operations team  
**Feedback:** Report issues or suggest improvements via inbox  
**Last reviewed:** 2026-02-22
