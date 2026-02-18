from app.orchestrator.policy_engine import PolicyEngine


def test_policy_engine_auto_category():
    decision = PolicyEngine().decide("docs_sync")
    assert decision.risk_tier == "A"
    assert decision.approval_mode == "auto"


def test_policy_engine_hard_gate_category():
    decision = PolicyEngine().decide("architecture_change")
    assert decision.risk_tier == "C"
    assert decision.approval_mode == "hard_gate"


def test_policy_engine_unknown_small_effort_auto():
    decision = PolicyEngine().decide("unknown_new_category", estimated_effort=1)
    assert decision.risk_tier == "A"
    assert decision.approval_mode == "auto"


def test_policy_engine_unknown_default_soft_gate():
    decision = PolicyEngine().decide("unknown_new_category")
    assert decision.risk_tier == "B"
    assert decision.approval_mode == "soft_gate"
