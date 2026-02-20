import pytest

from app.orchestrator.model_router import decide_models


def test_programmer_routes_to_standard_then_strong_tiers(monkeypatch):
    monkeypatch.setenv("LOBS_MODEL_TIER_STANDARD", "model/std-a,model/std-b")
    monkeypatch.setenv("LOBS_MODEL_TIER_STRONG", "model/strong-a")

    task = {"id": "t1", "title": "Implement feature", "notes": "", "status": "active"}
    d = decide_models("programmer", task)

    assert d.models[:3] == ["model/std-a", "model/std-b", "model/strong-a"]


def test_light_inbox_routes_to_cheap_then_standard_then_strong(monkeypatch):
    monkeypatch.setenv("LOBS_MODEL_TIER_CHEAP", "model/cheap-a")
    monkeypatch.setenv("LOBS_MODEL_TIER_STANDARD", "model/std-a")
    monkeypatch.setenv("LOBS_MODEL_TIER_STRONG", "model/strong-a")

    task = {"id": "t2", "title": "reply", "notes": "quick response", "status": "inbox"}
    d = decide_models("writer", task)

    assert d.models == ["model/cheap-a", "model/std-a", "model/strong-a"]


def test_available_models_allowlist_filters_candidates(monkeypatch):
    monkeypatch.setenv("LOBS_MODEL_TIER_CHEAP", "model/cheap-a,model/cheap-b")
    monkeypatch.setenv("LOBS_MODEL_TIER_STANDARD", "model/std-a")
    monkeypatch.setenv("LOBS_MODEL_TIER_STRONG", "model/strong-a")
    monkeypatch.setenv("LOBS_AVAILABLE_MODELS", "model/cheap-b,model/strong-a")

    task = {"id": "t3", "title": "reply", "notes": "quick response", "status": "inbox"}
    d = decide_models("writer", task)

    assert d.models == ["model/cheap-b", "model/strong-a"]


def test_runtime_overrides_argument_beats_env(monkeypatch):
    monkeypatch.setenv("LOBS_MODEL_TIER_STANDARD", "model/std-env")
    task = {"id": "t3b", "title": "Implement feature", "notes": "", "status": "active"}

    d = decide_models(
        "programmer",
        task,
        tier_overrides={
            "cheap": ["model/cheap-db"],
            "standard": ["model/std-db"],
            "strong": ["model/strong-db"],
        },
        available_models=["model/std-db", "model/strong-db"],
    )

    assert d.models == ["model/std-db", "model/strong-db"]


def test_high_criticality_always_includes_strong_tier(monkeypatch):
    monkeypatch.setenv("LOBS_MODEL_TIER_STANDARD", "model/std-a")
    monkeypatch.setenv("LOBS_MODEL_TIER_STRONG", "model/strong-a")

    task = {
        "id": "t5",
        "title": "Urgent production auth outage",
        "notes": "Investigate and fix auth failures in prod",
        "status": "active",
    }
    d = decide_models("researcher", task)

    assert d.criticality == "high"
    assert "model/strong-a" in d.models


@pytest.mark.asyncio
async def test_worker_spawn_uses_fallback_chain(db_session, monkeypatch, tmp_path):
    """Focused test: first model fails, second succeeds, audit shows fallback."""

    from app.models import Project
    from app.orchestrator.worker import WorkerManager

    monkeypatch.setenv("LOBS_MODEL_TIER_CHEAP", "model/cheap-a,model/cheap-b")
    monkeypatch.setenv("LOBS_MODEL_TIER_STANDARD", "model/std-a")
    monkeypatch.setenv("LOBS_MODEL_TIER_STRONG", "model/strong-a")

    # Ensure project exists
    project_id = "p1"
    db_session.add(Project(id=project_id, title="P", type="kanban", repo_path=str(tmp_path)))
    await db_session.commit()

    mgr = WorkerManager(db_session)

    calls = {"n": 0}

    async def fake_spawn_session(*, task_prompt: str, agent_id: str, model: str, label: str, routing_policy=None):
        calls["n"] += 1
        # fail first (cheap-a), succeed second (cheap-b)
        if calls["n"] == 1:
            return None, "provider error", "unknown"
        return {"runId": "r1", "childSessionKey": "s1"}, None, ""

    monkeypatch.setattr(mgr, "_spawn_session", fake_spawn_session)

    task = {"id": "t4", "title": "reply", "notes": "quick response", "status": "inbox"}
    ok = await mgr.spawn_worker(task=task, project_id=project_id, agent_type="writer")
    assert ok is True

    # Verify chosen model is second candidate (fallback)
    assert len(mgr.active_workers) == 1
    wi = next(iter(mgr.active_workers.values()))
    assert wi.model == "model/cheap-b"
    assert wi.model_audit and wi.model_audit["fallback_used"] is True
    assert wi.model_audit["fallback_reason"] == "provider_failure"
