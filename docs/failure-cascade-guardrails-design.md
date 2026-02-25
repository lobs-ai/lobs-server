# Failure Cascade Guardrails — Design Document

**Status:** Approved (converted from initiative b743f70e)  
**Task ID:** 33a954e3-783f-4537-b51d-6293e46a8e27  
**Author:** architect  
**Date:** 2026-02-25  

---

## 1. Problem Statement

The orchestrator's `DiagnosticTriggerEngine` detects failed/stalled tasks and spawns diagnostic agent
sessions. Each diagnostic session can produce `recommended_actions`, which `_create_remediation_tasks()`
converts into new `Task` records. If those remediation tasks also fail, the trigger engine sees them as
new candidate tasks and fires diagnostics again — producing another wave of remediation tasks. This
recursive cascade has been observed to generate "cancellation storms" (many tasks spawned and then
cancelled) and wastes model spend on diagnosis of diagnosis failures.

### The Cascade Path

```
failed task (depth=0)
  → DiagnosticTriggerEngine fires
  → diagnostic agent session
  → recommended_actions → _create_remediation_tasks()
  → new tasks (currently depth=0 — no distinction!)
    → those tasks fail
    → DiagnosticTriggerEngine fires AGAIN
    → more diagnostic sessions → more tasks
    → ... infinite cascade
```

The current debounce mechanism (`trigger_key` + `debounce_seconds`) only prevents duplicate diagnostics
for the **same** trigger key within the debounce window. New remediation tasks get new trigger keys,
so the debounce provides no cross-generation protection.

---

## 2. Proposed Solution

Three coordinated fixes:

### 2a. Diagnostic Depth Tracking (`Task.diagnostic_depth`)

Add an integer column `diagnostic_depth` (default 0) to the `tasks` table:

- **0** = task spawned by normal user/agent workflow
- **1** = task spawned by a diagnostic (first-generation remediation)
- **N** = Nth-generation diagnostic spawn

In `_create_remediation_tasks()`, propagate depth:
```
new_task.diagnostic_depth = parent_event_task.diagnostic_depth + 1
```

Where `parent_event_task` is the task that triggered the diagnostic event (found via
`DiagnosticTriggerEvent.task_id → Task`).

### 2b. Depth Enforcement in DiagnosticTriggerEngine

In each trigger detector (`_stalled_task_triggers`, `_failure_pattern_triggers`, etc.),
add a WHERE clause:
```sql
WHERE task.diagnostic_depth < :max_diagnostic_depth
```

Default `max_diagnostic_depth = 1` (configurable via `OrchestratorSetting`).
New runtime setting key: `orchestrator.diagnostics.max_depth`.

When a task at `depth >= max_depth` would normally trigger a diagnostic, instead call
`_escalate_to_stop_summarize(task)` — see §2c.

### 2c. Stop-and-Summarize Terminal State

When the depth limit is reached (or when `_create_remediation_tasks()` is called but the
resulting tasks would exceed max depth), the system escalates to human review rather than
spawning more agents:

**New `work_state` value:** `stop_and_summarize`

Tasks in `stop_and_summarize`:
- Are excluded from all `DiagnosticTriggerEngine` trigger detectors (hard WHERE filter)
- Are never queued to the worker
- Create one inbox item (decision card format) summarizing the cascade and requesting human triage

`_escalate_to_stop_summarize(task)` does:
1. `task.work_state = "stop_and_summarize"`
2. Walks the chain: original task → trigger event → diagnostic → remediation task → this task
3. Creates an `InboxItem` using DecisionCard format (see `docs/communication/decision-card-spec.md`)
4. Returns immediately (no agent spawning)

### 2d. Structured Remediation Checklist

Update the diagnostic prompt in `DiagnosticTriggerEngine._build_prompt()` to require a structured
`remediation_checklist` array alongside `recommended_actions`:

```json
{
  "issue_summary": "...",
  "root_causes": ["..."],
  "recommended_actions": ["..."],
  "remediation_checklist": [
    {
      "action": "Concrete step to take (imperative verb, specifics)",
      "verify_by": "How to confirm this worked",
      "agent": "programmer|researcher|architect|project-manager"
    }
  ],
  "confidence": 0.0
}
```

In `_create_remediation_tasks()`:
- Prefer `remediation_checklist` items over bare `recommended_actions` strings
- Skip items where `action` is empty or < 20 characters (too vague to act on)
- Skip items missing `verify_by` (no acceptance criteria)
- Embed `verify_by` text in the task's `notes` field so workers know when they're done
- If `remediation_checklist` is absent or empty, fall back to `recommended_actions` strings (backwards compat)

