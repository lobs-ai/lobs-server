# Goal-to-Plan Task Decomposition — Design Document

**Status:** Ready for implementation  
**Created:** 2026-02-25  
**Task ID:** 354cc69f-1b1d-4969-8c49-5174d057a337  
**Risk tier:** C (new feature, additive schema changes)

---

## 1. Problem Statement

Users must create tasks manually, one at a time. When someone has a vague objective ("add OAuth login" or "improve test coverage for the orchestrator"), they have to decompose it themselves into parent/subtasks, assign owners, and figure out ordering — pure project-management overhead.

**Goal:** Convert a natural-language objective into a structured execution plan (parent task + ordered subtasks + suggested agent owners) in one action, with a preview/edit step before committing anything to the database.

---

## 2. Proposed Solution

### Overview

Two-phase API: **generate → commit**.

```
POST /api/tasks/decompose        → calls LLM, returns preview plan, saves pending record
POST /api/tasks/decompose/{id}/commit  → user-confirmed plan, creates tasks in DB
DELETE /api/tasks/decompose/{id} → discard (learning signal, nothing created)
```

The client (Mission Control) shows the generated plan, lets the user edit it, then commits. The backend stores both the raw LLM output and the final committed plan for learning.

### Architecture

```
Client
  │
  ├─ POST /api/tasks/decompose (goal_text, project_id)
  │      ├─ DecompositionService.generate()
  │      │    ├─ ModelChooser → choose standard-tier model
  │      │    ├─ Gateway sessions_spawn (structured JSON prompt)
  │      │    ├─ Poll sessions_history → parse JSON plan
  │      │    └─ Store TaskDecomposition(status=pending)
  │      └─ Return DecompositionPreview
  │
  ├─ POST /api/tasks/decompose/{id}/commit (edited plan)
  │      ├─ DecompositionService.commit()
  │      │    ├─ Create parent Task
  │      │    ├─ Create subtasks (Task.parent_task_id = parent.id, ordered by subtask_order)
  │      │    └─ Update TaskDecomposition(status=committed, committed_plan=...)
  │      └─ Return { parent_task_id, subtask_ids }
  │
  └─ DELETE /api/tasks/decompose/{id}
         └─ TaskDecomposition.status = discarded
```

---

## 3. Data Model Changes

### 3.1 Add Two Columns to `tasks`

```sql
tasks.parent_task_id   TEXT  NULLABLE  REFERENCES tasks(id)
tasks.subtask_order    INT   NULLABLE
```

`parent_task_id`: enables subtask hierarchy. A task with no parent is a top-level task (existing behavior unchanged). A task with a parent is a subtask.

`subtask_order`: ordering within a parent. Int, lower = earlier. NULL means no ordering (top-level tasks, or manually-created subtasks without ordering).

No cascading deletes — if parent is deleted, subtasks become orphaned (parent_task_id becomes a dangling FK). SQLite doesn't enforce FK by default; this is acceptable. Mission Control should handle orphan display (show as top-level when parent missing).

### 3.2 New Table: `task_decompositions`

Stores every decompose attempt for audit and learning.

```sql
CREATE TABLE task_decompositions (
    id              TEXT PRIMARY KEY,
    goal_text       TEXT NOT NULL,         -- original user input
    project_id      TEXT REFERENCES projects(id),
    status          TEXT NOT NULL,         -- pending / committed / discarded
    model_used      TEXT,                  -- which model generated the plan
    raw_llm_output  JSON,                  -- LLM response before user edits
    committed_plan  JSON,                  -- what user actually committed (may differ)
    parent_task_id  TEXT REFERENCES tasks(id),  -- set when committed
    subtask_ids     JSON,                  -- list of created subtask IDs (when committed)
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    committed_at    DATETIME
);
```

**Why store both raw + committed?** Learning: if the user edits the plan significantly, that's a signal the LLM got it wrong. Diffing raw vs committed reveals prompt quality issues.

---

## 4. API Specification

### 4.1 `POST /api/tasks/decompose`

**Request:**
```json
{
  "goal_text": "Add OAuth2 login with Google and GitHub",
  "project_id": "proj-abc",
  "context": "Optional extra context like stack, constraints, team size"
}
```

