"""Capability-based task routing with regex fallback."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentCapability
from app.orchestrator.router import Router


class CapabilityRouter:
    """Selects best-fit agent from capability registry before regex fallback."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.fallback_router = Router()

    async def route(self, task: dict[str, Any]) -> str:
        explicit = (task.get("agent") or "").strip().lower()
        if explicit:
            return explicit

        text = f"{task.get('title', '')}\n{task.get('notes', '')}".lower()
        scores = await self._score_agents(text)

        if scores:
            best_agent, _score = max(scores.items(), key=lambda item: item[1])
            return best_agent

        return self.fallback_router.route(task)

    async def _score_agents(self, text: str) -> dict[str, float]:
        result = await self.db.execute(select(AgentCapability))
        rows = result.scalars().all()
        if not rows:
            return {}

        scores: dict[str, float] = defaultdict(float)
        for row in rows:
            capability = (row.capability or "").strip().lower()
            if not capability:
                continue

            # token-based partial match against task text
            matched_tokens = [tok for tok in capability.split() if tok in text]
            if not matched_tokens:
                continue

            overlap_ratio = len(matched_tokens) / max(len(capability.split()), 1)
            scores[row.agent_type] += overlap_ratio * float(row.confidence or 0.5)

        return scores
