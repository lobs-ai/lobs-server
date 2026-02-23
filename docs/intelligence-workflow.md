# Intelligence Workflow — Human-in-the-Loop Initiative Review

**Last Updated:** 2026-02-22

This guide explains how the **Intelligence** view in Mission Control works, how agent initiatives are proposed, and how you (Lobs) review and approve them.

## Overview

The Intelligence system is a continuous improvement loop where AI agents:

1. **Reflect** on their recent work (every 6 hours)
2. **Propose** initiatives — ideas for improvements, features, refactors, or new work
3. **Submit** those proposals for human review
4. **Receive feedback** on what gets approved/rejected/deferred
5. **Learn** from the feedback loop to improve future proposals

**You control the workflow.** Nothing becomes a task without your explicit approval.

---

## The Intelligence View

The Intelligence view has **three tabs**:

### 1. Initiatives Tab

This is where you review agent proposals.

**What you see:**
- List of all initiatives, grouped by status (Pending Review, Approved, Rejected, Deferred)
- Each initiative shows:
  - **Title** — what the agent wants to do
  - **Description** — detailed explanation and rationale
  - **Category** — type of work (see below)
  - **Risk Tier** — scope/complexity level (see below)
  - **Proposing Agent** — who suggested it
  - **Status** — current state (pending, approved, rejected, deferred)

**What you can do:**
- **Search/Filter** — by status, category, agent, or keywords
- **Sort** — by date, title, or risk tier
- **Select multiple** — for batch actions
- **Click to review** — opens detail view in right panel

**Detail View:**
- Full initiative description
- Metadata (dates, agents, category, risk tier)
- Decision history (if already reviewed)
- **Action buttons** (for pending initiatives):
  - **Approve** → Creates a task and assigns to selected agent
  - **Defer** → Postpone decision (keeps it in the system)
  - **Reject** → Decline the proposal (with optional feedback)
- Optional notes field for your decision rationale

**Batch Actions:**
When you select multiple initiatives, a batch action bar appears with:
- Approve all selected
- Defer all selected
- Reject all selected

### 2. Reflections Tab

Shows the reflection cycle history — when agents completed their strategic reflection sessions and what they proposed.

**What you see:**
- Reflection batch IDs and timestamps
- Which agents participated
- Status (pending, running, completed, failed)
- Number of initiatives proposed from each reflection
- Summary of findings (inefficiencies, risks, opportunities)

### 3. Sweep History Tab

Shows historical review sessions where you processed batches of initiatives.

**What you see:**
- Sweep ID and date
- Total initiatives in that sweep
- Breakdown: how many approved/rejected/deferred
- Summary notes from the review session

---

## How Initiatives Are Proposed

### Reflection Cycles

Every **6 hours**, the orchestrator triggers a **strategic reflection cycle**:

1. **Context packet** is built for each agent containing:
   - Recent tasks they worked on
   - Recent initiative decisions (what was approved/rejected for them)
   - System-wide activity
   - Agent-specific metrics
   
2. **Agent reflects** in an isolated session (NOT the main agent session):
   - Reviews what they've done recently
   - Identifies inefficiencies, missed opportunities, system risks
   - Proposes new initiatives based on their domain expertise
   
3. **Proposals are extracted** from the reflection output as JSON:
   - Each initiative has: title, description, category, risk tier, estimated effort
   - Stored in the `agent_initiatives` database table
   - Status set to `proposed` → routed to Lobs for review

4. **Summary sent to you** (via agent:main:main session):
   - List of new proposals
   - Agent's reflection findings
   - Links to review them in the UI

### Agent-Specific Focus Areas

Each agent type has a different reflection focus:

- **Programmer** — Code quality, tech debt, tests, bugs, refactors, new features
- **Architect** — System design, architecture drift, scalability, infrastructure gaps
- **Researcher** — Investigation opportunities, technology evaluation, best practices
- **Reviewer** — Code patterns causing bugs, quality infrastructure, review process improvements
- **Writer** — Undocumented features, stale docs, knowledge gaps, onboarding improvements

Agents are instructed to **propose concrete, actionable work** (1-3 day scope) rather than vague improvements.

---

## Initiative Categories

Initiatives are categorized to help you understand the type of work:

### Low-Risk Maintenance
- **docs_sync** — Update documentation to match code
- **test_hygiene** — Add missing tests, fix flaky tests
- **stale_triage** — Clean up old tasks, backlog items
- **light_research** — Investigate a question or technology
- **backlog_reprioritization** — Re-evaluate task priorities

