import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import AgentInitiative, AgentReflection, InboxItem, InitiativeDecisionRecord, OrchestratorSetting, Project, SystemSweep
from app.orchestrator.sweep_arbitrator import SweepArbitrator


async def _ensure_project(db_session):
    project = await db_session.get(Project, "lobs-server")
    if project is None:
        db_session.add(Project(id="lobs-server", title="Lobs Server", type="software"))
        await db_session.commit()


@pytest.mark.asyncio
async def test_sweep_arbitrator_auto_approves_medium_or_lower_initiative(db_session):
    await _ensure_project(db_session)
    initiative_id = str(uuid.uuid4())
    db_session.add(
        AgentInitiative(
            id=initiative_id,
            proposed_by_agent="writer",
            owner_agent="writer",
            title="Sync API docs for orchestrator endpoints",
            description="Bring docs in sync with latest endpoints",
            category="docs_sync",
            risk_tier="A",
            status="proposed",
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    result = await SweepArbitrator(db_session).run_once()
    assert result["approved"] == 1

    row = await db_session.get(AgentInitiative, initiative_id)
    assert row is not None
    assert row.status == "approved"
    assert row.policy_lane == "auto_allowed"
    assert row.task_id is not None

    inbox_rows = (await db_session.execute(select(InboxItem))).scalars().all()
    assert len(inbox_rows) == 0


@pytest.mark.asyncio
async def test_sweep_arbitrator_marks_budget_pressure_in_recommendation(db_session):
    await _ensure_project(db_session)
    db_session.add(
        OrchestratorSetting(
            key="autonomy_budget.daily",
            value={"writer": 0},
        )
    )

    candidate_id = str(uuid.uuid4())
    db_session.add(
        AgentInitiative(
            id=candidate_id,
            proposed_by_agent="writer",
            owner_agent="writer",
            title="Second docs initiative",
            description="should defer",
            category="docs_sync",
            risk_tier="A",
            status="proposed",
            created_at=datetime.now(timezone.utc),
        )
    )

    await db_session.commit()

    result = await SweepArbitrator(db_session).run_once()
    assert (result["deferred"] + result["lobs_review"]) == 1

    row = await db_session.get(AgentInitiative, candidate_id)
    assert row is not None
    assert row.status in {"deferred", "lobs_review"}
    detail = " ".join([(row.decision_summary or ""), (row.rationale or "")]).lower()
    assert "defer" in detail or "budget" in detail


@pytest.mark.asyncio
async def test_sweep_arbitrator_waits_for_all_agents_before_processing(db_session):
    await _ensure_project(db_session)

    db_session.add(
        AgentReflection(
            id=str(uuid.uuid4()),
            agent_type="writer",
            reflection_type="strategic",
            status="completed",
            created_at=datetime.now(timezone.utc),
        )
    )

    candidate_id = str(uuid.uuid4())
    db_session.add(
        AgentInitiative(
            id=candidate_id,
            proposed_by_agent="writer",
            owner_agent="writer",
            title="Small docs cleanup",
            description="Update stale endpoint docs",
            category="docs_sync",
            risk_tier="A",
            status="proposed",
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    arbitrator = SweepArbitrator(db_session)
    arbitrator.registry.available_types = lambda: ["writer", "researcher"]

    result = await arbitrator.run_once()

    assert result["approved"] == 0
    assert "researcher" in result.get("waiting_for_agents", [])

    row = await db_session.get(AgentInitiative, candidate_id)
    assert row is not None
    assert row.status == "proposed"


@pytest.mark.asyncio
async def test_sweep_arbitrator_flags_contradictions_and_tracks_capability_gaps(db_session):
    await _ensure_project(db_session)

    first_reflection = str(uuid.uuid4())
    second_reflection = str(uuid.uuid4())
    db_session.add_all([
        AgentReflection(
            id=first_reflection,
            agent_type="writer",
            reflection_type="strategic",
            status="completed",
            created_at=datetime.now(timezone.utc),
        ),
        AgentReflection(
            id=second_reflection,
            agent_type="writer",
            reflection_type="strategic",
            status="completed",
            created_at=datetime.now(timezone.utc),
        ),
    ])

    i1 = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="writer",
        source_reflection_id=first_reflection,
        title="Increase API timeout for batch sync",
        description="Increase timeout aggressively to avoid failures in sync",
        category="sync_policy",
        status="proposed",
    )
    i2 = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="writer",
        source_reflection_id=second_reflection,
        title="Reduce API timeout for batch sync",
        description="Reduce timeout to keep UI responsive during sync",
        category="sync_policy",
        status="proposed",
    )
    db_session.add_all([i1, i2])
    await db_session.commit()

    result = await SweepArbitrator(db_session).run_once()
    assert result["deferred"] >= 2
    assert result["contradictions"] >= 2

    decision_rows = (await db_session.execute(InitiativeDecisionRecord.__table__.select())).all()
    assert len(decision_rows) == 2
    assert all(row.capability_gap for row in decision_rows)
    assert all(row.contradiction_with_ids for row in decision_rows)

    sweep = (await db_session.execute(SystemSweep.__table__.select())).first()
    assert sweep is not None
    assert sweep.summary["capability_gaps"] >= 2
    assert sweep.decisions["contradiction_map"]


@pytest.mark.asyncio
async def test_sweep_arbitrator_routes_review_required_lane_to_lobs_queue(db_session):
    await _ensure_project(db_session)
    initiative_id = str(uuid.uuid4())
    db_session.add(
        AgentInitiative(
            id=initiative_id,
            proposed_by_agent="researcher",
            owner_agent="researcher",
            title="Automation proposal for recurring data extraction",
            description="Propose a new automation to scrape and normalize weekly external datasets",
            category="automation_proposal",
            risk_tier="B",
            status="proposed",
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    result = await SweepArbitrator(db_session).run_once()
    assert result["lobs_review"] == 1

    row = await db_session.get(AgentInitiative, initiative_id)
    assert row is not None
    assert row.status == "lobs_review"
    assert row.policy_lane == "review_required"
    assert "review required" in (row.rationale or "").lower()

    inbox_rows = (await db_session.execute(select(InboxItem))).scalars().all()
    assert len(inbox_rows) == 1
    assert "policy" in (inbox_rows[0].content or "").lower()


@pytest.mark.asyncio
async def test_sweep_arbitrator_rejects_blocked_lane(db_session):
    await _ensure_project(db_session)
    initiative_id = str(uuid.uuid4())
    db_session.add(
        AgentInitiative(
            id=initiative_id,
            proposed_by_agent="programmer",
            owner_agent="programmer",
            title="Cross-project architecture rewrite",
            description="Perform a broad architecture_change touching multiple services",
            category="architecture_change",
            risk_tier="C",
            status="proposed",
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    result = await SweepArbitrator(db_session).run_once()
    assert result["rejected"] == 1

    row = await db_session.get(AgentInitiative, initiative_id)
    assert row is not None
    assert row.status == "rejected"
    assert row.policy_lane == "blocked"
    assert "blocked" in (row.rationale or "").lower()
