import pytest

from app.orchestrator.model_router import (
    decide_models,
    MODEL_HAIKU,
    MODEL_GEMINI_FLASH,
    MODEL_SONNET,
    MODEL_OPUS,
)


def test_programmer_routes_to_sonnet_default():
    task = {"id": "t1", "title": "Implement feature", "notes": "", "status": "active"}
    d = decide_models("programmer", task)
    assert d.models[0] == MODEL_SONNET
    assert MODEL_OPUS in d.models  # fallback always available for programmer


def test_light_inbox_routes_to_haiku_then_gemini_tier():
    task = {"id": "t2", "title": "reply", "notes": "quick response", "status": "inbox"}
    d = decide_models("writer", task)
    assert d.models[0] == MODEL_HAIKU
    assert d.models[1] == MODEL_GEMINI_FLASH
    assert d.models[2] == MODEL_SONNET


def test_very_complex_includes_opus_fallback():
    task = {
        "id": "t3",
        "title": "Orchestrator model router policy engine",
        "notes": "Implement fallback chain and audit logging. Refactor spawn.",
        "status": "active",
    }
    d = decide_models("writer", task)
    assert d.models[0] == MODEL_SONNET
    assert MODEL_OPUS in d.models


def test_high_criticality_appends_opus_fallback():
    task = {
        "id": "t5",
        "title": "Urgent production auth outage",
        "notes": "Investigate and fix auth failures in prod",
        "status": "active",
    }
    d = decide_models("researcher", task)
    assert d.criticality == "high"
    assert d.models[-1] == MODEL_OPUS


@pytest.mark.asyncio
async def test_worker_spawn_uses_fallback_chain(db_session, monkeypatch, tmp_path):
    """Focused test: first model fails, second succeeds, audit shows fallback."""

    from app.models import Project
    from app.orchestrator.worker import WorkerManager

    # Ensure project exists
    project_id = "p1"
    db_session.add(Project(id=project_id, title="P", type="kanban", repo_path=str(tmp_path)))
    await db_session.commit()

    mgr = WorkerManager(db_session)

    calls = {"n": 0}

    async def fake_spawn_session(*, task_prompt: str, agent_id: str, model: str, label: str):
        calls["n"] += 1
        # fail first (haiku), succeed second (gemini)
        if calls["n"] == 1:
            return None, "provider error"
        return {"runId": "r1", "childSessionKey": "s1"}, None

    monkeypatch.setattr(mgr, "_spawn_session", fake_spawn_session)

    task = {"id": "t4", "title": "reply", "notes": "quick response", "status": "inbox"}
    ok = await mgr.spawn_worker(task=task, project_id=project_id, agent_type="writer")
    assert ok is True

    # Verify chosen model is second candidate (fallback)
    assert len(mgr.active_workers) == 1
    wi = next(iter(mgr.active_workers.values()))
    assert wi.model == MODEL_GEMINI_FLASH
    assert wi.model_audit and wi.model_audit["fallback_used"] is True
    assert wi.model_audit["fallback_reason"] == "provider_failure"
