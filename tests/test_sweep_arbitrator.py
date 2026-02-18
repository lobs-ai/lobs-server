import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import AgentInitiative, InboxItem, OrchestratorSetting
from app.orchestrator.sweep_arbitrator import SweepArbitrator


@pytest.mark.asyncio
async def test_sweep_arbitrator_routes_initiative_to_lobs_review(db_session):
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
    assert result["lobs_review"] == 1

    row = await db_session.get(AgentInitiative, initiative_id)
    assert row is not None
    assert row.status == "lobs_review"
    assert "Recommendation=" in (row.rationale or "")

    inbox_rows = (await db_session.execute(select(InboxItem))).scalars().all()
    assert len(inbox_rows) == 1
    assert "Lobs decision" in inbox_rows[0].title


@pytest.mark.asyncio
async def test_sweep_arbitrator_marks_budget_pressure_in_recommendation(db_session):
    db_session.add(
        OrchestratorSetting(
            key="autonomy_budget.daily",
            value={"writer": 1},
        )
    )

    # Existing approved initiative consumes today's budget.
    db_session.add(
        AgentInitiative(
            id=str(uuid.uuid4()),
            proposed_by_agent="writer",
            owner_agent="writer",
            title="Already approved item",
            description="existing",
            category="docs_sync",
            risk_tier="A",
            status="approved",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
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
    assert result["lobs_review"] == 1

    row = await db_session.get(AgentInitiative, candidate_id)
    assert row is not None
    assert row.status == "lobs_review"
    assert "Recommendation=defer" in (row.rationale or "")