### Medium-Risk Improvements
- **automation_proposal** — Build tools or scripts to automate work
- **moderate_refactor** — Restructure code without changing behavior
- **feature_proposal** — New user-facing capability (scoped small)
- **architecture_change** — Small structural improvements

### High-Risk Changes
- **destructive_operation** — Deletes or migrates data
- **cross_project_migration** — Changes that span multiple repos/systems
- **agent_recruitment** — Propose new agent types or capabilities
- **new_project** — Standalone new system/tool

**Purpose:** Helps you prioritize and assess risk at a glance.

---

## Risk Tiers

Initiatives are assigned a risk tier (A, B, C, or D) based on scope and complexity:

- **A (Low)** — Small, safe changes. Examples: documentation, adding tests, minor refactors
- **B (Medium)** — Moderate scope. Examples: new feature (scoped), automation tool, schema changes
- **C (High)** — Significant effort or system impact. Examples: architecture changes, cross-project work
- **D (Critical)** — Major undertakings or risky operations. Examples: data migrations, new projects

**Visual indicators:** Color-coded in the UI (green → yellow → orange → red)

---

## Approval Workflow

### Single Initiative Review

**Steps:**
1. Navigate to Intelligence → Initiatives tab
2. Filter to "Pending Review" (or search)
3. Click an initiative to see details
4. Read the description and rationale
5. Decide:
   - **Approve** → Initiative becomes a task
   - **Defer** → Keep it in the system for later decision
   - **Reject** → Decline with optional feedback
6. (Optional) Add decision notes
7. Click the action button

**What happens when you approve:**
- A new task is created in the selected project
- Task assigned to the selected agent (or suggested agent if not specified)
- Task notes include:
  - Source initiative ID
  - Original proposing agent
  - Category and risk tier
  - Initiative description
- Initiative status → `approved`
- Initiative links to the created task ID
- Decision recorded in `initiative_decision_records` table
- Feedback sent back to the proposing agent via a reflection record

### Batch Review (Recommended)

When you have multiple pending initiatives, batch review is faster and more efficient:

**Steps:**
1. Navigate to Intelligence → Initiatives
2. Filter to "Pending Review"
3. Select multiple initiatives (checkboxes)
4. Use batch action buttons:
   - **Approve** — all selected become tasks
   - **Defer** — all selected postponed
   - **Reject** — all selected declined
5. (Optional) Add notes explaining the batch decision

**Batch endpoint:** `POST /api/orchestrator/intelligence/initiatives/batch-decide`

