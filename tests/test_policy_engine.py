from app.orchestrator.policy_engine import PolicyEngine


def test_policy_engine_writer_doc_freshness_is_auto_allowed():
    decision = PolicyEngine().decide("writer_doc_freshness_maintenance")
    assert decision.risk_tier == "A"
    assert decision.lane == "auto_allowed"
    assert decision.approval_mode == "auto"


def test_policy_engine_research_bounded_exploration_is_auto_allowed():
    decision = PolicyEngine().decide("research_bounded_exploration")
    assert decision.risk_tier == "A"
    assert decision.lane == "auto_allowed"


def test_policy_engine_automation_proposal_requires_review():
    decision = PolicyEngine().decide("automation_proposal")
    assert decision.risk_tier == "B"
    assert decision.lane == "review_required"
    assert decision.approval_mode == "soft_gate"


def test_policy_engine_architecture_change_small_effort_lobs_review():
    """Small architecture changes (≤2 days) go to Lobs, not Rafe."""
    decision = PolicyEngine().decide("architecture_change", estimated_effort=2)
    assert decision.risk_tier == "B"
    assert decision.lane == "review_required"
    assert decision.approval_mode == "soft_gate"
    assert decision.escalate_to_rafe is False


def test_policy_engine_architecture_change_large_effort_escalates_to_rafe():
    """Large architecture changes (>2 days) escalate to Rafe."""
    decision = PolicyEngine().decide("architecture_change", estimated_effort=5)
    assert decision.risk_tier == "C"
    assert decision.lane == "review_required"
    assert decision.approval_mode == "soft_gate"
    assert decision.escalate_to_rafe is True


def test_policy_engine_architecture_change_no_effort_lobs_review():
    """Architecture changes without effort info default to Lobs review."""
    decision = PolicyEngine().decide("architecture_change")
    assert decision.risk_tier == "B"
    assert decision.lane == "review_required"
    assert decision.approval_mode == "soft_gate"
    assert decision.escalate_to_rafe is False


def test_policy_engine_destructive_operation_always_rafe():
    """Destructive operations always escalate to Rafe."""
    decision = PolicyEngine().decide("destructive_operation", estimated_effort=1)
    assert decision.risk_tier == "C"
    assert decision.lane == "blocked"
    assert decision.approval_mode == "hard_gate"
    assert decision.escalate_to_rafe is True


def test_policy_engine_new_project_small_effort_lobs_review():
    """Small new projects (≤2 days) go to Lobs."""
    decision = PolicyEngine().decide("new_project", estimated_effort=2)
    assert decision.risk_tier == "B"
    assert decision.escalate_to_rafe is False


def test_policy_engine_new_project_large_effort_escalates():
    """Large new projects (>2 days) escalate to Rafe."""
    decision = PolicyEngine().decide("new_project", estimated_effort=5)
    assert decision.risk_tier == "C"
    assert decision.escalate_to_rafe is True


def test_policy_engine_feature_proposal_small_effort_lobs_review():
    """Feature proposals (≤3 days) go to Lobs."""
    decision = PolicyEngine().decide("feature_proposal", estimated_effort=3)
    assert decision.risk_tier == "B"
    assert decision.escalate_to_rafe is False


def test_policy_engine_feature_proposal_large_effort_escalates():
    """Large feature proposals (>3 days) escalate to Rafe."""
    decision = PolicyEngine().decide("feature_proposal", estimated_effort=5)
    assert decision.risk_tier == "C"
    assert decision.escalate_to_rafe is True


def test_policy_engine_unknown_small_effort_auto():
    decision = PolicyEngine().decide("unknown_new_category", estimated_effort=1)
    assert decision.risk_tier == "A"
    assert decision.lane == "auto_allowed"
    assert decision.approval_mode == "auto"


def test_policy_engine_unknown_default_review_required():
    decision = PolicyEngine().decide("unknown_new_category")
    assert decision.risk_tier == "B"
    assert decision.lane == "review_required"
    assert decision.approval_mode == "soft_gate"