---

## 3. Tradeoffs Considered

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **Depth column on Task** | Simple, queryable, audit trail | One more schema column | ✅ Chosen |
| Depth in task notes (JSON) | No migration | Not queryable, fragile parsing | ❌ Rejected |
| Block all auto-remediation | Stops cascade entirely | Loses value of auto-remediation entirely | ❌ Too aggressive |
| Global remediation rate limit | Caps storm volume | Doesn't fix root cause; complex | ❌ Not needed given depth approach |
| `stop_and_summarize` as work_state | Reuses existing state machine, no new column | Requires worker/queue to handle new state | ✅ Chosen (minimal schema) |
| New `terminal` status | Cleaner semantics | Schema churn, ripple through API/UI | ❌ Rejected |

**On max depth = 1:** The task requirement is "beyond one generation." Depth=1 is a natural
boundary: diagnostics analyze real work, but shouldn't analyze their own diagnostic output
recursively. Configurable default allows tuning if needed.

---

## 4. Implementation Plan

### Task 1 — Schema: Add `diagnostic_depth` to tasks (small)

**File:** `app/models.py`  
**Migration:** `bin/migrate_add_diagnostic_depth.py`

Add to `Task`:
```python
diagnostic_depth = Column(Integer, default=0, nullable=False)
source_diagnostic_trigger_id = Column(String, ForeignKey("diagnostic_trigger_events.id"), nullable=True)
```

`source_diagnostic_trigger_id` links a remediation task back to the diagnostic event that spawned it
(enables chain-walking for stop-and-summarize inbox message).

Migration script: `ALTER TABLE tasks ADD COLUMN diagnostic_depth INTEGER NOT NULL DEFAULT 0`
and `ALTER TABLE tasks ADD COLUMN source_diagnostic_trigger_id VARCHAR`.

**Acceptance:**
- Column exists in `tasks` table after running migration
- Default value is 0 for all existing rows
- SQLAlchemy model reflects the new columns
- `pytest` passes

### Task 2 — Runtime Setting: `max_diagnostic_depth` (small)

**File:** `app/orchestrator/runtime_settings.py`

Add:
```python
SETTINGS_KEY_DIAG_MAX_DEPTH = "orchestrator.diagnostics.max_depth"
```

Default: `1`.

Add to `DEFAULT_RUNTIME_SETTINGS`.

**Acceptance:**
- Key and default exist in `runtime_settings.py`
- Default value is `1`

### Task 3 — Depth Propagation in `_create_remediation_tasks()` (small)

**File:** `app/orchestrator/worker.py`

In `_create_remediation_tasks()`:
1. Load `event.task_id` → `Task` to get `parent_depth = parent_task.diagnostic_depth` (or 0 if no task)
2. Compute `child_depth = parent_depth + 1`
3. Set `task.diagnostic_depth = child_depth` on each created task
4. Set `task.source_diagnostic_trigger_id = event.id` on each created task
5. Load `max_depth` from `OrchestratorSetting` (default 1)
6. If `child_depth >= max_depth`: do NOT create tasks; instead call
   `await self._escalate_to_stop_summarize(event)` and return `[]`

This means depth-1 tasks are created normally but can't spawn further diagnostics (enforced in step 5
of Task 4). If somehow a depth-1 task's diagnostic fires, the trigger engine blocks it in Task 4.

**Acceptance:**
- Remediation tasks created from a diagnostic event have `diagnostic_depth = 1`
- Remediation tasks have `source_diagnostic_trigger_id` set
- If `max_depth=1` and parent task is depth=1, no tasks created; escalation fires

### Task 4 — Depth Guard in DiagnosticTriggerEngine (medium)

**File:** `app/orchestrator/diagnostic_triggers.py`

1. Load `max_depth` from settings in `_load_settings()`
2. In `_stalled_task_triggers()` and `_failure_pattern_triggers()`, add:
   ```python
   Task.diagnostic_depth < max_depth
   ```
   to the WHERE clause of the SELECT query.
   
   Also exclude `work_state = "stop_and_summarize"` from all trigger selectors.

3. In `_spawn_diagnostic()` (or in `run_once()` before calling `_spawn_diagnostic()`): double-check
   that the task's `diagnostic_depth < max_depth`. If at limit, call `_escalate_to_stop_summarize()`
   instead of spawning.

