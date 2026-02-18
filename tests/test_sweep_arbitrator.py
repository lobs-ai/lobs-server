import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import AgentInitiative, AgentReflection, InboxItem, OrchestratorSetting, Project
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
