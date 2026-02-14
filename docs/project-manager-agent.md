# Project Manager Agent — Design & Implementation

**Status:** ✅ Implemented (2026-02-13)  
**Related:** Tiered approval system, autonomous task routing

---

## Overview

The **project-manager** is a specialized agent that acts as a central coordinator for task routing, delegation, and approval workflows. Rather than having the orchestrator directly route tasks to specialist agents (programmer, researcher, writer, etc.), tasks can be routed to project-manager for intelligent decision-making.

## Key Responsibilities

1. **Task Analysis** — Review task descriptions, context, and requirements
2. **Agent Selection** — Choose the appropriate specialist agent for the work
3. **Approval Review** — Evaluate completed work and decide whether to approve, request revisions, or escalate
4. **Task Creation** — Break down complex requests into smaller, actionable tasks
5. **Workflow Coordination** — Manage multi-step workflows across agents

## Architecture

```
┌──────────────────────────────────────────────────┐
│            Orchestrator Scanner                  │
│  • Finds eligible tasks                          │
└─────────────────┬────────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────────────┐
│            Orchestrator Router                   │
│  • Check task type & requirements                │
│  • Route to: specialist OR project-manager       │
└─────────────────┬────────────────────────────────┘
                  │
         ┌────────┴────────┐
         ▼                 ▼
┌──────────────┐   ┌──────────────────────┐
│  Specialist  │   │   Project Manager    │
│   Agents     │   │   • Analyzes task    │
│              │   │   • Delegates work   │
│              │   │   • Reviews output   │
└──────────────┘   └──────┬───────────────┘
                           │
                           ▼
                   ┌──────────────┐
                   │  Specialist  │
                   │    Agents    │
                   └──────────────┘
```

## When Tasks Go to Project Manager

The orchestrator routes tasks to project-manager when:

1. **Task requires coordination** — Multi-step workflows, cross-agent work
2. **Approval needed** — Tasks marked as requiring human approval
3. **Ambiguous requirements** — Unclear which specialist agent is best
4. **Complex analysis** — Needs strategic thinking before execution

Tasks route directly to specialists when:
- Clear agent type assignment (e.g., `assigned_agent_type = "programmer"`)
- Simple, well-scoped work
- Emergency fixes or time-sensitive work

## Tiered Approval System

Project-manager implements a **tiered approval workflow**:

### Tier 1: Auto-Approve (Proactive)
PM reviews completed work and approves if:
- ✅ All acceptance criteria met
- ✅ Code quality is high
- ✅ Tests passing
- ✅ Documentation updated
- ✅ No significant risks

**Result:** Task marked as done, no human review needed

### Tier 2: Human Approval Required
PM flags for human review when:
- ⚠️ Acceptance criteria partially met
- ⚠️ Trade-offs or compromises made
- ⚠️ Architecture changes
- ⚠️ Security/privacy implications
- ⚠️ Breaking changes to APIs

**Result:** Task moved to inbox for human decision

### Tier 3: Escalation
PM escalates when:
- ❌ Work doesn't match requirements
- ❌ Quality issues or bugs introduced
- ❌ Agent blocked or confused
- ❌ Scope creep or misunderstanding

**Result:** Task reassigned, detailed feedback provided

## Implementation Details

### Agent Location
- **Path:** `~/lobs-server/agents/project-manager/`
- **Config:** `agent.yaml` defines personality, capabilities, model
- **Prompts:** Managed in orchestrator (`prompter.py`)

### Prompt Structure

Project-manager receives:
- **Task details** — Title, description, acceptance criteria
- **Project context** — README, AGENTS.md, recent work
- **Available agents** — List of specialist types and their capabilities
- **Scripts** — Access to `lobs-tasks`, `lobs-status`, `lobs-inbox` scripts

### Decision Output Format

PM writes decision to `.work-summary`:

```markdown
## Analysis
[PM's assessment of the task]

## Decision
**Delegate to:** programmer

## Rationale
[Why this agent is the best choice]

## Instructions for Agent
[Specific guidance for the specialist]
```

Or for approvals:

```markdown
## Review
[PM's evaluation of the completed work]

## Decision
**Approve** | **Request Changes** | **Escalate**

## Feedback
[Specific feedback for the agent or human reviewer]
```

### Script Access

PM has access to these scripts in its workspace:

```bash
./scripts/lobs-tasks list-mine       # Check assigned tasks
./scripts/lobs-tasks get <id>        # Get task details
./scripts/lobs-tasks complete <id>   # Mark task completed
./scripts/lobs-status overview       # System status
./scripts/lobs-status projects       # List projects
./scripts/lobs-inbox list            # Check inbox
```

These scripts are shared across all agents (located in `~/lobs-server/bin/agent-scripts/` and copied to agent workspaces).

## Example Workflows

### Workflow 1: Task Delegation

1. User creates task: "Improve dashboard performance"
2. Orchestrator routes to project-manager (ambiguous requirements)
3. PM analyzes: needs profiling + optimization
4. PM creates subtasks:
   - "Profile dashboard rendering" → researcher
   - "Optimize slow components" → programmer
5. PM monitors progress, approves results

### Workflow 2: Auto-Approval

1. Programmer completes task: "Add dark mode toggle"
2. Orchestrator routes to project-manager for review
3. PM checks:
   - ✅ Toggle works
   - ✅ Tests added
   - ✅ Persists setting
   - ✅ Code quality good
4. PM auto-approves → task marked done

### Workflow 3: Human Review Required

1. Architect completes: "Redesign auth system"
2. PM reviews:
   - ✅ Design is solid
   - ⚠️ Breaking API changes
   - ⚠️ Migration required
3. PM sends to inbox: "Approve auth redesign?"
4. Human reviews, makes decision

## Benefits

1. **Reduced Human Load** — Auto-approve routine work
2. **Better Routing** — PM understands task nuances better than keyword matching
3. **Quality Control** — Consistent review criteria
4. **Learning** — PM learns what gets approved/rejected
5. **Flexibility** — Easy to adjust approval criteria

## Configuration

In `orchestrator/config.py`:

```python
# Enable PM routing for ambiguous tasks
USE_PROJECT_MANAGER = True

# Tasks that always go to PM for approval
PM_APPROVAL_REQUIRED = [
    "architecture changes",
    "security updates",
    "API changes"
]
```

## Future Enhancements

- **Learning from approvals** — Track what PM auto-approves vs. human overrides
- **Multi-agent coordination** — PM manages parallel work streams
- **Context carryover** — PM maintains project knowledge across tasks
- **Proactive suggestions** — PM proposes tasks based on codebase analysis

---

## Related Documentation

- [Tiered Approval System](tiered-approval-system.md) — Detailed approval workflow
- [Orchestrator AGENTS.md](../AGENTS.md) — Orchestrator architecture
- [Agent Scripts](../bin/agent-scripts/) — Shared agent tooling

**Last Updated:** 2026-02-14
