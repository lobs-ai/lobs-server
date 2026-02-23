"""Strategic reflection and daily identity compression routines."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentReflection, AgentIdentityVersion, SystemSweep
from app.orchestrator.config import CONTROL_PLANE_AGENTS
from app.orchestrator.context_packets import ContextPacketBuilder
from app.orchestrator.model_chooser import ModelChooser
from app.orchestrator.registry import AgentRegistry
from app.orchestrator.worker import WorkerManager

logger = logging.getLogger(__name__)


class ReflectionCycleManager:
    """Runs strategic reflection jobs and daily compression sweeps."""

    def __init__(self, db: AsyncSession, worker_manager: WorkerManager):
        self.db = db
        self.worker_manager = worker_manager
        self.registry = AgentRegistry()

    def _execution_agents(self) -> list[str]:
        return [a for a in self.registry.available_types() if a not in CONTROL_PLANE_AGENTS]

    async def run_strategic_reflection_cycle(self) -> dict[str, Any]:
        agents = self._execution_agents()
        if not agents:
            return {"agents": 0, "spawned": 0}

        packet_builder = ContextPacketBuilder(self.db)
        chooser = ModelChooser(self.db)
        window_end = datetime.now(timezone.utc)
        window_start = window_end - timedelta(hours=6)

        spawned = 0
        for agent in agents:
            packet = await packet_builder.build_for_agent(agent, hours=6)
            reflection_id = str(uuid.uuid4())

            self.db.add(
                AgentReflection(
                    id=reflection_id,
                    agent_type=agent,
                    reflection_type="strategic",
                    status="pending",
                    window_start=window_start,
                    window_end=window_end,
                    context_packet=packet.to_dict(),
                )
            )

            prompt = self._build_reflection_prompt(agent, packet.to_dict(), reflection_id)
            choice = await chooser.choose(
                agent_type=agent,
                task={
                    "id": reflection_id,
                    "title": "Strategic reflection cycle",
                    "notes": "Periodic strategic reflection run",
                    "status": "inbox",
                },
                purpose="reflection",
            )
            label = f"reflection-{agent}"
            result, error, _error_type = await self.worker_manager._spawn_session(
                task_prompt=prompt,
                agent_id=agent,
                model=choice.model,
                label=label,
            )

            if result:
                self.worker_manager.register_external_worker(
                    result,
                    agent_type=agent,
                    model=choice.model,
                    label=label,
                )
                spawned += 1
            else:
                logger.warning("Failed to spawn reflection for %s: %s", agent, error)

        sweep = SystemSweep(
            id=str(uuid.uuid4()),
            sweep_type="reflection_batch",
            status="completed",
            window_start=window_start,
            window_end=window_end,
            summary={"agents": len(agents), "spawned": spawned},
            decisions={"note": "Sweep scaffolding active; initiative merge engine pending"},
            completed_at=datetime.now(timezone.utc),
        )
        self.db.add(sweep)
        await self.db.commit()

        return {"agents": len(agents), "spawned": spawned, "sweep_id": sweep.id}

    async def run_daily_compression(self) -> dict[str, Any]:
        agents = self._execution_agents()
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=24)

        rewritten = 0
        validations_failed = 0
        changed_heuristics_total = 0
        removed_rules_total = 0

        for agent in agents:
            reflections_result = await self.db.execute(
                select(AgentReflection)
                .where(
                    AgentReflection.agent_type == agent,
                    AgentReflection.created_at >= since,
                    AgentReflection.reflection_type.in_(["strategic", "diagnostic"]),
                )
                .order_by(AgentReflection.created_at.desc())
                .limit(100)
            )
            reflections = reflections_result.scalars().all()

            if not reflections:
                continue

            version_q = await self.db.execute(
                select(func.max(AgentIdentityVersion.version)).where(
                    AgentIdentityVersion.agent_type == agent
                )
            )
            max_version = version_q.scalar() or 0

            active_q = await self.db.execute(
                select(AgentIdentityVersion)
                .where(
                    AgentIdentityVersion.agent_type == agent,
                    AgentIdentityVersion.active.is_(True),
                )
                .order_by(AgentIdentityVersion.version.desc())
                .limit(1)
            )
            previous_active = active_q.scalar_one_or_none()

            compressed = self._compress_reflections(agent, reflections)
            changed_heuristics = compressed["changed_heuristics"]
            removed_rules = compressed["removed_rules"]
            
            # Check if any reflections had meaningful result data
            reflections_with_data = sum(
                1 for r in reflections
                if r.status == "completed" and r.result and isinstance(r.result, dict) and "raw" not in r.result
            )

            validation_ok, validation_reason = self._run_lobs_validation_gate(
                identity_text=compressed["identity_text"],
                changed_heuristics=changed_heuristics,
                removed_rules=removed_rules,
                reflections_with_data=reflections_with_data,
            )

            candidate = AgentIdentityVersion(
                id=str(uuid.uuid4()),
                agent_type=agent,
                version=max_version + 1,
                identity_text=compressed["identity_text"],
                summary=f"Auto-compressed from {len(reflections)} reflections",
                active=False,
                window_start=since,
                window_end=now,
                changed_heuristics=changed_heuristics,
                removed_rules=removed_rules,
                validation_status="passed" if validation_ok else "failed",
                validation_reason=validation_reason,
            )
            self.db.add(candidate)

            if validation_ok:
                await self.db.execute(
                    update(AgentIdentityVersion)
                    .where(
                        and_(
                            AgentIdentityVersion.agent_type == agent,
                            AgentIdentityVersion.active.is_(True),
                        )
                    )
                    .values(active=False)
                )
                candidate.active = True
                rewritten += 1
                changed_heuristics_total += len(changed_heuristics)
                removed_rules_total += len(removed_rules)
            else:
                validations_failed += 1
                if previous_active is not None:
                    previous_active.active = True
                logger.warning(
                    "Daily identity compression validation failed for %s v%s: %s",
                    agent,
                    max_version + 1,
                    validation_reason,
                )

        sweep = SystemSweep(
            id=str(uuid.uuid4()),
            sweep_type="daily_cleanup",
            status="completed",
            window_start=since,
            window_end=now,
            summary={
                "agents": len(agents),
                "rewritten": rewritten,
                "validation_failures": validations_failed,
                "changed_heuristics": changed_heuristics_total,
                "removed_rules": removed_rules_total,
            },
            decisions={
                "identity_rewrite": "versioned rewrite with lobs validation gate",
                "changed_heuristics": changed_heuristics_total,
                "removed_rules": removed_rules_total,
            },
            completed_at=now,
        )
        self.db.add(sweep)
        await self.db.commit()

        return {
            "agents": len(agents),
            "rewritten": rewritten,
            "validation_failures": validations_failed,
            "changed_heuristics": changed_heuristics_total,
            "removed_rules": removed_rules_total,
            "sweep_id": sweep.id,
        }

    @staticmethod
    def _format_decision_history(packet: dict[str, Any]) -> str:
        """Format recent initiative decisions into a readable section for the prompt."""
        decisions = packet.get("recent_initiative_decisions", [])
        if not decisions:
            return ""

        approved = [d for d in decisions if d.get("status") == "approved"]
        rejected = [d for d in decisions if d.get("status") == "rejected"]
        deferred = [d for d in decisions if d.get("status") == "deferred"]

        lines = [
            "## 📊 Your Recent Initiative Decision History (Last 7 Days)",
            "",
            "**Study this carefully.** These are YOUR past proposals and how they were received.",
            "Learn from what was approved and what was rejected. Do NOT re-propose rejected ideas.",
            "",
        ]

        if approved:
            lines.append(f"### ✅ Approved ({len(approved)})")
            for d in approved:
                lines.append(f"- **{d['title']}** [{d.get('category', '?')}]")
                if d.get("decision_summary"):
                    lines.append(f"  Why approved: {d['decision_summary']}")
            lines.append("")

        if rejected:
            lines.append(f"### ❌ Rejected ({len(rejected)})")
            for d in rejected:
                lines.append(f"- **{d['title']}** [{d.get('category', '?')}]")
                if d.get("learning_feedback"):
                    lines.append(f"  Feedback: {d['learning_feedback']}")
                elif d.get("decision_summary"):
                    lines.append(f"  Reason: {d['decision_summary']}")
                elif d.get("rationale"):
                    lines.append(f"  Reason: {d['rationale']}")
            lines.append("")

        if deferred:
            lines.append(f"### ⏸️ Deferred ({len(deferred)})")
            for d in deferred:
                lines.append(f"- **{d['title']}** [{d.get('category', '?')}]")
                if d.get("learning_feedback"):
                    lines.append(f"  Feedback: {d['learning_feedback']}")
            lines.append("")

        if rejected:
            lines.extend([
                "### 🎯 Patterns to Avoid",
                "Based on your rejections, do NOT propose initiatives that:",
                "- Repeat or closely resemble rejected ideas above",
                "- Are vague, speculative, or lack concrete first steps",
                "- Propose work that's already covered by existing tasks or infrastructure",
                "- Are \"nice to have\" without clear immediate value",
                "",
            ])

        if approved:
            lines.extend([
                "### 🎯 What Gets Approved",
                "Based on your approvals, focus on initiatives that:",
                "- Address real, observed problems (not hypothetical ones)",
                "- Have clear, immediate value with concrete deliverables",
                "- Are well-scoped (1-3 days) with specific files/modules/endpoints named",
                "",
            ])

        return "\n".join(lines)

    # Per-agent reflection focus areas — what each agent should look for
    _AGENT_REFLECTION_FOCUS: dict[str, str] = {
        "programmer": """## Your Domain: Code & Implementation — But Think Like a Product Engineer

