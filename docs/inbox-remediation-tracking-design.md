# Inbox Remediation Tracking — Design Document

**Status:** Ready for implementation  
**Created:** 2026-02-24  
**Task ID:** 74373ad5-8344-4e11-ba5f-780ba9097715  
**Risk tier:** C (cross-project migration, internal plumbing)

---

## 1. Problem Statement

When `InboxProcessor._create_task()` converts an approved inbox item into a task, the connection is severed. No field on `Task` records which inbox item spawned it. Tasks can then transition to `rejected` with no reason code, and no view exists to surface inbox-sourced work that is silently decaying.

The result: **approved → queued → cancelled loops** that look like progress but deliver nothing. Throughput on already-approved, high-value fixes is low because no one detects the decay.

Three concrete gaps:

| Gap | Current State | Desired State |
|-----|--------------|---------------|
| Inbox→Task linkage | Lost at creation | `source_inbox_item_id` stored on Task |
| Cancellation accountability | Status changes silently | `cancel_reason` required on reject |
| Reviewer visibility | None | `/api/inbox/stuck-remediations` list |

---

## 2. Proposed Solution

Four incremental changes, each independently deployable:

### 2.1 Add Two Columns to `Task`

```
tasks.source_inbox_item_id  String  nullable  FK → inbox_items.id
tasks.cancel_reason         String  nullable  enum-ish reason code
```

**`cancel_reason` valid values:**
- `duplicate` — another task covers this
- `superseded` — approach changed, different task handles it
- `out_of_scope` — no longer relevant
- `blocked_indefinitely` — upstream blocker with no path forward
- `user_decision` — Rafe explicitly rejected it
- `automated_cleanup` — removed by a system sweep

These are advisory strings, not DB-enforced enums (easier to extend). Validation lives in the router.

### 2.2 Set Linkage at Spawn

In `InboxProcessor._create_task()`, add:
```python
task.source_inbox_item_id = inbox_item.id
```

That's a one-liner. The field propagates without changing any other behavior.

### 2.3 Enforce `cancel_reason` on Rejection

`PATCH /api/tasks/{id}/status` currently accepts any status transition with no annotation.

Add validation: **when `status` is set to `rejected`, `cancel_reason` must be provided** in the request body. If absent, return `422` with a clear message.

Scope: apply to ALL tasks (not just inbox-sourced). This fixes the broader silent-decay problem, not just inbox items. Inbox-sourced items benefit most, but the discipline is system-wide.

### 2.4 Stuck Remediations Endpoint

`GET /api/inbox/stuck-remediations`

Returns tasks where `source_inbox_item_id IS NOT NULL` AND any of:
- `status = 'active'` AND `updated_at < NOW() - 72h` AND `work_state = 'not_started'` (approved, never picked up)
- `status = 'active'` AND `work_state = 'in_progress'` AND `updated_at < NOW() - 24h` (started, gone quiet)
- `status = 'rejected'` AND `cancel_reason IS NULL` (silently cancelled — legacy data)

Response includes the task, its `source_inbox_item_id`, inbox item title (joined), and days-stale count.

### 2.5 Wire into Daily Ops Brief

`BriefService` gains a `StuckRemediationsAdapter` that calls the above query. If it returns ≥1 result, a **"⚠️ Stuck Remediations"** section is added to the daily brief:

```
### ⚠️ Stuck Remediations (2)
- "Fix cancellation audit trail" — approved 5d ago, never started  [task: abc123]
- "Upgrade model router" — in-progress 2d with no update  [task: def456]
```

This section is **omitted entirely** if there are no stuck items (zero noise on clean days).

---

## 3. Tradeoffs

### What we considered

**Alternative: Don't FK, just embed inbox_item_id in task notes**  
Rejected. Notes are freetext; queries against them are fragile and slow. A real column is indexable and queryable.

**Alternative: Strict enum in DB for cancel_reason**  
Rejected. SQLite enum support is minimal; Python-level validation is sufficient and easier to extend.

**Alternative: Separate `remediation_tasks` join table**  
Overkill. One nullable FK on Task is sufficient for the one-inbox-to-many-tasks case. The join table adds complexity with no immediate benefit.

**Alternative: New `cancelled` status vs reusing `rejected`**  
The existing `rejected` status already covers "this task was stopped deliberately." Adding `cancel_reason` to annotate *why* is cheaper than splitting statuses. If we need a new lifecycle state later, we can add it then.

### What we're not doing (yet)

- We're NOT tracking re-creation loops (same inbox item spawning N tasks across N rejections). That would require a `generation` counter. Flagged as future work.
- We're NOT backfilling `source_inbox_item_id` for historical tasks (the note footprint in task notes says `*Created from inbox item: ...*` — that's a manual recovery path if needed).

---

## 4. Implementation Plan

All tasks are independent after Task 1 (schema).

### Task 1: DB Migration — add two columns to Task (small)
- Add `source_inbox_item_id` (String, nullable, FK to `inbox_items.id`) to `Task` model in `app/models.py`
- Add `cancel_reason` (String, nullable) to `Task` model
- Add Alembic migration (follow `docs/database-migrations.md` pattern)
- Update `TaskSchema` / `TaskCreate` / `TaskUpdate` in `app/schemas.py` to include both fields (both optional)
- **Acceptance:** columns exist in DB, schema serializes them, no existing tests break

