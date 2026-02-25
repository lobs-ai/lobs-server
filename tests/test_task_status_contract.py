"""Contract tests: task-status count parity across API surfaces.

These tests enforce the invariant that ``GET /api/tasks/counts``,
``GET /api/status/overview`` (tasks section), and per-status list queries
(``GET /api/tasks?status=X``) all agree on every status bucket.

If any of these assertions fail it means a consumer could be shown
stale or wrong counts — a direct trust regression in the UI.
"""

import pytest
from httpx import AsyncClient
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def create_task(client: AsyncClient, task_id: str, status: str, project_id: str, **extra) -> dict:
    """Create a task with a given status and return the response JSON."""
    data = {
        "id": task_id,
        "title": f"Task {task_id}",
        "status": status,
        "project_id": project_id,
        "sort_order": 0,
        "pinned": False,
        **extra,
    }
    resp = await client.post("/api/tasks", json=data)
    assert resp.status_code == 200, f"Failed to create task {task_id}: {resp.text}"
    return resp.json()


async def get_counts(client: AsyncClient) -> dict:
    """Call the canonical counts endpoint and return the JSON body."""
    resp = await client.get("/api/tasks/counts")
    assert resp.status_code == 200, f"/api/tasks/counts failed: {resp.text}"
    return resp.json()


async def get_overview_task_section(client: AsyncClient) -> dict:
    """Return the ``tasks`` sub-object from the status overview."""
    resp = await client.get("/api/status/overview")
    assert resp.status_code == 200, f"/api/status/overview failed: {resp.text}"
    return resp.json()["tasks"]


async def count_by_list(client: AsyncClient, status: str) -> int:
    """Count tasks with a given status by calling the list endpoint with status filter."""
    resp = await client.get(f"/api/tasks?status={status}&limit=1000")
    assert resp.status_code == 200, f"List query for status={status} failed: {resp.text}"
    return len(resp.json())


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_counts_endpoint_returns_canonical_schema(client: AsyncClient, sample_project):
    """The /api/tasks/counts endpoint must include every canonical field."""
    counts = await get_counts(client)

    required_fields = {
        "inbox",
        "active",
        "waiting",
        "completed",
        "cancelled",
        "rejected",
        "archived",
        "blocked",
        "completed_today",
        "total_open",
        "total_terminal",
    }
    missing = required_fields - set(counts.keys())
    assert not missing, f"Missing fields from /api/tasks/counts: {missing}"

    for field in required_fields:
        assert isinstance(counts[field], int), f"Field '{field}' must be int, got {type(counts[field])}"


@pytest.mark.asyncio
async def test_overview_tasks_section_includes_extended_fields(client: AsyncClient, sample_project):
    """The overview tasks section must expose inbox, cancelled, total_open, total_terminal."""
    tasks_section = await get_overview_task_section(client)

    required_fields = {"active", "waiting", "blocked", "completed_today", "inbox", "cancelled"}
    missing = required_fields - set(tasks_section.keys())
    assert not missing, f"Missing fields from overview tasks section: {missing}"


# ---------------------------------------------------------------------------
# Parity tests: counts vs overview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parity_empty_database(client: AsyncClient, sample_project):
    """With no tasks, all counts must be zero across both endpoints."""
    counts = await get_counts(client)
    overview = await get_overview_task_section(client)

    assert counts["active"] == 0
    assert counts["inbox"] == 0
    assert counts["completed"] == 0
    assert counts["cancelled"] == 0
    assert counts["total_open"] == 0
    assert counts["total_terminal"] == 0

    # Overview must agree
    assert overview["active"] == counts["active"], "active mismatch: counts vs overview"
    assert overview["inbox"] == counts["inbox"], "inbox mismatch: counts vs overview"
    assert overview["cancelled"] == counts["cancelled"], "cancelled mismatch: counts vs overview"


@pytest.mark.asyncio
async def test_parity_active_count(client: AsyncClient, sample_project):
    """active count must match across counts endpoint, overview, and list query."""
    pid = sample_project["id"]
    await create_task(client, "active-1", "active", pid)
    await create_task(client, "active-2", "active", pid)
    await create_task(client, "inbox-1", "inbox", pid)

    counts = await get_counts(client)
    overview = await get_overview_task_section(client)
    list_count = await count_by_list(client, "active")

    # All three surfaces must agree
    assert counts["active"] == 2, f"Expected active=2, got {counts['active']}"
    assert overview["active"] == counts["active"], (
        f"active mismatch: counts={counts['active']}, overview={overview['active']}"
    )
    assert list_count == counts["active"], (
        f"active mismatch: counts={counts['active']}, list={list_count}"
    )


