# Tiered Approval System

The lobs-server orchestrator uses a **tiered approval system** for proactive task creation. This prevents the PM from creating inappropriate or excessive tasks while still allowing autonomous system maintenance.

## Overview

The Project Manager (PM) agent runs periodic proactive reviews (every 30 minutes) to check system health and create maintenance tasks when needed. To prevent task spam and token waste, the PM uses a three-tier classification system.

## The Three Tiers

### 🟢 SMALL — Create Directly

**Characteristics:**
- Clear, well-defined scope
- Low risk of breaking changes
- Minimal design decisions required
- Can be completed in 1-2 hours

**Examples:**
- Bug fixes (clear, reproducible issues)
- Missing documentation for specific APIs/modules
- Test coverage for specific untested functions
- Small refactors (< 50 lines of code)
- Security/critical dependency updates
- Typo fixes
- Dead code removal (specific cases)

**Process:**
The PM creates these tasks directly using:
```bash
./scripts/lobs-tasks create "task title" --project PROJECT_ID --notes "description" --agent AGENT_TYPE
```

### 🟡 MEDIUM — Create with Justification

**Characteristics:**
- Moderate scope
- Some design decisions needed
- Could affect multiple modules
- Requires explanation of value

**Examples:**
- Utility functions / helper modules
- Moderate refactors (50-200 lines)
- Performance optimizations (for specific identified bottlenecks)
- Non-critical dependency updates
- Database query optimizations
- Code consolidation (removing duplication)

**Process:**
The PM creates these tasks but must include:
1. **Why it's needed now** (what triggered the need)
2. **What specific problem it solves**
3. **Estimated scope** (files affected, rough LOC)

The task notes should contain this justification so the assigned agent understands the context.

### 🔴 LARGE — Inbox Item for Rafe

**Characteristics:**
- Significant scope
- Major design decisions required
- UI/UX implications
- Breaking changes or migrations
- Strategic direction needed

**Examples:**
- New features (API endpoints, capabilities)
- UI/UX changes
- Architecture changes (new modules, patterns)
- Database schema changes
- Breaking API changes
- Multi-day projects

**Process:**
The PM creates an inbox item instead of a task:
```bash
./scripts/lobs-inbox create "proposal title" --content "detailed proposal" --severity low
```

This allows Rafe to review, discuss, and decide whether/when to proceed.

## Deduplication Mechanism

The PM proactive review includes multiple layers of deduplication to prevent creating duplicate tasks:

### 1. In-Memory Cooldown (Fast Path)

- Tracks recently created tasks by normalized title
- Cooldown period: **24 hours**
- Prevents identical tasks from being created within 24h
- Uses normalized titles (lowercase, no special chars, collapsed whitespace)

### 2. Database Fuzzy Matching

Before creating any task, the PM prompt receives:
- All active tasks (any status = "active")
- Recently completed tasks (completed in last 24h)

The PM is explicitly instructed:
> **DO NOT create tasks that duplicate any of the above**

### 3. Project + Agent Locking

The dedup system checks:
- Is there already a task for the same project+agent combo in progress?
- If yes, reject (prevents multiple programmer tasks on same project)

### 4. Similarity Threshold

When checking for duplicates, the system uses:
- **70% similarity threshold** (SequenceMatcher ratio)
- Normalized titles for comparison
- Example: "Add tests for auth module" vs "Add authentication tests" → 73% match → duplicate

## Cooldown Rules

### Task Creation Cooldown
- **Duration:** 24 hours
- **Scope:** Per normalized task title
- **Storage:** In-memory dict (top 100 most recent)
- **Behavior:** If a similar task was created in the last 24h, reject with reason

### PM Review Cooldown
- **Duration:** 25 minutes minimum between proactive reviews
- **Purpose:** Prevent rapid-fire reviews on orchestrator restarts
- **Behavior:** If last review completed < 25 min ago, skip entirely

## Token Efficiency

The proactive review system is optimized for token efficiency:

### 1. Model Selection
- Uses **`anthropic/claude-haiku-4-5`** (cheapest, fastest)
- Not Sonnet or Opus (those are for specialist work)

### 2. Compact Task Summaries
Instead of passing full task objects with notes, the PM receives:
```
- Task title (status, work_state)
```

This reduces prompt size from ~200k tokens to ~5k tokens for a typical review.

### 3. Conditional Execution
The PM review is **skipped entirely** if:
- Last review was < 25 minutes ago
- No active or recent tasks exist (edge case)

### 4. Clear Exit Criteria
The PM is told:
> "Skip this review if everything looks healthy. Exit cleanly if no action needed."

This allows the PM to return early without creating tasks, saving completion tokens.

## How the PM Makes Decisions

The PM follows this workflow:

### Step 1: Health Check
```bash
./scripts/lobs-tasks list  # Check for stuck/failed tasks
./scripts/lobs-status agents  # Check agent health
```

### Step 2: Triage Existing Issues
- **Stuck tasks** (in_progress with no active worker)  
  → Reset: `./scripts/lobs-tasks set-work-state TASK_ID not_started`
  
- **Failed tasks** (work_state = failed)  
  → Reset if retryable, or escalate if repeated
  