**Batch decisions support:**
- Mixed decisions (approve some, reject others) in one API call
- Creating new tasks directly (inspired by initiatives, not tied to specific proposals)
- Full stats returned (approved/rejected/deferred counts)
- Error handling (missing initiatives don't block the batch)

**Use case:** After a reflection cycle completes, you get 5-10 new proposals. Review them all together, spot duplicates, prioritize, and make decisions in one session.

---

## The Feedback Loop

### How Agents Learn From Decisions

Every decision you make is fed back into the agent's future reflection prompts.

**Decision history includes:**
- Approved initiatives → "What Gets Approved" section
- Rejected initiatives → "Patterns to Avoid" section
- Deferred initiatives → Listed with feedback notes

**Example feedback prompt excerpt:**
```
## 📊 Your Recent Initiative Decision History (Last 7 Days)

### ✅ Approved (3)
- **Add retry logic to worker.py** [automation_proposal]
  Why approved: Solves real Gateway timeout issue we've been hitting

### ❌ Rejected (2)
- **Improve monitoring** [architecture_change]
  Why rejected: Too vague, no specific metrics or systems named

### 🎯 Patterns to Avoid
Based on your rejections, do NOT propose initiatives that:
- Are vague, speculative, or lack concrete first steps
- Propose work that's already covered by existing tasks
```

**Purpose:** Agents learn what you value and stop re-proposing rejected ideas.

### Decision Fields

When making a decision, you can provide:
- **revised_title** — Edit the initiative title before approving
- **revised_description** — Edit the description
- **selected_agent** — Override the suggested agent
- **selected_project_id** — Choose which project the task goes into
- **decision_summary** — Brief explanation of your decision
- **learning_feedback** — Specific guidance for the agent

**These fields are stored and shown back to the agent.**

---

## API Endpoints

### List Initiatives
```http
GET /api/orchestrator/intelligence/initiatives?status=proposed&limit=200
```

**Query params:**
- `status` (optional) — Filter by status (proposed, approved, rejected, deferred)
- `limit` (optional) — Max results (default: 200, max: 1000)

**Returns:**
```json
{
  "count": 5,
  "items": [
    {
      "id": "uuid",
      "proposed_by_agent": "programmer",
      "owner_agent": "programmer",
      "title": "Add retry logic to worker spawning",
      "description": "...",
      "category": "automation_proposal",
      "risk_tier": "A",
      "policy_lane": "review_required",
      "status": "proposed",
      "rationale": "...",
      "created_at": "2026-02-22T10:00:00Z",
      ...
    }
  ]
}
```

### Decide Initiative (Single)
```http
POST /api/orchestrator/intelligence/initiatives/{initiative_id}/decide
```

**Request body:**
```json
{
  "decision": "approve",  // or "defer" or "reject"
  "revised_title": "Add retry logic with exponential backoff",
  "revised_description": null,
  "selected_agent": "programmer",
  "selected_project_id": "lobs-server",
  "decision_summary": "Good concrete improvement",
  "learning_feedback": "This is the level of specificity we want"
}
```

**Returns:**
```json
{
  "initiative_id": "uuid",
  "status": "approved",
  "task_id": "task-uuid",
  "selected_agent": "programmer",
  "selected_project_id": "lobs-server"
}
```

### Batch Decide (Recommended)
```http
POST /api/orchestrator/intelligence/initiatives/batch-decide
```

**Request body:**
```json
{
  "decisions": [
    {
      "initiative_id": "uuid-1",
      "decision": "approve",
      "selected_agent": "programmer",
      "decision_summary": "Solves real problem"
    },
    {
      "initiative_id": "uuid-2",
      "decision": "reject",
      "decision_summary": "Too vague, lacks specifics"
    }
  ],
  "new_tasks": [
    {
      "title": "Investigate Ollama performance issues",
      "notes": "Inspired by multiple initiatives but not tied to one",
      "project_id": "lobs-server",
      "agent": "researcher",
      "rationale": "Common theme across 3 proposals"
    }
  ]
}
```

**Returns:**
```json
{
  "total": 2,
  "processed": 2,
  "approved": 1,
  "deferred": 0,
  "rejected": 1,
  "failed": 0,
  "results": [...],
  "errors": [],
  "created_tasks": [
    {
      "task_id": "uuid",
      "title": "Investigate Ollama performance issues",
      ...
    }
  ]
}
```

---

## Best Practices

### For Reviewing Initiatives

✅ **Do:**
- Review in batches when possible (more context, spot duplicates)
- Provide decision_summary for rejected initiatives (helps agents learn)
- Edit titles/descriptions to clarify approved work
- Check if the proposal is already covered by an existing task
- Look for concrete, actionable first steps

❌ **Don't:**
- Approve vague proposals ("improve X", "enhance Y")
- Approve work that duplicates existing tasks
- Reject without explanation (agents can't learn from silent rejections)
- Defer indefinitely (decide or reject)

### For Agents (Reflected in Reflection Prompts)

Agents are instructed to:
- Propose 1-3 high-quality ideas, not 5+ mediocre ones
- Be specific: name files, modules, endpoints
- Explain WHY it matters (value proposition)
- Include concrete first steps (1-3 day scope)
- Check decision history to avoid re-proposing rejected ideas

**Quality bar:** Every rejected initiative wastes review time. Agents are told: "An empty list is better than noise."

---

## Common Scenarios

### Scenario: Duplicate Proposals

**Problem:** Two agents propose the same improvement.

**Solution:**
1. Batch review catches this (you see both together)
2. Approve one, reject the other with note: "Duplicate of initiative X"
3. Or create a single new task inspired by both (use `new_tasks` in batch endpoint)

### Scenario: Good Idea, Wrong Scope

**Problem:** Agent proposes "Build full CI/CD pipeline" (too big).

**Solution:**
- Reject with feedback: "Too large. Propose the smallest first step (e.g., 'Add GitHub Actions config for tests')"
- Agent learns to scope smaller in future reflections

### Scenario: Partial Approval

**Problem:** Initiative has good core idea but needs refinement.

**Solution:**
- Approve with revised_title and revised_description
- Add learning_feedback: "This is good but needs X clarification. Next time include Y upfront."

### Scenario: Deferred Initiative Backlog

**Problem:** Many initiatives in "deferred" status.

**Solution:**
- Periodically review deferred items
- Either approve (now ready), reject (no longer relevant), or keep deferred
- Use search/filter to find old deferred items

---

## Troubleshooting

### No initiatives appearing

**Check:**
1. Are reflection cycles running? (GET `/api/orchestrator/health`)
2. Are agents completing reflections? (Intelligence → Reflections tab)
3. Are reflection outputs being parsed? (Check server logs for JSON extraction errors)
4. Is status filter set to "Pending Review"?

### Initiative approved but no task created

**Check:**
1. Did the approval return a `task_id`? (API response)
2. Is the project_id valid? (Default: `lobs-server`)
3. Check server logs for task creation errors
4. Verify database `tasks` table has the new row

### Batch decision partially failed

**Check:**
- Response includes `errors` array with details
- Common causes: initiative not found, invalid decision value, permission error
- Failed items don't block successful ones

### Agent keeps re-proposing rejected ideas

**Check:**
1. Is decision_summary or learning_feedback being provided?
2. Is decision history being loaded into reflection prompts? (Check reflection prompt logs)
3. Is the 7-day lookback window capturing the rejection? (Might need to reject again with explicit "DO NOT re-propose" note)

---

## Example Workflow

### Morning Review Session

**8:00 AM** — Reflection cycle completed overnight.

**8:05 AM** — You get a summary message:
```
5 new initiatives proposed:
- programmer: Add retry logic to worker.py
- architect: Extract API auth into middleware
- writer: Document intelligence workflow
- researcher: Investigate Ollama fine-tuning
- programmer: Fix flaky test in test_sessions.py
```

**8:10 AM** — You open Intelligence → Initiatives → Filter "Pending Review"

**8:15 AM** — You review them:
- Select: retry logic, auth middleware, intelligence workflow doc, flaky test fix
- Approve batch (all 4)
- Reject: Ollama fine-tuning (decision_summary: "Not a current priority, revisit in Q2")

**8:16 AM** — 4 new tasks created and assigned to agents. Done.

---

## Data Model Reference

### AgentInitiative Table

**Key fields:**
- `id` (PK)
- `proposed_by_agent` — Agent type that created it
- `source_reflection_id` — Link to reflection cycle
- `title`, `description`, `rationale`
- `category` — Work type (see categories above)
- `risk_tier` — A/B/C/D
- `policy_lane` — Usually "review_required"
- `status` — proposed, lobs_review, approved, rejected, deferred
- `approved_by` — Who decided (usually "lobs")
- `selected_agent` — Assigned agent after approval
- `selected_project_id` — Target project
- `task_id` — Created task ID (if approved)
- `decision_summary`, `learning_feedback` — Your notes
- `created_at`, `updated_at`

### InitiativeDecisionRecord Table

Tracks each decision for audit and analytics:
- `initiative_id` (FK)
- `sweep_id` — Batch review session ID (if batch)
- `decision` — approve/reject/defer
- `decided_by` — lobs
- `decision_summary` — Notes
- `overlap_with_ids`, `contradiction_with_ids` — For duplicate detection
- `capability_gap` — Flag if this revealed missing agent capability
- `source_reflection_ids` — Traceability
- `task_id` — Result task (if approved)

---

## Future Enhancements

**Planned:**
- Auto-categorization of initiatives via LLM
- Duplicate detection and clustering
- Initiative dependency graph (X blocks Y)
- Metrics dashboard (approval rates by agent, category trends)
- Scheduled batch reviews (e.g., every Monday at 9 AM)
- Initiative templates for common patterns

---

## Related Documentation

- [Orchestrator Architecture](../ARCHITECTURE.md#task-orchestrator) — How reflection cycles are triggered
- [Agent Configuration](../app/orchestrator/registry.py) — Agent capability definitions
- [Reflection Prompts](../app/orchestrator/reflection_cycle.py) — Full reflection prompt templates
- [Model Routing](./guides/model-routing.md) — How models are selected for reflections
- [Decision ADRs](./decisions/) — Architectural decisions about the intelligence system

---

## Questions?

If something in the Intelligence view isn't working as expected:
1. Check the Reflections tab for failed cycles
2. Review server logs for reflection/initiative errors
3. Verify orchestrator health: `GET /api/orchestrator/health`
4. Check the `agent_reflections` and `agent_initiatives` tables directly

For feature requests or bugs, create an initiative via the chat interface or file directly in the lobs-server issue tracker.
