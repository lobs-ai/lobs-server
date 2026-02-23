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

# Categories that ALWAYS escalate to Rafe regardless of effort
ALWAYS_RAFE_CATEGORIES = {
    "destructive_operation",
    "cross_project_migration",
}


@dataclass(frozen=True)
class PolicyDecision:
    """Result of policy evaluation for a proposed initiative."""

    risk_tier: str  # A/B/C
    lane: str  # auto_allowed/review_required/blocked
    approval_mode: str  # auto/soft_gate/hard_gate (backward compatibility)
    reason: str
    escalate_to_rafe: bool  # True for tier C items that need Rafe review


class PolicyEngine:
    """Classifies initiatives into explicit proactivity lanes."""

    def decide(self, category: str, *, estimated_effort: int | None = None) -> PolicyDecision:
        normalized = (category or "").strip().lower()

        # Categories that ALWAYS go to Rafe regardless of effort
        if normalized in ALWAYS_RAFE_CATEGORIES:
            return PolicyDecision(
                risk_tier="C",
                lane="blocked",
                approval_mode="hard_gate",
                reason="high-risk category always requires Rafe review",
                escalate_to_rafe=True,
            )

        # Routine maintenance - auto-allowed
        if normalized in AUTO_ALLOWED_CATEGORIES:
            return PolicyDecision(
                risk_tier="A",
                lane="auto_allowed",
                approval_mode="auto",
                reason="routine maintenance within autonomous lane",
                escalate_to_rafe=False,
            )

        # Standard review categories (always Lobs, never escalate)
        if normalized in REVIEW_REQUIRED_CATEGORIES:
            return PolicyDecision(
                risk_tier="B",
                lane="review_required",
                approval_mode="soft_gate",
                reason="new-scope or moderate-impact initiative requires Lobs review",
                escalate_to_rafe=False,
            )

        # Effort-based routing for feature_proposal
        if normalized == "feature_proposal":
            if estimated_effort is not None and estimated_effort <= 3:
                return PolicyDecision(
                    risk_tier="B",
                    lane="review_required",
                    approval_mode="soft_gate",
                    reason="feature proposal with small effort (≤3 days) — Lobs review",
                    escalate_to_rafe=False,
                )
            else:
                return PolicyDecision(
                    risk_tier="C",
                    lane="review_required",
                    approval_mode="soft_gate",
                    reason="large feature proposal (>3 days) — escalate to Rafe",
                    escalate_to_rafe=True,
                )

        # Effort-based routing for new_project
        if normalized == "new_project":
            if estimated_effort is not None and estimated_effort <= 2:
                return PolicyDecision(
                    risk_tier="B",
                    lane="review_required",
                    approval_mode="soft_gate",
                    reason="small new project (≤2 days) — Lobs review",
                    escalate_to_rafe=False,
                )
            else:
                return PolicyDecision(
                    risk_tier="C",
                    lane="review_required",
                    approval_mode="soft_gate",
                    reason="large new project (>2 days) — escalate to Rafe",
                    escalate_to_rafe=True,
                )

        # Business ideas always escalate to Rafe — these are strategic decisions
        if normalized == "business_idea":
            return PolicyDecision(
                risk_tier="C",
                lane="review_required",
                approval_mode="soft_gate",
                reason="business idea — escalate to Rafe for strategic review",
                escalate_to_rafe=True,
            )

        # Personal tools — small ones Lobs can approve, larger ones go to Rafe
        if normalized == "personal_tool":
            if estimated_effort is not None and estimated_effort <= 3:
                return PolicyDecision(
                    risk_tier="B",
                    lane="review_required",
                    approval_mode="soft_gate",
                    reason="personal tool with small effort (≤3 days) — Lobs review",
                    escalate_to_rafe=False,
                )
            else:
                return PolicyDecision(
                    risk_tier="C",
                    lane="review_required",
                    approval_mode="soft_gate",
                    reason="large personal tool (>3 days) — escalate to Rafe",
                    escalate_to_rafe=True,
                )

        # Effort-based routing for architecture_change
        if normalized == "architecture_change":
            if estimated_effort is not None and estimated_effort <= 2:
                return PolicyDecision(
                    risk_tier="B",
                    lane="review_required",
                    approval_mode="soft_gate",
                    reason="small architecture change (≤2 days) — Lobs review",
                    escalate_to_rafe=False,
                )
            else:
                return PolicyDecision(
                    risk_tier="C",
                    lane="review_required",
                    approval_mode="soft_gate",
                    reason="large architecture change (>2 days) — escalate to Rafe",
                    escalate_to_rafe=True,
                )

        # Small tasks (effort ≤ 2) default to auto-allowed
        if estimated_effort is not None and estimated_effort <= 2:
            return PolicyDecision(
                risk_tier="A",
                lane="auto_allowed",
                approval_mode="auto",
                reason="small bounded task under effort threshold",
                escalate_to_rafe=False,
            )

        # Unknown category defaults to review-required (Lobs, no Rafe escalation)
        return PolicyDecision(
            risk_tier="B",
            lane="review_required",
            approval_mode="soft_gate",
            reason="unknown category defaults to review-required lane",
            escalate_to_rafe=False,
        )

    @staticmethod
    def classify_batch(categories: Iterable[str]) -> dict[str, PolicyDecision]:
        engine = PolicyEngine()
        return {c: engine.decide(c) for c in categories}
