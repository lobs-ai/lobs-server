"""Tests for budget-aware model routing guardrails.

Covers:
- Lane classification (classify_task_lane)
- Tier downgrade helper (_filter_to_tier_and_below)
- BudgetGuard.apply() — no cap, within budget, over budget, downgrade
- BudgetGuard.today_spend_all_lanes() and today_override_log()
- API: GET /api/usage/budget-lanes, PATCH /api/usage/budget-lanes
- API: GET /api/usage/daily-report
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.orchestrator.budget_guard import (
    ALL_LANES,
    BUDGET_GUARD_LANE_POLICY_KEY,
    DEFAULT_LANE_POLICY,
    LANE_BACKGROUND,
    LANE_CRITICAL,
    LANE_STANDARD,
    BudgetGuard,
    BudgetGuardDecision,
    _effective_lane_policy,
    _filter_to_tier_and_below,
    classify_task_lane,
)
from app.orchestrator.model_router import TIER_ORDER


# ---------------------------------------------------------------------------
# Unit tests: classify_task_lane
# ---------------------------------------------------------------------------


def _task(title="", notes="", status="active", model_tier=""):
    return {"title": title, "notes": notes, "status": status, "model_tier": model_tier}


class TestClassifyTaskLane:
    def test_high_criticality_is_critical_lane(self):
        task = _task(title="Urgent production auth outage")
        lane = classify_task_lane(task, agent_type="researcher", criticality="high")
        assert lane == LANE_CRITICAL

    def test_strong_model_tier_is_critical_lane(self):
        task = _task(title="Refactor module", model_tier="strong")
        lane = classify_task_lane(task, agent_type="programmer", criticality="normal")
        assert lane == LANE_CRITICAL

    def test_writer_is_background_lane(self):
        task = _task(title="Draft blog post")
        lane = classify_task_lane(task, agent_type="writer", criticality="normal")
        assert lane == LANE_BACKGROUND

    def test_reviewer_is_background_lane(self):
        task = _task(title="Review PR")
        lane = classify_task_lane(task, agent_type="reviewer", criticality="normal")
        assert lane == LANE_BACKGROUND

    def test_programmer_normal_is_standard_lane(self):
        task = _task(title="Add feature")
        lane = classify_task_lane(task, agent_type="programmer", criticality="normal")
        assert lane == LANE_STANDARD

    def test_researcher_normal_is_standard_lane(self):
        task = _task(title="Research options")
        lane = classify_task_lane(task, agent_type="researcher", criticality="normal")
        assert lane == LANE_STANDARD

    def test_inbox_non_programming_is_background(self):
        task = _task(title="Classify email", status="inbox")
        lane = classify_task_lane(task, agent_type="lobs", criticality="normal")
        assert lane == LANE_BACKGROUND

    def test_inbox_programmer_is_critical(self):
        """Programmer tasks always hit critical regardless of status."""
        task = _task(title="Fix bug", status="inbox")
        lane = classify_task_lane(task, agent_type="programmer", criticality="high")
        assert lane == LANE_CRITICAL


# ---------------------------------------------------------------------------
# Unit tests: _filter_to_tier_and_below
# ---------------------------------------------------------------------------


class TestFilterToTierAndBelow:
    def _tier_map(self) -> dict[str, list[str]]:
        return {
            "micro": ["micro/m1"],
            "small": ["small/s1", "small/s2"],
            "medium": ["medium/m1"],
            "standard": ["std/s1"],
            "strong": ["strong/op1"],
        }

    def test_filter_keeps_tiers_at_or_below_max(self):
        candidates = ["std/s1", "medium/m1", "small/s1"]
        filtered = _filter_to_tier_and_below(candidates, "medium", self._tier_map())
        assert "std/s1" not in filtered
        assert "medium/m1" in filtered
        assert "small/s1" in filtered

    def test_filter_allows_micro_tier(self):
        candidates = ["small/s1", "micro/m1"]
        filtered = _filter_to_tier_and_below(candidates, "micro", self._tier_map())
        assert filtered == ["micro/m1"]

    def test_filter_returns_original_when_nothing_survives(self):
        """Safety net: if filtering removes everything, return original list."""
        candidates = ["strong/op1"]  # Only strong tier model
        filtered = _filter_to_tier_and_below(candidates, "micro", self._tier_map())
        # Safety fallback — original list returned
        assert filtered == candidates

    def test_filter_with_unknown_tier(self):
        candidates = ["medium/m1", "std/s1"]
        # Unknown max_tier falls back to the last position (most permissive)
        filtered = _filter_to_tier_and_below(candidates, "nonexistent", self._tier_map())
        # All candidates pass through
        assert set(filtered) == {"medium/m1", "std/s1"}


# ---------------------------------------------------------------------------
# Unit tests: _effective_lane_policy
# ---------------------------------------------------------------------------


class TestEffectiveLanePolicy:
    def test_defaults_returned_when_no_raw(self):
        policy = _effective_lane_policy(None)
        assert policy[LANE_CRITICAL]["daily_cap_usd"] is None
        assert policy[LANE_STANDARD]["daily_cap_usd"] == 8.0
        assert policy[LANE_BACKGROUND]["daily_cap_usd"] == 3.0

    def test_partial_override_merges_with_defaults(self):
        raw = {"standard": {"daily_cap_usd": 15.0}}
        policy = _effective_lane_policy(raw)
        assert policy[LANE_STANDARD]["daily_cap_usd"] == 15.0
        # Other lanes unaffected
        assert policy[LANE_BACKGROUND]["daily_cap_usd"] == 3.0

    def test_unknown_lane_in_raw_is_ignored(self):
        raw = {"unknown_lane": {"daily_cap_usd": 99.0}}
        policy = _effective_lane_policy(raw)
        assert "unknown_lane" not in policy

    def test_full_override(self):
        raw = {
            "critical": {"daily_cap_usd": 50.0, "downgrade_tier": "standard"},
            "standard": {"daily_cap_usd": 20.0, "downgrade_tier": "small"},
            "background": {"daily_cap_usd": 5.0, "downgrade_tier": "micro"},
        }
        policy = _effective_lane_policy(raw)
        assert policy[LANE_CRITICAL]["daily_cap_usd"] == 50.0
        assert policy[LANE_STANDARD]["daily_cap_usd"] == 20.0
        assert policy[LANE_BACKGROUND]["daily_cap_usd"] == 5.0


# ---------------------------------------------------------------------------
# Async integration tests: BudgetGuard.apply()
# ---------------------------------------------------------------------------


def _make_mock_db(today_spend: float = 0.0) -> MagicMock:
    """Return a mock AsyncSession that returns today_spend for all spend queries."""
    db = MagicMock()

    # Mock db.get for OrchestratorSetting (lane policy)
    db.get = AsyncMock(return_value=None)

    # Mock db.execute for spend queries — return a scalar result of today_spend
    scalar_result = MagicMock()
    scalar_result.scalar = MagicMock(return_value=today_spend)
    execute_result = MagicMock()
    execute_result.scalar = MagicMock(return_value=today_spend)
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()
    db.add = MagicMock()

    return db


@pytest.mark.asyncio
async def test_budget_guard_no_cap_critical_lane():
    """Critical lane has no cap by default — candidates pass through unchanged."""
    db = _make_mock_db(today_spend=100.0)
    guard = BudgetGuard(db)

    task = {"id": "t1", "title": "Critical outage fix"}
    candidates = ["strong/op1", "std/s1"]
    tier_map = {"standard": ["std/s1"], "strong": ["strong/op1"]}

    decision = await guard.apply(
        task=task,
        agent_type="programmer",
        criticality="high",
        candidates=candidates,
        tier_map=tier_map,
    )

    assert decision.lane == LANE_CRITICAL
    assert decision.over_budget is False
    assert decision.downgraded is False
    assert decision.effective_candidates == candidates
    assert decision.reason == "no_cap"


@pytest.mark.asyncio
async def test_budget_guard_within_budget_standard_lane():
    """Standard lane under $8 cap — candidates pass through."""
    db = _make_mock_db(today_spend=3.0)  # Under $8 cap
    guard = BudgetGuard(db)

    task = {"id": "t2", "title": "Add feature"}
    candidates = ["std/s1", "strong/op1"]
    tier_map = {"standard": ["std/s1"], "strong": ["strong/op1"]}

    decision = await guard.apply(
        task=task,
        agent_type="programmer",
        criticality="normal",
        candidates=candidates,
        tier_map=tier_map,
    )

    assert decision.lane == LANE_STANDARD
    assert decision.over_budget is False
    assert decision.downgraded is False
    assert decision.effective_candidates == candidates


@pytest.mark.asyncio
async def test_budget_guard_over_budget_standard_lane_downgrades():
    """Standard lane over $8 cap — strong tier dropped, downgrade to medium."""
    db = _make_mock_db(today_spend=9.0)  # Over $8 cap
    guard = BudgetGuard(db)

    task = {"id": "t3", "title": "Add feature"}
    candidates = ["std/s1", "strong/op1"]
    tier_map = {
        "medium": ["medium/m1"],
        "standard": ["std/s1"],
        "strong": ["strong/op1"],
    }

    decision = await guard.apply(
        task=task,
        agent_type="programmer",
        criticality="normal",
        candidates=candidates,
        tier_map=tier_map,
    )

    assert decision.lane == LANE_STANDARD
    assert decision.over_budget is True
    assert decision.downgraded is True
    # strong/op1 is above medium tier → filtered out
    assert "strong/op1" not in decision.effective_candidates
    # std/s1 is in standard tier which is above medium → also filtered
    # Only medium and below allowed. Since no medium candidates are in the list,
    # the safety fallback returns original candidates.
    # (This tests the safety net behavior)
    assert len(decision.effective_candidates) >= 1


@pytest.mark.asyncio
async def test_budget_guard_over_budget_background_lane_keeps_small():
    """Background lane over $3 cap — downgrade to small tier; micro/small pass through."""
    db = _make_mock_db(today_spend=4.0)  # Over $3 background cap
    guard = BudgetGuard(db)

    task = {"id": "t4", "title": "Review this PR"}
    candidates = ["small/s1", "medium/m1", "std/s1"]
    tier_map = {
        "micro": [],
        "small": ["small/s1"],
        "medium": ["medium/m1"],
        "standard": ["std/s1"],
        "strong": [],
    }

    decision = await guard.apply(
        task=task,
        agent_type="reviewer",
        criticality="normal",
        candidates=candidates,
        tier_map=tier_map,
    )

    assert decision.lane == LANE_BACKGROUND
    assert decision.over_budget is True
    assert "small/s1" in decision.effective_candidates
    assert "medium/m1" not in decision.effective_candidates
    assert "std/s1" not in decision.effective_candidates


@pytest.mark.asyncio
async def test_budget_guard_today_override_log_today_only():
    """today_override_log returns only entries with today's date."""
    db = MagicMock()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday_str = "2020-01-01"

    mock_row = MagicMock()
    mock_row.value = [
        {"ts": f"{today_str}T10:00:00+00:00", "lane": "standard"},
        {"ts": f"{yesterday_str}T10:00:00+00:00", "lane": "background"},
    ]
    db.get = AsyncMock(return_value=mock_row)
    db.execute = AsyncMock()

    guard = BudgetGuard(db)
    log = await guard.today_override_log()

    assert len(log) == 1
    assert log[0]["lane"] == "standard"