@pytest.mark.asyncio
async def test_parity_inbox_count(client: AsyncClient, sample_project):
    """inbox count must match across counts endpoint, overview, and list query."""
    pid = sample_project["id"]
    await create_task(client, "inbox-a", "inbox", pid)
    await create_task(client, "inbox-b", "inbox", pid)
    await create_task(client, "inbox-c", "inbox", pid)
    await create_task(client, "active-a", "active", pid)

    counts = await get_counts(client)
    overview = await get_overview_task_section(client)
    list_count = await count_by_list(client, "inbox")

    assert counts["inbox"] == 3, f"Expected inbox=3, got {counts['inbox']}"
    assert overview["inbox"] == counts["inbox"], (
        f"inbox mismatch: counts={counts['inbox']}, overview={overview['inbox']}"
    )
    assert list_count == counts["inbox"], (
        f"inbox mismatch: counts={counts['inbox']}, list={list_count}"
    )


@pytest.mark.asyncio
async def test_parity_completed_count(client: AsyncClient, sample_project):
    """completed count must match across counts endpoint, overview (completed_today), and list."""
    pid = sample_project["id"]
    now = datetime.now(timezone.utc)

    await create_task(client, "done-1", "completed", pid, finished_at=now.isoformat())
    await create_task(client, "done-2", "completed", pid, finished_at=now.isoformat())
    await create_task(client, "active-x", "active", pid)

    counts = await get_counts(client)
    overview = await get_overview_task_section(client)
    list_count = await count_by_list(client, "completed")

    assert counts["completed"] == 2, f"Expected completed=2, got {counts['completed']}"
    assert counts["completed_today"] == 2, f"Expected completed_today=2, got {counts['completed_today']}"
    assert overview["completed_today"] == counts["completed_today"], (
        f"completed_today mismatch: counts={counts['completed_today']}, overview={overview['completed_today']}"
    )
    assert list_count == counts["completed"], (
        f"completed mismatch: counts={counts['completed']}, list={list_count}"
    )


@pytest.mark.asyncio
async def test_parity_cancelled_count(client: AsyncClient, sample_project):
    """cancelled count must match across counts endpoint, overview, and list query."""
    pid = sample_project["id"]
    await create_task(client, "can-1", "cancelled", pid)
    await create_task(client, "can-2", "cancelled", pid)
    await create_task(client, "active-z", "active", pid)

    counts = await get_counts(client)
    overview = await get_overview_task_section(client)
    list_count = await count_by_list(client, "cancelled")

    assert counts["cancelled"] == 2, f"Expected cancelled=2, got {counts['cancelled']}"
    assert overview["cancelled"] == counts["cancelled"], (
        f"cancelled mismatch: counts={counts['cancelled']}, overview={overview['cancelled']}"
    )
    assert list_count == counts["cancelled"], (
        f"cancelled mismatch: counts={counts['cancelled']}, list={list_count}"
    )


@pytest.mark.asyncio
async def test_parity_all_statuses_together(client: AsyncClient, sample_project):
    """Create tasks in multiple statuses and verify every bucket across all surfaces."""
    pid = sample_project["id"]
    now = datetime.now(timezone.utc)

    # Create a mix
    await create_task(client, "m-inbox-1", "inbox", pid)
    await create_task(client, "m-inbox-2", "inbox", pid)
    await create_task(client, "m-active-1", "active", pid)
    await create_task(client, "m-active-2", "active", pid)
    await create_task(client, "m-active-3", "active", pid)
    await create_task(client, "m-completed-1", "completed", pid, finished_at=now.isoformat())
    await create_task(client, "m-cancelled-1", "cancelled", pid)
    await create_task(client, "m-cancelled-2", "cancelled", pid)
    await create_task(client, "m-rejected-1", "rejected", pid)
    await create_task(client, "m-waiting-1", "waiting_on", pid)

    counts = await get_counts(client)
    overview = await get_overview_task_section(client)

    # Verify canonical counts
    assert counts["inbox"] == 2
    assert counts["active"] == 3
    assert counts["waiting"] == 1
    assert counts["completed"] == 1
    assert counts["cancelled"] == 2
    assert counts["rejected"] == 1
    assert counts["total_open"] == 6    # inbox + active + waiting
    assert counts["total_terminal"] == 4  # completed + cancelled + rejected + archived

    # Verify overview matches counts for every shared field
    for field in ("active", "waiting", "blocked", "completed_today", "inbox", "cancelled"):
        assert overview[field] == counts.get(field, counts.get("waiting" if field == "waiting" else field)), (
            f"Mismatch for field '{field}': counts={counts.get(field)}, overview={overview[field]}"
        )

    # Verify list counts match
    for status, expected in [
        ("inbox", 2),
        ("active", 3),
        ("waiting_on", 1),
        ("completed", 1),
        ("cancelled", 2),
        ("rejected", 1),
    ]:
        list_count = await count_by_list(client, status)
        assert list_count == expected, (
            f"List count for status={status}: expected {expected}, got {list_count}"
        )


