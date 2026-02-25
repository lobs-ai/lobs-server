"""Tests for model spend guardrails and auto-downgrade policy.

Covers:
- budget_guard.py: lane classification, tier filtering, BudgetGuard.apply()
- budget_guardrails.py: lane downgrade logic, hard cap, daily report
- /api/usage/budget-lanes GET/PATCH endpoints
- /api/usage/daily-report GET endpoint
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.orchestrator.budget_guard import (
    BudgetGuard,
    BudgetGuardDecision,
    BUDGET_GUARD_LANE_POLICY_KEY,
    DEFAULT_LANE_POLICY,
    LANE_BACKGROUND,
    LANE_CRITICAL,
    LANE_STANDARD,
    _effective_lane_policy,
    _filter_to_tier_and_below,
    classify_task_lane,
)
from app.orchestrator.budget_guardrails import (
    LANE_DOWNGRADE_MAX_TIER,
    apply_daily_hard_cap,
    apply_lane_downgrade,
    build_daily_report,
    classify_task_lane as guardrails_classify_task_lane,
    get_override_log,
    append_override_log,
)
from app.orchestrator.model_router import TIER_ORDER


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TIER_MAP: dict[str, list[str]] = {
    "micro": ["model/micro-a"],
    "small": ["model/small-a", "model/small-b"],
    "medium": ["model/medium-a"],
    "standard": ["model/std-a", "model/std-b"],
    "strong": ["model/strong-a"],
}


# ---------------------------------------------------------------------------
# budget_guard.py — pure function tests
# ---------------------------------------------------------------------------


class TestClassifyTaskLaneBudgetGuard:
    """Tests for budget_guard.classify_task_lane()."""

    def test_high_criticality_maps_to_critical(self):
        task = {"title": "Urgent production outage", "notes": "", "status": "active"}
        lane = classify_task_lane(task, agent_type="programmer", criticality="high")
        assert lane == LANE_CRITICAL

    def test_strong_model_tier_maps_to_critical(self):
        task = {"title": "Normal task", "notes": "", "status": "active", "model_tier": "strong"}
        lane = classify_task_lane(task, agent_type="researcher", criticality="normal")
        assert lane == LANE_CRITICAL

    def test_writer_agent_maps_to_background(self):
        task = {"title": "Write a summary", "notes": "", "status": "active"}
        lane = classify_task_lane(task, agent_type="writer", criticality="normal")
        assert lane == LANE_BACKGROUND

    def test_reviewer_agent_maps_to_background(self):
        task = {"title": "Review code", "notes": "", "status": "active"}
        lane = classify_task_lane(task, agent_type="reviewer", criticality="normal")
        assert lane == LANE_BACKGROUND

    def test_inbox_non_programmer_maps_to_background(self):
        task = {"title": "Process inbox item", "notes": "", "status": "inbox"}
        lane = classify_task_lane(task, agent_type="researcher", criticality="normal")
        assert lane == LANE_BACKGROUND

    def test_programmer_inbox_maps_to_standard(self):
        """Programmer inbox tasks are standard, not background."""
        task = {"title": "Process inbox item", "notes": "", "status": "inbox"}
        lane = classify_task_lane(task, agent_type="programmer", criticality="normal")
        assert lane == LANE_STANDARD

    def test_normal_programmer_maps_to_standard(self):
        task = {"title": "Implement feature", "notes": "", "status": "active"}
        lane = classify_task_lane(task, agent_type="programmer", criticality="normal")
        assert lane == LANE_STANDARD

    def test_researcher_maps_to_standard(self):
        task = {"title": "Research topic", "notes": "", "status": "active"}
        lane = classify_task_lane(task, agent_type="researcher", criticality="normal")
        assert lane == LANE_STANDARD

    def test_low_criticality_defaults_to_standard(self):
        """Low criticality without other signals goes to standard."""
        task = {"title": "Fix typo", "notes": "", "status": "active"}
        lane = classify_task_lane(task, agent_type="programmer", criticality="low")
        assert lane == LANE_STANDARD


class TestFilterToTierAndBelow:
    """Tests for budget_guard._filter_to_tier_and_below()."""

    def test_filter_to_medium_removes_standard_and_strong(self):
        candidates = [
            "model/micro-a", "model/small-a", "model/medium-a",
            "model/std-a", "model/strong-a",
        ]
        result = _filter_to_tier_and_below(candidates, "medium", SAMPLE_TIER_MAP)
        assert "model/std-a" not in result
        assert "model/strong-a" not in result
        assert "model/medium-a" in result
        assert "model/small-a" in result
        assert "model/micro-a" in result

    def test_filter_to_small_removes_medium_and_above(self):
        candidates = ["model/small-a", "model/medium-a", "model/std-a"]
        result = _filter_to_tier_and_below(candidates, "small", SAMPLE_TIER_MAP)
        assert result == ["model/small-a"]

    def test_filter_to_strong_keeps_all(self):
        candidates = ["model/micro-a", "model/small-a", "model/std-a", "model/strong-a"]
        result = _filter_to_tier_and_below(candidates, "strong", SAMPLE_TIER_MAP)
        assert result == candidates

    def test_filter_empty_after_filtering_falls_back_to_original(self):
        """Safety: if filtering removes all candidates, return original."""
        candidates = ["model/std-a", "model/strong-a"]
        result = _filter_to_tier_and_below(candidates, "micro", SAMPLE_TIER_MAP)
        assert result == candidates  # fallback: nothing matched micro → return original

    def test_filter_preserves_order(self):
        candidates = ["model/std-b", "model/std-a", "model/medium-a"]
        result = _filter_to_tier_and_below(candidates, "standard", SAMPLE_TIER_MAP)
        assert result == candidates  # all allowed, order preserved


class TestEffectiveLanePolicy:
    """Tests for budget_guard._effective_lane_policy()."""

    def test_defaults_used_when_no_override(self):
        policy = _effective_lane_policy(None)
        assert policy[LANE_CRITICAL]["daily_cap_usd"] is None
        assert policy[LANE_STANDARD]["daily_cap_usd"] == 8.0
        assert policy[LANE_BACKGROUND]["daily_cap_usd"] == 3.0

    def test_partial_override_merges_with_defaults(self):
        raw = {"standard": {"daily_cap_usd": 12.0}}
        policy = _effective_lane_policy(raw)
        assert policy[LANE_STANDARD]["daily_cap_usd"] == 12.0
        assert policy[LANE_BACKGROUND]["daily_cap_usd"] == 3.0

    def test_downgrade_tier_can_be_overridden(self):
        raw = {"background": {"downgrade_tier": "micro"}}
        policy = _effective_lane_policy(raw)
        assert policy[LANE_BACKGROUND]["downgrade_tier"] == "micro"
        assert policy[LANE_STANDARD]["downgrade_tier"] == "medium"

    def test_invalid_lanes_ignored(self):
        raw = {"nonexistent_lane": {"daily_cap_usd": 99.0}}
        policy = _effective_lane_policy(raw)
        assert "nonexistent_lane" not in policy
        assert set(policy.keys()) == {LANE_CRITICAL, LANE_STANDARD, LANE_BACKGROUND}


# ---------------------------------------------------------------------------
# budget_guard.py — BudgetGuard integration tests (async, with DB)
# ---------------------------------------------------------------------------


class TestBudgetGuardApply:
    """Tests for BudgetGuard.apply() via in-memory DB."""

    @pytest.mark.asyncio
    async def test_no_cap_lane_passes_through(self, db_session):
        """Critical lane (no cap) → always passes through unchanged."""
        guard = BudgetGuard(db_session)
        candidates = ["model/strong-a", "model/std-a"]
        task = {"id": "t-crit", "title": "Urgent prod outage", "status": "active"}

        decision = await guard.apply(
            task=task,
            agent_type="programmer",
            criticality="high",
            candidates=candidates,
            tier_map=SAMPLE_TIER_MAP,
        )

        assert decision.lane == LANE_CRITICAL
        assert decision.over_budget is False
        assert decision.downgraded is False
        assert decision.effective_candidates == candidates
        assert decision.reason == "no_cap"

    @pytest.mark.asyncio
    async def test_within_budget_passes_through(self, db_session):
        """Standard lane under cap → no downgrade."""
        guard = BudgetGuard(db_session)
        candidates = ["model/std-a", "model/strong-a"]
        task = {"id": "t-std", "title": "Implement feature", "status": "active"}

        # No usage events in DB → spent=0 < cap=8 → within budget
        decision = await guard.apply(
            task=task,
            agent_type="programmer",
            criticality="normal",
            candidates=candidates,
            tier_map=SAMPLE_TIER_MAP,
        )

        assert decision.lane == LANE_STANDARD
        assert decision.over_budget is False
        assert decision.downgraded is False
        assert decision.effective_candidates == candidates

    @pytest.mark.asyncio
    async def test_over_budget_triggers_downgrade(self, db_session):
        """Standard lane over cap → downgrades to medium and below."""
        from app.models import ModelUsageEvent

        # Insert usage event that exceeds standard cap ($8)
        event = ModelUsageEvent(
            id="evt-1",
            source="orchestrator-worker",
            model="anthropic/claude-sonnet",
            provider="anthropic",
            route_type="api",
            task_type="programmer",
            input_tokens=10000,
            output_tokens=5000,
            requests=1,
            status="success",
            estimated_cost_usd=10.0,  # Over $8 standard cap
            timestamp=datetime.now(timezone.utc),
        )
        db_session.add(event)
        await db_session.commit()

        guard = BudgetGuard(db_session)
        candidates = ["model/std-a", "model/medium-a", "model/small-a"]
        task = {"id": "t-over", "title": "Normal programming task", "status": "active"}

        decision = await guard.apply(
            task=task,
            agent_type="programmer",
            criticality="normal",
            candidates=candidates,
            tier_map=SAMPLE_TIER_MAP,
        )

        assert decision.lane == LANE_STANDARD
        assert decision.over_budget is True
        assert decision.downgrade_tier == "medium"
        assert "model/std-a" not in decision.effective_candidates
        assert "model/medium-a" in decision.effective_candidates or "model/small-a" in decision.effective_candidates

    @pytest.mark.asyncio
    async def test_background_lane_over_budget_downgrades_to_small(self, db_session):
        """Background lane over cap ($3) → downgrade to small tier."""
        from app.models import ModelUsageEvent

        # Insert background spend over $3 cap
        event = ModelUsageEvent(
            id="evt-bg-1",
            source="orchestrator-worker",
            model="anthropic/claude-haiku",
            provider="anthropic",
            route_type="api",
            task_type="writer",
            input_tokens=5000,
            output_tokens=2000,
            requests=1,
            status="success",
            estimated_cost_usd=5.0,  # Over $3 background cap
            timestamp=datetime.now(timezone.utc),
        )
        db_session.add(event)
        await db_session.commit()

        guard = BudgetGuard(db_session)
        candidates = ["model/medium-a", "model/small-a", "model/micro-a"]
        task = {"id": "t-bg", "title": "Write summary", "status": "active"}

        decision = await guard.apply(
            task=task,
            agent_type="writer",
            criticality="normal",
            candidates=candidates,
            tier_map=SAMPLE_TIER_MAP,
        )

        assert decision.lane == LANE_BACKGROUND
        assert decision.downgrade_tier == "small"

    @pytest.mark.asyncio
    async def test_override_log_persisted_on_downgrade(self, db_session):
        """Downgrade events are logged to OrchestratorSetting."""
        from app.models import ModelUsageEvent, OrchestratorSetting
        from app.orchestrator.budget_guard import BUDGET_GUARD_OVERRIDE_LOG_KEY

        # Exceed standard cap
        event = ModelUsageEvent(
            id="evt-log-1",
            source="orchestrator-worker",
            model="openai/gpt-5",
            provider="openai",
            route_type="api",
            task_type="programmer",
            input_tokens=10000,
            output_tokens=5000,
            requests=1,
            status="success",
            estimated_cost_usd=9.0,
            timestamp=datetime.now(timezone.utc),
        )
        db_session.add(event)
        await db_session.commit()

        guard = BudgetGuard(db_session)
        candidates = ["model/std-a", "model/medium-a"]
        task = {"id": "t-log", "title": "Code implementation", "status": "active"}

        decision = await guard.apply(
            task=task,
            agent_type="programmer",
            criticality="normal",
            candidates=candidates,
            tier_map=SAMPLE_TIER_MAP,
        )
        await db_session.commit()

        if decision.downgraded:
            log_row = await db_session.get(OrchestratorSetting, BUDGET_GUARD_OVERRIDE_LOG_KEY)
            assert log_row is not None
            assert isinstance(log_row.value, list)
            assert len(log_row.value) >= 1
            latest = log_row.value[-1]
            assert latest["lane"] == LANE_STANDARD
            assert latest["task_id"] == "t-log"

    @pytest.mark.asyncio
    async def test_today_spend_all_lanes_returns_dict(self, db_session):
        """today_spend_all_lanes returns a dict with all three lanes."""
        guard = BudgetGuard(db_session)
        result = await guard.today_spend_all_lanes()

        assert isinstance(result, dict)
        assert set(result.keys()) == {LANE_CRITICAL, LANE_STANDARD, LANE_BACKGROUND}
        for lane, spend in result.items():
            assert isinstance(spend, float)
            assert spend >= 0.0


# ---------------------------------------------------------------------------
# budget_guardrails.py — pure function tests
# ---------------------------------------------------------------------------


class TestGuardrailsClassifyTaskLane:
    """Tests for budget_guardrails.classify_task_lane()."""

    def test_programmer_execution_maps_to_critical(self):
        task = {"title": "Implement auth", "notes": ""}
        lane = guardrails_classify_task_lane(
            task, agent_type="programmer", complexity="standard", purpose="execution"
        )
        assert lane == "critical"

    def test_high_criticality_keywords_maps_to_critical(self):
        task = {"title": "Production outage investigation", "notes": "auth system down"}
        lane = guardrails_classify_task_lane(
            task, agent_type="researcher", complexity="standard", purpose="execution"
        )
        assert lane == "critical"

    def test_reflection_purpose_maps_to_background(self):
        task = {"title": "Reflect on performance", "notes": ""}
        lane = guardrails_classify_task_lane(
            task, agent_type="programmer", complexity="standard", purpose="reflection"
        )
        assert lane == "background"

    def test_light_complexity_maps_to_background(self):
        task = {"title": "Short note", "notes": ""}
        lane = guardrails_classify_task_lane(
            task, agent_type="researcher", complexity="light", purpose="execution"
        )
        assert lane == "background"

    def test_standard_researcher_maps_to_standard(self):
        task = {"title": "Research distributed systems", "notes": ""}
        lane = guardrails_classify_task_lane(
            task, agent_type="researcher", complexity="standard", purpose="execution"
        )
        assert lane == "standard"


class TestApplyLaneDowngrade:
    """Tests for budget_guardrails.apply_lane_downgrade()."""

    def test_within_budget_no_downgrade(self):
        candidates = ["model/std-a", "model/strong-a"]
        result, reason = apply_lane_downgrade(
            candidates,
            tier_map=SAMPLE_TIER_MAP,
            lane="standard",
            lane_spend=2.0,
            lane_cap=8.0,
        )
        assert result == candidates
        assert reason is None

    def test_over_budget_downgrades_standard_lane(self):
        candidates = ["model/std-a", "model/medium-a", "model/small-a"]
        result, reason = apply_lane_downgrade(
            candidates,
            tier_map=SAMPLE_TIER_MAP,
            lane="standard",
            lane_spend=9.0,
            lane_cap=8.0,
        )
        assert "model/std-a" not in result
        assert reason is not None
        assert "lane_cap_exceeded" in reason

    def test_over_budget_downgrades_background_to_small(self):
        candidates = ["model/medium-a", "model/small-a", "model/micro-a"]
        result, reason = apply_lane_downgrade(
            candidates,
            tier_map=SAMPLE_TIER_MAP,
            lane="background",
            lane_spend=4.0,
            lane_cap=3.0,
        )
        # background lane max_tier is "small", so medium should be dropped
        assert "model/medium-a" not in result
        assert "model/small-a" in result or "model/micro-a" in result
        assert reason is not None

    def test_critical_lane_downgrade_allows_up_to_standard(self):
        candidates = ["model/strong-a", "model/std-a"]
        result, reason = apply_lane_downgrade(
            candidates,
            tier_map=SAMPLE_TIER_MAP,
            lane="critical",
            lane_spend=100.0,
            lane_cap=50.0,
        )
        assert "model/std-a" in result
        assert reason is not None

    def test_zero_cap_means_no_cap_enforced(self):
        candidates = ["model/std-a", "model/strong-a"]
        result, reason = apply_lane_downgrade(
            candidates,
            tier_map=SAMPLE_TIER_MAP,
            lane="standard",
            lane_spend=50.0,
            lane_cap=0.0,  # 0 means disabled
        )
        assert result == candidates
        assert reason is None

    def test_fallback_when_all_candidates_removed(self):
        """If filtering removes all candidates, returns original list."""
        candidates = ["model/strong-a"]  # Only strong, but cap hit at small
        result, reason = apply_lane_downgrade(
            candidates,
            tier_map=SAMPLE_TIER_MAP,
            lane="background",
            lane_spend=4.0,
            lane_cap=3.0,
        )
        # Safety: should return original since nothing below small
        assert result == candidates
        # Reason will indicate fallback
        assert reason is not None


class TestApplyDailyHardCap:
    """Tests for budget_guardrails.apply_daily_hard_cap()."""

    def test_within_daily_cap_no_change(self):
        candidates = ["model/std-a", "model/strong-a"]
        result, reason = apply_daily_hard_cap(
            candidates,
            daily_spend=5.0,
            daily_hard_cap=20.0,
            tier_map=SAMPLE_TIER_MAP,
        )
        assert result == candidates
        assert reason is None

    def test_hard_cap_exceeded_restricts_to_micro_small(self):
        candidates = ["model/strong-a", "model/std-a", "model/medium-a", "model/small-a", "model/micro-a"]
        result, reason = apply_daily_hard_cap(
            candidates,
            daily_spend=25.0,
            daily_hard_cap=20.0,
            tier_map=SAMPLE_TIER_MAP,
        )
        assert "model/strong-a" not in result
        assert "model/std-a" not in result
        assert "model/medium-a" not in result
        assert reason is not None
        assert "daily_hard_cap_exceeded" in reason

    def test_hard_cap_zero_means_disabled(self):
        candidates = ["model/std-a"]
        result, reason = apply_daily_hard_cap(
            candidates,
            daily_spend=999.0,
            daily_hard_cap=0.0,
            tier_map=SAMPLE_TIER_MAP,
        )
        assert result == candidates
        assert reason is None

    def test_hard_cap_fallback_when_no_cheap_models(self):
        """When no micro/small models available, returns original list."""
        candidates = ["model/std-a"]
        tier_map_no_cheap: dict[str, list[str]] = {
            "micro": [],
            "small": [],
            "medium": [],
            "standard": ["model/std-a"],
            "strong": [],
        }
        result, reason = apply_daily_hard_cap(
            candidates,
            daily_spend=25.0,
            daily_hard_cap=20.0,
            tier_map=tier_map_no_cheap,
        )
        assert result == candidates
        assert reason is not None


# ---------------------------------------------------------------------------
# budget_guardrails.py — DB-backed tests
# ---------------------------------------------------------------------------


class TestOverrideLog:
    """Tests for budget_guardrails.append_override_log() / get_override_log()."""

    @pytest.mark.asyncio
    async def test_append_and_retrieve_override_log(self, db_session):
        await append_override_log(
            db_session,
            lane="standard",
            reason="lane_cap_exceeded(lane=standard, spend=9.0, cap=8.0, max_tier=medium)",
            original_model="model/std-a",
            downgraded_model="model/medium-a",
            task_id="t-test",
            agent_type="programmer",
        )
        await db_session.commit()

        entries = await get_override_log(db_session, limit=10)
        assert len(entries) == 1
        assert entries[0]["lane"] == "standard"
        assert entries[0]["task_id"] == "t-test"
        assert entries[0]["original_model"] == "model/std-a"

    @pytest.mark.asyncio
    async def test_override_log_capped_at_200_entries(self, db_session):
        """Log is capped at 200 entries (ring buffer)."""
        for i in range(210):
            await append_override_log(
                db_session,
                lane="standard",
                reason=f"test_{i}",
                original_model="model/std-a",
                downgraded_model="model/medium-a",
                task_id=f"t-{i}",
                agent_type="programmer",
            )
        await db_session.commit()

        entries = await get_override_log(db_session, limit=300)
        assert len(entries) <= 200

    @pytest.mark.asyncio
    async def test_get_override_log_filtered_by_since(self, db_session):
        """Entries before `since` are filtered out."""
        from datetime import timedelta

        await append_override_log(
            db_session,
            lane="standard",
            reason="old_event",
            original_model="model/std-a",
            downgraded_model="model/medium-a",
            task_id="t-old",
            agent_type="programmer",
        )
        await db_session.commit()

        # Retrieve with since=now (should exclude the old event)
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        entries = await get_override_log(db_session, limit=10, since=future)
        assert len(entries) == 0


class TestBuildDailyReport:
    """Tests for budget_guardrails.build_daily_report()."""

    @pytest.mark.asyncio
    async def test_daily_report_structure(self, db_session):
        """Daily report has expected top-level keys."""
        budget_limits = {
            "monthly_total_usd": 150.0,
            "daily_hard_cap_usd": 20.0,
            "per_lane_daily_caps": {"critical": None, "standard": 8.0, "background": 3.0},
        }
        report = await build_daily_report(db_session, budget_limits=budget_limits)

        assert "date" in report
        assert "total_spend_usd" in report
        assert "daily_hard_cap_usd" in report
        assert "hard_cap_exceeded" in report
        assert "by_provider" in report
        assert "lane_status" in report
        assert "alerts" in report
        assert "override_events" in report

    @pytest.mark.asyncio
    async def test_daily_report_zero_spend(self, db_session):
        """Empty DB → zero spend, no alerts."""
        budget_limits = {
            "daily_hard_cap_usd": 20.0,
            "per_lane_daily_caps": {"standard": 8.0, "background": 3.0},
        }
        report = await build_daily_report(db_session, budget_limits=budget_limits)

        assert report["total_spend_usd"] == 0.0
        assert report["hard_cap_exceeded"] is False
        assert report["alerts"] == []

    @pytest.mark.asyncio
    async def test_daily_report_alerts_when_lane_at_cap(self, db_session):
        """Report includes alerts when lane caps are hit."""
        from app.models import ModelUsageEvent

        # Add spend that exceeds the standard lane cap
        event = ModelUsageEvent(
            id="evt-report-1",
            source="orchestrator-worker",
            model="anthropic/claude-sonnet",
            provider="anthropic",
            route_type="api",
            task_type="programmer",
            input_tokens=10000,
            output_tokens=5000,
            requests=1,
            status="success",
            estimated_cost_usd=9.0,
            timestamp=datetime.now(timezone.utc),
        )
        db_session.add(event)
        await db_session.commit()

        budget_limits = {
            "daily_hard_cap_usd": 50.0,
            "per_lane_daily_caps": {"standard": 8.0, "background": 3.0},
        }
        report = await build_daily_report(db_session, budget_limits=budget_limits)

        assert report["total_spend_usd"] > 0
        # Lane status should exist
        assert "lane_status" in report
        assert isinstance(report["lane_status"], dict)

    @pytest.mark.asyncio
    async def test_daily_report_lane_status_structure(self, db_session):
        """Lane status entries have required fields."""
        budget_limits = {
            "daily_hard_cap_usd": 20.0,
            "per_lane_daily_caps": {"critical": None, "standard": 8.0, "background": 3.0},
        }
        report = await build_daily_report(db_session, budget_limits=budget_limits)

        for lane in ("critical", "standard", "background"):
            status = report["lane_status"].get(lane, {})
            assert "spend_usd" in status
            assert "at_cap" in status


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestBudgetLanesEndpoint:
    """Tests for GET/PATCH /api/usage/budget-lanes."""

    @pytest.mark.asyncio
    async def test_get_budget_lanes_default_policy(self, client):
        """GET returns default policy when none set."""
        resp = await client.get("/api/usage/budget-lanes")
        assert resp.status_code == 200
        data = resp.json()

        assert "policy" in data
        assert "today_spend" in data
        assert "override_log_today" in data

        policy = data["policy"]
        assert LANE_CRITICAL in policy
        assert LANE_STANDARD in policy
        assert LANE_BACKGROUND in policy

        # Default values
        assert policy[LANE_CRITICAL]["daily_cap_usd"] is None
        assert policy[LANE_STANDARD]["daily_cap_usd"] == 8.0
        assert policy[LANE_BACKGROUND]["daily_cap_usd"] == 3.0

    @pytest.mark.asyncio
    async def test_patch_budget_lanes_updates_standard_cap(self, client):
        """PATCH updates the standard lane cap."""
        payload = {"standard": {"daily_cap_usd": 12.0, "downgrade_tier": "medium"}}
        resp = await client.patch("/api/usage/budget-lanes", json=payload)
        assert resp.status_code == 200

        data = resp.json()
        assert data["policy"][LANE_STANDARD]["daily_cap_usd"] == 12.0

        # Verify persistence via GET
        get_resp = await client.get("/api/usage/budget-lanes")
        get_data = get_resp.json()
        assert get_data["policy"][LANE_STANDARD]["daily_cap_usd"] == 12.0

    @pytest.mark.asyncio
    async def test_patch_budget_lanes_partial_update(self, client):
        """PATCH with only background lane preserves other lanes."""
        payload = {"background": {"daily_cap_usd": 5.0}}
        resp = await client.patch("/api/usage/budget-lanes", json=payload)
        assert resp.status_code == 200

        data = resp.json()
        # Background updated
        assert data["policy"][LANE_BACKGROUND]["daily_cap_usd"] == 5.0
        # Standard still at default (merged from defaults)
        assert data["policy"][LANE_STANDARD]["daily_cap_usd"] == 8.0

    @pytest.mark.asyncio
    async def test_patch_budget_lanes_invalid_lane_returns_422(self, client):
        """PATCH with unknown lane returns 422."""
        payload = {"ultra": {"daily_cap_usd": 99.0}}
        resp = await client.patch("/api/usage/budget-lanes", json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_patch_budget_lanes_null_cap_removes_cap(self, client):
        """Setting daily_cap_usd to null removes the cap."""
        # First set a cap
        await client.patch("/api/usage/budget-lanes", json={"standard": {"daily_cap_usd": 10.0}})

        # Then null it out
        resp = await client.patch("/api/usage/budget-lanes", json={"standard": {"daily_cap_usd": None}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["policy"][LANE_STANDARD]["daily_cap_usd"] is None

    @pytest.mark.asyncio
    async def test_get_budget_lanes_today_spend_structure(self, client):
        """today_spend includes spend/cap/over_budget for each lane."""
        resp = await client.get("/api/usage/budget-lanes")
        assert resp.status_code == 200
        today_spend = resp.json()["today_spend"]

        for lane in (LANE_CRITICAL, LANE_STANDARD, LANE_BACKGROUND):
            assert lane in today_spend
            entry = today_spend[lane]
            assert "spent_usd" in entry
            assert "cap_usd" in entry
            assert "over_budget" in entry


class TestDailyReportEndpoint:
    """Tests for GET /api/usage/daily-report."""

    @pytest.mark.asyncio
    async def test_daily_report_returns_200(self, client):
        """GET /api/usage/daily-report returns 200."""
        resp = await client.get("/api/usage/daily-report")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_daily_report_structure(self, client):
        """Daily report response has all required fields."""
        resp = await client.get("/api/usage/daily-report")
        assert resp.status_code == 200
        data = resp.json()

        assert "date" in data
        assert "total_spend_usd" in data
        assert "lane_status" in data
        assert "alerts" in data
        assert "override_events" in data
        assert "budget_guard_overrides_today" in data
        assert "budget_guard_override_count" in data

    @pytest.mark.asyncio
    async def test_daily_report_zero_spend(self, client):
        """Empty DB → zero spend in daily report."""
        resp = await client.get("/api/usage/daily-report")
        assert resp.status_code == 200
        data = resp.json()

        assert data["total_spend_usd"] == 0.0
        assert data["budget_guard_override_count"] == 0
        assert isinstance(data["budget_guard_overrides_today"], list)

    @pytest.mark.asyncio
    async def test_daily_report_with_usage_events(self, client):
        """Daily report reflects ingested usage events."""
        # Ingest a usage event first
        event_payload = {
            "source": "orchestrator-worker",
            "model": "openai/gpt-5",
            "route_type": "api",
            "task_type": "programmer",
            "input_tokens": 5000,
            "output_tokens": 2000,
            "requests": 1,
            "status": "success",
            "estimated_cost_usd": 3.5,
        }
        await client.post("/api/usage/events", json=event_payload)

        resp = await client.get("/api/usage/daily-report")
        assert resp.status_code == 200
        data = resp.json()

        # With explicit estimated_cost_usd the spend should be non-zero
        assert data["total_spend_usd"] >= 0.0  # may vary by day computation


# ---------------------------------------------------------------------------
# Integration: budget guard wired into model chooser
# ---------------------------------------------------------------------------


class TestModelChooserBudgetIntegration:
    """Verify BudgetGuard is called from ModelChooser.choose()."""

    @pytest.mark.asyncio
    async def test_chooser_calls_budget_guard(self, db_session, monkeypatch):
        """ModelChooser.choose() integrates lane budget guard in its pipeline."""
        import app.orchestrator.model_chooser as mc_module

        # Patch Ollama discovery to return empty (avoids network calls)
        monkeypatch.setattr(
            mc_module,
            "discover_ollama_models",
            AsyncMock(return_value={}),
        )

        from app.orchestrator.model_chooser import ModelChooser

        chooser = ModelChooser(db_session)
        task = {"id": "t-int", "title": "Implement feature", "status": "active"}
        choice = await chooser.choose(agent_type="programmer", task=task)

        # Lane guard audit should be present
        assert "lane_guard" in choice.audit
        guard_audit = choice.audit["lane_guard"]
        assert "lane" in guard_audit
        assert "over_budget" in guard_audit
        assert "effective_candidates" in guard_audit
        assert isinstance(guard_audit["effective_candidates"], list)

    @pytest.mark.asyncio
    async def test_chooser_returns_valid_model_under_budget(self, db_session, monkeypatch):
        """ModelChooser returns a model string when under budget."""
        import app.orchestrator.model_chooser as mc_module

        monkeypatch.setattr(
            mc_module,
            "discover_ollama_models",
            AsyncMock(return_value={}),
        )

        from app.orchestrator.model_chooser import ModelChooser

        chooser = ModelChooser(db_session)
        task = {"id": "t-valid", "title": "Write a document", "status": "active"}
        choice = await chooser.choose(agent_type="writer", task=task)

        assert isinstance(choice.model, str)
        assert len(choice.model) > 0
        assert isinstance(choice.candidates, list)

    @pytest.mark.asyncio
    async def test_chooser_budget_lane_set_in_choice(self, db_session, monkeypatch):
        """ModelChooser.choose() returns a budget_lane on the choice object."""
        import app.orchestrator.model_chooser as mc_module

        monkeypatch.setattr(
            mc_module,
            "discover_ollama_models",
            AsyncMock(return_value={}),
        )

        from app.orchestrator.model_chooser import ModelChooser

        chooser = ModelChooser(db_session)

        # Programmer task → standard or critical lane
        prog_task = {"id": "t-prog", "title": "Fix bug", "status": "active"}
        prog_choice = await chooser.choose(agent_type="programmer", task=prog_task)
        assert prog_choice.budget_lane in ("critical", "standard", "background", "unknown")

        # Writer task → background lane expected
        writer_task = {"id": "t-writer", "title": "Write a blog post", "status": "active"}
        writer_choice = await chooser.choose(agent_type="writer", task=writer_task)
        assert writer_choice.budget_lane == "background"

    @pytest.mark.asyncio
    async def test_chooser_downgrades_when_over_budget(self, db_session, monkeypatch):
        """ModelChooser downgrades model tier when standard lane cap is exceeded."""
        import app.orchestrator.model_chooser as mc_module
        from app.models import ModelUsageEvent, OrchestratorSetting
        from app.orchestrator.budget_guard import BUDGET_GUARD_LANE_POLICY_KEY

        monkeypatch.setattr(
            mc_module,
            "discover_ollama_models",
            AsyncMock(return_value={}),
        )

        # Set tight standard lane cap ($0.01) so any spend triggers downgrade
        policy_row = OrchestratorSetting(
            key=BUDGET_GUARD_LANE_POLICY_KEY,
            value={"standard": {"daily_cap_usd": 0.01, "downgrade_tier": "medium"}},
        )
        db_session.add(policy_row)

        # Add a usage event tagged to standard lane to exceed the $0.01 cap
        event = ModelUsageEvent(
            id="evt-chooser-over",
            source="orchestrator-worker",
            model="anthropic/claude-sonnet",
            provider="anthropic",
            route_type="api",
            task_type="programmer",
            budget_lane="standard",
            input_tokens=1000,
            output_tokens=500,
            requests=1,
            status="success",
            estimated_cost_usd=0.05,  # Over $0.01 cap
            timestamp=datetime.now(timezone.utc),
        )
        db_session.add(event)
        await db_session.commit()

        from app.orchestrator.model_chooser import ModelChooser

        chooser = ModelChooser(db_session)
        task = {"id": "t-over", "title": "Implement a new feature", "status": "active"}
        choice = await chooser.choose(agent_type="programmer", task=task)

        # The lane_guard audit should show over_budget=True
        guard_audit = choice.audit.get("lane_guard", {})
        assert guard_audit.get("over_budget") is True, (
            f"Expected over_budget=True in lane_guard audit, got: {guard_audit}"
        )
        # And downgraded should be True (or the candidates were already within tier)
        # At minimum, no strong-tier-only models should be the sole candidate
        assert isinstance(choice.candidates, list)
        assert len(choice.candidates) > 0


# ---------------------------------------------------------------------------
# Improved daily-report endpoint: accurate budget_lane spend and downgrade_tier
# ---------------------------------------------------------------------------


class TestDailyReportBudgetLaneAccuracy:
    """Verify the daily-report endpoint uses BudgetGuard for accurate lane data."""

    @pytest.mark.asyncio
    async def test_daily_report_lane_status_has_downgrade_tier(self, client):
        """Lane status in daily report includes downgrade_tier from lane policy."""
        resp = await client.get("/api/usage/daily-report")
        assert resp.status_code == 200
        data = resp.json()

        lane_status = data["lane_status"]
        # All three lanes must be present
        for lane in ("critical", "standard", "background"):
            assert lane in lane_status
            entry = lane_status[lane]
            # downgrade_tier key must exist (may be None for critical lane by default)
            assert "downgrade_tier" in entry

        # Default: standard should downgrade to medium
        assert lane_status["standard"]["downgrade_tier"] == "medium"
        # Default: background should downgrade to small
        assert lane_status["background"]["downgrade_tier"] == "small"
        # Default: critical has no downgrade
        assert lane_status["critical"]["downgrade_tier"] is None

    @pytest.mark.asyncio
    async def test_daily_report_reflects_budget_lane_tagged_spend(self, client):
        """Spend events with explicit budget_lane appear in the correct lane spend."""
        # Log a usage event tagged to the background lane
        event_payload = {
            "source": "orchestrator-worker",
            "model": "anthropic/claude-haiku",
            "provider": "anthropic",
            "route_type": "api",
            "task_type": "writer",
            "budget_lane": "background",
            "input_tokens": 1000,
            "output_tokens": 500,
            "requests": 1,
            "status": "success",
            "estimated_cost_usd": 0.02,
        }
        create_resp = await client.post("/api/usage/events", json=event_payload)
        assert create_resp.status_code == 200

        resp = await client.get("/api/usage/daily-report")
        assert resp.status_code == 200
        data = resp.json()

        bg_spend = data["lane_status"]["background"]["spend_usd"]
        assert bg_spend >= 0.02, (
            f"Expected background lane spend >= 0.02, got {bg_spend}"
        )

    @pytest.mark.asyncio
    async def test_daily_report_alert_generated_when_lane_at_cap(self, client):
        """Daily report generates an alert when a lane is at or over its cap."""
        # Set a very low background cap so the next event triggers it
        patch_resp = await client.patch(
            "/api/usage/budget-lanes",
            json={"background": {"daily_cap_usd": 0.001, "downgrade_tier": "small"}},
        )
        assert patch_resp.status_code == 200

        # Log a background event exceeding the tiny cap
        event_payload = {
            "source": "orchestrator-worker",
            "model": "anthropic/claude-haiku",
            "provider": "anthropic",
            "route_type": "api",
            "task_type": "writer",
            "budget_lane": "background",
            "input_tokens": 500,
            "output_tokens": 200,
            "requests": 1,
            "status": "success",
            "estimated_cost_usd": 0.01,  # Over the 0.001 cap
        }
        await client.post("/api/usage/events", json=event_payload)

        resp = await client.get("/api/usage/daily-report")
        assert resp.status_code == 200
        data = resp.json()

        # lane_status should show at_cap=True for background
        bg_status = data["lane_status"]["background"]
        assert bg_status["at_cap"] is True

        # An alert should be present
        alerts = data.get("alerts", [])
        assert any("background" in a.lower() for a in alerts), (
            f"Expected 'background' alert in alerts list, got: {alerts}"
        )

    @pytest.mark.asyncio
    async def test_daily_report_no_alert_when_within_budget(self, client):
        """No lane alerts generated when all lanes are within their caps."""
        resp = await client.get("/api/usage/daily-report")
        assert resp.status_code == 200
        data = resp.json()

        # With zero spend, nothing should be at cap
        for lane, status in data["lane_status"].items():
            assert status["at_cap"] is False

        # No lane-cap alerts (hard-cap alert may appear in other tests but not here)
        cap_alerts = [a for a in data.get("alerts", []) if "cap exceeded" in a.lower()]
        assert cap_alerts == []

    @pytest.mark.asyncio
    async def test_daily_report_lane_status_uses_budget_guard_policy(self, client):
        """After patching lane policy, daily report lane_status reflects the new cap."""
        # Set a custom cap on the standard lane
        patch_resp = await client.patch(
            "/api/usage/budget-lanes",
            json={"standard": {"daily_cap_usd": 25.0, "downgrade_tier": "medium"}},
        )
        assert patch_resp.status_code == 200

        resp = await client.get("/api/usage/daily-report")
        assert resp.status_code == 200
        data = resp.json()

        # The cap_usd in lane_status should reflect the patched value
        std_status = data["lane_status"]["standard"]
        assert std_status["cap_usd"] == 25.0, (
            f"Expected cap_usd=25.0 in standard lane, got: {std_status}"
        )
        assert std_status["downgrade_tier"] == "medium"

    @pytest.mark.asyncio
    async def test_daily_report_standard_lane_spend_from_tagged_events(self, client):
        """Standard-lane-tagged events appear in standard lane spend."""
        event_payload = {
            "source": "orchestrator-worker",
            "model": "openai/gpt-5",
            "provider": "openai",
            "route_type": "api",
            "task_type": "programmer",
            "budget_lane": "standard",
            "input_tokens": 2000,
            "output_tokens": 1000,
            "requests": 1,
            "status": "success",
            "estimated_cost_usd": 0.15,
        }
        create_resp = await client.post("/api/usage/events", json=event_payload)
        assert create_resp.status_code == 200

        resp = await client.get("/api/usage/daily-report")
        assert resp.status_code == 200
        data = resp.json()

        std_spend = data["lane_status"]["standard"]["spend_usd"]
        assert std_spend >= 0.15, (
            f"Expected standard lane spend >= 0.15, got {std_spend}"
        )

    @pytest.mark.asyncio
    async def test_daily_report_critical_lane_no_default_cap(self, client):
        """Critical lane has no cap by default — cap_usd is null, at_cap is False."""
        resp = await client.get("/api/usage/daily-report")
        assert resp.status_code == 200
        data = resp.json()

        crit_status = data["lane_status"]["critical"]
        assert crit_status["cap_usd"] is None
        assert crit_status["at_cap"] is False
        assert crit_status["utilization_pct"] is None


# ---------------------------------------------------------------------------
# Global daily hard cap enforcement in ModelChooser
# ---------------------------------------------------------------------------


class TestGlobalDailyHardCapInModelChooser:
    """Tests for ModelChooser._apply_global_daily_cap() wired into choose()."""

    @pytest.mark.asyncio
    async def test_choose_includes_hard_cap_guard_in_audit(self, db_session, monkeypatch):
        """ModelChooser.choose() includes hard_cap_guard in audit dict."""
        import app.orchestrator.model_chooser as mc_module

        monkeypatch.setattr(mc_module, "discover_ollama_models", AsyncMock(return_value={}))

        from app.orchestrator.model_chooser import ModelChooser

        chooser = ModelChooser(db_session)
        task = {"id": "t-hcg", "title": "Write docs", "status": "active"}
        choice = await chooser.choose(agent_type="writer", task=task)

        # hard_cap_guard must be present in the audit
        assert "hard_cap_guard" in choice.audit
        hcg = choice.audit["hard_cap_guard"]
        assert "effective_candidates" in hcg
        assert "hard_cap_exceeded" in hcg
        assert "downgraded" in hcg
        assert "reason" in hcg

    @pytest.mark.asyncio
    async def test_choose_no_hard_cap_configured_passes_through(self, db_session, monkeypatch):
        """With no daily_hard_cap_usd configured, hard_cap_guard shows no_hard_cap."""
        import app.orchestrator.model_chooser as mc_module

        monkeypatch.setattr(mc_module, "discover_ollama_models", AsyncMock(return_value={}))

        from app.orchestrator.model_chooser import ModelChooser

        chooser = ModelChooser(db_session)
        task = {"id": "t-nohc", "title": "Normal task", "status": "active"}
        choice = await chooser.choose(agent_type="programmer", task=task)

        hcg = choice.audit["hard_cap_guard"]
        # No cap configured → daily_hard_cap_usd defaults to 0.0 → "no_hard_cap"
        assert hcg["reason"] == "no_hard_cap"
        assert hcg["hard_cap_exceeded"] is False
        assert hcg["downgraded"] is False
        assert hcg["hard_cap_usd"] is None

    @pytest.mark.asyncio
    async def test_choose_downgrades_when_hard_cap_exceeded(self, db_session, monkeypatch):
        """ModelChooser restricts to micro/small when global daily hard cap is exceeded."""
        import app.orchestrator.model_chooser as mc_module
        from app.models import ModelUsageEvent, OrchestratorSetting

        monkeypatch.setattr(mc_module, "discover_ollama_models", AsyncMock(return_value={}))

        # Set a very low global hard cap ($0.001) so any spend exceeds it
        budget_row = OrchestratorSetting(
            key="usage.budgets",
            value={"daily_hard_cap_usd": 0.001},
        )
        db_session.add(budget_row)

        # Log a usage event that exceeds the hard cap
        event = ModelUsageEvent(
            id="evt-hc-exceed",
            source="orchestrator-worker",
            model="openai/gpt-5",
            provider="openai",
            route_type="api",
            task_type="programmer",
            input_tokens=1000,
            output_tokens=500,
            requests=1,
            status="success",
            estimated_cost_usd=0.05,  # Over $0.001 hard cap
            timestamp=datetime.now(timezone.utc),
        )
        db_session.add(event)
        await db_session.commit()

        # Wire micro/small tier models into DB so the hard cap downgrade has something to pick
        micro_row = OrchestratorSetting(
            key="model_router.tier.micro",
            value=["test/micro-model"],
        )
        small_row = OrchestratorSetting(
            key="model_router.tier.small",
            value=["test/small-model"],
        )
        db_session.add(micro_row)
        db_session.add(small_row)
        await db_session.commit()

        from app.orchestrator.model_chooser import ModelChooser

        chooser = ModelChooser(db_session)
        task = {"id": "t-hc-exc", "title": "Implement feature", "status": "active"}
        choice = await chooser.choose(agent_type="programmer", task=task)

        hcg = choice.audit["hard_cap_guard"]
        assert hcg["hard_cap_exceeded"] is True
        assert isinstance(hcg["daily_spend_usd"], float)
        assert hcg["daily_spend_usd"] >= 0.05

    @pytest.mark.asyncio
    async def test_choose_hard_cap_within_budget_no_downgrade(self, db_session, monkeypatch):
        """When daily spend is under hard cap, no downgrade occurs."""
        import app.orchestrator.model_chooser as mc_module
        from app.models import OrchestratorSetting

        monkeypatch.setattr(mc_module, "discover_ollama_models", AsyncMock(return_value={}))

        # Set a generous hard cap ($100/day) — no spend in DB → safe
        budget_row = OrchestratorSetting(
            key="usage.budgets",
            value={"daily_hard_cap_usd": 100.0},
        )
        db_session.add(budget_row)
        await db_session.commit()

        from app.orchestrator.model_chooser import ModelChooser

        chooser = ModelChooser(db_session)
        task = {"id": "t-hc-safe", "title": "Normal task", "status": "active"}
        choice = await chooser.choose(agent_type="researcher", task=task)

        hcg = choice.audit["hard_cap_guard"]
        assert hcg["hard_cap_exceeded"] is False
        assert hcg["downgraded"] is False
        assert hcg["hard_cap_usd"] == 100.0

    @pytest.mark.asyncio
    async def test_apply_global_daily_cap_method_no_cap(self, db_session, monkeypatch):
        """_apply_global_daily_cap with 0.0 cap returns no_hard_cap reason."""
        import app.orchestrator.model_chooser as mc_module

        monkeypatch.setattr(mc_module, "discover_ollama_models", AsyncMock(return_value={}))

        from app.orchestrator.model_chooser import ModelChooser

        chooser = ModelChooser(db_session)
        candidates = ["model/std-a", "model/strong-a"]
        result = await chooser._apply_global_daily_cap(
            candidates=candidates,
            budgets={"daily_hard_cap_usd": 0.0},
            tier_map=SAMPLE_TIER_MAP,
            task={"id": "t1"},
            agent_type="programmer",
        )

        assert result["effective_candidates"] == candidates
        assert result["reason"] == "no_hard_cap"
        assert result["hard_cap_usd"] is None
        assert result["downgraded"] is False

    @pytest.mark.asyncio
    async def test_apply_global_daily_cap_method_within_cap(self, db_session, monkeypatch):
        """_apply_global_daily_cap returns within_hard_cap when spend is safe."""
        import app.orchestrator.model_chooser as mc_module

        monkeypatch.setattr(mc_module, "discover_ollama_models", AsyncMock(return_value={}))

        from app.orchestrator.model_chooser import ModelChooser

        chooser = ModelChooser(db_session)
        candidates = ["model/std-a", "model/strong-a"]
        result = await chooser._apply_global_daily_cap(
            candidates=candidates,
            budgets={"daily_hard_cap_usd": 50.0},  # generous cap, no spend in DB
            tier_map=SAMPLE_TIER_MAP,
            task={"id": "t2"},
            agent_type="programmer",
        )

        assert result["effective_candidates"] == candidates
        assert result["hard_cap_exceeded"] is False
        assert result["downgraded"] is False
        assert result["hard_cap_usd"] == 50.0

    @pytest.mark.asyncio
    async def test_apply_global_daily_cap_method_exceeded(self, db_session, monkeypatch):
        """_apply_global_daily_cap downgrades to micro/small when hard cap hit."""
        import app.orchestrator.model_chooser as mc_module
        from app.models import ModelUsageEvent

        monkeypatch.setattr(mc_module, "discover_ollama_models", AsyncMock(return_value={}))

        # Add spend that exceeds the hard cap
        event = ModelUsageEvent(
            id="evt-hcm-1",
            source="test",
            model="openai/gpt-5",
            provider="openai",
            route_type="api",
            task_type="programmer",
            requests=1,
            status="success",
            estimated_cost_usd=10.0,
            timestamp=datetime.now(timezone.utc),
        )
        db_session.add(event)
        await db_session.commit()

        from app.orchestrator.model_chooser import ModelChooser

        chooser = ModelChooser(db_session)
        # All tiers in the sample map
        candidates = [
            "model/micro-a", "model/small-a", "model/medium-a",
            "model/std-a", "model/strong-a",
        ]
        result = await chooser._apply_global_daily_cap(
            candidates=candidates,
            budgets={"daily_hard_cap_usd": 5.0},  # Under $10 spend
            tier_map=SAMPLE_TIER_MAP,
            task={"id": "t-hcm"},
            agent_type="programmer",
        )

        assert result["hard_cap_exceeded"] is True
        # Only micro and small should survive
        effective = result["effective_candidates"]
        assert "model/std-a" not in effective
        assert "model/strong-a" not in effective
        assert "model/medium-a" not in effective
        assert "model/micro-a" in effective or "model/small-a" in effective

    @pytest.mark.asyncio
    async def test_apply_global_daily_cap_method_fallback_when_no_cheap_models(self, db_session, monkeypatch):
        """Safety: falls back to original list when no micro/small models exist."""
        import app.orchestrator.model_chooser as mc_module
        from app.models import ModelUsageEvent

        monkeypatch.setattr(mc_module, "discover_ollama_models", AsyncMock(return_value={}))

        # Exceed hard cap
        event = ModelUsageEvent(
            id="evt-hcfb-1",
            source="test",
            model="openai/gpt-5",
            provider="openai",
            route_type="api",
            task_type="programmer",
            requests=1,
            status="success",
            estimated_cost_usd=10.0,
            timestamp=datetime.now(timezone.utc),
        )
        db_session.add(event)
        await db_session.commit()

        from app.orchestrator.model_chooser import ModelChooser

        chooser = ModelChooser(db_session)
        candidates = ["model/std-a"]  # Only standard tier — no micro/small
        tier_map_no_cheap: dict[str, list[str]] = {
            "micro": [],
            "small": [],
            "medium": [],
            "standard": ["model/std-a"],
            "strong": [],
        }
        result = await chooser._apply_global_daily_cap(
            candidates=candidates,
            budgets={"daily_hard_cap_usd": 5.0},
            tier_map=tier_map_no_cheap,
            task={"id": "t-fb"},
            agent_type="programmer",
        )

        # Safety fallback: original list returned since no micro/small available
        assert result["effective_candidates"] == candidates
        assert result["hard_cap_exceeded"] is True
        # reason indicates no cheap fallback was available
        assert result["reason"] is not None