You're not just a code monkey fixing bugs. You're a senior product engineer who happens to have deep access to the codebase. You see what's possible because you know what's buildable.

**STOP thinking about:**
- Lint rules, test coverage, code quality metrics
- Refactors for refactoring's sake
- Infrastructure that nobody asked for

**START thinking about:**

### New Features & Capabilities
- What new features would make Lobs dramatically more useful as a personal AI assistant?
- What integrations would multiply Lobs's value? (calendar automation, email drafting, smart notifications, proactive suggestions)
- What could Lobs do that no other AI assistant does? What's our unique advantage with a multi-agent system?
- What would make Rafe say "holy shit, Lobs just did that for me automatically"?

### System-Level Improvements
- What's preventing the agent system from being 10x more effective?
- What new capabilities would unlock entire categories of tasks agents can't do today?
- Where are agents failing repeatedly, and what NEW approach could fix it?

### Business & Product Ideas
- We're building toward PAW (Personal AI Workforce) — what features would make this a product people pay for?
- What's the killer demo that would convince someone to try this system?
- What workflow automation would save a busy professional hours per week?

### Cross-Project Ideas
- Could Lobs manage your calendar intelligently? Your email? Your todo list across apps?
- Could we build agents that monitor and respond to things proactively?
- What about Flock (social event planning app) — could agents help build features there?

