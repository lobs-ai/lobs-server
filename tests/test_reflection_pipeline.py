import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import (
    AgentIdentityVersion,
    AgentInitiative,
    AgentReflection,
    DiagnosticTriggerEvent,
    OrchestratorSetting,
    Project,
    SystemSweep,
    Task,
    WorkerRun,
)
from app.orchestrator.context_packets import ContextPacketBuilder
from app.orchestrator.diagnostic_triggers import DiagnosticTriggerEngine
from app.orchestrator.reflection_cycle import ReflectionCycleManager
from app.orchestrator.runtime_settings import (
    SETTINGS_KEY_DIAG_DEBOUNCE_SECONDS,
    SETTINGS_KEY_DIAG_REMEDIATION_MAX_TASKS,
)
from app.orchestrator.worker import WorkerManager


@pytest.mark.asyncio
async def test_context_packet_is_versioned_and_inspectable(db_session):
    now = datetime.now(timezone.utc)

    db_session.add_all(
        [
            Task(
                id="task-recent",
                title="Recent done task",
                status="active",
                work_state="in_progress",
                agent="programmer",
                updated_at=now,
            ),
            Task(
                id="task-other-agent",
                title="Other agent task",
                status="active",
                work_state="ready",
                agent="researcher",
                updated_at=now,
            ),
            AgentInitiative(
                id="initiative-1",
                proposed_by_agent="programmer",
                title="Tighten flaky tests",
                description="Reduce reruns",
                category="test_hygiene",
                risk_tier="A",
                status="proposed",
                updated_at=now,
            ),
            AgentReflection(
                id=str(uuid.uuid4()),
                agent_type="programmer",
                reflection_type="initiative_feedback",
                status="completed",
                result={
                    "initiative_id": "initiative-1",
                    "decision": "approved",
                    "selected_agent": "programmer",
                    "task_id": "task-recent",
                    "learning_feedback": "Keep tests deterministic",
                },
                created_at=now,
            ),
            WorkerRun(
                worker_id="programmer-worker-1",
                started_at=now - timedelta(minutes=5),
                ended_at=now,
                succeeded=True,
            ),
        ]
    )
    await db_session.commit()

    packet = await ContextPacketBuilder(db_session).build_for_agent("programmer", hours=6)
    payload = packet.to_dict()

    assert payload["schema_version"] == "agent-context-packet.v1"
    assert payload["packet_type"] == "strategic_reflection_input"
    assert payload["agent_type"] == "programmer"
    assert isinstance(payload["recent_tasks_summary"], list)
    assert isinstance(payload["backlog_summary"], list)
    assert isinstance(payload["active_initiatives"], list)
    assert payload["active_initiatives"][0]["initiative_id"] == "initiative-1"
    assert payload["active_initiatives"][0]["last_feedback"]["decision"] == "approved"


@pytest.mark.asyncio
async def test_reflection_output_persists_structured_fields(db_session):
    reflection_id = str(uuid.uuid4())
    db_session.add(
        AgentReflection(
            id=reflection_id,
            agent_type="programmer",
            reflection_type="strategic",
            status="pending",
            context_packet={"schema_version": "agent-context-packet.v1"},
        )
    )
    await db_session.commit()

    from tests.conftest import TestSessionLocal
    manager = WorkerManager(db_session, session_factory=TestSessionLocal)
    await manager._persist_reflection_output(
        agent_type="programmer",
        reflection_label="reflection-programmer",
        reflection_type="strategic",
        summary=json.dumps(
            {
                "inefficiencies_detected": ["handoff lag"],
                "system_risks": ["missing retries"],
                "missed_opportunities": ["cache warm-up"],
                "identity_adjustments": ["ask clarifying questions sooner"],
                "proposed_initiatives": [
                    {
                        "title": "Improve retries",
                        "description": "Add bounded retries around flaky API calls",
                        "category": "automation_proposal",
                        "estimated_effort": 2,
                        "suggested_owner_agent": "programmer",
                    }
                ],
            }
        ),
        succeeded=True,
    )

    reflection = await db_session.get(AgentReflection, reflection_id)
    assert reflection is not None
    assert reflection.status == "completed"
    assert reflection.inefficiencies == ["handoff lag"]
    assert reflection.system_risks == ["missing retries"]
    assert reflection.missed_opportunities == ["cache warm-up"]
    assert reflection.identity_adjustments == ["ask clarifying questions sooner"]

    initiatives = (await db_session.execute(AgentInitiative.__table__.select())).all()
    assert len(initiatives) == 1


