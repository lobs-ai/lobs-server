"""Coverage for task improvements roadmap core APIs."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_task_without_project_goes_to_inbox(client: AsyncClient):
    payload = {"id": "task-no-project", "title": "No Project", "status": "inbox"}
    resp = await client.post("/api/tasks", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == "inbox"


@pytest.mark.asyncio
async def test_intent_router_v1(client: AsyncClient):
    resp = await client.post("/api/intent/route", json={"text": "Please research alternatives"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["recommended_agent"] == "researcher"


@pytest.mark.asyncio
async def test_workspace_files_and_links(client: AsyncClient):
    ws = {"id": "ws-1", "slug": "main", "name": "Main Workspace"}
    resp = await client.post("/api/workspaces", json=ws)
    assert resp.status_code == 200

    file_1 = {"id": "f1", "workspace_id": "ws-1", "path": "a.md", "content": "a"}
    file_2 = {"id": "f2", "workspace_id": "ws-1", "path": "b.md", "content": "b"}
    assert (await client.post("/api/workspaces/ws-1/files", json=file_1)).status_code == 200
    assert (await client.post("/api/workspaces/ws-1/files", json=file_2)).status_code == 200

    link = {"id": "l1", "workspace_id": "ws-1", "source_file_id": "f1", "target_file_id": "f2", "relation": "references", "weight": 1.0}
    lr = await client.post("/api/workspaces/ws-1/links", json=link)
    assert lr.status_code == 200

    listed = await client.get("/api/workspaces/ws-1/links")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


@pytest.mark.asyncio
async def test_governance_registries_and_backfill(client: AsyncClient, sample_project):
    profile = {"id": "ap1", "agent_type": "programmer", "display_name": "Programmer"}
    p = await client.post("/api/governance/agent-profiles", json=profile)
    assert p.status_code == 200

    routine = {"id": "rt1", "name": "daily-sync", "policy_tier": "safe"}
    r = await client.post("/api/governance/routines", json=routine)
    assert r.status_code == 200

    rr = {
        "id": "rr1",
        "project_id": sample_project["id"],
        "prompt": "Find best approach",
        "status": "pending"
    }
    cr = await client.post(f"/api/research/{sample_project['id']}/requests", json=rr)
    assert cr.status_code == 200

    bf = await client.post("/api/governance/knowledge-requests/backfill-from-research")
    assert bf.status_code == 200
    assert bf.json()["created"] >= 1
