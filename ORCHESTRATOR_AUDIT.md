# Orchestrator Port Audit Report

**Date:** 2026-02-12  
**Auditor:** Subagent (orchestrator-audit)  
**Purpose:** Verify completeness of migration from `~/lobs-orchestrator/` to `~/lobs-server/app/orchestrator/`

---

## Executive Summary

The orchestrator port to lobs-server is **incomplete**. While core infrastructure (engine, worker, scanner) has been ported, **critical functionality has been stripped out**, most notably:

1. **🚨 CRITICAL: Prompter module** — Task prompt building with project context, agent guidance, engineering rules
2. **Circuit breaker** — Failure rate limiting and infrastructure health tracking
3. **Autonomous work finding** — Proactive task discovery and opportunity detection
4. **Agent configuration system** — Agent definitions, templates, and registry
5. **Advanced monitoring** — Failure pattern detection, diagnostic task creation, proactive suggestions
6. **Advanced orchestration** — Workflow management, pipelines, governance, collaboration

The ported version provides **basic task execution** but lacks the **intelligence layer** that made the original orchestrator effective.

---

## Module-by-Module Comparison

### ✅ Fully Ported Modules

| Module | Original | Ported | Status |
|--------|----------|--------|---------|
| **Scanner** | `orchestrator/services/scanner.py` | `app/orchestrator/scanner.py` | ✅ **Core logic ported** (simplified for DB) |
| **Router** | `orchestrator/core/router.py` | `app/orchestrator/router.py` | ✅ **Keyword-based routing preserved** |
| **Agent Tracker** | `orchestrator/core/agent_tracker.py` | `app/orchestrator/agent_tracker.py` | ✅ **Status tracking ported to DB** |

**Notes:**
- Scanner: Ported with DB queries instead of git operations. GitHub integration removed (intentional).
- Router: Simplified but functional. LLM-based routing removed.
- Agent Tracker: Fully functional with DB persistence instead of JSON files.

---

### ⚠️ Partially Ported Modules (Missing Features)

#### **Engine** (`orchestrator/core/engine.py` → `app/orchestrator/engine.py`)

**What was ported:**
- ✅ Main polling loop
- ✅ Task dispatch to workers
- ✅ Pause/resume lifecycle
- ✅ Worker health checking
- ✅ Graceful shutdown

**What was NOT ported:**
- ❌ **Proactive work scanning** (`_process_proactive_work`, `_should_scan_proactive`)
  - Opportunity detection and autonomous task creation
  - AI-driven suggestions for next work
  - Quiet hours and daily limits for proactive work
  - Architect proposal creation from opportunities
  - Inbox proposal creation
- ❌ **Workflow state management** (`_update_workflow_state`)
- ❌ **Initiative tracking** (`get_initiative_status`)
- ❌ **System heartbeat pulsing** (`_pulse_system_heartbeat`)
- ❌ **Autonomous agent launching** (`_launch_autonomous_agents`)
- ❌ **AI suggestion generation** (`_get_ai_suggestions`, `_pick_advisor_agent_type`)

**Impact:** 🔴 **High**  
The ported engine is a **basic task poller** vs the original's **intelligent orchestration system**.

---

#### **Worker** (`orchestrator/core/worker.py` → `app/orchestrator/worker.py`)

**What was ported:**
- ✅ Worker spawning via OpenClaw
- ✅ Process lifecycle management (timeouts, kill logic)
- ✅ Domain locks (one worker per project)
- ✅ Status updates to DB
- ✅ Log tailing and error extraction
- ✅ Git operations (pull/push/commit)
- ✅ Worker cleanup on completion/failure

**What was NOT ported:**
- ❌ **Task prompt building** — uses trivial `title + notes` instead of Prompter
- ❌ **Agent template file writing** (`_write_agent_template_files`)
- ❌ **Agent memory injection** (`_write_memory_to_workspace`)
- ❌ **GitHub integration** (issue status updates, PR linking) — intentionally removed
- ❌ **Automatic code review triggering** (`_trigger_automatic_review`)
- ❌ **Handoff processing** (`_process_handoffs`)
- ❌ **AI-powered commit message generation** (`_generate_commit_message`)
- ❌ **Conflict recovery** (`_attempt_conflict_recovery`)
- ❌ **Usage tracking from OpenClaw sessions** (`_capture_worker_usage`)

**Impact:** 🔴 **High**  
The prompt building omission is **CRITICAL** — workers get barebones instructions instead of rich context.

---

#### **Monitor** (`orchestrator/core/monitor.py` → `app/orchestrator/monitor.py`)

**What was ported:**
- ✅ Stuck task detection (basic)
- ✅ Worker health checks

