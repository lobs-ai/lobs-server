# Research-to-Roadmap Bridge — Design Document

**Status:** Research Complete / Ready for Architecture  
**Created:** 2026-02-25  
**Author:** researcher  
**Origin:** Initiative 317d4f58-bc81-4217-978b-54bd491c8a04  
**Summary:** `research-findings.md`

---

## 1. Problem

The Lobs initiative pipeline has a completion gap:

```
propose → sweep review → approve → researcher task spawned → task completed → ???
```

At the `???` step, research findings land in a markdown document in the research directory. There is no machine-readable signal about what to do next. No programmer task is spawned. No archive signal is sent. The initiative stays `approved` indefinitely.

**The result:** Idea accumulation. The backlog of approved-but-unshipped initiatives grows. Trust in the pipeline erodes because ideas that got approved never visibly progress.

**Quantitative signal:** At the time of this writing, 27 initiatives are in `deferred` state and an unknown number of `approved` initiatives have completed research tasks with no follow-on action. The weekly ops brief doesn't surface this.

---

## 2. Goal

Every researcher task that originates from an `AgentInitiative` must close with a **Decision Memo** — a short structured artifact that answers: *build, no-build, or defer?*

The memo:
1. Is machine-readable (JSON stored in `Task.decision_memo`)
2. Contains enough information for an automatic programmer handoff (if BUILD)
3. Triggers archive of the initiative (if NO-BUILD)
4. Surfaces a named re-evaluation condition (if DEFER)

---

## 3. Decision Memo Schema

### 3.1 JSON Schema (`DecisionMemoPayload`)

```python
class DecisionMemoPayload(BaseModel):
    problem: str                        # max 200 chars — concrete pain, not abstract description
    user_segment: str                   # max 100 chars — who feels this most
    spec_touchpoints: list[str]         # affected DB tables, API routes, services
    mvp_scope: list[str]                # max 3 items — the minimum that delivers value
    out_of_scope: list[str]             # explicit exclusions for MVP
    owner_agent: Literal["programmer", "architect", "researcher"]
    estimated_tier: Literal["A", "B", "C"]   # A=hours, B=days, C=week+
    blocking_dependencies: list[str]    # task IDs blocking this, or []
    decision: Literal["build", "no-build", "defer"]
    rationale: str                      # max 300 chars — the "why now" or "why not"
    defer_trigger: Optional[str]        # required if decision == "defer"; specific condition
```

### 3.2 Markdown Render (for inbox / daily brief display)

```markdown
## 🗺️ Research Decision Memo — [INITIATIVE TITLE]

**Initiative ID:** [id]  
**Research Task:** [task.id]  
**Date:** [ISO date]  
**Author:** [agent]

---

### Problem
[problem field]

### User Segment
[user_segment field]

### Spec Touchpoints
[spec_touchpoints as bullet list]

### MVP Scope
- [ ] [mvp_scope[0]]
- [ ] [mvp_scope[1]]
- [ ] [mvp_scope[2]]

### Out of Scope (for MVP)
[out_of_scope as bullet list]

### Owner
**Agent:** [owner_agent]  
**Estimated tier:** [estimated_tier]  
**Blocking dependencies:** [blocking_dependencies or "none"]

### Decision
**→ [BUILD | NO-BUILD | DEFER]**

**Rationale:** [rationale]

**If DEFER:** Revisit trigger: [defer_trigger]
```

---

## 4. The Gate Mechanism

### 4.1 Schema Changes

**New column: `Task.source_initiative_id`**
- Type: `String`, nullable, FK → `agent_initiatives.id`, indexed
- Set at task spawn time in `initiative_decisions._spawn_task_for_initiative()`
- Migration: additive, safe to deploy

**New column: `Task.decision_memo`**
- Type: `JSON`, nullable
- Populated at task close via `PATCH /api/tasks/{id}`
- Indexed on `IS NOT NULL` for pruning queries

### 4.2 Gate Logic

Gate triggers when `PATCH /api/tasks/{id}` sets `status = "completed"` AND `task.source_initiative_id IS NOT NULL` AND `task.agent == "researcher"`.

**48-hour grace period:** If `task.started_at` is less than 48 hours ago, gate is `WARN` (returns 200 with a `warnings` field). After 48 hours, gate is `BLOCK` (returns 422).

```
PATCH /api/tasks/{id}
  Body: { "status": "completed" }  ← no decision_memo
  Task age: < 48h
  Response: 200 + { "warnings": ["Research task will require decision_memo within 48h of start."] }

PATCH /api/tasks/{id}
  Body: { "status": "completed" }  ← no decision_memo
  Task age: > 48h  
  Response: 422 { "detail": "Tasks from research initiatives require a decision_memo at close." }

PATCH /api/tasks/{id}
  Body: { "status": "completed", "decision_memo": {...} }  ← memo included
  Response: 200 OK
```

### 4.3 Post-Close Automation

When a task closes with a valid memo, trigger these side effects:

