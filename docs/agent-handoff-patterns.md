# Agent Handoff Patterns & Result Formats

**Date:** 2026-02-23  
**Audience:** Developers, AI agents, contributors  
**Purpose:** Document how agents communicate completed work, make decisions, and pass results through the Lobs system.

---

## Overview

Agents complete work in three ways:

1. **Inbox Items** — When decisions are needed (proposals, suggestions, escalations)
2. **Reports** — When sharing informational results (documentation, analysis, status)
3. **Research** — When delivering technical findings (investigation reports, data)

This document explains when to use each, their data structures, and practical examples.

---

## 1. Inbox Items — Decisions Required

**Use when:** The agent has completed work, but **a human decision is required** to proceed.

**Examples:**
- Proposing a new feature or architectural change
- Suggesting a code refactor with analysis
- Escalating a blocked task to a human
- Creating a prerequisite task before continuing
- Requesting clarification on ambiguous requirements
- Proposing a scope change (rescoping an initiative)

### 1.1 Inbox Item Schema

```python
class InboxItemCreate(BaseModel):
    id: str                          # UUID
    title: str                       # What needs a decision
    filename: Optional[str]          # Optional: original filename
    relative_path: Optional[str]     # Optional: project path
    content: str                     # Full decision context (markdown)
    summary: Optional[str]           # 1-sentence tl;dr
    is_read: bool = False           # Not read yet
```

### 1.2 Content Guidelines

Inbox item `content` should follow this structure:

```markdown
## Problem / Context
[2-3 sentences explaining the situation]

## Decision Needed
[What specific decision should the human make?]

## Recommendation
[What does the agent recommend? Be clear but not pushy.]

## Supporting Analysis
[Tradeoffs, data, examples that inform the decision]

## Next Steps if Approved
[What happens after human approves]

## Next Steps if Rejected
[What's the fallback plan]
```

### 1.3 Inbox Item Types

Different proposal types go to the same inbox but have standard formats:

#### 3.3a Feature Proposal
```markdown
## Feature: [name]
- **Value:** Why build this
- **Scope:** 1-2 sentences of what it does
- **Effort:** Estimated hours/days
- **Dependencies:** Other work needed first
- **Risks:** Known unknowns
```

#### 1.3b Rescoping Initiative
```markdown
## Rescope: [original initiative title]
- **Original scope:** [what was requested]
- **Why rescoping:** [what's blocking/too large]
- **Revised scope:** [new boundaries]
- **Revised effort:** New estimate
- **Prerequisite work needed:** If any
```

#### 1.3c Escalation
```markdown
## Escalation: [task title]
- **Why escalated:** [human expertise needed]
- **Current blocker:** [what's stuck]
- **Agent attempts:** [what was tried]
- **Options presented:** [A, B, C]
- **Recommendation:** [which option and why]
```

#### 1.3d Suggestions & Improvements
```markdown
## Suggestion: [improvement]
- **Current behavior:** What's happening now
- **Proposed change:** What could be better
- **Rationale:** Why it matters
- **Risk:** Any downsides
- **Effort:** To implement (if applicable)
```

### 1.4 API Example

**Create an inbox item:**

```bash
curl -X POST http://localhost:8000/api/inbox \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "inbox-feat-001",
    "title": "Feature: Model tier picker in task create UI",
    "summary": "Add dropdown to select cheap/standard/strong for model routing",
    "content": "## Problem\nUsers can't control which model tier a task uses. Critical for cost management.\n\n## Recommendation\nAdd a tier picker dropdown in task create/edit. Default to auto-selection.\n\n## Effort\n~4 hours (UI + API endpoint)",
    "is_read": false
  }'
```

**Response:**
```json
{
  "id": "inbox-feat-001",
  "title": "Feature: Model tier picker in task create UI",
  "summary": "Add dropdown to select cheap/standard/strong for model routing",
  "content": "...",
  "is_read": false,
  "created_at": "2026-02-23T15:00:00Z",
  "modified_at": "2026-02-23T15:00:00Z"
}
```

---

## 2. Reports — Informational Results

