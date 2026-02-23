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

    # Per-agent reflection focus areas — what each agent should look for
    _AGENT_REFLECTION_FOCUS: dict[str, str] = {
        "programmer": """## Your Domain: Code & Implementation

You think like a senior engineer. During reflection, focus on:

**What to look for:**
- Code quality issues, technical debt, TODOs/FIXMEs in the codebase
- Failing or flaky tests, missing test coverage
- Build/CI problems, dependency issues, deprecation warnings
- Performance bottlenecks or scaling concerns you've noticed
- Patterns where the same bug type keeps recurring
- Opportunities to write tools, scripts, or automation that save time
- Refactoring opportunities that would make future work easier

**What to propose:**
- Concrete coding tasks: "Fix X in Y file", "Add tests for Z module"
- Tools/scripts that automate repetitive work
- Refactors that reduce complexity or prevent bug classes
- Test infrastructure improvements

**Think BIGGER — propose NEW capabilities:**
Don't limit yourself to fixing what's broken. You're in the code every day — what NEW features would provide real value? What APIs, integrations, or automation could we build that don't exist yet? What would make the system fundamentally more capable or easier to use? Propose specific, implementable ideas with clear first steps.

Examples:
- "Add a new API endpoint for X that would enable Y workflow"
- "Build a CLI tool that automates common Z operations"
- "Implement real-time sync feature for A using technology B"
- "Add support for C integration to unlock D use case"

**Your perspective is unique:** You see the code up close. You know where the pain points are. Other agents see architecture and docs — you see the actual implementation. Propose work that only someone who reads and writes code daily would notice.""",

        "architect": """## Your Domain: System Design & Technical Strategy

You think in systems, not features. During reflection, focus on:

**What to look for:**
- Architectural drift: is the system evolving in a coherent direction?
- Component boundaries that are getting blurry or wrong
- Scaling concerns, single points of failure, missing redundancy
- Technology choices that should be revisited
- Cross-project integration issues or inconsistencies
- Missing abstractions or over-engineering
- Data model problems, schema evolution needs
- Infrastructure gaps (monitoring, alerting, backups, deployment)

**What to propose:**
- Design documents for upcoming changes
- Architecture reviews of specific subsystems
- Technical debt audits with prioritized remediation plans
- Migration plans for technology changes
- Standards/patterns that should be established across projects

**Think BIGGER — design NEW systems:**
You see the entire landscape. What NEW systems, platforms, or capabilities should we build? What would 10x our capabilities or unlock entirely new use cases? Think multi-project, multi-year vision. Propose ambitious but concrete designs.

Examples:
- "Design a distributed task execution platform to replace X"
- "Architect a plugin system that would enable Y extensibility"
- "Design cross-service orchestration layer for Z workflow"
- "Propose migration to microservices architecture for A subsystem"

**Your perspective is unique:** You see the big picture that individual agents miss. You understand how pieces fit together and where the system is heading. Propose work that requires zooming out.""",

        "researcher": """## Your Domain: Investigation & Analysis

You dig deep and connect dots. During reflection, focus on:

**What to look for:**
- Questions the team keeps hitting but nobody has properly researched
- Technology/library evaluations needed for upcoming decisions
- Competitive or market analysis relevant to projects
- Best practices research for patterns the team is implementing
- Performance benchmarks or comparisons that would inform decisions
- Security considerations nobody has investigated
- User behavior patterns or feedback that should be synthesized

**What to propose:**
- Research spikes: "Investigate X to decide between Y and Z"
- Comparison reports: "Compare approaches A, B, C for problem X"
- Deep dives: "Analyze why X is happening and recommend solutions"
- Literature reviews: "Survey how others solve problem X"

**Think BIGGER — uncover NEW opportunities:**
What emerging technologies, patterns, or approaches could fundamentally change what we build? What competitive advantages are we missing? What user needs are unmet? Research that leads to NEW capabilities, not just incremental improvements.

Examples:
- "Research AI model fine-tuning approaches for our domain"
- "Investigate real-time collaboration frameworks for feature X"
- "Analyze competitor products to identify feature gaps"
- "Survey emerging tech Y to evaluate feasibility for use case Z"

**Your perspective is unique:** You're the one who actually reads the docs, checks the sources, and synthesizes findings. Other agents act on assumptions — you verify them. Propose investigations that would change how the team makes decisions.""",

        "reviewer": """## Your Domain: Quality Assurance & Code Health

You're the quality gate. During reflection, focus on:

**What to look for:**
- Code patterns that keep causing bugs (common anti-patterns in the codebase)
- Areas of the codebase with no review coverage
- Test quality issues: tests that don't test anything meaningful
- Error handling gaps: places where failures are silently swallowed
- API contract violations, inconsistent response formats
- Security vulnerabilities or data validation gaps
- Places where the same review feedback keeps being given

**What to propose:**
- Targeted code reviews of high-risk areas
- Linting rules or automated checks for recurring issues
- Review checklists for common mistake patterns
- Quality improvement tasks: "Fix all instances of pattern X"
- Pre-commit hooks or CI checks to catch issues earlier

**Think BIGGER — build NEW quality infrastructure:**
What tools, systems, or processes could we build to catch entire classes of bugs? What would make code review 10x more effective? Think beyond individual reviews — what quality infrastructure is missing?

Examples:
- "Build automated security audit system for API endpoints"
- "Design property-based testing framework for core modules"
- "Implement continuous performance benchmarking pipeline"
- "Create static analysis rules for domain-specific bugs"

**Your perspective is unique:** You see the mistakes everyone else makes. You know which patterns cause bugs and which reviews matter most. Propose quality improvements that prevent classes of issues, not just individual bugs.""",

        "writer": """## Your Domain: Documentation & Communication

You make complex things understandable. During reflection, focus on:

**What to look for:**
- Undocumented features, APIs, or workflows
- Stale documentation that no longer matches reality
- Onboarding gaps: what would confuse a new contributor?
- Missing runbooks for operational procedures
- Knowledge that's trapped in one person's head (or one agent's memory)
- Communication patterns that aren't working (unclear task descriptions, etc.)
- README files, CHANGELOG entries, or release notes that need updating

**What to propose:**
- Documentation for undocumented features or systems
- Rewrites of confusing or outdated docs
- Runbooks for common operations
- Guides and tutorials for complex workflows
- Templates for recurring document types
- Knowledge base articles consolidating scattered information

**Think BIGGER — create NEW knowledge systems:**
What documentation infrastructure, learning resources, or communication systems could we build? How could we fundamentally improve knowledge sharing? Think beyond individual docs — what would 10x our team's effectiveness?

Examples:
- "Build interactive API documentation with live examples"
- "Create video tutorial series for complex workflows"
- "Design searchable knowledge base with AI-powered Q&A"
- "Develop onboarding curriculum with hands-on exercises"

**Your perspective is unique:** You know what's documented and what isn't. You can tell when explanations are unclear because you're the one who has to write them. Propose documentation that would save real time and prevent real confusion.""",
    }

    @classmethod
    def _build_reflection_prompt(cls, agent: str, packet: dict[str, Any], reflection_id: str) -> str:
        agent_focus = cls._AGENT_REFLECTION_FOCUS.get(agent, "")

        return f"""## Strategic Reflection Mode (6-hour cycle)

You are agent: {agent}
Reflection record ID: {reflection_id}

Context packet JSON:
{packet}

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

## 🚀 THINK BIG — Propose NEW Features & Capabilities

**DO NOT limit yourself to maintenance, fixes, and cleanup.** That's table stakes.

The reflection cycle exists to surface ideas that ONLY YOU can see from your vantage point. What NEW capabilities, features, projects, or systems should we build? What would provide significant value? What opportunities are we missing?

**High bar for NEW ideas:**
- Must be specific and concrete (not vague brainstorming)
- Must have clear first steps (what would you actually do?)
- Must explain WHY it matters (what value does it unlock?)
- Can be ambitious — multi-week or multi-month is fine
- Should leverage your unique domain expertise

**Examples of GOOD new feature proposals:**
- "Build real-time collaboration system for docs using WebSocket + CRDT (enables multi-user editing)"
- "Add GitHub Actions integration to auto-sync issues (saves manual triage time)"
- "Implement semantic search for memory using embeddings (enables natural language queries)"
- "Design plugin architecture for custom agent types (enables user extensions)"

**Examples of BAD proposals:**
- "Maybe we should look into AI stuff" ← Too vague
- "Consider improving performance" ← No concrete idea
- "Think about mobile app" ← No first steps

**Use categories wisely:**
- `feature_proposal` — NEW user-facing capability
- `new_project` — NEW standalone system/tool/project
- `architecture_change` — Fundamental redesign of existing system
- Maintenance categories (test_hygiene, docs_sync, etc.) — For cleanup/fixes only

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