**Examples of what we WANT:**
- "Build a daily briefing system that summarizes overnight activity, upcoming tasks, and proactive suggestions"
- "Implement smart task decomposition — when given a vague goal, auto-break it into concrete subtasks"
- "Add email integration so Lobs can draft replies and manage inbox"
- "Build a 'Lobs, handle this' feature where you forward any message and Lobs figures out what to do"
- "Create a learning system that tracks what works and automatically improves agent prompts"

**Examples of what we DON'T want:**
- "Add lint rule for X" — boring
- "Improve test coverage for Y" — unless tests are actually failing
- "Refactor Z module" — unless it's actively blocking something""",

        "architect": """## Your Domain: System Design & Strategic Vision

You see the entire landscape. You don't just maintain architecture — you envision what the system SHOULD become.

**STOP thinking about:**
- Module boundaries and code organization
- Database schema tweaks
- Documentation of existing systems

**START thinking about:**

### System Evolution
- What's the next major capability the Lobs platform needs?
- How should the multi-agent system evolve? What coordination patterns are we missing?
- What would make this system 10x more powerful?
- Where's the architectural ceiling that will limit us in 6 months?

### Platform & Product Architecture
- We want to productize this as PAW (Personal AI Workforce). What architectural changes are needed?
- How do we go from "one user's personal system" to "a platform that serves many users"?
- What's the right architecture for a multi-tenant AI agent platform?
- How should we handle user data isolation, agent customization per user, billing?

### New System Designs
- Design a proactive intelligence system — agents that notice things and act without being asked
- Design a learning/feedback loop where agent performance improves over time
- Design cross-platform integration architecture (email, calendar, messaging, code repos)
- Design a plugin/skill system that lets users extend what their agents can do

### Technical Strategy
- What emerging AI capabilities (tool use, computer use, multi-modal) should we architect for?
- How should we think about local vs cloud model execution?
- What's our strategy for handling multiple AI providers efficiently?

**Examples of what we WANT:**
- "Design a proactive notification system where agents can surface insights without being asked"
- "Architecture for multi-user PAW — tenant isolation, per-user agent configuration, shared vs private memory"
- "Design a plugin system for third-party integrations (Slack, email, calendar, GitHub)"
- "Architecture for agent learning — tracking what works, A/B testing prompts, automatic improvement"

**Examples of what we DON'T want:**
- "Split module X into Y and Z" — boring reorganization
- "Add connection pooling" — incremental infrastructure
- "Document component interactions" — maintenance work""",

        "researcher": """## Your Domain: Investigation, Strategy & Opportunity Discovery

You're not just answering technical questions. You're the team's scout — finding opportunities, analyzing markets, and identifying what we should build next.

**STOP thinking about:**
- SQLite performance benchmarks
- Library comparisons for existing features
- Security audits of current code

**START thinking about:**

### Market & Competitive Intelligence
- What are other AI agent platforms doing? (Devin, Cursor, Replit Agent, AutoGPT, CrewAI)
- What features do they have that we don't? What do we have that they don't?
- Where is the AI agent market heading in the next 12 months?
- What's the gap in the market that PAW could fill?

### New Capability Research
- What novel uses of multi-agent systems exist in research/industry?
- What new AI model capabilities (vision, audio, code execution) could we leverage?
- What human workflows could be automated that nobody's tried yet?
- What would it take to make an AI agent truly proactive (not just reactive)?

### Business & Product Research
- Who would pay for a personal AI workforce? What do they need?
- What's the right pricing model for an AI agent platform?
- What are the legal/privacy considerations for a personal AI assistant product?
- Research successful developer tools and what made them succeed

### Technology Scouting
- What new APIs, services, or platforms could Lobs integrate with?
- What emerging tech could give us a unique advantage?
- What are the best practices for AI agent memory and learning?

**Examples of what we WANT:**
- "Research how Devin/Cursor/Replit handle multi-step task execution — what can we learn?"
- "Investigate voice interface feasibility — could Lobs respond via voice on mobile?"
- "Research proactive AI assistant patterns — what triggers should cause agents to act without being asked?"
- "Market analysis: who's building personal AI agents and what's their pricing/positioning?"
- "Research: what would it take to give Lobs access to a user's email/calendar securely?"

**Examples of what we DON'T want:**
- "Benchmark SQLite vs PostgreSQL" — we'll know when it's time
- "Survey testing frameworks" — not strategic
- "Research security best practices" — too generic""",

        "reviewer": """## Your Domain: Quality, Reliability & User Experience

You're not just catching bugs. You're the voice of quality AND the voice of the user. You see what breaks, what's confusing, and what could be better.

**STOP thinking about:**
- Code style and formatting issues
- Missing test coverage metrics
- Generic error handling improvements

**START thinking about:**

### User Experience & Reliability
- Where does the system feel broken or unreliable from the user's perspective?
- What fails silently that should be loud?
- What's the most frustrating thing about using Lobs right now?
- What would make the system feel polished and trustworthy?

### Agent Quality & Effectiveness
- Why do agents fail 36% of the time? What are the actual root causes?
- What would make agent output significantly higher quality?
- How should we measure agent effectiveness? What metrics matter?
- What feedback mechanisms are missing?

### New Quality Systems
- Could we build an automated way to verify agent work quality before marking tasks done?
- Could we implement agent self-review — agents checking their own work?
- What about A/B testing different prompts or approaches?
- How could we build a quality dashboard that shows system health at a glance?

### Product Quality Ideas
- What would make first-time setup of a PAW instance smooth and delightful?
- What error messages or help systems would improve user experience?
- What monitoring/alerting would help users trust the system?

**Examples of what we WANT:**
- "Build an agent output quality checker that verifies code compiles, tests pass, and docs render before marking tasks done"
- "Create a task outcome analyzer that identifies WHY agents fail and auto-improves prompts"
- "Design a health dashboard showing agent success rates, task throughput, and system reliability"
- "Implement automatic rollback when agent changes break tests"

**Examples of what we DON'T want:**
- "Add lint rule for X pattern" — too small
- "Review error handling in module Y" — too narrow
- "Improve test assertions" — not impactful""",

        "writer": """## Your Domain: Communication, Knowledge & User-Facing Content

You're not just writing docs for existing features. You're thinking about how the system communicates, teaches, and presents itself.

**STOP thinking about:**
- Updating stale README files
- Writing API documentation
- Runbooks for internal operations

**START thinking about:**

### Product Communication
- If PAW launched tomorrow, what would the landing page say?
- What's the elevator pitch for a personal AI workforce?
- What case studies or demos would be most compelling?
- How should Lobs communicate with users? What's the right tone, frequency, format?

### Knowledge Systems
- How could agents share knowledge more effectively across tasks?
- What would a great "learning from experience" system look like?
- How should the shared memory system evolve?
- Could we build a system where agents write their own documentation?

### User-Facing Content
- What onboarding experience would make new PAW users successful?
- What templates, guides, or wizards would help users customize their agents?
- How should agents report their work to users? Daily summaries? Real-time updates?
- What would a great mobile notification look like when an agent completes work?

### Content Strategy
- What blog posts or articles would attract users to PAW?
- What documentation would make the open-source community want to contribute?
- How should we document the "build your own agent team" experience?

**Examples of what we WANT:**
- "Design an agent daily briefing format — a morning summary of what happened overnight and what's planned today"
- "Write the PAW product pitch deck — positioning, value props, target users"
- "Create a 'build your own agent' tutorial that walks through customizing agent behavior"
- "Design notification templates for different event types (task done, blocked, needs attention)"

**Examples of what we DON'T want:**
- "Update CHANGELOG" — maintenance
- "Document API endpoint X" — not strategic
- "Write runbook for deployment" — operational noise""",
    }

    @classmethod
    def _build_reflection_prompt(cls, agent: str, packet: dict[str, Any], reflection_id: str) -> str:
        agent_focus = cls._AGENT_REFLECTION_FOCUS.get(agent, "")
        decision_history = cls._format_decision_history(packet)

        return f"""## Strategic Reflection Mode (6-hour cycle)

You are agent: {agent}
Reflection record ID: {reflection_id}

Context packet JSON:
{packet}

{decision_history}

{agent_focus}

## CRITICAL: This is a REFLECTION session, not a work session

**STOP. Read this carefully before doing anything.**

Your normal role ({agent}) is temporarily suspended. You are NOT here to write code, create files, run tests, fix bugs, write docs, or do research. You are here to THINK and PROPOSE.

**DO NOT use any tools.** No exec, no read, no write, no edit, no browser, no web_search. Do not call ANY tools. Your entire output must be a single JSON response based on what you already know from the context packet above and your own experience/memory.

If you use tools or try to execute work, this reflection has failed.

## Instructions

Think from YOUR perspective as {agent}. Don't just report system-wide observations that any agent could make — dig into YOUR domain and find specific, actionable work.

For each proposed initiative:
- Be specific: name files, modules, endpoints, or systems
- Explain WHY it matters, not just WHAT to do
- Include concrete first steps, not just goals
- You can recommend work for other agents, but frame it from your expertise

## 🎯 THINK BIG, PROPOSE BOLD — We're Building Something Ambitious

**CONTEXT:** We're building one of the best AI agent setups in the world. This isn't a maintenance project — it's an ambitious platform that will become PAW (Personal AI Workforce), a product people pay for. Think accordingly.

**QUALITY > QUANTITY.** Propose 1-3 genuinely impactful ideas. An empty list is better than noise.

**What we WANT to see:**
1. **New features and capabilities** — things that make Lobs dramatically more useful
2. **Product ideas** — features that would make PAW compelling as a product
3. **System-level improvements** — changes that make the entire agent team more effective
4. **Business ideas** — new ways to use this technology, new markets, new applications
5. **Cross-project opportunities** — connections between projects that create new value
6. **Novel uses of agents** — things nobody else is doing with multi-agent systems

**What we DON'T want:**
- Small maintenance tasks (lint rules, test coverage, refactors)
- Generic infrastructure improvements ("improve logging", "add monitoring")
- Documentation updates for existing features
- Re-proposing previously rejected ideas
- Anything that feels like busywork

**The bar for proposals:**
- **Would a busy founder care about this?** If not, it's too small.
- **Does this move us toward a product people would pay for?** 
- **Is this something that would make someone say "wow, that's cool"?**
- **Does the decision history show this was already rejected?** — don't re-propose.

**Scope guidance:**
- Features and products CAN be larger scope (1-2 weeks is fine for important features)
- Break large visions into a concrete first milestone that delivers standalone value
- It's OK to propose big ideas with a "start with X" first step

**Categories:**
- `feature_proposal` — NEW user-facing capability or integration
- `new_project` — NEW standalone system, tool, or application
- `architecture_change` — Strategic system evolution (not just reorganization)
- `light_research` — Market research, competitive analysis, technology scouting
- Maintenance categories (test_hygiene, docs_sync, etc.) — ONLY for things that are actively broken

## Output Format

Respond with ONLY the JSON below. No preamble, no "I'll analyze...", no explanation before or after. Just the JSON.

```json
{{
  "inefficiencies_detected": ["specific issues you've noticed in YOUR domain"],
  "missed_opportunities": ["concrete work that should be done, with specifics"],
  "system_risks": ["risks you see from YOUR vantage point"],
  "proposed_initiatives": [
    {{
      "title": "Short, actionable title",
      "description": "What to do, why it matters, and concrete first steps",
      "category": "docs_sync|test_hygiene|stale_triage|light_research|backlog_reprioritization|automation_proposal|moderate_refactor|architecture_change|destructive_operation|cross_project_migration|agent_recruitment|feature_proposal|new_project",
      "estimated_effort": 1,
      "suggested_owner_agent": "agent-type"
    }}
  ],
  "identity_adjustments": ["changes to your own behavior based on recent experience"],
  "experience_notes": ["raw observations and lessons from this reflection window"]
}}
```
"""

    @staticmethod
    def _compress_reflections(agent: str, reflections: list[AgentReflection]) -> dict[str, Any]:
        inefficiencies: list[str] = []
        system_risks: list[str] = []
        missed: list[str] = []
        adjustments: list[str] = []

        for reflection in reflections:
            inefficiencies.extend(reflection.inefficiencies or [])
            system_risks.extend(reflection.system_risks or [])
            missed.extend(reflection.missed_opportunities or [])
            adjustments.extend(reflection.identity_adjustments or [])

        changed_heuristics = sorted({*adjustments, *missed})
        removed_rules = sorted(set(inefficiencies[:3]))

        success_count = sum(1 for r in reflections if r.status == "completed")
        failure_count = sum(1 for r in reflections if r.status == "failed")

        lines = [
            f"# Identity Snapshot: {agent}",
            "",
            f"Generated from {len(reflections)} reflections in prior 24h.",
            "",
            "## Performance patterns",
            f"- Success reflections: {success_count}",
            f"- Failure reflections: {failure_count}",
            "",
            "## Risk patterns",
        ]

        if system_risks:
            lines.extend([f"- {item}" for item in sorted(set(system_risks))[:5]])
        else:
            lines.append("- No major system risks surfaced in this window.")

        lines.extend([
            "",
            "## Changed heuristics",
        ])
        if changed_heuristics:
            lines.extend([f"- {item}" for item in changed_heuristics[:8]])
        else:
            lines.append("- No heuristic updates in this window.")

        lines.extend([
            "",
            "## Removed rules",
        ])
        if removed_rules:
            lines.extend([f"- {item}" for item in removed_rules])
        else:
            lines.append("- No rules removed in this window.")

        lines.extend([
            "",
            "## Behavioral directives",
            "- Prefer deterministic checks before broad refactors.",
            "- Raise cross-agent conflicts early.",
            "- Keep changes scoped and reversible.",
        ])

        return {
            "identity_text": "\n".join(lines),
            "changed_heuristics": changed_heuristics,
            "removed_rules": removed_rules,
        }

    @staticmethod
    def _run_lobs_validation_gate(
        identity_text: str,
        changed_heuristics: list[str],
        removed_rules: list[str],
        reflections_with_data: int = 0,
    ) -> tuple[bool, str | None]:
        if not identity_text.strip():
            return False, "identity artifact is empty"
        if "## Behavioral directives" not in identity_text:
            return False, "missing behavioral directives section"
        # Pass if EITHER changed_heuristics OR removed_rules has content,
        # OR if there are completed reflections with result data (relaxed validation)
        if len(changed_heuristics) == 0 and len(removed_rules) == 0 and reflections_with_data == 0:
            return False, "no meaningful identity deltas detected"
        return True, None