**Use when:** The agent has completed work and the human **just needs to read the result** (no decision required).

**Examples:**
- Documentation written or updated
- Code review summary
- Analysis or investigation findings
- Testing results and coverage
- Migration completed successfully
- Refactor finished with metrics
- Research document with conclusions

### 2.1 Report Schema

Reports are stored as **AgentDocument** records:

```python
class AgentDocumentCreate(BaseModel):
    id: str                          # UUID
    title: str                       # Report name
    filename: Optional[str]          # e.g., "TESTING.md"
    relative_path: Optional[str]     # e.g., "docs/TESTING.md"
    content: str                     # Full markdown report
    content_is_truncated: bool       # If content exceeds limits
    source: Optional[str]            # e.g., "agent:writer"
    status: str = "pending"          # pending, approved, rejected
    topic_id: Optional[str]          # Related topic, if any
    project_id: Optional[str]        # Related project, if any
    task_id: Optional[str]           # Related task, if any
    date: Optional[datetime]         # When report was generated
    summary: Optional[str]           # 1-2 sentence tl;dr
```

### 2.2 Report Structure

Reports should follow this pattern:

```markdown
# [Report Title]

**Date:** [YYYY-MM-DD]  
**Agent:** [writer, programmer, researcher, etc.]  
**Project:** [project name]  
**Status:** Complete

---

## Summary
[1-2 sentence executive summary]

## What Was Done
[Bullet list of actions/accomplishments]

## Key Findings / Results
[Main content: analysis, documentation, code review, etc.]

### Section 1
[Details]

### Section 2
[Details]

## Metrics (if applicable)
- Lines changed: X
- Files modified: Y
- Coverage: Z%
- Time spent: N hours

## Cross-References
- Related docs: [links]
- Related tasks: [task IDs]
- Related issues: [links]

## Notes for Next Person
[Anything to know for continuation]
```

### 2.3 Report Types

#### 2.3a Documentation Report
```markdown
# Documentation: [what was documented]
**Files created/modified:** list
**Status:** Ready for review/merged
**Audience:** [technical, admin, end-user, developers]
```

#### 2.3b Testing Report
```markdown
# Testing: [what was tested]
**Coverage:** X% → Y%
**Tests added:** N
**Issues found:** [list]
**Blockers:** [any issues preventing full coverage]
```

#### 2.3c Code Review Report
```markdown
# Code Review: [PR or feature]
**Reviewed by:** [agent]
**Files:** X modified, Y added, Z deleted
**Quality:** [summary of findings]
**Issues:** [Critical, Major, Minor issues]
**Recommendation:** Approve / Needs fixes / Escalate
```

#### 2.3d Migration/Refactor Report
```markdown
# Refactor/Migration: [what changed]
**Scope:** [from X to Y]
**Migration path:** [if applicable]
**Backward compatibility:** Yes/No/Partial
**Testing:** [coverage of migration]
**Results:** [metrics, performance, etc.]
**Rollback plan:** [in case of issues]
```

### 2.4 API Example

**Create a report:**

```bash
curl -X POST http://localhost:8000/api/documents \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "doc-testing-001",
    "title": "Testing Report: Orchestrator Engine",
    "filename": "TESTING_ORCHESTRATOR.md",
    "relative_path": "docs/TESTING_ORCHESTRATOR.md",
    "content": "# Testing Report: Orchestrator Engine\n\n**Date:** 2026-02-23\n**Agent:** programmer\n\n## Summary\nAdded comprehensive unit tests for orchestrator scanning and routing logic.\n\n## What Was Done\n- Added 12 new tests for scanner.py\n- Added 8 new tests for router.py\n- Improved coverage from 65% to 78%\n...",
    "source": "agent:programmer",
    "status": "pending",
    "project_id": "lobs-server",
    "summary": "Added 20 tests for orchestrator. Coverage 65% → 78%."
  }'
```

