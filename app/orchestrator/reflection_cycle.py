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

            validation_ok, validation_reason = self._run_lobs_validation_gate(
                identity_text=compressed["identity_text"],
                changed_heuristics=changed_heuristics,
                removed_rules=removed_rules,
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
    def _build_reflection_prompt(agent: str, packet: dict[str, Any], reflection_id: str) -> str:
        return f"""## Strategic Reflection Mode (6-hour cycle)

You are agent: {agent}
Reflection record ID: {reflection_id}

Context packet JSON:
{packet}

## Instructions

Analyze your recent work, the system state, and your own performance. Think about:
1. What inefficiencies exist in your workflow or the broader system?
2. What opportunities are being missed?
3. What risks should the team be aware of?
4. What proactive work would you like to do? (documentation, research, improvements, etc.)
5. What about your own behavior should change based on recent experience?

## Memory Updates

Include any updates to your experience memory (observations, lessons learned, patterns noticed).
These will be stored in your memory/ directory for future reference.

## Governance Constraints (mandatory)

- You are in PROPOSAL mode only.
- Do NOT execute work, run migrations, edit code, or create tasks/issues/inbox items.
- All proposals go to Lobs-PM for review. Nothing is auto-approved.
- If a proposal requires action, put it only in `proposed_initiatives`.

## Output Format

Return STRICT JSON with this schema (no prose outside JSON):
{{
  "inefficiencies_detected": ["..."],
  "missed_opportunities": ["..."],
  "system_risks": ["..."],
  "proposed_initiatives": [
    {{
      "title": "...",
      "description": "...",
      "category": "docs_sync|test_hygiene|stale_triage|light_research|backlog_reprioritization|automation_proposal|moderate_refactor|architecture_change|destructive_operation|cross_project_migration|agent_recruitment",
      "estimated_effort": 1,
      "suggested_owner_agent": "optional-agent-type"
    }}
  ],
  "identity_adjustments": ["..."],
  "experience_notes": ["raw observations and lessons from this reflection window"]
}}
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
    ) -> tuple[bool, str | None]:
        if not identity_text.strip():
            return False, "identity artifact is empty"
        if "## Behavioral directives" not in identity_text:
            return False, "missing behavioral directives section"
        if len(changed_heuristics) == 0 and len(removed_rules) == 0:
            return False, "no meaningful identity deltas detected"
        return True, None
