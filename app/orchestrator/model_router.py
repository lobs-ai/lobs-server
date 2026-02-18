"""Model routing policy engine (first pass).

Goals:
- Classify tasks by complexity + criticality using lightweight heuristics
- Route to default models by agent type
- Provide a fallback chain for provider/model failures
- Emit audit metadata so model choices are inspectable

This module is intentionally small and reversible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Complexity = Literal["light", "standard", "very_complex"]
Criticality = Literal["low", "normal", "high"]


# Canonical model IDs used by OpenClaw Gateway in this repo.
MODEL_HAIKU = "anthropic/claude-haiku-4-5"
MODEL_GEMINI_FLASH = "google-gemini-cli/gemini-3-pro-preview"
MODEL_SONNET = "anthropic/claude-sonnet-4-5"
MODEL_OPUS = "anthropic/claude-opus-4-6"


@dataclass(frozen=True, slots=True)
class ModelDecision:
    """Result of policy evaluation."""

    complexity: Complexity
    criticality: Criticality
    models: list[str]  # ordered preference list, first is primary
    policy: str  # short string identifier
    audit: dict[str, Any]


_VERY_COMPLEX_KEYWORDS = (
    "orchestrator",
    "policy engine",
    "model router",
    "migration",
    "refactor",
    "end-to-end",
    "distributed",
    "database",
    "schema",
    "performance",
    "security",
    "auth",
)

_HIGH_CRITICALITY_KEYWORDS = (
    "incident",
    "outage",
    "downtime",
    "urgent",
    "security",
    "vulnerability",
    "data loss",
    "prod",
    "production",
    "auth",
    "payment",
)

_LOW_CRITICALITY_KEYWORDS = (
    "typo",
    "copy edit",
    "quick reply",
    "small cleanup",
)


def _task_text(task: dict[str, Any]) -> str:
    title = (task.get("title") or "").strip()
    notes = (task.get("notes") or "").strip()
    return f"{title}\n{notes}".strip().lower()


def classify_task(task: dict[str, Any], *, agent_type: str) -> tuple[Complexity, Criticality]:
    """Heuristic classifier.

    This deliberately avoids calling LLMs; it should be deterministic.
    """

    text = _task_text(task)

    # Complexity
    word_count = len(text.split())
    very_complex = any(k in text for k in _VERY_COMPLEX_KEYWORDS) or word_count >= 220
    complexity: Complexity = "very_complex" if very_complex else ("light" if word_count <= 30 else "standard")

    # Criticality
    if any(k in text for k in _HIGH_CRITICALITY_KEYWORDS):
        criticality: Criticality = "high"
    elif any(k in text for k in _LOW_CRITICALITY_KEYWORDS):
        criticality = "low"
    else:
        criticality = "normal"

    # Inbox / lightweight coordination tasks are typically cheap.
    status = (task.get("status") or "").strip().lower()
    if status == "inbox" and agent_type in {"project-manager", "writer", "reviewer", "architect", "researcher"}:
        complexity = "light"

    return complexity, criticality


def decide_models(agent_type: str, task: dict[str, Any]) -> ModelDecision:
    """Return model preference list and audit metadata."""

    complexity, criticality = classify_task(task, agent_type=agent_type)

    # Policy table (minimum shippable):
    # - programming: Sonnet by default
    # - light inbox-ish: Haiku/Gemini tier
    # - very complex: include Opus fallback
    policy = "default"

    if agent_type == "programmer":
        policy = "programmer_default"
        models = [MODEL_SONNET, MODEL_OPUS]
    elif complexity == "light" and (task.get("status") or "").lower() == "inbox":
        # "light inbox" => fast/cheap tier first
        policy = "light_inbox"
        models = [MODEL_HAIKU, MODEL_GEMINI_FLASH, MODEL_SONNET, MODEL_OPUS]
    else:
        # Default for non-programming work: Sonnet; allow Opus fallback for "very_complex".
        policy = "non_programming_default"
        models = [MODEL_SONNET] + ([MODEL_OPUS] if complexity == "very_complex" else [])

    # Critical tasks get a stronger fallback chain.
    if criticality == "high" and MODEL_OPUS not in models:
        models = models + [MODEL_OPUS]
        policy = f"{policy}+high_crit"

    audit = {
        "agent_type": agent_type,
        "task_id": task.get("id"),
        "task_status": task.get("status"),
        "complexity": complexity,
        "criticality": criticality,
        "policy": policy,
        "models": models,
    }

    return ModelDecision(
        complexity=complexity,
        criticality=criticality,
        models=models,
        policy=policy,
        audit=audit,
    )
