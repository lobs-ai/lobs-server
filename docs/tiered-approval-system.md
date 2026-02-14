# Tiered Approval System — Design & Implementation

**Status:** ✅ Implemented (2026-02-13)  
**Related:** Project-manager agent, autonomous workflows

---

## Problem

**Before:** All completed agent work went to inbox for human review → inbox overload, high latency

**Goal:** Enable agents to approve routine work while escalating significant changes to humans

---

## Solution: Three-Tier Approval

The **project-manager agent** reviews completed work and decides:

1. **Auto-Approve** — Routine work that meets standards
2. **Human Review** — Significant changes requiring judgment
3. **Escalate** — Problems requiring intervention

---

## Tier 1: Auto-Approve

PM autonomously approves work when:

✅ **Acceptance criteria fully met**  
✅ **Code quality meets standards** (no hacks, proper error handling)  
✅ **Tests included and passing**  
✅ **Documentation updated** (inline comments, README changes)  
✅ **No breaking changes**  
✅ **Low risk** (isolated changes, well-tested patterns)

**Examples:**
- Add logging to function
- Fix typo in UI text
- Update dependency version (patch/minor)
- Refactor internal function (no API changes)
- Add test coverage for existing feature

**Action:** PM marks task as `done`, updates work log, no human notification

---

## Tier 2: Human Approval Required

PM sends to inbox when:

⚠️ **Architecture changes** (new patterns, significant refactoring)  
⚠️ **Breaking changes** (API changes, migration required)  
⚠️ **Security/privacy implications**  
⚠️ **Trade-offs made** (performance vs. complexity, UX compromises)  
⚠️ **Acceptance criteria partially met** (good-faith attempt, but incomplete)  
⚠️ **Novel patterns** (first-time use of new library, unfamiliar approach)

**Examples:**
- Switch from REST to GraphQL
- Add OAuth provider
- Redesign database schema
- Change error handling strategy
- Remove deprecated feature

**Action:** PM creates inbox item with:
- Summary of changes
- What was achieved
- What needs human decision
- Recommended action

---

## Tier 3: Escalate

PM escalates when:

❌ **Work doesn't match requirements** (agent misunderstood)  
❌ **Quality issues** (bugs introduced, poor code quality)  
❌ **Agent blocked** (missing info, unclear requirements)  
❌ **Scope creep** (agent went beyond task scope)  
❌ **Conflicts** (changes conflict with other work)

**Examples:**
- Agent implemented wrong feature
- Code doesn't compile/test
- Agent couldn't figure out approach
- Agent made assumptions about requirements

**Action:** PM creates detailed feedback, reassigns task, or blocks for human clarification

---

## Decision Criteria

PM uses this rubric to decide approval tier:

| Factor | Auto-Approve | Human Review | Escalate |
|--------|--------------|--------------|----------|
| **Requirements met** | 100% | 80-99% | <80% |
| **Code quality** | High | Medium-High | Low |
| **Tests** | Included, passing | Included | Missing/failing |
| **Breaking changes** | None | Some | Many |
| **Risk** | Low | Medium | High |
| **Complexity** | Low | Medium | High |
| **Novelty** | Familiar patterns | Some new | Completely new |

---

## Implementation

### 1. Orchestrator Routing

When a task is completed, orchestrator routes to project-manager if:
- Task has `requires_approval = True` flag
- Task is in project with PM reviews enabled
- Task was delegated by PM (PM reviews its own delegations)

```python
# orchestrator/router.py
def route_completed_task(task):
    if task.requires_approval or task.delegated_by == 'project-manager':
        return 'project-manager'
    else:
        return 'auto-finalize'  # No review needed
```

### 2. PM Review Process

PM receives completed task with:
- Original task description & acceptance criteria
- Agent's work summary (`.work-summary`)
- File changes (git diff)
- Test results
- Project context

PM evaluates and writes decision to `.work-summary`:

```markdown
## Approval Review

### Summary
[One-sentence summary of the work]

### Assessment
[Detailed evaluation against acceptance criteria]

### Decision: AUTO-APPROVE | HUMAN-REVIEW | ESCALATE

### Rationale
[Why this decision was made]

### [If HUMAN-REVIEW] Human Decision Required
[Specific questions or concerns for human reviewer]

### [If ESCALATE] Issues Found
[Specific problems that need resolution]
```

### 3. Orchestrator Finalization

Orchestrator reads PM's decision:

```python
# orchestrator/engine.py
if decision == 'AUTO-APPROVE':
    finalize_task(task_id, status='done')
    log_auto_approval(task_id, pm_feedback)
    
elif decision == 'HUMAN-REVIEW':
    create_inbox_item(task, pm_feedback)
    task.status = 'awaiting_approval'
    
elif decision == 'ESCALATE':
    task.status = 'blocked'
    notify_escalation(task, pm_feedback)
```

---

## Configuration

### Per-Project Settings

Projects can configure approval requirements in project metadata:

```json
{
  "project_id": "lobs-server",
  "approval_settings": {
    "require_pm_review": true,
    "auto_approve_enabled": true,
    "always_human_review": [
      "auth changes",
      "database migrations",
      "API breaking changes"
    ]
  }
}
```

### Global Orchestrator Settings

