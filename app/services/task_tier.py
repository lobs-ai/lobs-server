"""Task tier classification service.

Classifies a task into one of: small | standard | strong
using a local LM Studio endpoint (http://localhost:1234).

Called at task creation time so model_tier is populated before the
orchestrator picks up the task.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.models import Task as TaskModel

logger = logging.getLogger(__name__)

_LMSTUDIO_URL = os.environ.get("LMSTUDIO_HOST", "http://localhost:1234")
_CLASSIFY_TIMEOUT = 2.0  # seconds

VALID_TIERS = ("small", "standard", "strong")

# Projects that require at least 'standard' tier
_PROJECT_HARD_MINIMUMS: dict[str, str] = {
    "lobs-server": "standard",
    "lobs-mission-control": "standard",
    "lobs-mobile": "standard",
}

_TIER_ORDER = ["small", "standard", "strong"]

_CLASSIFY_PROMPT = """\
Classify this software task into one tier:
- small: boilerplate, docs, config, experiments, scripts, simple bug fixes
- standard: production features, multi-file changes, API endpoints, refactors, research
- strong: architecture, security, complex debugging, system design, DB migrations

Task title: {title}
Project: {project_id}
Agent: {agent}
Notes (first 500 chars): {notes}

Reply with exactly one word: small, standard, or strong."""


def _apply_hard_minimum(tier: str, project_id: str | None) -> str:
    """Apply project hard minimum after LLM classification."""
    if not project_id:
        return tier
    for key, min_tier in _PROJECT_HARD_MINIMUMS.items():
        if key in project_id:
            if _TIER_ORDER.index(min_tier) > _TIER_ORDER.index(tier):
                return min_tier
    return tier


async def classify_task_tier(task: "TaskModel", db: AsyncSession) -> str:
    """Classify a task's model tier.

    Priority:
    1. If task.model_tier is already set -> return it (caller override)
    2. Try LM Studio classification (2s timeout)
    3. Default to 'standard' on any failure
    4. Apply project hard minimums after all of the above
    """
    # 1. Caller override
    if task.model_tier and task.model_tier in VALID_TIERS:
        return task.model_tier

    tier = "standard"

    try:
        notes = str(task.notes or "")[:500]
        prompt = _CLASSIFY_PROMPT.format(
            title=task.title or "",
            project_id=task.project_id or "unknown",
            agent=task.agent or "unknown",
            notes=notes,
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_LMSTUDIO_URL}/v1/chat/completions",
                json={
                    "model": "local",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 10,
                    "temperature": 0,
                },
                timeout=aiohttp.ClientTimeout(total=_CLASSIFY_TIMEOUT),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = (
                        data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                        .strip()
                        .lower()
                    )
                    # Extract first valid tier word
                    for word in content.split():
                        word = word.strip(".,;:!?\"'")
                        if word in VALID_TIERS:
                            tier = word
                            break
                    logger.info(
                        "[TASK_TIER] LM Studio classified task=%s tier=%s (raw=%r)",
                        task.id,
                        tier,
                        content,
                    )
                else:
                    logger.debug(
                        "[TASK_TIER] LM Studio returned status=%s, defaulting to standard",
                        resp.status,
                    )
    except Exception as exc:
        logger.debug("[TASK_TIER] LM Studio unreachable or timeout (%s), defaulting to standard", exc)
        tier = "standard"

    # Apply project hard minimums last
    final_tier = _apply_hard_minimum(tier, task.project_id)
    if final_tier != tier:
        logger.info(
            "[TASK_TIER] Project hard minimum applied: %s -> %s (project=%s)",
            tier,
            final_tier,
            task.project_id,
        )
    return final_tier