### Task 2: InboxProcessor linkage (small)
- In `app/orchestrator/inbox_processor.py`, `_create_task()`: set `task.source_inbox_item_id = inbox_item.id`
- **Acceptance:** creating a task from inbox approval → Task row has `source_inbox_item_id` populated

### Task 3: cancel_reason enforcement on rejection (small)
- In `app/routers/tasks.py`, `PATCH /tasks/{id}/status`:
  - If incoming `status == "rejected"` and `cancel_reason` is None/missing → return 422
  - Store `cancel_reason` on the task when provided
- Update `TaskStatusUpdate` schema to include optional `cancel_reason: str | None`
- Valid codes: `duplicate`, `superseded`, `out_of_scope`, `blocked_indefinitely`, `user_decision`, `automated_cleanup`
- Return list of valid codes in 422 error body for discoverability
- **Acceptance:** `PATCH status=rejected` without cancel_reason returns 422; with it, stores the reason; other status transitions unaffected

### Task 4: Stuck remediations endpoint (small)
- Add `GET /api/inbox/stuck-remediations` to `app/routers/inbox.py`
- Query: Tasks where `source_inbox_item_id IS NOT NULL` AND:
  - `status='active'`, `work_state='not_started'`, `updated_at < now - 72h` → label `stale_queued`
  - `status='active'`, `work_state='in_progress'`, `updated_at < now - 24h` → label `stale_in_progress`
  - `status='rejected'`, `cancel_reason IS NULL` → label `silent_cancel`
- Join to `inbox_items` for `inbox_title` field
- Response: `list[StuckRemediation]` with fields: `task_id`, `task_title`, `inbox_item_id`, `inbox_title`, `reason` (the label above), `days_stale`
- **Acceptance:** endpoint returns correct items; empty list on clean system; no auth regression

### Task 5: Wire into Daily Ops Brief (small)
- In `app/services/brief_service.py` (once it exists, per `docs/daily-ops-brief-design.md`):
  - Add `StuckRemediationsAdapter` that queries the same logic as Task 4 (share the query helper)
  - If count > 0 → add `BriefSection("Stuck Remediations", "⚠️", items)` to brief
  - If count == 0 → omit section entirely
- **Acceptance:** brief includes section when tasks are stuck; omits when all clear

---

## 5. Testing Strategy

### Task 1 (schema)
- Migration runs cleanly on existing DB (nullable columns, no data loss)
- `Task()` created without new fields still works (defaults to NULL)

### Task 2 (linkage)
- Unit test `_create_task()` with a mock `InboxItem`: assert returned `Task.source_inbox_item_id == inbox_item.id`

### Task 3 (cancellation enforcement)
- Integration test: `PATCH /tasks/{id}/status` body `{status: "rejected"}` → 422
- Integration test: `PATCH /tasks/{id}/status` body `{status: "rejected", cancel_reason: "user_decision"}` → 200, task has cancel_reason
- Integration test: `PATCH /tasks/{id}/status` body `{status: "completed"}` → 200 (no cancel_reason required)

### Task 4 (endpoint)
- Unit test: seed DB with 3 tasks (one stale_queued, one silent_cancel, one healthy). Query returns 2.
- Integration test: `GET /api/inbox/stuck-remediations` returns 200 with correct shape

### Task 5 (brief)
- Mock the stuck remediation query to return 1 item → assert BriefSection is present
- Mock to return 0 → assert section is absent

---

## 6. Files to Change

| File | Change |
|------|--------|
| `app/models.py` | Add `source_inbox_item_id`, `cancel_reason` to `Task` |
| `app/schemas.py` | Add fields to `Task*` schemas; new `StuckRemediation` schema |
| `alembic/versions/*.py` | New migration |
| `app/orchestrator/inbox_processor.py` | Set `source_inbox_item_id` in `_create_task()` |
| `app/routers/tasks.py` | Enforce `cancel_reason` on rejection |
| `app/routers/inbox.py` | Add `GET /stuck-remediations` |
| `app/services/brief_service.py` | Add stuck remediations adapter/section |

---

## 7. Risks & Edge Cases

- **Existing rejected tasks** have no `cancel_reason`. The `silent_cancel` label in the stuck-remediations query surfaces these automatically, creating a one-time backlog. Programmers should NOT backfill — let the reviewer triage them via the new endpoint.
- **Brief service may not exist yet** (daily-ops-brief task may be in-flight). Task 5 should be blocked on that task completing. Programmer can stub it or note the dependency.
- **InboxProcessor creates tasks in bulk** (multiple tasks per action). Each should get the same `source_inbox_item_id`. The single-field approach handles this naturally.
- **FK constraint**: if `inbox_items` row is deleted, the FK will raise. Use a soft-delete pattern or set `ondelete="SET NULL"` on the FK.

---

## 8. Architecture Impact

This is internal plumbing. No new product behavior, no UI changes. The only externally visible change is:
- New fields on Task responses (`source_inbox_item_id`, `cancel_reason`)
- New endpoint (`GET /api/inbox/stuck-remediations`)
- Daily brief may gain a new section

ARCHITECTURE.md should be updated to mention the inbox→task tracking linkage under the Orchestrator section.
