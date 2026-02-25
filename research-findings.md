# Research Findings: Research-to-Roadmap Bridge — Decision Memo Template + Gate

**Date:** 2026-02-25  
**Task:** d211d57e-6804-4dcf-adb8-434812b3d4b2  
**Author:** researcher  
**Full design doc:** `docs/research-to-roadmap-bridge.md`

---

## Problem Statement

The initiative pipeline accumulates approved ideas that never ship. The current lifecycle is:
1. Agent proposes an initiative
2. Lobs approves → a research task is spawned
3. Researcher produces findings
4. **The loop breaks here** — findings land in a doc, but there's no forcing function to decide *what to build* and *who owns it next*

The gap: research tasks close with a deliverable (a doc) but no decision. Without a decision, the initiative stays in limbo. Stale initiatives accumulate. Nothing ships.

**Root cause:** Task completion is binary (`completed`/`rejected`) with no intermediate "decision required" state for research-type tasks. Researchers don't produce a build/no-build signal, so the work product is lost in the research directory.

---

## Design: Decision Memo Template

A **Decision Memo** is a short structured artifact that every researcher must produce at task close when the task originated from an `AgentInitiative`. It answers one question: *Should this be built, and if so, what exactly?*

### Memo Schema

```markdown
## 🗺️ Research Decision Memo — [INITIATIVE TITLE]

**Initiative ID:** [agent_initiative.id]
**Research Task:** [task.id]
**Date:** [ISO date]
**Author:** [agent]

---

### Problem
[1–2 sentences. What pain does this address? Be concrete — name the user action or system failure it eliminates.]

### User Segment
[Who experiences this pain most acutely? Be specific: "researcher agent on every task close" is better than "all users".]

### Spec Touchpoints
[Which existing system surfaces does this touch?]
- DB tables: [e.g., tasks, agent_initiatives]
- API routes: [e.g., PATCH /api/tasks/{id}]
- Services: [e.g., app/services/decomposition.py]
- UI surfaces: [e.g., Mission Control kanban card]

### MVP Scope
[What is the minimum implementation that delivers the core value? Max 3 bullet points. Each should be a single implementable unit.]
- [ ] [Concrete deliverable 1]
- [ ] [Concrete deliverable 2]
- [ ] [Concrete deliverable 3]

### Out of Scope (for MVP)
[What explicitly does NOT go in the first version?]

### Owner
**Agent:** [programmer | architect | researcher]  
**Estimated tier:** [A=hours | B=days | C=week+]  
**Blocking dependencies:** [task IDs or "none"]

### Decision
**→ BUILD** / **→ NO-BUILD** / **→ DEFER**

**Rationale:** [1–2 sentences explaining the decision. If NO-BUILD or DEFER, say why now is wrong.]

**If DEFER:** Revisit trigger: [specific condition, e.g. "after Calendar v2 ships" or "when DAU > 100"]
```

### Field Definitions

| Field | Required | Purpose |
|-------|----------|---------|
| `Problem` | Yes | Makes the research actionable by naming the specific pain |
| `User Segment` | Yes | Prevents over-broad builds that try to serve everyone |
| `Spec Touchpoints` | Yes | Forces researcher to map research to implementation surfaces; catches "this is actually 4 services" complexity early |
| `MVP Scope` | Yes | The contract for the programmer — not "implement X" but "specifically these 3 things" |
| `Out of Scope` | Yes | As important as scope — prevents scope creep at programmer handoff |
| `Owner` | Yes | Assigns accountability before the task closes |
| `Decision` | Yes | The gate. No memo = task can't close. |

---

## Design: The Gate Mechanism

### Where the Gate Lives

The gate enforces at **task completion** for tasks with `AgentInitiative` lineage (i.e., `Task.source_initiative_id` is set, or the task title matches a known research pattern).

**Implementation surface:** `PATCH /api/tasks/{id}` — when `status` is being set to `completed` and the task has initiative lineage, the request must include a `decision_memo` body or the endpoint returns `422 Unprocessable Entity`.

### Gate Logic

```python
# Pseudocode — not production code
def validate_task_completion(task, update_request):
    if update_request.status == "completed":
        if task_has_initiative_lineage(task):
            if not update_request.decision_memo:
                raise HTTPException(
                    422,
                    detail="Tasks originating from research initiatives require a decision memo at close. "
                           "Include decision_memo in the request body."
                )
        # Validate memo fields
        if update_request.decision_memo:
            validate_memo_fields(update_request.decision_memo)
```

### What "Initiative Lineage" Means

A task has initiative lineage if:
- `task.agent == "researcher"` AND the task was spawned from an `AgentInitiative` (trackable via `AgentInitiative.task_id` FK or a new `Task.source_initiative_id` column)
- OR: task title contains `[research]` prefix (softer heuristic, no schema change needed)

**Recommendation:** Add `Task.source_initiative_id` (String, FK to `agent_initiatives.id`, nullable). This is the cleanest signal with zero false positives.

### Gate Enforcement Levels

Three options, in ascending strictness:

| Level | Mechanism | Tradeoff |
|-------|-----------|---------|
| **Soft gate** | Warn but allow close without memo | Low friction, low enforcement — memos stay optional |
| **Hard gate** | 422 if no memo on initiative-linked researcher tasks | Forces compliance, may block legitimate quick closes |
| **Hard gate + grace** | 422 after 48h from task start, warn before | **Recommended** — avoids blocking fast turnarounds while enforcing the process for real research tasks |

