import uuid
from datetime import datetime, timezone

import pytest

from app.models import AgentCapability, AgentInitiative, AgentReflection, InitiativeDecisionRecord, Project, Task
from app.orchestrator.initiative_decisions import InitiativeDecisionEngine


@pytest.mark.asyncio
async def test_decision_engine_approve_converts_to_task_and_feedback(db_session):
    db_session.add(Project(id="lobs-server", title="Lobs Server", type="kanban", archived=False))
    source_reflection_id = str(uuid.uuid4())
    db_session.add(
        AgentReflection(
            id=source_reflection_id,
            agent_type="researcher",
            reflection_type="strategic",
            status="completed",
            created_at=datetime.now(timezone.utc),
        )
    )

    initiative = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="researcher",
        source_reflection_id=source_reflection_id,
        title="Research flaky tests in orchestrator",
        description="Identify top failure clusters and propose fixes",
        category="light_research",
        status="lobs_review",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(initiative)
    await db_session.commit()

    result = await InitiativeDecisionEngine(db_session).decide(
        initiative,
        decision="approve",
        revised_title="Investigate flaky orchestrator tests",
        learning_feedback="Good direction; tighten scope and add measurable success criteria.",
        decision_summary="Approved with narrower scope",
    )

    assert result["status"] == "approved"
    assert result["task_id"] is not None

    task = await db_session.get(Task, result["task_id"])
    assert task is not None
    assert task.title == "Investigate flaky orchestrator tests"
    assert source_reflection_id in (task.notes or "")

    feedback_rows = (await db_session.execute(
        AgentReflection.__table__.select().where(AgentReflection.reflection_type == "initiative_feedback")
    )).all()
    assert len(feedback_rows) == 1

    decision_rows = (await db_session.execute(
        InitiativeDecisionRecord.__table__.select().where(InitiativeDecisionRecord.initiative_id == initiative.id)
    )).all()
    assert len(decision_rows) == 1
    assert decision_rows[0].decision == "approve"


@pytest.mark.asyncio
async def test_decision_engine_suggests_non_proposer_agent(db_session):
    db_session.add(
        AgentCapability(
            agent_type="writer",
            capability="documentation api docs",
            confidence=0.9,
            source="identity",
        )
    )
    db_session.add(
        AgentCapability(
            agent_type="researcher",
            capability="benchmark profiling investigation",
            confidence=0.9,
            source="identity",
        )
    )

    initiative = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="researcher",
        title="Update API documentation for new orchestrator endpoints",
        description="Write clear docs and endpoint examples",
        category="docs_sync",
        status="lobs_review",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(initiative)
    await db_session.commit()

    suggested = await InitiativeDecisionEngine(db_session).suggest_agent(initiative)
    assert suggested == "writer"
