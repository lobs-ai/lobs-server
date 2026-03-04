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


@pytest.mark.asyncio
async def test_decision_engine_reuses_task_for_same_title_and_agent_when_completed(db_session):
    db_session.add(Project(id="lobs-server", title="Lobs Server", type="kanban", archived=False))

    existing_task = Task(
        id=str(uuid.uuid4()),
        title="Investigate flaky orchestrator tests",
        status="completed",
        work_state="completed",
        project_id="lobs-server",
        owner="lobs",
        agent="reviewer",
        notes="done already",
    )
    db_session.add(existing_task)

    initiative = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="researcher",
        title="Investigate flaky orchestrator tests",
        description="Repeat check to ensure dedupe by title+agent works",
        category="light_research",
        status="lobs_review",
        selected_agent="reviewer",
        selected_project_id="lobs-server",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(initiative)
    await db_session.commit()

    result = await InitiativeDecisionEngine(db_session).decide(
        initiative,
        decision="approve",
    )

    assert result["status"] == "approved"
    assert result["task_id"] == existing_task.id

    matching_tasks = (
        await db_session.execute(
            Task.__table__.select().where(Task.title == "Investigate flaky orchestrator tests")
        )
    ).all()
    assert len(matching_tasks) == 1


@pytest.mark.asyncio
async def test_decision_engine_skips_creation_when_artifact_path_exists(db_session, tmp_path):
    db_session.add(Project(id="lobs-server", title="Lobs Server", type="kanban", archived=False))

    artifact = tmp_path / "existing-output.md"
    artifact.write_text("already built")

    initiative = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="writer",
        title="Publish summary dashboard",
        description=f"Create summary. artifact_path: {artifact}",
        category="docs_sync",
        status="lobs_review",
        selected_agent="writer",
        selected_project_id="lobs-server",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(initiative)
    await db_session.commit()

    result = await InitiativeDecisionEngine(db_session).decide(
        initiative,
        decision="approve",
    )

    assert result["status"] == "approved"
    assert result["task_id"] is None

    created = (
        await db_session.execute(
            Task.__table__.select().where(Task.title == "Publish summary dashboard")
        )
    ).all()
    assert len(created) == 0


@pytest.mark.asyncio
async def test_decision_engine_rejects_approve_when_title_missing(db_session):
    db_session.add(Project(id="lobs-server", title="Lobs Server", type="kanban", archived=False))

    initiative = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="researcher",
        title=None,
        description="Has description but no title",
        category="light_research",
        status="lobs_review",
        selected_agent="reviewer",
        selected_project_id="lobs-server",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(initiative)
    await db_session.commit()

    with pytest.raises(ValueError, match="initiative title is required to create a task"):
        await InitiativeDecisionEngine(db_session).decide(
            initiative,
            decision="approve",
        )