| `decision` | Automated Action |
|-----------|-----------------|
| `"build"` | Create programmer handoff from `owner_agent` + `mvp_scope` + `spec_touchpoints`. Mark `AgentInitiative.status = "tasked"`. |
| `"no-build"` | Mark `AgentInitiative.status = "archived"` with `rationale` as reason. |
| `"defer"` | Mark `AgentInitiative.status = "deferred"` with `defer_trigger` in `decision_summary`. Pruning review surfaces these after 30 days. |

Phase 1 implementation: store the memo, trigger is manual (human reads memo, takes action).  
Phase 2: automate the side effects listed above.

---

## 5. Weekly Pruning Review

### 5.1 Queries

Run every Monday 8am ET. Three sweeps:

**Sweep A — Orphan initiatives:**
```sql
SELECT * FROM agent_initiatives 
WHERE status = 'approved' 
  AND task_id IS NULL 
  AND created_at < NOW() - INTERVAL '14 days'
```

**Sweep B — Memo-missing completions:**
```sql
SELECT i.*, t.id as task_id, t.finished_at
FROM agent_initiatives i
JOIN tasks t ON t.source_initiative_id = i.id
WHERE t.status = 'completed' 
  AND t.decision_memo IS NULL
```

**Sweep C — Stale proposals:**
```sql
SELECT * FROM agent_initiatives
WHERE status = 'proposed'
  AND created_at < NOW() - INTERVAL '30 days'
```

### 5.2 Output

Creates one inbox item of type `suggestion`, urgency `🟢 Advisory`:

```
## 📋 Weekly Initiative Pruning — [DATE]

**Orphan initiatives (approved, no task):** N
**Memo-missing completions:** N
**Stale proposals (30d+):** N

[Grouped lists with IDs and titles]

### Suggested Actions
- For orphans: spawn a task or archive
- For memo-missing: write a retroactive memo
- For stale proposals: review and reject/defer

*Auto-rejection rule: Proposals > 60 days old with no activity will be auto-rejected next Monday.*
```

### 5.3 Auto-Rejection Rule

Lobs automatically rejects proposals older than 60 days with `reason = "Pruned: no activity for 60 days"`. This is reversible (status can be reset to `proposed`) but reduces noise without human involvement.

---

## 6. Implementation Plan

### Phase 1: Schema + Gate (Programmer, ~Tier B)

**Files to create/modify:**

1. **New Alembic migration** — `add_decision_memo_to_tasks`
   - Add `Task.source_initiative_id` (String, FK, nullable, indexed)
   - Add `Task.decision_memo` (JSON, nullable)

2. **`app/schemas.py`** — Add `DecisionMemoPayload` Pydantic model

3. **`app/routers/tasks.py`** — `PATCH /api/tasks/{id}` gate logic
   - Check initiative lineage on completion
   - 48h grace / hard block
   - Store memo in `task.decision_memo`

4. **`app/orchestrator/initiative_decisions.py`** — Set `Task.source_initiative_id` when spawning tasks from initiatives

5. **`AGENTS.md` (lobs-server)** — Add note: "Researcher tasks with initiative lineage must close with `decision_memo`"

### Phase 2: Pruning Service (Programmer, ~Tier A)

6. **`app/services/initiative_pruning.py`** — Implement three sweeps, create inbox item
7. **Orchestrator engine** — Add weekly Monday 8am timer, same pattern as `_brief_hour_et`

### Phase 3: Auto-Handoff (Programmer, ~Tier B)

8. **`app/routers/tasks.py`** — Post-close side effects based on `decision` field
9. **Auto-create programmer task** from BUILD memos using decompose API

---

## 7. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Boilerplate memos to pass gate | Medium | Medium | Pruning review spot-checks; Lobs flags memos with no spec touchpoints |
| Gate blocks fast researcher tasks | Low | Low | 48h grace covers it; `no-build` memos take 2 minutes |
| `source_initiative_id` not set (orchestrator path) | Medium | High | Log warning in orchestrator when initiative task is spawned without the FK; Phase 1 deploys both schema + FK-population together |
| Deferred items pile up | Medium | Low | Pruning review surfaces defers older than 30 days; defer requires `defer_trigger` |

---

## 8. Success Metrics

- **Memo coverage:** % of researcher tasks with initiative lineage that close with a valid memo (target: 100% within 30 days of deploy)
- **Conversion rate:** % of BUILD decisions that result in a programmer task within 7 days (target: > 80%)
- **Archive rate:** % of NO-BUILD decisions that result in `AgentInitiative.status = "archived"` (target: 100%)
- **Pruning backlog:** Count of orphan initiatives at each weekly review (target: trending to 0 over 4 weeks)

---

## 9. Related Docs

- `docs/communication/decision-card-spec.md` — The analogous pattern for blocked-task human approvals
- `docs/goal-to-plan-decomposition-design.md` — The BUILD path output (decompose → programmer task)
- `app/orchestrator/initiative_decisions.py` — Where initiative → task spawning happens
- `ARCHITECTURE.md` — Recent architectural changes section
