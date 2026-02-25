# Research Findings: Research-to-Roadmap Bridge — Decision Memo Template + Gate

**Task ID:** d211d57e-6804-4dcf-adb8-434812b3d4b2  
**Date:** 2026-02-25  
**Author:** researcher  
**Category:** architecture_change | Risk Tier B

---

## Problem Statement

The Lobs system generates a steady stream of research initiatives (via 6-hour reflection cycles). Many get approved but then stall: the research task closes, but nothing gets built. The gap is structural — there is no required handoff artifact connecting "research done" to "implementation begins." Ideas accumulate in a long approved-but-dormant backlog, which dilutes signal and wastes review attention.

**Root cause:** The task lifecycle has no explicit bridge between `completed` research and `queued` implementation work. There is no forcing function to answer "should we build this, and if so, what exactly?"

---

## Current State Analysis

### Initiative Lifecycle (as-built)

```
Reflection → AgentInitiative (proposed) → Lobs review → approved/rejected/deferred
                                                              ↓
                                                    Task created (agent assigned)
                                                              ↓
                                                    Task runs → completed
                                                              ↓
                                                    [NOTHING REQUIRED] ← gap here
```

Source files:
- `app/models.py` — `AgentInitiative`, `InitiativeDecisionRecord`, `Task`
- `app/routers/orchestrator_reflections.py` — initiative review/decision endpoints
- `app/orchestrator/initiative_decisions.py` — `InitiativeDecisionEngine`
- `docs/intelligence-workflow.md` — lifecycle documentation

### What's Missing

1. **No exit artifact required** — a researcher task can close with no deliverable beyond a markdown file
2. **No build/no-build decision** — whether to implement is implicit (approve the initiative → assume build)
3. **No implementation surface specification** — no required link from research to affected files, endpoints, or schemas
4. **No stale-initiative pruning** — approved initiatives that never convert to tasks accumulate silently

---

## Solution Design

### 1. Decision Memo Schema

Every research task targeting an approved initiative **must** produce a Decision Memo at close. The memo is a structured artifact with the following schema:

```json
{
  "initiative_id": "string (UUID, links back to AgentInitiative)",
  "task_id": "string (UUID, the research task that produced this memo)",
  "created_at": "ISO 8601 timestamp",
  
  "verdict": "build | no-build | defer",
  
  "problem": {
    "statement": "1-3 sentence crisp description of the problem being solved",
    "evidence": "what signals/data support this being real (optional)",
    "severity": "critical | high | medium | low"
  },
  
  "user_segment": {
    "primary": "who is most affected (e.g. 'Lobs reviewing 10+ initiatives/day')",
    "secondary": "adjacent beneficiaries if any",
    "usage_frequency": "daily | weekly | occasional | rare"
  },
  
  "spec_touchpoints": {
    "api_endpoints": ["list of affected or new endpoints, e.g. /api/tasks/{id}/memo"],
    "models": ["DB models to add/modify, e.g. DecisionMemo, Task"],
    "routers": ["router files to touch, e.g. app/routers/tasks.py"],
    "migrations": ["migration required: true/false + brief description"],
    "frontend_surfaces": ["Mission Control views affected, e.g. IntelligenceView, TaskDetailView"]
  },
  
  "mvp_scope": {
    "description": "Minimum viable scope — what must ship for this to be useful",
    "out_of_scope": "Explicitly what is NOT in MVP (prevents scope creep)",
    "estimated_complexity": "trivial | small | medium | large",
    "suggested_agent": "programmer | architect | researcher | writer"
  },
  
  "owner": "agent type or 'human' — who is responsible for follow-through",
  
  "references": ["file paths, URLs, or doc titles that informed this memo"]
}
```

#### Verdict Semantics

| Verdict | Meaning | System Action |
|---------|---------|---------------|
| `build` | Evidence supports shipping; MVP is defined | Auto-create implementation task with memo attached |
| `no-build` | Not worth building — wrong problem, low value, or already exists | Archive initiative; add brief rationale |
| `defer` | Interesting but not now — missing data, dependencies, or bandwidth | Re-queue for next quarterly sweep |

---

### 2. The Gate Mechanism

The gate is enforced at **task close time** for any task spawned from an approved research initiative.

#### Implementation Points

**Option A — Soft gate (recommended for MVP):**  
- When a researcher task completes and `task.source_initiative_id` is set, the orchestrator checks for a `DecisionMemo` record linked to that task
- If missing → task status is set to `needs_memo` instead of `completed`
- Brief service flags `needs_memo` tasks in the daily 8am ops brief
- Human or agent can provide the memo retroactively to unblock

**Option B — Hard gate:**  
- Researcher agent is required to call `POST /api/tasks/{id}/memo` as part of task close
- Task cannot transition to `completed` without a memo
- Riskier — could cause more stuck tasks if agent fails to produce one

**Recommendation:** Start with Option A (soft gate). It preserves the existing task lifecycle and adds observability without creating a new failure mode.

#### Database Changes Required

```sql
-- New table
CREATE TABLE decision_memos (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  initiative_id TEXT REFERENCES agent_initiatives(id),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  verdict TEXT NOT NULL CHECK (verdict IN ('build', 'no-build', 'defer')),
  problem_statement TEXT NOT NULL,
  problem_severity TEXT,
  user_segment_primary TEXT,
  spec_touchpoints JSON,
  mvp_scope_description TEXT,
  mvp_out_of_scope TEXT,
  mvp_complexity TEXT,
  suggested_agent TEXT,
  owner TEXT,
  references_ JSON,
  raw_memo JSON  -- full schema stored for forward compatibility
);

-- Task model addition
ALTER TABLE tasks ADD COLUMN decision_memo_id TEXT REFERENCES decision_memos(id);
ALTER TABLE tasks ADD COLUMN needs_memo BOOLEAN DEFAULT FALSE;
```

