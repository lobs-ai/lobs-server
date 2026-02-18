"""Policy engine for initiative autonomy and approval gating."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


AUTO_APPROVE_CATEGORIES = {
    "docs_sync",
    "test_hygiene",
    "stale_triage",
    "light_research",
}

SOFT_APPROVE_CATEGORIES = {
    "backlog_reprioritization",
    "automation_proposal",
    "moderate_refactor",
}

HARD_APPROVE_CATEGORIES = {
    "architecture_change",
    "destructive_operation",
    "cross_project_migration",
    "agent_recruitment",
}


@dataclass(frozen=True)
class PolicyDecision:
    """Result of policy evaluation for a proposed initiative."""

    risk_tier: str  # A/B/C
    approval_mode: str  # auto/soft_gate/hard_gate
    reason: str


class PolicyEngine:
    """Classifies initiatives into autonomy tiers."""

    def decide(self, category: str, *, estimated_effort: int | None = None) -> PolicyDecision:
        normalized = (category or "").strip().lower()

        if normalized in AUTO_APPROVE_CATEGORIES:
            return PolicyDecision(
                risk_tier="A",
                approval_mode="auto",
                reason="low-risk recurring maintenance",
            )

        if normalized in SOFT_APPROVE_CATEGORIES:
            return PolicyDecision(
                risk_tier="B",
                approval_mode="soft_gate",
                reason="moderate impact; allow with governance visibility",
            )

        if normalized in HARD_APPROVE_CATEGORIES:
            return PolicyDecision(
                risk_tier="C",
                approval_mode="hard_gate",
                reason="high-impact or irreversible change",
            )

        if estimated_effort is not None and estimated_effort <= 2:
            return PolicyDecision(
                risk_tier="A",
                approval_mode="auto",
                reason="small bounded task under effort threshold",
            )

        return PolicyDecision(
            risk_tier="B",
            approval_mode="soft_gate",
            reason="unknown category defaults to supervised autonomy",
        )

    @staticmethod
    def classify_batch(categories: Iterable[str]) -> dict[str, PolicyDecision]:
        engine = PolicyEngine()
        return {c: engine.decide(c) for c in categories}