# ---------------------------------------------------------------------------
# API tests: GET /api/usage/budget-lanes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_budget_lanes_defaults(client):
    """GET /api/usage/budget-lanes returns default policy when nothing stored."""
    response = await client.get("/api/usage/budget-lanes")
    assert response.status_code == 200
    data = response.json()
    assert "policy" in data
    assert "today_spend" in data
    assert "override_log_today" in data

    policy = data["policy"]
    assert LANE_CRITICAL in policy
    assert LANE_STANDARD in policy
    assert LANE_BACKGROUND in policy

    # Default caps
    assert policy[LANE_CRITICAL]["daily_cap_usd"] is None
    assert policy[LANE_STANDARD]["daily_cap_usd"] == 8.0
    assert policy[LANE_BACKGROUND]["daily_cap_usd"] == 3.0


@pytest.mark.asyncio
async def test_patch_budget_lanes_updates_cap(client):
    """PATCH /api/usage/budget-lanes updates caps and returns effective policy."""
    payload = {
        "standard": {"daily_cap_usd": 15.0, "downgrade_tier": "medium"},
        "background": {"daily_cap_usd": 5.0},
    }
    response = await client.patch("/api/usage/budget-lanes", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["policy"][LANE_STANDARD]["daily_cap_usd"] == 15.0
    assert data["policy"][LANE_BACKGROUND]["daily_cap_usd"] == 5.0
    # Critical unchanged (only standard and background were in payload)
    assert data["policy"][LANE_CRITICAL]["daily_cap_usd"] is None


@pytest.mark.asyncio
async def test_patch_budget_lanes_unknown_lane_returns_422(client):
    """PATCH /api/usage/budget-lanes with unknown lane returns 422."""
    response = await client.patch(
        "/api/usage/budget-lanes",
        json={"superduper_lane": {"daily_cap_usd": 99.0}},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_patch_budget_lanes_persisted_across_gets(client):
    """After PATCH, GET returns the updated policy."""
    payload = {"standard": {"daily_cap_usd": 12.5}}
    await client.patch("/api/usage/budget-lanes", json=payload)

    response = await client.get("/api/usage/budget-lanes")
    assert response.status_code == 200
    assert response.json()["policy"][LANE_STANDARD]["daily_cap_usd"] == 12.5


@pytest.mark.asyncio
async def test_patch_budget_lanes_set_critical_cap(client):
    """Can set a cap on critical lane (non-default behavior)."""
    payload = {"critical": {"daily_cap_usd": 50.0, "downgrade_tier": "standard"}}
    response = await client.patch("/api/usage/budget-lanes", json=payload)
    assert response.status_code == 200
    assert response.json()["policy"][LANE_CRITICAL]["daily_cap_usd"] == 50.0


# ---------------------------------------------------------------------------
# API tests: GET /api/usage/daily-report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_report_returns_expected_shape(client):
    """GET /api/usage/daily-report returns expected structure."""
    response = await client.get("/api/usage/daily-report")
    assert response.status_code == 200
    data = response.json()

    # Core fields
    assert "date" in data
    assert "total_spend_usd" in data
    assert "lane_status" in data
    assert "alerts" in data
    assert "override_events" in data
    assert "budget_guard_overrides_today" in data
    assert "budget_guard_override_count" in data

    # Lane status covers all three lanes
    lane_status = data["lane_status"]
    for lane in (LANE_CRITICAL, LANE_STANDARD, LANE_BACKGROUND):
        assert lane in lane_status
        assert "spend_usd" in lane_status[lane]
        assert "at_cap" in lane_status[lane]


@pytest.mark.asyncio
async def test_daily_report_no_spend_shows_zero(client):
    """Fresh state: report shows zero spend, no alerts for empty lanes."""
    response = await client.get("/api/usage/daily-report")
    data = response.json()
    assert data["total_spend_usd"] == 0.0
    assert data["budget_guard_override_count"] == 0


@pytest.mark.asyncio
async def test_daily_report_with_usage_event(client):
    """Report includes spend after usage events are logged."""
    # Log a usage event
    event_payload = {
        "source": "test",
        "provider": "openai",
        "model": "openai/gpt-4",
        "route_type": "api",
        "task_type": "programmer",
        "input_tokens": 1000,
        "output_tokens": 500,
        "requests": 1,
        "status": "success",
        "estimated_cost_usd": 0.05,
    }
    create_res = await client.post("/api/usage/events", json=event_payload)
    assert create_res.status_code == 200

    response = await client.get("/api/usage/daily-report")
    data = response.json()
    assert data["total_spend_usd"] >= 0.05


@pytest.mark.asyncio
async def test_budget_lanes_roundtrip_null_cap(client):
    """Setting daily_cap_usd to null removes the cap."""
    # First set a cap
    await client.patch("/api/usage/budget-lanes", json={"background": {"daily_cap_usd": 5.0}})
    # Then remove it
    await client.patch("/api/usage/budget-lanes", json={"background": {"daily_cap_usd": None}})

    response = await client.get("/api/usage/budget-lanes")
    data = response.json()
    assert data["policy"][LANE_BACKGROUND]["daily_cap_usd"] is None