```python
# orchestrator/config.py
PM_AUTO_APPROVE_ENABLED = True
PM_AUTO_APPROVE_MAX_FILES_CHANGED = 10  # Escalate if >10 files
PM_AUTO_APPROVE_MAX_LINES_CHANGED = 500  # Escalate if >500 lines
```

---

## Metrics & Monitoring

Track approval effectiveness:

```sql
-- Auto-approval rate
SELECT 
    COUNT(CASE WHEN pm_decision = 'auto-approve' THEN 1 END) * 100.0 / COUNT(*) as auto_approve_pct
FROM worker_runs 
WHERE reviewed_by_pm = true;

-- Human override rate (PM approved, human rejected)
SELECT COUNT(*) FROM inbox_items 
WHERE source = 'pm_review' AND status = 'rejected';
```

**Success metrics:**
- Auto-approval rate: 60-80% (balance efficiency with safety)
- Human override rate: <5% (PM decisions align with human judgment)
- Escalation rate: <10% (most work is reviewable)

---

## Example: Auto-Approve Flow

**Task:** "Add request timeout to API service"

**Agent (programmer) delivers:**
- ✅ Added `timeout=30` parameter to all HTTP requests
- ✅ Added timeout error handling
- ✅ Updated tests to verify timeout behavior
- ✅ Updated API service docstring

**PM reviews:**
- Requirements met: ✅ (timeout added, working)
- Code quality: ✅ (clean implementation)
- Tests: ✅ (included, passing)
- Breaking changes: ❌ (internal change only)
- Risk: Low (standard practice, well-tested)

**PM decision:** AUTO-APPROVE

**Result:** Task marked done, no inbox item created, human never sees it

---

## Example: Human Review Flow

**Task:** "Redesign user authentication flow"

**Agent (architect) delivers:**
- ⚠️ New OAuth provider integration
- ⚠️ Session management changes
- ⚠️ Database schema updates
- ⚠️ Breaking changes to login API

**PM reviews:**
- Requirements met: ✅ (good design)
- Code quality: ✅ (well-architected)
- Tests: ✅ (comprehensive)
- Breaking changes: ⚠️ (significant)
- Risk: High (auth is critical)

**PM decision:** HUMAN-REVIEW

**Inbox item created:**
```markdown
**Title:** Approve auth redesign — OAuth integration + breaking changes

**Summary:**
Architect redesigned authentication to use OAuth2. Design is solid but includes:
- Breaking API changes (login endpoint signature changed)
- Database migration required (new sessions table)
- Third-party dependency (OAuth provider)

**Recommendation:** Approve with staged rollout plan

**Human decision needed:** 
- Approve breaking changes?
- Confirm OAuth provider choice
- Review migration plan
```

**Result:** Human reviews, provides feedback, approves with modifications

---

## Example: Escalation Flow

**Task:** "Optimize database queries"

**Agent (programmer) delivers:**
- ❌ Rewrote entire ORM layer (scope creep)
- ❌ Introduced 3 new dependencies
- ❌ Tests failing
- ❌ Original slow query not addressed

**PM reviews:**
- Requirements met: ❌ (original problem unsolved)
- Code quality: ⚠️ (over-engineered)
- Tests: ❌ (failing)
- Scope creep: ✅ (went way beyond task)

**PM decision:** ESCALATE

**Feedback to agent:**
```markdown
**ESCALATED:** Work does not address original requirements

**Issues:**
1. Task was to optimize specific slow query, not rewrite ORM
2. Tests are failing (5 failures in test_database.py)
3. New dependencies not justified (Alternatives exist)

**Next steps:**
- Revert ORM changes
- Focus on original query optimization
- Provide benchmark showing improvement

**Reassigning to:** programmer (new session)
```

**Result:** Task reassigned with clear feedback, agent tries again with focused scope

---

## Benefits

1. **Reduced Inbox Load** — 60-80% of tasks auto-approved
2. **Faster Iteration** — Routine work completes without human latency
3. **Consistent Standards** — PM applies same criteria every time
4. **Learning** — PM gets better at approval decisions over time
5. **Focus** — Humans review only significant decisions
6. **Transparency** — All approvals logged and reviewable

---

## Risks & Mitigation

| Risk | Mitigation |
|------|------------|
| PM approves bad work | Human audit sample of auto-approvals weekly |
| PM too conservative | Track override rate, adjust criteria |
| PM too lenient | Set hard limits (file count, line count) |
| Disagreement with human | Override log helps PM learn human preferences |
| Security issues auto-approved | Always require human review for auth/security tags |

---

## Future Enhancements

- **Learning from overrides** — PM adapts criteria based on human feedback
- **Confidence scores** — PM reports certainty level with decisions
- **Approval templates** — Pre-defined criteria for common task types
- **Multi-PM review** — Complex tasks reviewed by multiple PMs
- **Approval analytics** — Dashboard showing approval trends and patterns

---

## Related Documentation

- [Project Manager Agent](project-manager-agent.md) — PM architecture and responsibilities
- [Orchestrator AGENTS.md](../AGENTS.md) — Orchestrator overview
- [Worker API](../AGENTS.md#worker-api) — Worker handoff format

**Last Updated:** 2026-02-14