**Response:**
```json
{
  "id": "doc-testing-001",
  "title": "Testing Report: Orchestrator Engine",
  "status": "pending",
  "date": "2026-02-23T15:00:00Z",
  "created_at": "2026-02-23T15:00:00Z",
  "modified_at": "2026-02-23T15:00:00Z",
  "summary": "Added 20 tests for orchestrator. Coverage 65% → 78%."
}
```

---

## 3. Research Findings — Technical Investigation

**Use when:** The agent has investigated a technical problem and wants to **document findings** for future reference.

**Examples:**
- Performance investigation results
- Dependency analysis report
- API exploration findings
- Database schema analysis
- Pattern discovery in codebase
- Configuration option review
- Tool evaluation report

### 3.1 Research Schema

Research findings are also stored as **AgentDocument** but with `source: "research"`:

```python
class AgentDocumentCreate(BaseModel):
    id: str                   # UUID
    title: str                # Research question or topic
    content: str              # Detailed findings
    source: str = "research"  # Mark as research
    status: str = "pending"   # pending, approved, filed
    topic_id: Optional[str]   # Related knowledge topic
    date: Optional[datetime]  # Investigation date
    summary: Optional[str]    # Key finding in 1-2 sentences
```

### 3.2 Research Report Structure

```markdown
# Research: [Question / Topic]

**Date:** [YYYY-MM-DD]  
**Investigated by:** [agent]  
**Status:** Final / In Progress / Blocked

---

## Question
[What was being investigated?]

## Summary
[1-2 sentence answer to the question]

## Methodology
[How was this investigated?]
- Tools used
- Data sources
- Scope limitations

## Findings
### Finding 1: [Title]
[Evidence and details]

### Finding 2: [Title]
[Evidence and details]

## Conclusions
[What does this mean for the system?]

## Recommendations
[What should be done based on findings?]

## Related Resources
- [Documentation links]
- [Code references]
- [External resources]

## Uncertainty & Caveats
[What's not fully verified, where could we be wrong]
```

### 3.3 Research Examples

#### 3.3a Performance Investigation
```markdown
# Research: Orchestrator scanner performance at scale

## Question
How does scanner performance degrade with 10k+ tasks?

## Findings
- Linear scan: 500ms at 5k tasks, 2s at 10k
- Index on work_state reduces to 50ms → 200ms
- N+1 queries on agent lookups add 300ms

## Recommendation
Add database index on (work_state, created_at)
```

#### 3.3b Dependency Analysis
```markdown
# Research: SQLAlchemy async support in orchestrator

## Question
Can we migrate from sync SQLAlchemy to async without breaking the orchestrator?

## Findings
- Orchestrator already uses async/await
- Current DB: async aiosqlite
- Router.py has 3 blocking calls in critical path

## Recommendation
Safe to migrate. Fix those 3 calls.
```

### 3.4 API Example

**Create a research document:**

```bash
curl -X POST http://localhost:8000/api/documents \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "research-001",
    "title": "Research: Model tier costs and performance tradeoffs",
    "content": "# Research: Model tier costs and performance tradeoffs\n\n## Question\nHow do the 5 model tiers compare on cost, latency, and quality?\n\n## Findings\n- Ollama local: $0/cost, 500ms latency, 70% quality\n- Claude Haiku: $0.80/M input, 200ms, 75% quality\n- Claude Sonnet: $3/M input, 300ms, 85% quality\n...",
    "source": "research",
    "status": "pending",
    "topic_id": "model-routing",
    "summary": "Analyzed cost/latency/quality tradeoffs across 5 model tiers."
  }'
```

---

## 4. Decision Matrix: Which Channel?

Use this matrix to decide where to put your result:

| Scenario | Channel | Why |
|----------|---------|-----|
| "I think we should build feature X" | Inbox | Needs approval |
| "Here are the docs I wrote for X" | Report | No decision needed |
| "I found that X is slow, here's why" | Research | For future reference |
| "I need clarification on X" | Inbox | Needs human input |
| "X is complete, here's what I did" | Report | FYI |
| "Comparing options A vs B" | Research | Analysis for decision-making |
| "I recommend we use library X" | Inbox + Research | Proposal (decision) + findings (analysis) |
| "I tested feature X, results:" | Report | Informational |
| "I'm blocked on X because..." | Inbox | Escalation |

