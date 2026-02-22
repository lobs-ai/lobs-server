import pytest

from app.orchestrator.model_router import decide_models


def test_programmer_routes_to_standard_then_strong_tiers(monkeypatch):
    monkeypatch.setenv("LOBS_MODEL_TIER_STANDARD", "model/std-a,model/std-b")
    monkeypatch.setenv("LOBS_MODEL_TIER_STRONG", "model/strong-a")

    task = {"id": "t1", "title": "Implement feature", "notes": "", "status": "active"}
    d = decide_models("programmer", task)

    assert d.models[:3] == ["model/std-a", "model/std-b", "model/strong-a"]
    assert d.policy == "programmer_default"


def test_writer_routes_small_then_medium_then_standard(monkeypatch):
    """Writer with light task routes small → medium → standard."""
    monkeypatch.setenv("LOBS_MODEL_TIER_SMALL", "model/small-a")
    monkeypatch.setenv("LOBS_MODEL_TIER_MEDIUM", "model/medium-a")
    monkeypatch.setenv("LOBS_MODEL_TIER_STANDARD", "model/std-a")

    task = {"id": "t2", "title": "reply", "notes": "quick response", "status": "inbox"}
    d = decide_models("writer", task)

    assert d.models == ["model/small-a", "model/medium-a", "model/std-a"]
    assert d.policy == "writer_default"


def test_light_inbox_non_writer_routes_micro_to_standard(monkeypatch):
    """Non-writer light inbox task goes micro → small → medium → standard."""
    monkeypatch.setenv("LOBS_MODEL_TIER_MICRO", "model/micro-a")
    monkeypatch.setenv("LOBS_MODEL_TIER_SMALL", "model/small-a")
    monkeypatch.setenv("LOBS_MODEL_TIER_MEDIUM", "model/medium-a")
    monkeypatch.setenv("LOBS_MODEL_TIER_STANDARD", "model/std-a")

    task = {"id": "t2b", "title": "reply", "notes": "quick response", "status": "inbox"}
    d = decide_models("researcher", task)

    assert d.models == ["model/micro-a", "model/small-a", "model/medium-a", "model/std-a"]
    assert d.policy == "light_inbox"


def test_available_models_allowlist_filters_candidates(monkeypatch):
    monkeypatch.setenv("LOBS_MODEL_TIER_MICRO", "model/micro-a")
    monkeypatch.setenv("LOBS_MODEL_TIER_SMALL", "model/small-a,model/small-b")
    monkeypatch.setenv("LOBS_MODEL_TIER_MEDIUM", "model/medium-a")
    monkeypatch.setenv("LOBS_MODEL_TIER_STANDARD", "model/std-a")
    monkeypatch.setenv("LOBS_AVAILABLE_MODELS", "model/small-b,model/medium-a,model/std-a")

    task = {"id": "t3", "title": "reply", "notes": "quick response", "status": "inbox"}
    d = decide_models("researcher", task)

    assert d.models == ["model/small-b", "model/medium-a", "model/std-a"]


def test_runtime_overrides_argument_beats_env(monkeypatch):
    monkeypatch.setenv("LOBS_MODEL_TIER_STANDARD", "model/std-env")
    task = {"id": "t3b", "title": "Implement feature", "notes": "", "status": "active"}

    d = decide_models(
        "programmer",
        task,
        tier_overrides={
            "medium": ["model/medium-db"],
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


def test_explicit_tier_override_on_task(monkeypatch):
    """Task with model_tier='small' starts from small tier upward."""
    monkeypatch.setenv("LOBS_MODEL_TIER_SMALL", "model/small-a")
    monkeypatch.setenv("LOBS_MODEL_TIER_MEDIUM", "model/medium-a")
    monkeypatch.setenv("LOBS_MODEL_TIER_STANDARD", "model/std-a")
    monkeypatch.setenv("LOBS_MODEL_TIER_STRONG", "model/strong-a")

    task = {"id": "t6", "title": "Fix bug", "status": "active", "model_tier": "small"}
    d = decide_models("programmer", task)

    assert d.policy == "explicit_small"
    assert d.models == ["model/small-a", "model/medium-a", "model/std-a", "model/strong-a"]


def test_reviewer_light_routes_small_medium_standard(monkeypatch):
    """Light reviewer task routes small → medium → standard."""
    monkeypatch.setenv("LOBS_MODEL_TIER_SMALL", "model/small-a")
    monkeypatch.setenv("LOBS_MODEL_TIER_MEDIUM", "model/medium-a")
    monkeypatch.setenv("LOBS_MODEL_TIER_STANDARD", "model/std-a")

    task = {"id": "t7", "title": "review", "notes": "check this", "status": "inbox"}
    d = decide_models("reviewer", task)

    assert d.policy == "reviewer_light"
    assert d.models == ["model/small-a", "model/medium-a", "model/std-a"]


@pytest.mark.asyncio
async def test_worker_spawn_uses_fallback_chain(db_session, monkeypatch, tmp_path):
    """Focused test: first model fails, second succeeds, audit shows fallback."""

    from app.models import Project
    from app.orchestrator.worker import WorkerManager

    monkeypatch.setenv("LOBS_MODEL_TIER_SMALL", "model/small-a,model/small-b")
    monkeypatch.setenv("LOBS_MODEL_TIER_MEDIUM", "model/medium-a")
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
        # fail first (small-a), succeed second (small-b)
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
    assert wi.model == "model/small-b"
    assert wi.model_audit and wi.model_audit["fallback_used"] is True
    assert wi.model_audit["fallback_reason"] == "provider_failure"