**Response (200):**
```json
{
  "decomposition_id": "decomp-uuid",
  "parent_task": {
    "title": "Add OAuth2 login (Google + GitHub)",
    "notes": "Umbrella task for OAuth integration. Depends on all subtasks.",
    "agent": "architect",
    "shape": "initiative"
  },
  "subtasks": [
    {
      "title": "Design OAuth2 flow and provider config schema",
      "notes": "Decide on session model, token storage, scopes. Produce ADR.",
      "agent": "architect",
      "order": 1,
      "depends_on": []
    },
    {
      "title": "Implement OAuth2 backend routes and token exchange",
      "notes": "FastAPI routes for /auth/google and /auth/github callbacks. JWT generation.",
      "agent": "programmer",
      "order": 2,
      "depends_on": [1]
    },
    {
      "title": "Add OAuth login UI in Mission Control",
      "notes": "SwiftUI login screen with Google/GitHub buttons. Handle redirect flow.",
      "agent": "programmer",
      "order": 3,
      "depends_on": [2]
    },
    {
      "title": "Write integration tests for OAuth flows",
      "notes": "Cover happy path and failure cases (revoked token, expired session).",
      "agent": "programmer",
      "order": 4,
      "depends_on": [2]
    }
  ],
  "rationale": "Decomposed into 4 subtasks: architecture decision, backend, UI, tests. Backend and UI can overlap once the flow is designed."
}
```

**Errors:**
- `422` — missing required fields
- `503` — Gateway unreachable, LLM call failed
- `504` — LLM timed out (Gateway returned no response within 45s)

**Side effects:** Creates a `TaskDecomposition` row with `status=pending`. No tasks are created yet.

---

### 4.2 `POST /api/tasks/decompose/{decomposition_id}/commit`

**Request:** The edited plan. Same structure as the preview response's task objects. Client sends back the (optionally edited) plan.

```json
{
  "parent_task": {
    "title": "Add OAuth2 login (Google + GitHub)",
    "notes": "Umbrella task.",
    "agent": "architect",
    "shape": "initiative",
    "status": "inbox"
  },
  "subtasks": [
    {
      "title": "Design OAuth2 flow and provider config schema",
      "notes": "...",
      "agent": "architect",
      "order": 1,
      "blocked_by": []
    }
  ]
}
```

**Response (201):**
```json
{
  "parent_task_id": "task-uuid-parent",
  "subtask_ids": ["task-uuid-1", "task-uuid-2", "task-uuid-3", "task-uuid-4"]
}
```

**Side effects:**
- Creates parent task + N subtask records
- Sets `Task.parent_task_id` on each subtask pointing to parent
- Sets `Task.subtask_order` from `order` field
- Updates `TaskDecomposition.status = committed`, stores `committed_plan`, sets `parent_task_id`, `subtask_ids`, `committed_at`
- Fires a `ControlLoopEvent(TaskCreated)` for the parent task (subtasks get their own if the orchestrator picks them up)

**Blocked_by translation:** `depends_on` in the preview uses index references (1-based order). The commit converts these to actual task IDs for `Task.blocked_by`.

**Errors:**
- `404` — decomposition not found
- `409` — decomposition already committed or discarded
- `422` — invalid plan structure

---

### 4.3 `DELETE /api/tasks/decompose/{decomposition_id}`

**Response (200):** `{"status": "discarded"}`

**Side effects:** Sets `TaskDecomposition.status = discarded`. No tasks were created; nothing to clean up. Learning: discarded plans are negative examples.

---

### 4.4 `GET /api/tasks/decompose/{decomposition_id}`

**Response (200):** Full `TaskDecomposition` record including status and (if committed) the parent and subtask IDs.

Useful for the client to check status of a pending decomposition (e.g., if generate call was fire-and-forget in a background task).

---

## 5. DecompositionService

**Location:** `app/services/decomposition.py`

**Key methods:**