---

## 5. Handoff Workflow in Practice

### 5.1 Writer Agent Example

**Task:** Document orchestrator flow

**Handoff Process:**
1. **Create report** → Document the flow, save as AgentDocument
   ```
   POST /api/documents
   {
     "id": "doc-orchestrator-flow",
     "title": "Documentation: Orchestrator Flow Design",
     "status": "pending",
     "source": "agent:writer"
   }
   ```

2. **If improvements needed** → Create inbox item with suggestion
   ```
   POST /api/inbox
   {
     "id": "inbox-suggest-flow-clarity",
     "title": "Suggestion: Add swim-lane diagram to orchestrator flow",
     "content": "Would improve clarity. Takes ~1 hour to add.",
     "is_read": false
   }
   ```

### 5.2 Programmer Agent Example

**Task:** Implement feature X

**Handoff Process:**
1. **If scope needs clarification** → Escalate
   ```
   POST /api/inbox
   {
     "id": "inbox-escalate-scope",
     "title": "Escalation: Feature X scope unclear",
     "content": "Requirements conflict on point Y. Need decision.",
     "is_read": false
   }
   ```

2. **After feature is done** → Create report
   ```
   POST /api/documents
   {
     "id": "doc-feature-x-complete",
     "title": "Completed: Feature X implementation",
     "status": "pending",
     "source": "agent:programmer",
     "summary": "5 files modified, all tests passing."
   }
   ```

3. **If refactor suggested** → Create inbox item
   ```
   POST /api/inbox
   {
     "id": "inbox-refactor-db-schema",
     "title": "Suggestion: Refactor database schema",
     "content": "Current schema has N+1 issues. Would improve performance by 30%.",
     "is_read": false
   }
   ```

### 5.3 Researcher Agent Example

**Task:** Investigate performance issue

**Handoff Process:**
1. **Create research findings**
   ```
   POST /api/documents
   {
     "id": "research-perf-001",
     "title": "Research: Scanner performance at scale",
     "source": "research",
     "status": "pending",
     "summary": "Linear scan degrades to 2s at 10k tasks."
   }
   ```

2. **If recommendation is important** → Create inbox item
   ```
   POST /api/inbox
   {
     "id": "inbox-add-db-index",
     "title": "Proposal: Add database index on work_state",
     "content": "Research shows this would reduce scanner time 80%. Low risk, ~1 hour work.",
     "is_read": false
   }
   ```

---

## 6. Best Practices

### 6.1 Clarity & Conciseness
- **Inbox items:** Keep `title` to one sentence. `summary` should be the gist.
- **Reports:** Include executive summary at the top.
- **Research:** State findings clearly before methodology.

### 6.2 Linking Work
- Always include `project_id`, `task_id`, `topic_id` when known.
- Use markdown links to cross-reference related items.
- Include URLs to GitHub commits/PRs if applicable.

### 6.3 Next Steps
- **Inbox:** Always include "Next Steps if Approved/Rejected"
- **Reports:** Include "Notes for next person" section
- **Research:** Include "Recommendations" section

### 6.4 Metadata
- Always include `date` (when work was done)
- Always include `source` (which agent/system created it)
- Use consistent `status` values: `pending`, `approved`, `rejected`, `filed`

### 6.5 Escalation Rules

**Create an inbox item (escalate) when:**
- Work is blocked and requires human decision
- Agent needs clarification on requirements
- Two architectural options exist and agent is unsure
- Scope needs to be adjusted mid-task
- Risk assessment suggests human review

**DO NOT create inbox item when:**
- Information is purely informational (use Report instead)
- Agent is just requesting feedback (use Research + suggestion comment)
- Work is complete and successful (use Report)

---

## 7. Integration Points

### 7.1 Task Completion Flow

```
Task marked complete
  ↓
Orchestrator calls worker.finalize()
  ↓
Agent creates Report (AgentDocument)
  ↓
If needs decision → Create Inbox Item
  ↓
Human reviews in dashboard
  ↓
Human approves/rejects
  ↓
Task marked reviewed
```

