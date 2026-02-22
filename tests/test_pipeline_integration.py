"""End-to-end integration tests for Reflection → Initiative → Task pipeline.

This test suite validates the complete flow:
1. Agent reflection produces proposed_initiatives
2. Initiatives are automatically created as AgentInitiative records
3. Sweep arbitrator filters and processes initiatives
4. Lobs reviews initiatives (via LLM or manual API)
5. Approved initiatives become tasks with full audit trail
6. Scanner picks up initiative-created tasks
7. Next reflection cycle sees results
"""

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import (
    AgentCapability,
    AgentInitiative,
    AgentReflection,
    InitiativeDecisionRecord,
    Project,
    SystemSweep,
    Task,
)
from app.orchestrator.initiative_decisions import InitiativeDecisionEngine
from app.orchestrator.reflection_cycle import ReflectionCycleManager
from app.orchestrator.scanner import Scanner
from app.orchestrator.sweep_arbitrator import SweepArbitrator
from app.orchestrator.worker import WorkerManager


class StubWorkerManager:
    """Stub worker manager that doesn't actually spawn sessions."""

    def __init__(self):
        self.calls = []
        self.sweep_requested = False

    async def _spawn_session(self, **kwargs):
        self.calls.append(kwargs)
        return {"run_id": str(uuid.uuid4())}, None, None