#### New Endpoints

```
POST /api/tasks/{task_id}/memo      — submit a decision memo for a task
GET  /api/tasks/{task_id}/memo      — retrieve memo for a task
GET  /api/intelligence/initiatives/{initiative_id}/memo — get memo for the initiative
PATCH /api/tasks/{task_id}/memo     — update memo verdict (human override)
GET  /api/intelligence/needs-memo   — list tasks needing memos (for daily brief)
```

---

### 3. Weekly Stale Initiative Pruning

A recurring sweep to process initiatives that have been `approved` but never converted to a task, or tasks that are `completed` but have no memo after N days.

#### Pruning Rules

| Condition | Action |
|-----------|--------|
| Initiative `approved` with no spawned task after 14 days | Flag as `stale_approved` |
| Task `completed` with `needs_memo=true` after 7 days | Flag as `stale_memo` |
| Task `completed` with `verdict=build` but no follow-up implementation task after 21 days | Flag as `stale_handoff` |

#### Sweep Mechanism

Reuse the existing `SystemSweep` pattern from `app/routers/orchestrator_reflections.py`. Add a new sweep type: `pruning_review`.

The weekly ops sweep:
1. Runs every Monday at 8am ET (alongside the existing daily brief via `BriefService`)
2. Adds a `StaleInitiativesAdapter` to `BriefService` (parallel to `StuckRemediationsAdapter`)
3. Posts to daily brief: list of stale items with recommended actions
4. Human can bulk-resolve from the Intelligence view

---

## MVP vs Full Implementation

### MVP (Phase 1 — recommended first ship)

1. Add `DecisionMemo` model + migration
2. Add `POST /api/tasks/{id}/memo` and `GET /api/tasks/{id}/memo` endpoints
3. Add `needs_memo` flag to `Task`; set it in orchestrator when a research task completes with `source_initiative_id` set
4. Add `GET /api/intelligence/needs-memo` for daily brief integration
5. Update researcher agent system prompt to require memo at task close for initiative-sourced tasks
6. Add memo to `BriefService` daily ops summary

Estimated complexity: **small–medium** (2–3 programmer sessions)

### Full Implementation (Phase 2)

1. Frontend: Decision Memo UI in Mission Control Intelligence view
2. Auto-create implementation tasks from `verdict=build` memos
3. Weekly pruning sweep automation
4. Stale initiative batch review UI

---

## Risks and Gotchas

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Agents produce low-quality memos | Medium | Add schema validation at `POST /memo`; reviewer can override verdict |
| Gate creates stuck tasks | Medium | Use soft gate (Option A); `needs_memo` is advisory, not blocking |
| Memo schema too rigid | Low | Store `raw_memo JSON` for forward compatibility; evolve schema iteratively |
| Weekly sweep adds noise | Low | Prune sweep output is additive to daily brief, not a separate notification |
| `source_initiative_id` not always set | Medium | Audit task creation paths in `InitiativeDecisionEngine` to ensure FK is always written |

---

## Implementation Surfaces Summary

| Surface | File | Change |
|---------|------|--------|
| DB Model | `app/models.py` | Add `DecisionMemo` class, `Task.needs_memo`, `Task.decision_memo_id` |
| Migration | `migrations/` | New migration for above |
| API Router | `app/routers/tasks.py` | Add memo CRUD endpoints |
| API Router | `app/routers/orchestrator_reflections.py` | Add `needs-memo` list endpoint |
| Orchestrator | `app/orchestrator/engine.py` or `worker.py` | Set `needs_memo=True` on research task complete |
| Brief Service | `app/services/brief_service.py` | Add `StaleInitiativesAdapter` |
| Agent Prompt | researcher AGENTS.md / system prompt | Require memo section at task close |
| Frontend | `IntelligenceView.swift` | Phase 2: memo display + verdict UI |

---

## Recommendation

**Build this.** The Research-to-Roadmap gap is real — visible in the existing `approved-but-dormant` initiative backlog. The memo schema is the right shape: it forces specificity (spec touchpoints, MVP scope) without being bureaucratic (5 fields, not 20). The soft gate (Option A) is the right implementation approach for MVP.

**Priority:** High. Ship the DB model + endpoints first; integrate with researcher agent prompt immediately. The 14-day stale-approved pruning will surface how many existing initiatives need retrospective memos.

**First action for programmer:** See spec touchpoints above. Start with `DecisionMemo` model + migration + `POST /api/tasks/{id}/memo`.

---

## References

- `app/models.py` — `AgentInitiative`, `Task`, `InitiativeDecisionRecord`
- `app/routers/orchestrator_reflections.py` — initiative lifecycle + decision engine
- `app/orchestrator/initiative_decisions.py` — `InitiativeDecisionEngine`
- `docs/intelligence-workflow.md` — full initiative review workflow documentation
- `docs/inbox-remediation-tracking-design.md` — parallel pattern for tracking approval→task decay
- `app/services/brief_service.py` + `StuckRemediationsAdapter` — weekly sweep pattern to reuse
- `ARCHITECTURE.md` — `BriefService` direct engine timer pattern
