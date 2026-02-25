# First-Failure Evidence Bundle — Design Document

**Status:** Ready for implementation  
**Task ID:** 24b2a273-eadf-4120-98ce-263efb5126ec  
**Date:** 2026-02-25  
**Author:** architect

---

## 1. Problem Statement

When a task fails, diagnostic agents currently re-derive context from scattered sources: `Task.failure_reason` (a single text field), `WorkerRun` records (operational metrics, not diagnostic evidence), `DiagnosticTriggerEvent` rows, and escalation tier. This re-derivation:

- Takes time and wastes model tokens on archaeology
- Is lossy — state at the *moment of first failure* is not preserved
- Causes repeated diagnostic loops when the same task fails again

**Goal:** Generate a deterministic, structured failure evidence bundle exactly once per failed task run, persisted to the database, so that retry tasks and diagnostic prompts consume it directly rather than re-deriving context from scratch.

---

## 2. Proposed Solution

### 2a. Architecture Overview

```
  worker._handle_completion(succeeded=False)
             │
             ▼
  FailureBundleBuilder.capture(task, worker_run, error_log)
     │   ─── checks if bundle already exists for task_id
     │   ─── builds structured artifact
     │   ─── persists FailureBundle row (once, immutable)
     │   ─── writes Task.failure_bundle_id FK
             │
             ▼
  FailureBundle (DB row) — lives forever
  {
    id, task_id, worker_run_id,
    state_timeline,          ← [{ts, event, from_state, to_state}]
    triggering_exception,    ← raw error_log excerpt
    first_failing_command,   ← best-effort from transcript (nullable)
    cancellation_reason,     ← human-readable classification
    error_type,              ← classify_error_type() code
    escalation_tier_at_failure,
    retry_count_at_failure,
    model_used,
    duration_seconds,
    created_at
  }
             │
    ┌────────┴────────────────────────┐
    ▼                                 ▼
  EscalationManager                DiagnosticTriggerEngine
  (attach bundle_id to              (inject bundle excerpt
  retry/diag task notes)            into diagnostic prompt)
             │
             ▼
  FailureExplainerService
  (load bundle, enrich explanation
  with timeline + exception text)
```

### 2b. First-Failure-Only Semantics

A bundle is created **once** per task. The enforcement mechanism:

1. `FailureBundle` table has a `UNIQUE` index on `task_id`
2. `FailureBundleBuilder.capture()` does a SELECT before inserting — if a bundle already exists, returns the existing ID and does nothing else
3. This means: the first failure's context is preserved across retries, escalation, and repeated diagnostic runs

**Why first failure?** Because the first failure captures the original breaking state. Subsequent failures happen in a different context (different model, different agent, retry with patched prompt) and would obscure the root cause.

### 2c. Data Schema

#### New table: `failure_bundles`

```sql
CREATE TABLE failure_bundles (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    worker_run_id INTEGER REFERENCES worker_runs(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Structured evidence
    state_timeline JSON NOT NULL,        -- [{ts, event, from_state, to_state}]
    triggering_exception TEXT,           -- raw error_log (first 2000 chars)
    first_failing_command TEXT,          -- best-effort from transcript (nullable)
    cancellation_reason TEXT,            -- human-readable cause

    -- Classification
    error_type TEXT,                     -- classify_error_type() result code
    escalation_tier_at_failure INTEGER,
    retry_count_at_failure INTEGER,

    -- Run metrics at time of failure
    model_used TEXT,
    duration_seconds REAL,
    exit_code INTEGER,

    UNIQUE(task_id)
);
```

#### `Task` table — new column

```sql
ALTER TABLE tasks ADD COLUMN failure_bundle_id TEXT REFERENCES failure_bundles(id);
```

### 2d. `state_timeline` Format

Built at capture time by combining available timestamps:

```json
[
  {"ts": "2026-02-25T08:00:00Z", "event": "task_created",   "to_state": "active"},
  {"ts": "2026-02-25T08:01:00Z", "event": "task_started",   "from_state": "not_started", "to_state": "in_progress"},
  {"ts": "2026-02-25T08:01:05Z", "event": "worker_spawned", "worker_id": "xxx", "model": "claude-sonnet-4-6"},
  {"ts": "2026-02-25T08:06:00Z", "event": "worker_failed",  "from_state": "in_progress", "to_state": "not_started",
   "error_type": "session_error", "escalation_tier": 1}
]
```

Events included:
- `task_created` — from `Task.created_at`
- `task_started` — from `Task.started_at`
- `worker_spawned` — from `WorkerRun.started_at` (worker_id, model)
- Prior failure events — from earlier `WorkerRun` rows for this task (succeeded=False)
- `worker_failed` — the current failing run

### 2e. `first_failing_command`

Best-effort extraction from the session transcript (already parsed for token usage). Look for first tool_use call that returned a non-zero exit code or error. If the transcript is unavailable or unparseable, store `None`. This field is informational — not required for correctness.

### 2f. `cancellation_reason` Classification

Map `error_type` and `WorkerRun.timeout_reason` to a human-readable reason:

| error_type | cancellation_reason |
|---|---|
| `session_timeout` / `timeout_*` | "Worker timed out" |
| `budget_exhausted` / `quota_exceeded` | "Model budget/quota exceeded" |
| `sessions_spawn_failed` | "Failed to spawn agent session" |
| `validity_contract` | "Run validity contract violated" |
| `no_file_changes` | "Worker produced no file changes" |
| everything else | "Worker exited with error: {error_type}" |

---

## 3. Tradeoffs Considered

### Option A: JSON blob on WorkerRun (rejected)
Extending `WorkerRun.task_log` with failure evidence would work but conflates operational metrics with diagnostic evidence. `task_log` is unbounded JSON. Querying it requires JSON extraction. A dedicated table is cleaner and queryable.

### Option B: New table (chosen)
Separate concern, own schema, unique constraint enforces first-failure semantics naturally, queryable with plain SQL, zero impact on existing WorkerRun reads.

### Option C: Persist to filesystem (rejected)
The existing architecture is all DB-backed. Filesystem artifacts require path management and would be lost on DB reset. JSON in SQLite is the right call for this system.

### First-failure only vs. per-run bundle
First-failure is chosen because it preserves the breaking state. Per-run bundles would need a separate mechanism to identify "the original failure" and would accumulate unboundedly. A future enhancement could capture a lightweight "retry evidence" blob on WorkerRun itself if needed.

---

## 4. Implementation Plan

### Phase 1: Schema (small)
**Handoff to programmer — H1**
- Migration script: create `failure_bundles` table, add `Task.failure_bundle_id` column
- SQLAlchemy model in `app/models.py`

### Phase 2: FailureBundleBuilder service (medium)
**Handoff to programmer — H2**
- `app/services/failure_bundle.py`
- `FailureBundleBuilder.capture(task, worker_run_id, error_log, exit_code, model, duration_seconds)`
- Build timeline from task + WorkerRun history
- First-failing-command extraction (best-effort)
- Idempotency check (SELECT before INSERT)

### Phase 3: Worker hook (small)
**Handoff to programmer — H3**
- In `worker._handle_completion()` failure branch
- Call `FailureBundleBuilder.capture()` after task is marked failed
- Pass `failure_bundle_id` through to escalation calls and spawned diagnostic tasks (in notes/context)

### Phase 4: Diagnostic injection (small)
**Handoff to programmer — H4**
- In `diagnostic_triggers._build_prompt()` and `_spawn_diagnostic()`
- Load `FailureBundle` for the failing task
- Inject bundle as structured section into the diagnostic prompt

### Phase 5: FailureExplainer integration (small)
**Handoff to programmer — H5**
- In `FailureExplainerService` (if/when implemented per failure-explainer-design.md)
- Load `FailureBundle` by task_id
- Include `state_timeline`, `triggering_exception`, `first_failing_command`, `cancellation_reason` in `supporting_data`
- Use `error_type` to skip redundant DB queries for known failure codes

---

## 5. Testing Strategy

### Unit tests (`tests/test_failure_bundle.py`)

1. **Build structure test** — Mock Task + WorkerRun, assert timeline has expected events in order
2. **Idempotency test** — Call `capture()` twice for same task_id, assert only one DB row, second call returns existing ID
3. **First-failing-command** — Mock transcript with tool_use error, assert field populated correctly; mock no transcript, assert field is None
4. **cancellation_reason mapping** — Test each error_type → human reason mapping
5. **Task.failure_bundle_id set** — After capture(), assert Task row has FK set

### Integration test (in `tests/test_worker.py`)

6. Simulate a worker failure through `_handle_completion()`, assert `FailureBundle` row exists with expected task_id
7. Simulate second failure (retry), assert no duplicate bundle, bundle row unchanged

### Diagnostic prompt test

8. Assert diagnostic prompt contains `## Failure Evidence Bundle` section when bundle exists
9. Assert diagnostic prompt still works when no bundle exists (graceful degradation)

---

## 6. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Timeline reconstruction is lossy (only have timestamps, not events) | Document clearly in schema — timeline is best-effort reconstruction, not event sourcing |
| `first_failing_command` extraction fails | Make it nullable; treat it as enrichment, not required |
| Bundle persisted after task marked failed but before retry spawned — FK not available for retry task | The `worker._handle_completion()` flow is synchronous within the DB session; ensure bundle write and task write commit before escalation spawns |
| Diagnostic agents don't use the bundle even if injected | Verify by testing prompt format; add "IMPORTANT: Do not re-derive failure context — use the bundle below" instruction |

---

## 7. ARCHITECTURE.md Update Note

After implementation, add to "Recent Architectural Changes":
- **First-Failure Evidence Bundle** — Deterministic failure packet generated once per failed task. New `failure_bundles` table with unique-per-task constraint. Contains state transition timeline, triggering exception, first failing command, cancellation reason. Captured in `worker._handle_completion()`, injected into diagnostic prompts, consumed by `FailureExplainerService`. Design: `docs/failure-evidence-bundle-design.md`.