@pytest.mark.asyncio
async def test_end_to_end_reflection_to_task_pipeline(db_session):
    """
    Simplified pipeline test: reflection → initiatives → decision → tasks → scanner.
    
    Tests the core pipeline without the sweep arbitrator complexity.
    """
    # Setup: create a project for tasks
    db_session.add(Project(id="test-project", title="Test Project", type="kanban", archived=False))
    await db_session.commit()

    # Step 1: Create a pending strategic reflection
    reflection_id = str(uuid.uuid4())
    db_session.add(
        AgentReflection(
            id=reflection_id,
            agent_type="programmer",
            reflection_type="strategic",
            status="pending",
            context_packet={"schema_version": "agent-context-packet.v1"},
            window_start=datetime.now(timezone.utc),
            window_end=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    # Step 2: Simulate reflection completion with proposed initiatives
    worker_manager = WorkerManager(db_session)
    
    reflection_output = {
        "inefficiencies_detected": ["Duplicate test setup code"],
        "system_risks": ["No CI timeout protection"],
        "missed_opportunities": ["Could batch API calls"],
        "identity_adjustments": ["Ask for requirements before coding"],
        "proposed_initiatives": [
            {
                "title": "Add test helpers for common setup patterns",
                "description": "Extract repeated test setup into shared helpers to reduce duplication",
                "category": "test_hygiene",
                "estimated_effort": 2,
                "suggested_owner_agent": "programmer",
            },
        ],
        "experience_notes": ["Test duplication makes changes harder"],
    }

    await worker_manager._persist_reflection_output(
        agent_type="programmer",
        reflection_label="reflection-programmer",
        reflection_type="strategic",
        summary=json.dumps(reflection_output),
        succeeded=True,
    )

    # Verify: Reflection output was persisted correctly
    reflection = await db_session.get(AgentReflection, reflection_id)
    assert reflection is not None
    assert reflection.status == "completed"
    assert reflection.inefficiencies == ["Duplicate test setup code"]
    assert len(reflection.identity_adjustments) > 0

    # Verify: Initiatives were created
    initiatives_result = await db_session.execute(
        select(AgentInitiative)
        .where(AgentInitiative.source_reflection_id == reflection_id)
    )
    initiatives = initiatives_result.scalars().all()
    assert len(initiatives) == 1
    
    initiative = initiatives[0]
    assert initiative.title == "Add test helpers for common setup patterns"
    assert initiative.category == "test_hygiene"
    assert initiative.status == "proposed"
    assert initiative.proposed_by_agent == "programmer"
    assert initiative.score == 2.0

    # Step 3: Manually decide on initiative (simulating Lobs review)
    decision_engine = InitiativeDecisionEngine(db_session)
    
    # Approve the initiative
    result = await decision_engine.decide(
        initiative,
        decision="approve",
        revised_title="Create test helpers module",
        decision_summary="Good idea, reduces duplication",
        decided_by="lobs",
    )
    
    assert result["status"] == "approved"
    assert result["task_id"] is not None

    # Verify: Task was created for approved initiative
    task = await db_session.get(Task, result["task_id"])
    assert task is not None
    assert task.title == "Create test helpers module"
    assert task.status == "active"
    assert task.agent == initiative.selected_agent
    assert reflection_id in task.notes
    assert initiative.id in task.notes

    # Verify: Decision record exists
    decisions = (await db_session.execute(
        select(InitiativeDecisionRecord)
        .where(InitiativeDecisionRecord.initiative_id == initiative.id)
    )).scalars().all()
    assert len(decisions) == 1
    assert decisions[0].decision == "approve"

    # Step 4: Verify scanner picks up the task
    scanner = Scanner(db_session)
    eligible_tasks = await scanner.get_eligible_tasks()
    
    task_ids = [t["id"] for t in eligible_tasks]
    assert result["task_id"] in task_ids

    # Verify task details in scanner output
    scanned_task = next(t for t in eligible_tasks if t["id"] == result["task_id"])
    assert scanned_task["status"] == "active"
    assert scanned_task["work_state"] == "not_started"
    assert scanned_task["agent"] == initiative.selected_agent


@pytest.mark.asyncio
async def test_sweep_processes_llm_review_results_with_decision_engine(db_session):
    """
    Test that sweep LLM review results are processed through the decision engine.
    
    This ensures proper audit trail, feedback reflections, and task creation.
    """
    # Setup
    db_session.add(Project(id="test-project", title="Test Project", type="kanban", archived=False))
    
    initiative = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="researcher",
        title="Research best practices for async Python",
        description="Investigate and document async patterns",
        category="light_research",
        status="lobs_review",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(initiative)
    await db_session.commit()

    # Simulate LLM sweep review output
    llm_output = {
        "decisions": [
            {
                "initiative_id": initiative.id,
                "decision": "approve",
                "reason": "Aligns with current priorities, clear deliverable",
                "owner_agent": "researcher",
                "task_title": "Document async Python best practices",
                "task_notes": "Focus on common patterns in our codebase",
                "priority": "medium",
                "project_id": "test-project",
            }
        ],
        "observations": ["Good research proposals this cycle"],
    }

    # Process through worker manager's sweep review handler
    worker_manager = WorkerManager(db_session)
    await worker_manager._process_sweep_review_results(json.dumps(llm_output))

    # Verify: Initiative was approved via decision engine
    await db_session.refresh(initiative)
    assert initiative.status == "approved"
    assert initiative.task_id is not None

    # Verify: Task was created
    task = await db_session.get(Task, initiative.task_id)
    assert task is not None
    assert task.title == "Document async Python best practices"
    assert task.agent == "researcher"
    assert initiative.id in task.notes

    # Verify: Decision record exists
    decisions = (await db_session.execute(
        select(InitiativeDecisionRecord)
        .where(InitiativeDecisionRecord.initiative_id == initiative.id)
    )).scalars().all()
    assert len(decisions) == 1
    assert decisions[0].decision == "approve"
    assert decisions[0].decided_by == "lobs"

    # Verify: Feedback reflection exists
    feedback_reflections = (await db_session.execute(
        select(AgentReflection)
        .where(
            AgentReflection.agent_type == "researcher",
            AgentReflection.reflection_type == "initiative_feedback"
        )
    )).scalars().all()
    assert len(feedback_reflections) == 1


@pytest.mark.asyncio
async def test_agent_routing_uses_capabilities(db_session):
    """
    Test that initiative agent selection uses capability matching.
    
    When an initiative is approved, the decision engine should suggest
    the most appropriate agent based on capabilities.
    """
    # Setup: register agent capabilities
    db_session.add_all([
        AgentCapability(
            agent_type="writer",
            capability="documentation api-docs technical-writing",
            confidence=0.9,
            source="identity",
        ),
        AgentCapability(
            agent_type="researcher",
            capability="investigation analysis benchmarking",
            confidence=0.85,
            source="identity",
        ),
        Project(id="test-project", title="Test Project", type="kanban", archived=False),
    ])
    await db_session.commit()

    # Create an initiative that should match "writer"
    initiative = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="programmer",  # Different from suggested agent
        title="Update API documentation for new endpoints",
        description="Document all new REST API endpoints with examples",
        category="docs_sync",
        status="proposed",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(initiative)
    await db_session.commit()

    # Test agent suggestion
    decision_engine = InitiativeDecisionEngine(db_session)
    suggested_agent = await decision_engine.suggest_agent(initiative)
    
    # Should suggest writer, not programmer (the proposer)
    assert suggested_agent == "writer"

    # Approve the initiative
    result = await decision_engine.decide(
        initiative,
        decision="approve",
        decision_summary="Good documentation needed",
        decided_by="lobs",
    )

    # Verify the task was created with the correct agent
    task = await db_session.get(Task, result["task_id"])
    assert task is not None
    assert task.agent == "writer"


@pytest.mark.asyncio
async def test_scanner_awareness_of_initiative_tasks(db_session):
    """
    Test that scanner correctly identifies tasks created from initiatives.
    
    Initiative-created tasks should be treated exactly like manually-created tasks.
    """
    # Setup
    db_session.add(Project(id="test-project", title="Test Project", type="kanban", archived=False))
    
    # Create initiative and approve it
    initiative = AgentInitiative(
        id=str(uuid.uuid4()),
        proposed_by_agent="programmer",
        title="Refactor database connection pooling",
        description="Improve connection pool management",
        category="moderate_refactor",
        status="proposed",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(initiative)
    await db_session.commit()

    decision_engine = InitiativeDecisionEngine(db_session)
    result = await decision_engine.decide(
        initiative,
        decision="approve",
        decision_summary="Approved for better performance",
        decided_by="lobs",
    )

    # Scanner should pick it up
    scanner = Scanner(db_session)
    eligible_tasks = await scanner.get_eligible_tasks()
    
    task_ids = [t["id"] for t in eligible_tasks]
    assert result["task_id"] in task_ids
    
    # Task should have all required fields
    task_dict = next(t for t in eligible_tasks if t["id"] == result["task_id"])
    assert task_dict["status"] == "active"
    assert task_dict["work_state"] == "not_started"
    assert task_dict["kind"] == "task"
    assert "initiative" in task_dict["notes"].lower()