```python
class DecompositionService:
    def __init__(self, db: AsyncSession): ...

    async def generate(
        self,
        goal_text: str,
        project_id: str,
        context: str | None = None,
    ) -> DecompositionPreview:
        """Call LLM, save pending record, return preview plan."""
        
    async def commit(
        self,
        decomposition_id: str,
        parent_task_data: dict,
        subtasks_data: list[dict],
    ) -> CommitResult:
        """Create tasks, update record, return created IDs."""

    async def discard(self, decomposition_id: str) -> None:
        """Mark record discarded."""
    
    async def get(self, decomposition_id: str) -> TaskDecompositionRecord:
        """Fetch decomposition record."""

    def _build_prompt(self, goal_text: str, project_title: str, context: str | None) -> str:
        """Build LLM prompt requesting structured JSON plan."""

    def _parse_llm_response(self, raw: str) -> dict:
        """Extract JSON from LLM response (handles ```json wrapping)."""

    async def _call_llm(self, prompt: str) -> tuple[str, str]:
        """
        Spawn a Gateway session, poll history, return (raw_text, model_used).
        Uses ModelChooser with purpose='decomposition', tier preference 'standard'.
        Polls up to 45 seconds (9 polls × 5s each).
        """
```

**LLM prompt design:** The prompt asks the model to act as a technical project planner and return a JSON structure with `parent_task`, `subtasks[]`, and `rationale`. Each subtask has `title`, `notes`, `agent`, `order`, `depends_on` (list of order integers). Prompt enforces JSON-only output (no prose) and includes the allowed agent types.

**Model tier:** `standard` — needs enough reasoning to produce a coherent dependency order. `medium` as fallback.

**Polling:** Same pattern as `auto_assigner.py` — spawn a Gateway session, poll `sessions_history` in a loop until we get an assistant message, or timeout. Timeout = 45s total (suitable for standard-tier models).

---

## 6. Tradeoffs

### Two-phase vs. one-shot
**Chosen:** Two-phase (generate preview, then commit).  
**Why:** Users must see and edit the plan before tasks are created. One-shot would require immediate deletion if the plan is wrong — worse UX and leaves orphan data.  
**Cost:** Slightly more complex API. Worth it for the safety guarantee.

### Synchronous vs. async decomposition
**Chosen:** Synchronous — client blocks on `POST /api/tasks/decompose` until LLM responds.  
**Why:** Plans are small (≤10 subtasks). Standard-tier models respond in 5-15 seconds. Simpler than a polling/webhook flow.  
**Risk:** If LLM takes >30s, client sees a timeout. Mitigated by 45s server-side timeout and clear `504` response.  
**Alternative rejected:** Background task with polling — needed if we ever support very large decompositions (20+ subtasks) or very slow models, but premature for now.

### `blocked_by` as task IDs vs. index references
**Chosen:** Preview uses index-based `depends_on` (simple for preview display). Commit converts to task IDs in `Task.blocked_by` (consistent with existing task schema).  
**Why:** Preview tasks don't have IDs yet; indices work during planning. At commit time, we generate UUIDs and rewrite references.

### Parent-child vs. flat tagging
**Chosen:** `parent_task_id` FK on Task.  
**Why:** Enables queries like "get all subtasks of task X". Clean relational structure. Alternative (tag `parent:uuid` in notes or JSON field) is queryable but fragile.  
**Cost:** New column + migration. Low risk.

### LLM provider / model
**Chosen:** ModelChooser with `standard` tier preference.  
**Why:** Consistent with how the rest of the orchestrator routes. Respects existing budget guardrails. Falls back to `medium` if standard is budget-capped.

---

## 7. Implementation Plan

### Task 1 — DB migration and model updates (small)
- Add `parent_task_id`, `subtask_order` columns to `tasks`
- Create `task_decompositions` table
- Update `Task` SQLAlchemy model
- Update `Task` Pydantic schemas (`parent_task_id`, `subtask_order` fields)
- Add `TaskDecomposition` SQLAlchemy model and Pydantic schemas

**Acceptance:** Migration runs clean. Models have new fields. Existing task tests still pass.

### Task 2 — DecompositionService (medium)
- Implement `app/services/decomposition.py`
- Prompt design: request structured JSON with parent task + subtasks + rationale
- LLM call pattern: follow `auto_assigner._choose_agent_llm()` (sessions_spawn → poll sessions_history)
- Parse/validate LLM JSON response; handle failures gracefully (return 503 with message)
- Unit tests for `_parse_llm_response()` (mock LLM output variants including ```json wrapping)
- Integration test for `generate()` with mocked Gateway

**Acceptance:** `generate()` returns a valid `DecompositionPreview`. `_parse_llm_response()` handles at least 3 LLM output variants. Failure paths return appropriate exceptions.

### Task 3 — API router endpoints (small)
- Add four endpoints to `app/routers/tasks.py` (or new `app/routers/decompose.py` if cleaner)
- `POST /api/tasks/decompose` → service.generate()
- `POST /api/tasks/decompose/{id}/commit` → service.commit()
- `DELETE /api/tasks/decompose/{id}` → service.discard()
- `GET /api/tasks/decompose/{id}` → service.get()
- Input validation: `goal_text` required and non-empty; `project_id` must exist; plan validation on commit
- Wire into `app/main.py`

**Acceptance:** All four endpoints return correct status codes. Can round-trip: generate → commit → verify tasks in DB. `depends_on` indices are correctly converted to `Task.blocked_by` task IDs on commit.

### Task 4 — Learning metadata (small, can be done with Task 2)
- `TaskDecomposition.raw_llm_output` stores unparsed LLM response string
- `TaskDecomposition.committed_plan` stores the final committed plan JSON
- Status transitions enforced: `pending → committed` or `pending → discarded` (no re-commit)
- `GET /api/agent-learning/decompositions` endpoint (optional, low priority) — list decompositions for review

**Acceptance:** After generate+commit, decomposition record has both `raw_llm_output` and `committed_plan` stored. Status is `committed`.

---

## 8. Testing Strategy

### Unit tests
- `_parse_llm_response()`: test with clean JSON, ```json-wrapped JSON, prose with embedded JSON, invalid JSON → should raise
- `_build_prompt()`: verify required sections appear in prompt (goal, JSON schema, agent list)
- `commit()`: verify `depends_on` → `blocked_by` index-to-ID conversion

### Integration tests
- Round-trip test: POST decompose (mock Gateway) → POST commit → verify tasks in DB with correct `parent_task_id`, `subtask_order`, `blocked_by`
- Discard test: POST decompose → DELETE → verify status=discarded, no tasks created
- Double-commit test: POST decompose → commit → commit again → 409
- Missing project test: POST decompose with bad project_id → 404

### Edge cases to cover
- Empty subtasks list — valid (user stripped all subtasks, only parent created)
- Single subtask with no dependencies
- All subtasks depending on index 1 (fan-out pattern)
- LLM returns a plan with 15 subtasks — no hard limit, handle gracefully
- LLM times out — clean 504 with no orphan DB records (pending record is OK; it's just never committed)
- Partially invalid JSON (LLM truncates) — return 503 with explanation

---

## 9. Observability

- Log decomposition attempts: goal length, project, model chosen, response time, success/fail
- Log commit: parent_task_id, subtask count, was plan edited (compare raw vs committed)
- `TaskDecomposition.status` distribution is queryable: how many plans are committed vs discarded (quality signal)
- `raw_llm_output` vs `committed_plan` diff (future): automated quality scoring

---

## 10. Frontend Scope (lobs-mission-control)

Backend is the primary scope here. For completeness, the iOS app work includes:

1. **`TaskDecompositionRequest` / `TaskDecompositionPreview` / `TaskCommitRequest` models** in `Models.swift`
2. **`APIService.decomposeGoal()` and `APIService.commitDecomposition()`** methods
3. **Decompose flow UI**: text input for goal → loading state (LLM call) → preview list with edit capability → commit button
4. **Parent/subtask display**: Mission Control should visually group subtasks under their parent in the kanban view

These are separate handoffs to the Mission Control programmer.

---

## 11. Risk Summary

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| LLM produces invalid JSON | Medium | Low | Robust parser + 503 fallback |
| Standard-tier model times out (>45s) | Low | Medium | 504 with clear message; retry in UI |
| Budget guardrails cap standard tier | Medium | Low | Falls back to medium tier via ModelChooser |
| Parent-child creates performance issues on list queries | Low | Low | `parent_task_id` not on hot list queries today; index if needed |
| User commits plan with circular depends_on | Low | Low | Validate at commit time (cycle detection) |

---

*Architect: lobs-architect | 2026-02-25*