class _StubWorkerManager:
    def __init__(self):
        self.calls = []

    async def _spawn_session(self, **kwargs):
        self.calls.append(kwargs)
        return {"runId": str(uuid.uuid4()), "childSessionKey": "stub-session-key"}, None, "none"
    
    def register_external_worker(self, *args, **kwargs):
        pass


@pytest.mark.asyncio
async def test_reflection_cycle_runs_for_all_registered_execution_agents(db_session, monkeypatch):
    stub_worker = _StubWorkerManager()
    manager = ReflectionCycleManager(db_session, stub_worker)  # type: ignore[arg-type]

    monkeypatch.setattr(manager.registry, "available_types", lambda: ["programmer", "researcher", "project-manager", "sink"])

    result = await manager.run_strategic_reflection_cycle()

    assert result["agents"] == 2
    assert result["spawned"] == 2
    assert len(stub_worker.calls) == 2

    reflections = (await db_session.execute(AgentReflection.__table__.select())).all()
    assert len(reflections) == 2


@pytest.mark.asyncio
async def test_daily_compression_creates_version_and_report(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    db_session.add(
        AgentReflection(
            id=str(uuid.uuid4()),
            agent_type="programmer",
            reflection_type="strategic",
            status="completed",
            created_at=now,
            inefficiencies=["stop duplicating retries"],
            missed_opportunities=["favor smaller commits"],
            identity_adjustments=["always run focused tests first"],
            system_risks=["slow CI feedback loop"],
        )
    )
    await db_session.commit()

    manager = ReflectionCycleManager(db_session, _StubWorkerManager())  # type: ignore[arg-type]
    monkeypatch.setattr(manager.registry, "available_types", lambda: ["programmer", "project-manager"])

    result = await manager.run_daily_compression()

    assert result["rewritten"] == 1
    assert result["validation_failures"] == 0
    assert result["changed_heuristics"] >= 1
    assert result["removed_rules"] >= 1

    versions = (await db_session.execute(select(AgentIdentityVersion).where(AgentIdentityVersion.agent_type == "programmer"))).scalars().all()
    assert len(versions) == 1
    assert versions[0].active is True
    assert versions[0].validation_status == "passed"
    assert versions[0].changed_heuristics
    assert versions[0].removed_rules

    sweep = await db_session.get(SystemSweep, result["sweep_id"])
    assert sweep is not None
    assert sweep.summary["changed_heuristics"] >= 1
    assert sweep.summary["removed_rules"] >= 1


@pytest.mark.asyncio
async def test_daily_compression_validation_failure_keeps_previous_active(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    db_session.add(
        AgentIdentityVersion(
            id=str(uuid.uuid4()),
            agent_type="programmer",
            version=1,
            identity_text="# previous",
            active=True,
            validation_status="passed",
            changed_heuristics=["existing rule"],
            removed_rules=[],
        )
    )
    db_session.add(
        AgentReflection(
            id=str(uuid.uuid4()),
            agent_type="programmer",
            reflection_type="strategic",
            status="completed",
            created_at=now,
            inefficiencies=[],
            missed_opportunities=[],
            identity_adjustments=[],
            system_risks=[],
        )
    )
    await db_session.commit()

    manager = ReflectionCycleManager(db_session, _StubWorkerManager())  # type: ignore[arg-type]
    monkeypatch.setattr(manager.registry, "available_types", lambda: ["programmer"])

    result = await manager.run_daily_compression()

    assert result["rewritten"] == 0
    assert result["validation_failures"] == 1

    versions = (
        await db_session.execute(
            select(AgentIdentityVersion)
            .where(AgentIdentityVersion.agent_type == "programmer")
            .order_by(AgentIdentityVersion.version.asc())
        )
    ).scalars().all()
    assert len(versions) == 2
    assert versions[0].version == 1 and versions[0].active is True
    assert versions[1].version == 2 and versions[1].active is False
    assert versions[1].validation_status == "failed"
    assert versions[1].validation_reason == "no meaningful identity deltas detected"


@pytest.mark.asyncio
async def test_diagnostic_triggers_are_auditable_with_debounce(db_session):
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            Task(
                id="stalled-1",
                title="Stalled",
                status="active",
                work_state="in_progress",
                agent="programmer",
                updated_at=now - timedelta(hours=3),
            ),
            OrchestratorSetting(key=SETTINGS_KEY_DIAG_DEBOUNCE_SECONDS, value=7200),
        ]
    )
    await db_session.commit()

    class _SpawnOkWorker:
        async def _spawn_session(self, **kwargs):
            return {"runId": "abc", "childSessionKey": "stub-session-key"}, None, "none"
        
        def register_external_worker(self, *args, **kwargs):
            pass

    engine = DiagnosticTriggerEngine(db_session, _SpawnOkWorker())  # type: ignore[arg-type]
    first = await engine.run_once()
    second = await engine.run_once()

    assert first["fired"] >= 1
    assert second["suppressed"] >= 1

    events = (await db_session.execute(select(DiagnosticTriggerEvent))).scalars().all()
    assert any(e.status == "spawned" and e.trigger_type == "stalled_task" for e in events)
    assert any(e.status == "suppressed" and e.suppression_reason.startswith("debounce:") for e in events)