**What was NOT ported:**
- ❌ **Inbox processing** (`process_inbox`, `_triage_inbox_item`, `_spawn_agent_task_from_inbox`)
- ❌ **Auto-unblock logic** (`auto_unblock_blocked_tasks`, `_extract_depends_on`, `_deps_completed`)
- ❌ **Failure pattern detection** (`check_failure_patterns`, `_detect_patterns`, `_should_create_diagnostic`)
- ❌ **Diagnostic task creation** (`_create_diagnostic_task`)
- ❌ **Proactive suggestion generation** (`generate_proactive_suggestions`)
- ❌ **Project context analysis** (`_read_project_context_files`, `_get_recent_commits`)

**Impact:** 🟡 **Medium**  
Basic monitoring works, but the **intelligence layer** (auto-recovery, diagnostics, suggestions) is missing.

---

#### **Escalation** (`orchestrator/core/escalation.py` → `app/orchestrator/escalation.py`)

**What was ported:**
- ✅ Basic failure alert creation
- ✅ Task escalation to stuck status

**What was NOT ported:**
- ❌ **Multi-tier escalation** (Level 1/2/3 handling)
  - `_handle_level_1` — auto-retry with same agent
  - `_handle_level_2` — route to reviewer agent for analysis
  - `_handle_level_3` — human escalation with rich context
- ❌ **Reviewer result processing** (`process_reviewer_result`)
- ❌ **AI-powered retry decision** (`_extract_reviewer_recommendation`)
- ❌ **Subtask decomposition** (`_apply_reviewer_subtask`)

**Impact:** 🟡 **Medium**  
Failures are logged but not intelligently handled. No auto-recovery attempts.

---

### ❌ Not Ported (Intentional Exclusions)

These modules were **git-workflow specific** and correctly excluded:

| Module | Reason |
|--------|--------|
| `services/control.py` | Git-based task state management — replaced by DB |
| `core/reconciler.py` | Git repo reconciliation — not needed with DB |
| `services/github_onboarding.py` | GitHub project setup — not needed |

---

### ❌ Not Ported (Critical Missing Functionality)

#### **🚨 services/prompter.py — CRITICAL MISSING**

**What it does:**
- Builds comprehensive task prompts with:
  - Agent context (AGENTS.md + SOUL.md)
  - Engineering rules (global + project-specific)
  - Product context (README, CONTEXT.md, etc.)
  - Agent-specific guidance (programmer vs researcher vs writer)
  - Project file summaries
  - Recent commits
  - Input/output path specifications
  - Structured task details
  - Output contract (`.work-summary` format)

**Current state in port:**
```python
# app/orchestrator/worker.py:140
prompt_content = f"{task_title}\n\n{task_notes}".strip()
```

**Impact:** 🔴 **CRITICAL**  
Workers get **barebones prompts** instead of rich, structured context. This severely degrades agent effectiveness.

**Must port:** ✅ **YES**

---

#### **core/circuit_breaker.py — Missing Resilience**

**What it does:**
- Tracks infrastructure failure rates (OpenClaw crashes, API errors)
- Opens circuit after N consecutive failures
- Prevents cascading failures
- Provides cooldown periods

**Impact:** 🟡 **Medium**  
Without this, a flaky OpenClaw instance could cause rapid-fire worker spawn failures.

**Should port:** ⚠️ **Recommended**

---

#### **core/agents.py + core/registry.py — Missing Agent System**

**What it does:**
- Defines agent configurations (AgentConfig dataclass)
- Loads agent templates (AGENTS.md, SOUL.md, IDENTITY.md)
- Provides agent registry for lookup
- Used by Prompter to inject agent context

**Impact:** 🔴 **High**  
Prompter depends on this. Without it, no agent-specific context.

**Must port:** ✅ **YES** (required for Prompter)

---

#### **core/autonomous.py — Missing Proactive Work**

**What it does:**
- Launches idle agents to find work autonomously
- Cooldown tracking per agent type
- Status reporting for autonomous mode

**Impact:** 🟢 **Low**  
Nice-to-have for autonomous operation, but not critical for directed tasks.

**Should port:** 📋 **Future enhancement**

---

### ❌ Not Ported (Nice-to-Have Features)

| Module | Purpose | Priority |
|--------|---------|----------|
| `core/awareness.py` | System awareness context | 📋 Future |
| `core/collaboration.py` | Multi-agent collaboration | 📋 Future |
| `core/governance.py` | Task governance rules | 📋 Future |
| `core/pipelines.py` | Multi-step pipelines | 📋 Future |
| `core/workflow.py` | Workflow state management | 📋 Future |
| `core/heartbeat.py` | Heartbeat management | 📋 Future |
| `core/agent_memory.py` | Per-agent memory evolution | 📋 Future |
| `core/failure_rotation.py` | Agent rotation on failures | 📋 Future |
| `core/observer.py` | Event observation hooks | 📋 Future |
| `core/recurring_scheduler.py` | Recurring task scheduling | 📋 Future |
| `services/opportunities.py` | Opportunity detection | 📋 Future |
| `services/messages.py` | Message sending | 📋 Future |
| `services/agent_meta.py` | Agent metadata management | 📋 Future |
| `services/chat.py` | Chat interface | 📋 Future |