# ---------------------------------------------------------------------------
# Derived field tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_total_open_is_sum_of_open_statuses(client: AsyncClient, sample_project):
    """total_open must equal inbox + active + waiting."""
    pid = sample_project["id"]
    await create_task(client, "to-inbox-1", "inbox", pid)
    await create_task(client, "to-active-1", "active", pid)
    await create_task(client, "to-active-2", "active", pid)
    await create_task(client, "to-waiting-1", "waiting_on", pid)
    await create_task(client, "to-completed-1", "completed", pid)

    counts = await get_counts(client)
    assert counts["total_open"] == counts["inbox"] + counts["active"] + counts["waiting"], (
        f"total_open={counts['total_open']} != inbox+active+waiting="
        f"{counts['inbox']}+{counts['active']}+{counts['waiting']}"
    )


@pytest.mark.asyncio
async def test_total_terminal_is_sum_of_terminal_statuses(client: AsyncClient, sample_project):
    """total_terminal must equal completed + cancelled + rejected + archived."""
    pid = sample_project["id"]
    now = datetime.now(timezone.utc)
    await create_task(client, "tt-completed-1", "completed", pid, finished_at=now.isoformat())
    await create_task(client, "tt-cancelled-1", "cancelled", pid)
    await create_task(client, "tt-rejected-1", "rejected", pid)
    await create_task(client, "tt-archived-1", "archived", pid)
    await create_task(client, "tt-active-1", "active", pid)

    counts = await get_counts(client)
    expected_terminal = (
        counts["completed"] + counts["cancelled"] + counts["rejected"] + counts["archived"]
    )
    assert counts["total_terminal"] == expected_terminal, (
        f"total_terminal={counts['total_terminal']} != sum={expected_terminal}"
    )


@pytest.mark.asyncio
async def test_blocked_count_only_includes_open_tasks(client: AsyncClient, sample_project, db_session):
    """blocked must not include tasks that are in terminal states."""
    from app.models import Task as TaskModel

    pid = sample_project["id"]

    # Create open blocked task
    t1 = TaskModel(
        id="blocked-open-1",
        title="Open blocked",
        status="active",
        project_id=pid,
        blocked_by=["some-other-task"],
    )
    # Create terminal blocked task (should NOT count as blocked)
    t2 = TaskModel(
        id="blocked-done-1",
        title="Done but was blocked",
        status="completed",
        project_id=pid,
        blocked_by=["some-other-task"],
    )
    db_session.add(t1)
    db_session.add(t2)
    await db_session.commit()

    counts = await get_counts(client)
    assert counts["blocked"] == 1, (
        f"Expected blocked=1 (only open tasks), got {counts['blocked']}"
    )


@pytest.mark.asyncio
async def test_completed_today_excludes_old_completed(client: AsyncClient, sample_project, db_session):
    """completed_today must not include tasks completed before today."""
    from app.models import Task as TaskModel

    pid = sample_project["id"]
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    old_task = TaskModel(
        id="done-old-1",
        title="Completed yesterday",
        status="completed",
        project_id=pid,
        finished_at=yesterday,
    )
    new_task = TaskModel(
        id="done-new-1",
        title="Completed today",
        status="completed",
        project_id=pid,
        finished_at=now,
    )
    db_session.add(old_task)
    db_session.add(new_task)
    await db_session.commit()

    counts = await get_counts(client)
    assert counts["completed"] == 2, f"Expected completed=2, got {counts['completed']}"
    assert counts["completed_today"] == 1, (
        f"Expected completed_today=1, got {counts['completed_today']}"
    )


# ---------------------------------------------------------------------------
# Project-scoped counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_scoped_counts(client: AsyncClient, sample_project):
    """When project_id is provided only that project's tasks are counted."""
    pid = sample_project["id"]

    # Create a second project
    other_resp = await client.post("/api/projects", json={
        "id": "other-project",
        "title": "Other Project",
        "archived": False,
        "type": "kanban",
        "sort_order": 1,
    })
    assert other_resp.status_code == 200
    other_pid = other_resp.json()["id"]

    await create_task(client, "p1-active-1", "active", pid)
    await create_task(client, "p1-active-2", "active", pid)
    await create_task(client, "p2-active-1", "active", other_pid)

    # All projects
    all_counts = await get_counts(client)
    assert all_counts["active"] == 3

    # Scoped to first project
    resp = await client.get(f"/api/tasks/counts?project_id={pid}")
    assert resp.status_code == 200
    scoped = resp.json()
    assert scoped["active"] == 2, f"Expected active=2 for project {pid}, got {scoped['active']}"

    # Scoped to second project
    resp2 = await client.get(f"/api/tasks/counts?project_id={other_pid}")
    assert resp2.status_code == 200
    scoped2 = resp2.json()
    assert scoped2["active"] == 1, f"Expected active=1 for project {other_pid}, got {scoped2['active']}"
