"""Policy engine for initiative autonomy lanes and approval gating."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


AUTO_ALLOWED_CATEGORIES = {
    # Writer routine maintenance
    "docs_sync",
    "writer_doc_freshness_maintenance",
    # Researcher bounded exploration
    "light_research",
    "research_bounded_exploration",
    # General hygiene
    "test_hygiene",
    "stale_triage",
}

REVIEW_REQUIRED_CATEGORIES = {
    "backlog_reprioritization",
    "automation_proposal",
    "moderate_refactor",
}

BLOCKED_CATEGORIES = {
    "architecture_change",
    "destructive_operation",
    "cross_project_migration",
    "agent_recruitment",
}


@dataclass(frozen=True)
class PolicyDecision:
    """Result of policy evaluation for a proposed initiative."""

    risk_tier: str  # A/B/C
    lane: str  # auto_allowed/review_required/blocked
    approval_mode: str  # auto/soft_gate/hard_gate (backward compatibility)
    reason: str


class PolicyEngine:
    """Classifies initiatives into explicit proactivity lanes."""

    def decide(self, category: str, *, estimated_effort: int | None = None) -> PolicyDecision:
        normalized = (category or "").strip().lower()

        if normalized in AUTO_ALLOWED_CATEGORIES:
            return PolicyDecision(
                risk_tier="A",
                lane="auto_allowed",
                approval_mode="auto",
                reason="routine maintenance within autonomous lane",
            )

        if normalized in REVIEW_REQUIRED_CATEGORIES:
            return PolicyDecision(
                risk_tier="B",
                lane="review_required",
                approval_mode="soft_gate",
                reason="new-scope or moderate-impact initiative requires Lobs review",
            )

        if normalized in BLOCKED_CATEGORIES:
            return PolicyDecision(
                risk_tier="C",
                lane="blocked",
                approval_mode="hard_gate",
                reason="high-risk category is blocked pending explicit override",
            )

        if estimated_effort is not None and estimated_effort <= 2:
            return PolicyDecision(
                risk_tier="A",
                lane="auto_allowed",
                approval_mode="auto",
                reason="small bounded task under effort threshold",
            )

        return PolicyDecision(
            risk_tier="B",
            lane="review_required",
            approval_mode="soft_gate",
            reason="unknown category defaults to review-required lane",
        )

    @staticmethod
    def classify_batch(categories: Iterable[str]) -> dict[str, PolicyDecision]:
        engine = PolicyEngine()
        return {c: engine.decide(c) for c in categories}