### 7.2 API Flow

**Typical agent handoff via API:**

```python
# 1. Agent completes task
async def finalize_task(task_id):
    # Do final work
    
    # 2. Create report of what was done
    report = {
        "id": f"doc-{task_id}",
        "title": f"Completed: {task.title}",
        "content": "# Report\n...",
        "source": "agent:writer",
        "status": "pending",
        "task_id": task_id
    }
    await api.post("/api/documents", report)
    
    # 3. If decision needed, create inbox item
    if needs_approval:
        inbox = {
            "id": f"inbox-{task_id}",
            "title": "Decision needed: " + task.title,
            "content": "Proposal and analysis...",
            "is_read": False
        }
        await api.post("/api/inbox", inbox)
    
    # 4. Mark task complete
    await api.put(f"/api/tasks/{task_id}", {
        "work_state": "completed",
        "review_state": "needs_review" if needs_approval else "auto_approved"
    })
```

---

## 8. Examples by Agent Type

### 8.1 Writer Agent Handoffs

**Scenario: Write API documentation**

**Report (always):**
```markdown
# Report: API Documentation Complete

**Files created:** docs/api-reference.md (2500 words)
**Time:** 3 hours
**Status:** Ready for review

Documented all 15 endpoints with examples, error codes, and rate limits.
```

**Inbox (if feedback needed):**
```markdown
## Suggestion: Add interactive API explorer

Could embed Swagger/Redoc for live testing. Would add 2 hours.
```

### 8.2 Programmer Agent Handoffs

**Scenario: Implement feature with investigation**

**Research (findings):**
```markdown
# Research: SQLAlchemy async migration feasibility

Investigated whether orchestrator can use async SQLAlchemy.
Conclusion: Safe. 3 blocking calls to fix.
```

**Report (implementation):**
```markdown
# Report: Feature X implemented

- 5 files modified, 3 new tests added
- All existing tests passing
- Performance: +20% faster on task creation
```

**Inbox (if decision needed):**
```markdown
## Escalation: Should we migrate to async SQLAlchemy now?

Research shows it's safe and improves performance.
Recommend doing it, but it's a bigger refactor.
```

### 8.3 Researcher Agent Handoffs

**Scenario: Investigate model routing performance**

**Research (findings):**
```markdown
# Research: Model routing performance at scale

Found that model selection takes 200ms on 100k+ tasks.
Could optimize with caching (50ms).
```

**Inbox (recommendation):**
```markdown
## Proposal: Add model selection caching

Reduces overhead from 200ms to 50ms. Low risk.
Estimated effort: 4 hours.
```

---

## 9. FAQ

**Q: Can an inbox item also include findings?**  
A: Yes! Inbox items can include research data if it supports the decision. Keep research separate in a related Research document for future reference.

**Q: What if work isn't complete?**  
A: Create a status report anyway, but mark `status: "in_progress"`. Include blockers.

**Q: How detailed should reports be?**  
A: Aim for 1-3 pages. Full code diffs/transcripts go in git commits, not reports.

**Q: Can I update a report after creating it?**  
A: Yes! Use `PUT /api/documents/{id}` to update. Good practice to note changes.

**Q: What's the difference between "suggestion" and "proposal"?**  
A: Both go in Inbox. "Suggestion" is lighter (nice-to-have). "Proposal" is more formal (needs decision).

**Q: Should I wait for inbox approval before continuing?**  
A: Depends on the task. Check task dependencies. If dependent tasks exist, they'll block automatically.

---

## 10. See Also

- **[Agent Task Reporting Specification](agent-reporting-spec.md)** — Detailed schema for task completion reports
- **[AGENTS.md](../AGENTS.md)** — Complete API reference for all endpoints
- **[Orchestrator Flow](orchestrator-flow.md)** — How tasks move through the system
- **[Task State Management](../AGENTS.md#task-state-management)** — work_state vs review_state

---

**Last Updated:** 2026-02-23  
**Version:** 1.0  
**Status:** Complete