4. Implement `_escalate_to_stop_summarize(task_id, event)`:
   - Set `task.work_state = "stop_and_summarize"`
   - Walk the causal chain: task → source_diagnostic_trigger_id → DiagnosticTriggerEvent → 
     DiagnosticTriggerEvent.task_id (original failing task)
   - Build human-readable summary of the cascade
   - Create `InboxItem` in decision-card format (🟡 Standard 24h urgency):
     - Title: `⚠️ Diagnostic cascade stopped: {original_task_title}`
     - Body: cascade summary with task IDs, failure reasons, what diagnostics tried
     - Recommendation: manual triage of original task
     - Consequence of no response: task remains stuck indefinitely

**Acceptance:**
- A task with `diagnostic_depth=1` does NOT appear as a trigger candidate
- A task with `work_state="stop_and_summarize"` does NOT appear as a trigger candidate
- When depth limit is reached, an InboxItem is created
- `run_once()` return dict includes `"stopped_cascades": N` counter
- Unit tests: mock a depth-1 task, verify it's excluded from candidate list

### Task 5 — Structured Checklist Prompt + Validation (medium)

**File:** `app/orchestrator/diagnostic_triggers.py` (`_build_prompt`)  
**File:** `app/orchestrator/worker.py` (`_create_remediation_tasks`)

1. Update `_build_prompt()` to require `remediation_checklist` in the JSON schema
2. In `_create_remediation_tasks()`:
   - Check for `remediation_checklist` key; use it if present
   - For each checklist item: skip if `action` empty or < 20 chars; skip if `verify_by` empty
   - Embed `verify_by` in task `notes` as acceptance criteria block
   - Fall back to `recommended_actions` strings if `remediation_checklist` absent or all items invalid
3. Add metric to event outcome: `{"checklist_items_valid": N, "checklist_items_skipped": M}`

**Acceptance:**
- `_create_remediation_tasks()` correctly uses `remediation_checklist` when present
- Short/empty actions are filtered (unit tests with mock payloads)
- Fallback to `recommended_actions` works when checklist absent
- Created task `notes` contains the `verify_by` text

---

## 5. Testing Strategy

### Unit Tests (new or update `tests/test_diagnostic_triggers.py`)

1. **Depth filtering:** Mock DB with tasks at depth=0 and depth=1. Verify only depth=0 tasks appear in trigger candidates.
2. **stop_and_summarize exclusion:** Task with `work_state="stop_and_summarize"` must not appear in candidates.
3. **Depth propagation:** Call `_create_remediation_tasks()` with a mock event whose task is at depth=0; verify created tasks have depth=1.
4. **Cascade blocking:** Parent task at depth=1 with `max_depth=1` → `_create_remediation_tasks()` returns `[]` and creates inbox item.
5. **Checklist validation:** Test with valid checklist, with empty actions, with missing verify_by, with absent checklist.

### Integration Tests

1. Run full diagnostic loop with a seeded failed task; verify no more than N=1 generation of remediations.
2. Verify `source_diagnostic_trigger_id` is set on created tasks.

### Observability

- `run_once()` return dict extended with `"stopped_cascades"` and `"checklist_tasks_skipped"` counters
- Engine logs `[DIAGNOSTIC] Cascade blocked: task {id} at depth {d} (max={max})` at WARNING level
- Engine logs `[DIAGNOSTIC] Stop-and-summarize: created inbox item {item_id}` at INFO level

---

## 6. Schema Summary

```
tasks:
  + diagnostic_depth            INTEGER NOT NULL DEFAULT 0
  + source_diagnostic_trigger_id VARCHAR (FK diagnostic_trigger_events.id, nullable)
```

No new tables. No API surface changes (diagnostic_depth is internal plumbing; not exposed in
task list/detail responses unless Mission Control asks for it — punt to future).

---

## 7. Migration

Script: `bin/migrate_add_diagnostic_depth.py`  
Pattern: same as `bin/migrate_add_agent_to_memories.py` (PRAGMA table_info check, ALTER TABLE, no rollback needed for additive columns).

---

## 8. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Existing tasks at depth=0 trigger one more diagnostic cycle after deploy | High | Low | Expected; debounce prevents spam; no code change needed |
| `stop_and_summarize` tasks block real work | Low | Medium | Worker/queue must handle new state (skip-and-log); add to worker exclusion list |
| Checklist requirement causes LLM to produce worse output | Low | Low | Fallback to `recommended_actions` preserved; prompt change is additive |
| `source_diagnostic_trigger_id` FK causes issues if trigger event deleted | Low | Low | Set nullable; no cascade delete |
