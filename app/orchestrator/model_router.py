"""Model routing policy engine (first pass, tier-based).

Goals:
- Classify tasks by complexity + criticality using lightweight heuristics
- Route by model tiers (cheap/standard/strong), not fixed model IDs
- Resolve tiers to available models from environment-configured pools
- Provide fallback chain for provider/model failures
- Emit audit metadata so model choices are inspectable

This module is intentionally small and reversible.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

Complexity = Literal["light", "standard", "very_complex"]
Criticality = Literal["low", "normal", "high"]
ModelTier = Literal["cheap", "standard", "strong"]


# Tier defaults (override with env vars in deployment):
# - LOBS_MODEL_TIER_CHEAP
# - LOBS_MODEL_TIER_STANDARD
# - LOBS_MODEL_TIER_STRONG
# - LOBS_AVAILABLE_MODELS (optional allow-list)
DEFAULT_TIER_MODELS: dict[ModelTier, tuple[str, ...]] = {
    "cheap": (
        "anthropic/claude-haiku-4-5",
        "google-gemini-cli/gemini-3-pro-preview",
    ),
    "standard": (
        "openai-codex/gpt-5.3-codex",
        "anthropic/claude-sonnet-4-5",
    ),
    "strong": (
        "anthropic/claude-opus-4-6",
        "openai-codex/gpt-5.2",
    ),
}


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


def _parse_csv_env(var_name: str) -> list[str]:
    raw = (os.environ.get(var_name) or "").strip()
    if not raw:
        return []
    return [v.strip() for v in raw.split(",") if v.strip()]


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _tier_models(tier: ModelTier) -> list[str]:
    env_name = {
        "cheap": "LOBS_MODEL_TIER_CHEAP",
        "standard": "LOBS_MODEL_TIER_STANDARD",
        "strong": "LOBS_MODEL_TIER_STRONG",
    }[tier]
    from_env = _parse_csv_env(env_name)
    return from_env if from_env else list(DEFAULT_TIER_MODELS[tier])


def _available_models_allowlist() -> set[str] | None:
    allowed = _parse_csv_env("LOBS_AVAILABLE_MODELS")
    if not allowed:
        return None
    return set(allowed)


def _resolve_plan_to_models(plan: list[ModelTier]) -> tuple[list[str], dict[str, list[str]], set[str] | None]:
    tier_map = {
        "cheap": _tier_models("cheap"),
        "standard": _tier_models("standard"),
        "strong": _tier_models("strong"),
    }

    candidate_models: list[str] = []
    for tier in plan:
        candidate_models.extend(tier_map[tier])

    candidate_models = _dedupe_keep_order(candidate_models)

    allowlist = _available_models_allowlist()
    if allowlist is not None:
        candidate_models = [m for m in candidate_models if m in allowlist]

    return candidate_models, tier_map, allowlist


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

    # Policy table by tiers:
    # - programming: standard -> strong
    # - light inbox: cheap -> standard -> strong
    # - default non-programming: standard (+ strong for very_complex)
    policy = "default"

    if agent_type == "programmer":
        policy = "programmer_default"
        plan: list[ModelTier] = ["standard", "strong"]
    elif complexity == "light" and (task.get("status") or "").lower() == "inbox":
        policy = "light_inbox"
        plan = ["cheap", "standard", "strong"]
    else:
        policy = "non_programming_default"
        plan = ["standard"] + (["strong"] if complexity == "very_complex" else [])

    # Critical tasks must include strong tier.
    if criticality == "high" and "strong" not in plan:
        plan = plan + ["strong"]
        policy = f"{policy}+high_crit"

    models, tier_map, allowlist = _resolve_plan_to_models(plan)

    # Safety fallback: if allow-list removes everything, fall back to defaults
    # to avoid empty model selection. Worker layer will still handle failures.
    if not models:
        fallback_models = _dedupe_keep_order(
            list(DEFAULT_TIER_MODELS["standard"]) + list(DEFAULT_TIER_MODELS["cheap"]) + list(DEFAULT_TIER_MODELS["strong"])
        )
        models = fallback_models
        policy = f"{policy}+empty_allowlist_fallback"

    audit = {
        "agent_type": agent_type,
        "task_id": task.get("id"),
        "task_status": task.get("status"),
        "complexity": complexity,
        "criticality": criticality,
        "policy": policy,
        "tier_plan": plan,
        "tier_models": tier_map,
        "available_models_allowlist": sorted(list(allowlist)) if allowlist is not None else None,
        "models": models,
    }

    return ModelDecision(
        complexity=complexity,
        criticality=criticality,
        models=models,
        policy=policy,
        audit=audit,
    )
