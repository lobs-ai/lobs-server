"""Sync agent identity capabilities into DB registry."""

from __future__ import annotations

import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentCapability
from app.orchestrator.config import CONTROL_PLANE_AGENTS
from app.orchestrator.registry import AgentRegistry


class CapabilityRegistrySync:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.registry = AgentRegistry()

    async def sync(self) -> dict[str, int]:
        added = 0
        updated = 0
        agents = self.registry.available_types()

        for agent in agents:
            if agent in CONTROL_PLANE_AGENTS:
                continue

            config = self.registry.get_agent(agent)
            for capability in config.capabilities:
                existing_q = await self.db.execute(
                    select(AgentCapability).where(
                        AgentCapability.agent_type == agent,
                        AgentCapability.capability == capability,
                    )
                )
                existing = existing_q.scalar_one_or_none()
                if existing is None:
                    self.db.add(
                        AgentCapability(
                            agent_type=agent,
                            capability=capability,
                            confidence=0.7,
                            source="identity",
                        )
                    )
                    added += 1
                else:
                    existing.confidence = existing.confidence or 0.7
                    updated += 1

        # Commit with retry-on-lock logic (exponential backoff)
        for _attempt in range(5):
            try:
                await self.db.commit()
                break
            except Exception as _e:
                if _attempt < 4:
                    await asyncio.sleep(_attempt * 0.5)
                    await self.db.rollback()
                else:
                    # Log error and re-raise if all retries exhausted
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error("[CAPABILITY_REGISTRY] Failed to sync after 5 attempts: %s", _e)
                    try:
                        await self.db.rollback()
                    except Exception:
                        pass
                    raise
        
        return {"added": added, "updated": updated}