- **Repeated failures** (same task failed 3+ times)  
  → Create inbox item for human review

### Step 3: Identify New Work
Look for gaps in:
- Test coverage (new code without tests)
- Documentation (new APIs/modules)
- Known bugs (from recent failure logs)
- Security updates (from dependabot or npm audit)

### Step 4: Create Tasks (Tier-Appropriate)
- **Small** → Create directly with `./scripts/lobs-tasks create`
- **Medium** → Create with justification in notes
- **Large** → Create inbox item with `./scripts/lobs-inbox create`

### Step 5: Check Against Recent Tasks
Before creating, the PM **must** verify:
- No similar task in the provided recent task list
- Task title is specific (not "improve tests", but "add tests for auth.py login function")

## Configuration

### Intervals (in `engine.py`)
```python
self._pm_proactive_interval = 1800  # 30 minutes between proactive reviews
self._pm_interval = 60  # 60 seconds for routing reviews (separate)
```

### Thresholds
```python
SIMILARITY_THRESHOLD = 0.70  # 70% title similarity = duplicate
COOLDOWN_HOURS = 24  # 24-hour cooldown per task title
MIN_REVIEW_GAP_MINUTES = 25  # Minimum gap between PM reviews
```

## Example Scenarios

### ✅ Good: Small Task Creation
**Situation:** PM notices `auth.py` has no tests  
**Action:** Creates task:
```bash
./scripts/lobs-tasks create "Add unit tests for auth.py login flow" \
  --project lobs-server \
  --notes "auth.py:45-120 has no test coverage. Add tests for login, logout, token refresh." \
  --agent programmer
```
**Tier:** 🟢 Small (clear scope, specific module)

### ✅ Good: Medium Task with Justification
**Situation:** PM notices 3 endpoints have duplicate validation logic  
**Action:** Creates task with justification:
```bash
./scripts/lobs-tasks create "Extract validation helpers to utils module" \
  --project lobs-server \
  --notes "Endpoints /api/tasks, /api/projects, /api/agents have duplicate UUID validation (lines 23, 45, 67). Extract to utils/validators.py to DRY. Estimated 80 LOC affected." \
  --agent programmer
```
**Tier:** 🟡 Medium (moderate refactor, clear value)

### ✅ Good: Large Task → Inbox Item
**Situation:** PM thinks adding a GraphQL API would be useful  
**Action:** Creates inbox item:
```bash
./scripts/lobs-inbox create "Proposal: Add GraphQL API" \
  --content "Current REST API works but could benefit from GraphQL for complex queries. Would require: (1) graphene-python dependency, (2) schema design, (3) resolver implementation, (4) authentication layer. Estimated 2-3 days work. Benefits: flexible querying, reduced overfetching." \
  --severity low
```
**Tier:** 🔴 Large (architecture decision, requires Rafe approval)

### ❌ Bad: Duplicate Task
**Situation:** PM sees task "Fix auth token expiry bug" was created 2 hours ago  
**Recent Tasks List:** Includes "Fix auth token expiry bug (active, in_progress)"  
**Action:** PM correctly DOES NOT create a duplicate  
**Reason:** Dedup system caught it

### ❌ Bad: Vague Task
**Situation:** PM thinks "improve code quality"  
**Action:** PM correctly DOES NOT create this task  
**Reason:** Too vague, no specific scope, violates SMALL tier criteria

### ❌ Bad: Premature Optimization
**Situation:** PM thinks "optimize database queries" without profiling  
**Action:** PM correctly DOES NOT create this task  
**Reason:** No evidence of performance problem, violates tier guidelines

## Monitoring

### Logs to Watch
```bash
# Check if PM is creating too many tasks
tail -f /tmp/lobs-server.log | grep "PM proactive review"

# Check dedup rejections
tail -f /tmp/lobs-server.log | grep "Cooldown"
```

### Metrics to Track
- **Tasks created per PM review** (should be 0-2 on average)
- **Token usage per review** (should be < 10k tokens)
- **Dedup rejection rate** (should increase if working correctly)

### Red Flags
🚩 PM creating 5+ tasks in one review → Prompt too permissive  
🚩 Same task created multiple times → Dedup not working  
🚩 Review consuming > 50k tokens → Task context too verbose  
🚩 PM creating "improve X" tasks → Tier guidelines not followed  

## Rollback Plan

If the proactive system causes issues again:

### Quick Fix (Triage-Only Mode)
Edit `engine.py`, replace PM prompt with:
```python
prompt = """Triage existing stuck/failed tasks only. DO NOT create new tasks."""
```

### Nuclear Option (Disable Entirely)
```python
self._pm_proactive_interval = 86400  # Once per day
```

### Restore from This Commit
```bash
cd ~/lobs-server
git revert HEAD  # Reverts the dedup + tiered approval changes
```

## See Also

- `app/orchestrator/engine.py` — Main orchestrator engine with dedup logic
- `app/orchestrator/scanner.py` — Task eligibility scanning
- `app/orchestrator/router.py` — Legacy regex-based routing
- `scripts/lobs-tasks` — CLI for task management
- `scripts/lobs-inbox` — CLI for inbox item creation
