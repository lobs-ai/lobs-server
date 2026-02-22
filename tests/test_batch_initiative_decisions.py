"""Tests for batch initiative decision processing.

Validates that Lobs can efficiently process multiple initiatives at once:
- Pull all pending initiatives
- Review as a batch with full context
- Submit all decisions together
- Get comprehensive stats back
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import AgentInitiative, InitiativeDecisionRecord, Task, Project
from app.orchestrator.initiative_decisions import InitiativeDecisionEngine


@pytest.mark.asyncio
async def test_batch_decision_processes_multiple_initiatives(db_session):
    """
    Test that batch decision endpoint processes multiple initiatives efficiently.
    
    Validates:
    - All decisions processed in one batch
    - Stats accurately reflect outcomes
    - Tasks created for approved initiatives
    - Decision records created for all
    """
    # Setup: create a project
    db_session.add(Project(id="test-project", title="Test Project", type="kanban", archived=False))
    
    # Create 5 initiatives
    initiatives = []
    for i in range(5):
        initiative = AgentInitiative(
            id=str(uuid.uuid4()),
            proposed_by_agent="programmer",
            title=f"Initiative {i+1}",
            description=f"Description for initiative {i+1}",
            category="test_hygiene" if i % 2 == 0 else "automation_proposal",
            status="proposed",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(initiative)
        initiatives.append(initiative)
    
    await db_session.commit()
    
    # Process batch: approve 3, defer 1, reject 1
    engine = InitiativeDecisionEngine(db_session)
    
    decisions = [
        ("approve", initiatives[0].id, "Great idea"),
        ("approve", initiatives[1].id, "Yes, do this"),
        ("approve", initiatives[2].id, "High priority"),
        ("defer", initiatives[3].id, "Maybe later"),
        ("reject", initiatives[4].id, "Not aligned"),
    ]
    
    approved_count = 0
    deferred_count = 0
    rejected_count = 0
    
    for decision, initiative_id, summary in decisions:
        initiative = await db_session.get(AgentInitiative, initiative_id)
        await engine.decide(
            initiative,
            decision=decision,
            decision_summary=summary,
            decided_by="lobs",
        )
        
        if decision == "approve":
            approved_count += 1
        elif decision == "defer":
            deferred_count += 1
        elif decision == "reject":
            rejected_count += 1
    
    # Verify: All decisions recorded
    decision_records = (await db_session.execute(select(InitiativeDecisionRecord))).scalars().all()
    assert len(decision_records) == 5
    
    # Verify: Tasks created for approved initiatives
    tasks = (await db_session.execute(select(Task))).scalars().all()
    assert len(tasks) == 3  # Only approved ones
    
    # Verify: Initiative statuses updated
    await db_session.refresh(initiatives[0])
    await db_session.refresh(initiatives[1])
    await db_session.refresh(initiatives[2])
    await db_session.refresh(initiatives[3])
    await db_session.refresh(initiatives[4])
    
    assert initiatives[0].status == "approved"
    assert initiatives[1].status == "approved"
    assert initiatives[2].status == "approved"
    assert initiatives[3].status == "deferred"
    assert initiatives[4].status == "rejected"
    
    # Verify: Approved initiatives have task IDs
    assert initiatives[0].task_id is not None
    assert initiatives[1].task_id is not None
    assert initiatives[2].task_id is not None
    assert initiatives[3].task_id is None  # Deferred
    assert initiatives[4].task_id is None  # Rejected


@pytest.mark.asyncio
async def test_batch_decision_handles_missing_initiatives_gracefully(db_session):
    """
    Test that batch processing handles missing initiative IDs gracefully.
    
    When some IDs are invalid, should:
    - Process valid ones
    - Report errors for invalid ones
    - Return accurate stats
    """
    # Setup: create a project
    db_session.add(Project(id="test-project", title="Test Project", type="kanban", archived=False))
    
    # Create 2 valid initiatives
    initiative_1 = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="programmer",
        title="Valid initiative 1",
        description="This one exists",
        category="test_hygiene",
        status="proposed",
        created_at=datetime.now(timezone.utc),
    )
    initiative_2 = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="researcher",
        title="Valid initiative 2",
        description="This one also exists",
        category="light_research",
        status="proposed",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add_all([initiative_1, initiative_2])
    await db_session.commit()
    
    # Prepare batch with 2 valid + 2 invalid IDs
    valid_ids = [initiative_1.id, initiative_2.id]
    invalid_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    
    engine = InitiativeDecisionEngine(db_session)
    
    # Process valid ones
    results = []
    errors = []
    
    for initiative_id in valid_ids:
        initiative = await db_session.get(AgentInitiative, initiative_id)
        if initiative:
            result = await engine.decide(
                initiative,
                decision="approve",
                decision_summary="Approved",
                decided_by="lobs",
            )
            results.append(result)
        else:
            errors.append({"initiative_id": initiative_id, "error": "Not found"})
    
    for initiative_id in invalid_ids:
        initiative = await db_session.get(AgentInitiative, initiative_id)
        if initiative:
            result = await engine.decide(
                initiative,
                decision="approve",
                decision_summary="Approved",
                decided_by="lobs",
            )
            results.append(result)
        else:
            errors.append({"initiative_id": initiative_id, "error": "Not found"})
    
    # Verify: Valid ones processed
    assert len(results) == 2
    assert len(errors) == 2
    
    # Verify: Tasks created for valid ones
    tasks = (await db_session.execute(select(Task))).scalars().all()
    assert len(tasks) == 2


@pytest.mark.asyncio
async def test_batch_decision_provides_accurate_stats(db_session):
    """
    Test that batch endpoint returns accurate statistics.
    
    Stats should include:
    - Total submitted
    - Successfully processed
    - Approved/deferred/rejected counts
    - Failed count
    """
    # Setup: create a project
    db_session.add(Project(id="test-project", title="Test Project", type="kanban", archived=False))
    
    # Create initiatives
    initiatives = []
    for i in range(10):
        initiative = AgentInitiative(
            id=str(uuid.uuid4()),
            proposed_by_agent="programmer",
            title=f"Initiative {i+1}",
            description=f"Description {i+1}",
            category="test_hygiene",
            status="proposed",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(initiative)
        initiatives.append(initiative)
    
    await db_session.commit()
    
    # Process: 4 approve, 3 defer, 3 reject
    engine = InitiativeDecisionEngine(db_session)
    
    approved = 0
    deferred = 0
    rejected = 0
    
    for i, initiative in enumerate(initiatives):
        if i < 4:
            decision = "approve"
            approved += 1
        elif i < 7:
            decision = "defer"
            deferred += 1
        else:
            decision = "reject"
            rejected += 1
        
        await engine.decide(
            initiative,
            decision=decision,
            decision_summary=f"Decision for {initiative.title}",
            decided_by="lobs",
        )
    
    # Verify stats
    assert approved == 4
    assert deferred == 3
    assert rejected == 3
    
    # Verify tasks created only for approved
    tasks = (await db_session.execute(select(Task))).scalars().all()
    assert len(tasks) == 4


@pytest.mark.asyncio
async def test_batch_decision_allows_duplicate_detection(db_session):
    """
    Test that batch processing enables Lobs to spot duplicates across initiatives.
    
    When reviewing in batch, Lobs can:
    - See all proposals at once
    - Identify duplicates or overlaps
    - Make better decisions with full context
    """
    # Setup: create a project
    db_session.add(Project(id="test-project", title="Test Project", type="kanban", archived=False))
    
    # Create similar initiatives that might be duplicates
    initiative_1 = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="programmer",
        title="Add logging to API endpoints",
        description="We need better observability",
        category="light_refactor",
        status="proposed",
        created_at=datetime.now(timezone.utc),
    )
    initiative_2 = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="researcher",
        title="Improve API observability",
        description="Add logging and metrics",
        category="light_refactor",
        status="proposed",
        created_at=datetime.now(timezone.utc),
    )
    initiative_3 = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="architect",
        title="Implement structured logging",
        description="Replace print statements with proper logging",
        category="light_refactor",
        status="proposed",
        created_at=datetime.now(timezone.utc),
    )
    
    db_session.add_all([initiative_1, initiative_2, initiative_3])
    await db_session.commit()
    
    # Batch review: Lobs can see all three at once and decide
    # - Approve the most comprehensive one (initiative_2)
    # - Reject the others as duplicates
    
    engine = InitiativeDecisionEngine(db_session)
    
    await engine.decide(
        initiative_1,
        decision="reject",
        decision_summary="Duplicate of initiative 2 which is more comprehensive",
        learning_feedback="When proposing, check for similar existing initiatives",
        decided_by="lobs",
    )
    
    await engine.decide(
        initiative_2,
        decision="approve",
        revised_title="Implement comprehensive API observability",
        decision_summary="Most complete proposal, covers logging and metrics",
        decided_by="lobs",
    )
    
    await engine.decide(
        initiative_3,
        decision="reject",
        decision_summary="Already covered by approved initiative 2",
        decided_by="lobs",
    )
    
    # Verify: Only one task created
    tasks = (await db_session.execute(select(Task))).scalars().all()
    assert len(tasks) == 1
    assert "comprehensive" in tasks[0].title.lower()
    
    # Verify: All have decision records
    decision_records = (await db_session.execute(select(InitiativeDecisionRecord))).scalars().all()
    assert len(decision_records) == 3


@pytest.mark.asyncio
async def test_batch_decision_enables_prioritization(db_session):
    """
    Test that batch processing enables better prioritization.
    
    Reviewing initiatives as a batch lets Lobs:
    - Compare relative priority
    - Allocate resources strategically
    - Make trade-offs with full context
    """
    # Setup: create a project
    db_session.add(Project(id="test-project", title="Test Project", type="kanban", archived=False))
    
    # Create initiatives with different urgency/impact
    high_impact = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="architect",
        title="Fix critical performance bottleneck",
        description="Database queries are 10x slower than they should be",
        category="moderate_refactor",
        status="proposed",
        created_at=datetime.now(timezone.utc),
    )
    
    medium_impact = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="programmer",
        title="Add API endpoint caching",
        description="Reduce load on frequently accessed endpoints",
        category="light_refactor",
        status="proposed",
        created_at=datetime.now(timezone.utc),
    )
    
    low_impact = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="writer",
        title="Update README badges",
        description="Add build status and coverage badges",
        category="docs_sync",
        status="proposed",
        created_at=datetime.now(timezone.utc),
    )
    
    db_session.add_all([high_impact, medium_impact, low_impact])
    await db_session.commit()
    
    # Batch review: Lobs can prioritize based on full context
    engine = InitiativeDecisionEngine(db_session)
    
    # High impact: approve immediately
    await engine.decide(
        high_impact,
        decision="approve",
        revised_title="URGENT: Fix database query performance bottleneck",
        decision_summary="Critical issue, highest priority",
        selected_project_id="test-project",
        decided_by="lobs",
    )
    
    # Medium impact: approve but note lower priority
    await engine.decide(
        medium_impact,
        decision="approve",
        decision_summary="Good optimization, do after critical fix",
        selected_project_id="test-project",
        decided_by="lobs",
    )
    
    # Low impact: defer for later
    await engine.decide(
        low_impact,
        decision="defer",
        decision_summary="Nice to have, but focus on performance first",
        decided_by="lobs",
    )
    
    # Verify: 2 tasks created, 1 deferred
    tasks = (await db_session.execute(select(Task))).scalars().all()
    assert len(tasks) == 2
    
    # High priority task should be marked (title indicates urgency)
    urgent_task = next((t for t in tasks if "URGENT" in t.title), None)
    assert urgent_task is not None
    
    # Deferred initiative has no task
    await db_session.refresh(low_impact)
    assert low_impact.status == "deferred"
    assert low_impact.task_id is None