**Impact:** 🟢 **Low**  
These are **advanced features** that can be added incrementally. The core orchestrator can function without them.

---

## Critical Missing Functionality Summary

### 🚨 Must Fix Now

1. **Prompter module** — Port `services/prompter.py`
   - Dependency: Need `core/agents.py` + `core/registry.py` for agent configs
   - Dependency: Need to determine how to load project context files
   - **This is blocking worker effectiveness**

2. **Agent configuration system** — Port `core/agents.py` + `core/registry.py`
   - Required by Prompter
   - Defines agent templates and loads context files

### ⚠️ Should Fix Soon

3. **Circuit breaker** — Port `core/circuit_breaker.py`
   - Prevents cascading failures
   - Simple standalone module

4. **Enhanced monitor** — Add back:
   - Failure pattern detection
   - Diagnostic task creation
   - Auto-unblock logic for dependency chains

5. **Enhanced escalation** — Add back:
   - Multi-tier escalation (auto-retry → reviewer → human)
   - Reviewer result processing

### 📋 Nice-to-Have (Future)

6. **Proactive work** in engine — Add back:
   - Opportunity detection
   - AI suggestions
   - Autonomous agent launching

7. **Advanced features** — Consider porting:
   - Pipelines (multi-step workflows)
   - Governance (task approval rules)
   - Collaboration (multi-agent coordination)

---

## Recommended Action Plan

### Phase 1: Critical Fixes (This Week)

1. **Port agent configuration system**
   - [ ] Create `app/orchestrator/agents.py` with AgentConfig model
   - [ ] Create `app/orchestrator/registry.py` to load agent templates
   - [ ] Define where agent templates live (copy from `~/lobs-orchestrator/agents/`?)
   - [ ] Test agent config loading

2. **Port prompter module**
   - [ ] Create `app/orchestrator/prompter.py`
   - [ ] Port `Prompter.build_task_prompt()` method
   - [ ] Port `Prompter.build_diagnostic_prompt()` method
   - [ ] Port `Prompter.build_research_prompt()` method
   - [ ] Update `worker.py` to use Prompter instead of simple concatenation
   - [ ] Test prompt generation with sample tasks

3. **Verify worker effectiveness**
   - [ ] Run a test task (programmer agent)
   - [ ] Compare prompt quality (old vs new)
   - [ ] Verify agent gets proper context

### Phase 2: Resilience (Next Week)

4. **Port circuit breaker**
   - [ ] Create `app/orchestrator/circuit_breaker.py`
   - [ ] Integrate with worker spawn logic
   - [ ] Test failure handling

5. **Enhance monitor**
   - [ ] Add failure pattern detection
   - [ ] Add diagnostic task creation
   - [ ] Add auto-unblock logic

6. **Enhance escalation**
   - [ ] Add multi-tier escalation
   - [ ] Add reviewer integration

### Phase 3: Intelligence (Future)

7. **Add proactive work**
   - [ ] Port opportunity detection
   - [ ] Port AI suggestion generation
   - [ ] Add to engine's run loop

8. **Consider advanced features**
   - Evaluate need for pipelines, governance, collaboration
   - Port incrementally as use cases emerge

---

## Testing Recommendations

After porting critical modules:

1. **Unit tests**
   - Prompter prompt generation
   - Agent config loading
   - Circuit breaker state transitions

2. **Integration tests**
   - End-to-end task execution
   - Worker prompt injection
   - Failure handling

3. **Comparison tests**
   - Run same task on both orchestrators
   - Compare prompts
   - Compare worker behavior
   - Measure success rates

---

## Conclusion

The orchestrator port is **functionally incomplete** but **architecturally sound**. The DB-based state management is cleaner than git-based. However, the **intelligence layer has been stripped out**, most critically:

1. **Prompter** — workers get trivial prompts
2. **Agent configs** — no agent-specific context
3. **Circuit breaker** — no failure resilience
4. **Advanced monitoring** — no auto-recovery or diagnostics

**Immediate priority:** Port Prompter + Agent configs. This is blocking worker effectiveness.

**Next priority:** Port Circuit Breaker for resilience.

**Future work:** Incrementally restore intelligence features (proactive work, suggestions, collaboration).

---

**End of audit.**
