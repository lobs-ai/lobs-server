"""Strategic reflection and daily identity compression routines."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentReflection, AgentIdentityVersion, SystemSweep
from app.orchestrator.context_packets import ContextPacketBuilder
from app.orchestrator.registry import AgentRegistry
from app.orchestrator.worker import WorkerManager

logger = logging.getLogger(__name__)


class ReflectionCycleManager:
    """Runs strategic reflection jobs and daily compression sweeps."""

    def __init__(self, db: AsyncSession, worker_manager: WorkerManager):
        self.db = db
        self.worker_manager = worker_manager
        self.registry = AgentRegistry()

    async def run_strategic_reflection_cycle(self) -> dict[str, Any]:
        agents = [a for a in self.registry.available_types() if a != "project-manager"]
        if not agents:
            return {"agents": 0, "spawned": 0}

        packet_builder = ContextPacketBuilder(self.db)
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
            result, error = await self.worker_manager._spawn_session(
                task_prompt=prompt,
                agent_id=agent,
                model="anthropic/claude-haiku-4-5",
                label=f"reflection-{agent}",
            )

            if result:
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
        agents = [a for a in self.registry.available_types() if a != "project-manager"]
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=24)

        rewritten = 0
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

            compressed_text = self._compress_reflections(agent, reflections)

            self.db.add(
                AgentIdentityVersion(
                    id=str(uuid.uuid4()),
                    agent_type=agent,
                    version=max_version + 1,
                    identity_text=compressed_text,
                    summary=f"Auto-compressed from {len(reflections)} reflections",
                    active=True,
                    window_start=since,
                    window_end=now,
                )
            )

            rewritten += 1

        sweep = SystemSweep(
            id=str(uuid.uuid4()),
            sweep_type="daily_cleanup",
            status="completed",
            window_start=since,
            window_end=now,
            summary={"agents": len(agents), "rewritten": rewritten},
            decisions={"identity_rewrite": "created new version rows"},
            completed_at=now,
        )
        self.db.add(sweep)
        await self.db.commit()

        return {"agents": len(agents), "rewritten": rewritten, "sweep_id": sweep.id}

    @staticmethod
    def _build_reflection_prompt(agent: str, packet: dict[str, Any], reflection_id: str) -> str:
        return f"""## Strategic Reflection Mode (6-hour cycle)

You are agent: {agent}
Reflection record ID: {reflection_id}

Context packet JSON:
{packet}

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
      "estimated_effort": 1
    }}
  ],
  "identity_adjustments": ["..."]
}}
"""

    @staticmethod
    def _compress_reflections(agent: str, reflections: list[AgentReflection]) -> str:
        lines = [
            f"# Identity Snapshot: {agent}",
            "",
            f"Generated from {len(reflections)} recent reflections.",
            "",
            "## Stable strengths",
            "- Executes scoped work quickly when context is bounded.",
            "",
            "## Risk patterns",
            "- Watch for repeated failures and stale backlogs.",
            "",
            "## Behavioral directives",
            "- Prefer deterministic checks before proposing broad refactors.",
            "- Raise cross-agent conflicts early.",
        ]
        return "\n".join(lines)