**Recommendation: Hard gate + 48h grace.** Rationale: the 48h window handles cases where research resolves quickly (researcher finds "this already exists, no-build") without requiring a full memo on 10-minute tasks. After 48h, the memo is mandatory.

---

## Design: Weekly Pruning Review

### Problem with Stale Initiatives

Initiatives that are `approved` but have no linked task, or tasks that are `completed` but have no decision memo (pre-gate), accumulate silently. Without a forcing function to prune them, the backlog grows until nobody trusts it.

### Pruning Review Spec

**Trigger:** Weekly, every Monday at 8am ET (same engine pattern as `BriefService` daily brief)

**Query:**
1. `AgentInitiative` where `status = 'approved'` AND `task_id IS NULL` AND `created_at < NOW() - 14 days` → **orphan initiatives** (approved but no task ever spawned)
2. `AgentInitiative` where `status = 'approved'` AND task exists AND task `status = 'completed'` AND `decision_memo IS NULL` → **memo-missing closures** (completed without gate, pre-gate backlog)
3. `AgentInitiative` where `status = 'proposed'` AND `created_at < NOW() - 30 days` → **stale proposals** (never reached Lobs review)

**Output:** Inbox item (type: `suggestion`, urgency: `🟢 Advisory`) with:
```
## 📋 Weekly Initiative Pruning — [DATE]

**Orphan initiatives (approved, no task):** N
**Memo-missing closures:** N  
**Stale proposals (30d+):** N

### Action Required
Review the list below and either:
- Spawn a task for orphan initiatives
- Mark stale proposals as `rejected` with reason
- Write retroactive memos for memo-missing closures

[Full list with links]
```

**Automation:** Lobs can auto-reject stale proposals (> 60 days old, no activity) with reason `"Pruned: no activity for 60 days"`. This reduces the noise without human involvement.

---

## Implementation Surfaces

This is a **process + schema + enforcement** change. Concrete implementation surfaces:

### 1. DB Schema (minimal)
- Add `Task.source_initiative_id` (String, FK → `agent_initiatives.id`, nullable, index)
- Add `Task.decision_memo` (JSON, nullable) — stores the structured memo at task close
- Migration: `alembic` migration file

### 2. API Changes
- `PATCH /api/tasks/{id}` — add optional `decision_memo: DecisionMemoPayload` to request body
- Add gate validation logic (hard gate + 48h grace)
- `GET /api/tasks/{id}` — include `decision_memo` in response

### 3. New Pydantic Schema: `DecisionMemoPayload`
```python
class DecisionMemoPayload(BaseModel):
    problem: str               # max 200 chars
    user_segment: str          # max 100 chars
    spec_touchpoints: list[str]
    mvp_scope: list[str]       # max 3 items
    out_of_scope: list[str]
    owner_agent: str           # programmer | architect | researcher
    estimated_tier: str        # A | B | C
    blocking_dependencies: list[str]  # task IDs or []
    decision: Literal["build", "no-build", "defer"]
    rationale: str             # max 300 chars
    defer_trigger: Optional[str]  # required if decision == "defer"
```

### 4. Initiative Spawning Change
- When Lobs spawns a task from an `AgentInitiative`, populate `Task.source_initiative_id`
- File: `app/orchestrator/initiative_decisions.py` → `_spawn_task_for_initiative()`

### 5. Pruning Service
- New method: `BriefService._run_initiative_pruning()` or standalone `InitiativePruningService`
- Weekly timer in orchestrator engine (same pattern as `_brief_hour_et`)
- Produces inbox item with pruning report

### 6. Handoff Automation (optional, phase 2)
- When memo `decision == "build"`, auto-create a programmer handoff from `owner_agent` and `mvp_scope`
- When `decision == "no-build"`, mark `AgentInitiative.status = 'archived'` with reason
- This closes the loop: research task → decision memo → programmer task (or archive) without human routing

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Researchers write boilerplate memos to pass the gate | Medium | Weekly pruning reviewer (human or Lobs) spot-checks memo quality; low-effort memos with no spec touchpoints get flagged |
| Gate breaks fast researcher task completion | Low | 48h grace period covers it; also: no-build memos are fast to write |
| `source_initiative_id` backfill gaps | Low | Gate only applies to tasks created after deploy; legacy tasks are exempt |
| Too many "defer" decisions accumulate | Medium | Require a specific `defer_trigger` condition (not "later"); pruning review surface defers older than 30 days |

---

## Recommendation

**Build this.** The decision memo adds ~5 minutes of friction per researcher task. The benefit: every research task produces a routable artifact (build → programmer handoff, no-build → archive, defer → conditional queue). This eliminates the "approved → completed → nothing happened" cycle that's currently the primary reason ideas don't ship.

**Phased approach:**
1. **Phase 1 (schema + gate):** Add `decision_memo` field, implement 48h hard gate, update researcher AGENTS.md to require memo format. No automation yet — just structured data capture.
2. **Phase 2 (pruning):** Weekly pruning review inbox item. Surfaces backlog for cleanup.
3. **Phase 3 (auto-handoff):** `decision == "build"` auto-creates programmer task from memo's `owner_agent` + `mvp_scope`. Closes the loop fully.

---

## What to Do Next

**Hand off to architect** to spec the DB migration + API changes (Phases 1–2).  
**Hand off to programmer** for Phase 1 implementation once architect spec is done.

See `docs/research-to-roadmap-bridge.md` for the full design document with implementation guide.
