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


def test_policy_engine_blocked_category():
    decision = PolicyEngine().decide("architecture_change")
    assert decision.risk_tier == "C"
    assert decision.lane == "blocked"
    assert decision.approval_mode == "hard_gate"


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