@pytest.mark.asyncio
async def test_diagnostic_output_can_auto_create_remediation_tasks(db_session):
    trigger_event_id = str(uuid.uuid4())
    reflection_id = str(uuid.uuid4())

    db_session.add(Project(id="proj-1", title="Proj", type="kanban"))
    db_session.add(
        DiagnosticTriggerEvent(
            id=trigger_event_id,
            trigger_type="repeated_failure",
            trigger_key="failure:task-1",
            status="spawned",
            agent_type="programmer",
            project_id="proj-1",
            trigger_payload={"kind": "repeated_failure"},
        )
    )
    db_session.add(
        AgentReflection(
            id=reflection_id,
            agent_type="programmer",
            reflection_type="diagnostic",
            status="pending",
            context_packet={"trigger": {"trigger_event_id": trigger_event_id}},
        )
    )
    db_session.add(OrchestratorSetting(key=SETTINGS_KEY_DIAG_REMEDIATION_MAX_TASKS, value=2))
    await db_session.commit()

    from tests.conftest import TestSessionLocal
    manager = WorkerManager(db_session, session_factory=TestSessionLocal)
    await manager._persist_reflection_output(
        agent_type="programmer",
        reflection_label="diagnostic-programmer",
        reflection_type="diagnostic",
        summary=json.dumps(
            {
                "issue_summary": "retry storm",
                "root_causes": ["flaky dependency"],
                "recommended_actions": [
                    "Add bounded retry jitter",
                    "Raise timeout for upstream call",
                    "Extra action should be ignored by cap",
                ],
                "confidence": 0.88,
            }
        ),
        succeeded=True,
    )

    event = await db_session.get(DiagnosticTriggerEvent, trigger_event_id)
    assert event is not None
    assert event.status == "completed"
    assert len(event.remediation_task_ids or []) == 2

    remediation_tasks = (
        await db_session.execute(
            select(Task).where(Task.id.in_(event.remediation_task_ids or []))
        )
    ).scalars().all()
    assert len(remediation_tasks) == 2
    assert all(task.status == "inbox" for task in remediation_tasks)
