import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import AgentInitiative, Project, OrchestratorSetting, Task
from app.orchestrator.sweep_arbitrator import SweepArbitrator


@pytest.mark.asyncio
async def test_sweep_arbitrator_auto_approve_creates_task(db_session):
    db_session.add(
        Project(
            id="lobs-server",
            title="Lobs Server",
            type="kanban",
            archived=False,
        )
    )
    db_session.add(
        AgentInitiative(
            id=str(uuid.uuid4()),
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

    tasks = (await db_session.execute(select(Task))).scalars().all()
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_sweep_arbitrator_respects_daily_budget(db_session):
    db_session.add(
        Project(
            id="lobs-server",
            title="Lobs Server",
            type="kanban",
            archived=False,
        )
    )
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
    assert result["deferred"] == 1

    row = await db_session.get(AgentInitiative, candidate_id)
    assert row is not None
    assert row.status == "deferred"
