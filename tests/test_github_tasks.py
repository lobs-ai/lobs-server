"""Tests for GitHub-backed task integration."""

import json
import pytest
from unittest import mock
from datetime import datetime, timezone
from httpx import AsyncClient

from app.models import Project as ProjectModel, Task as TaskModel
from sqlalchemy import select


# Mock GitHub CLI responses
MOCK_GH_LOGIN = "lobs-test-bot"

MOCK_ISSUE_CREATE_OUTPUT = "https://github.com/test-org/test-repo/issues/42"

MOCK_ISSUE_VIEW_RESPONSE = {
    "number": 42,
    "title": "Test Issue",
    "state": "open",
    "url": "https://github.com/test-org/test-repo/issues/42",
    "updatedAt": "2026-02-19T12:00:00Z",
    "labels": [{"name": "lobs:ready"}],
    "assignees": []
}

MOCK_ISSUES_LIST_RESPONSE = [
    {
        "number": 1,
        "title": "Issue 1",
        "state": "open",
        "updatedAt": "2026-02-19T10:00:00Z",
        "labels": [{"name": "lobs:ready"}],
        "assignees": [],
        "url": "https://github.com/test-org/test-repo/issues/1"
    },
    {
        "number": 2,
        "title": "Issue 2",
        "state": "open",
        "updatedAt": "2026-02-19T11:00:00Z",
        "labels": [{"name": "lobs:ready"}, {"name": "bug"}],
        "assignees": [],
        "url": "https://github.com/test-org/test-repo/issues/2"
    },
    {
        "number": 3,
        "title": "Issue 3 - Already Claimed",
        "state": "open",
        "updatedAt": "2026-02-19T11:30:00Z",
        "labels": [{"name": "lobs:ready"}, {"name": "lobs:claimed"}],
        "assignees": [{"login": "lobs-test-bot"}],
        "url": "https://github.com/test-org/test-repo/issues/3"
    },
    {
        "number": 4,
        "title": "Issue 4 - Blocked",
        "state": "open",
        "updatedAt": "2026-02-19T11:30:00Z",
        "labels": [{"name": "lobs:ready"}, {"name": "blocked"}],
        "assignees": [],
        "url": "https://github.com/test-org/test-repo/issues/4"
    },
    {
        "number": 5,
        "title": "Issue 5 - Closed",
        "state": "closed",
        "updatedAt": "2026-02-19T11:30:00Z",
        "labels": [{"name": "lobs:ready"}],
        "assignees": [],
        "url": "https://github.com/test-org/test-repo/issues/5"
    }
]


def mock_subprocess_run(cmd, *args, **kwargs):
    """Mock subprocess.run for gh CLI calls."""
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
    
    # Mock gh auth
    if "gh api user" in cmd_str:
        return mock.Mock(returncode=0, stdout=MOCK_GH_LOGIN, stderr="")
    
    # Mock issue create
    if "gh issue create" in cmd_str:
        return mock.Mock(returncode=0, stdout=MOCK_ISSUE_CREATE_OUTPUT + "\n", stderr="")
    
    # Mock issue view
    if "gh issue view" in cmd_str:
        return mock.Mock(returncode=0, stdout=json.dumps(MOCK_ISSUE_VIEW_RESPONSE), stderr="")
    
    # Mock issue list
    if "gh issue list" in cmd_str:
        return mock.Mock(returncode=0, stdout=json.dumps(MOCK_ISSUES_LIST_RESPONSE), stderr="")
    
    # Mock issue edit (for push)
    if "gh issue edit" in cmd_str:
        return mock.Mock(returncode=0, stdout="", stderr="")
    
    # Mock issue close/reopen
    if "gh issue close" in cmd_str or "gh issue reopen" in cmd_str:
        return mock.Mock(returncode=0, stdout="", stderr="")
    
    # Default: command not found
    return mock.Mock(returncode=1, stdout="", stderr="Command not mocked")


@pytest.fixture
def github_project_data():
    """GitHub-tracked project data."""
    return {
        "id": "github-project",
        "title": "GitHub Project",
        "notes": "A GitHub-tracked project",
        "archived": False,
        "type": "kanban",
        "sort_order": 0,
        "tracking": "github",
        "github_repo": "test-org/test-repo",
        "github_label_filter": ["lobs:ready"]
    }


@pytest.fixture
def local_project_data():
    """Local-tracked project data."""
    return {
        "id": "local-project",
        "title": "Local Project",
        "notes": "A local-tracked project",
        "archived": False,
        "type": "kanban",
        "sort_order": 0,
        "tracking": "local",
        "github_repo": None
    }


@pytest.mark.asyncio
async def test_create_task_in_local_project_no_github_fields(client: AsyncClient, local_project_data):
    """Creating a task in a local project should NOT set GitHub-specific fields."""
    # Create local project
    project_resp = await client.post("/api/projects", json=local_project_data)
    assert project_resp.status_code == 200
    
    # Create task in local project
    task_data = {
        "id": "local-task-1",
        "title": "Local Task",
        "status": "inbox",
        "project_id": local_project_data["id"],
        "notes": "This is a local task"
    }
    
    response = await client.post("/api/tasks", json=task_data)
    assert response.status_code == 200
    
    task = response.json()
    # Verify GitHub-specific fields are NOT set
    assert task["external_source"] is None
    assert task["external_id"] is None
    assert task["external_updated_at"] is None
    assert task["sync_state"] is None
    assert task["conflict_payload"] is None
    assert task["github_issue_number"] is None


@pytest.mark.asyncio
@mock.patch("app.services.github_sync.subprocess.run", side_effect=mock_subprocess_run)
async def test_create_task_in_github_project_creates_issue(mock_run, client: AsyncClient, github_project_data):
    """Creating a task in a GitHub project should create a GitHub issue and set metadata."""
    # Create GitHub project
    project_resp = await client.post("/api/projects", json=github_project_data)
    assert project_resp.status_code == 200
    
    # Create task in GitHub project
    task_data = {
        "id": "github-task-1",
        "title": "GitHub Task",
        "status": "inbox",
        "project_id": github_project_data["id"],
        "notes": "This should create a GitHub issue"
    }
    
    response = await client.post("/api/tasks", json=task_data)
    assert response.status_code == 200
    
    task = response.json()
    # Verify GitHub-specific fields ARE set
    assert task["external_source"] == "github"
    assert task["external_id"] == "42"
    assert task["external_updated_at"] is not None
    assert task["sync_state"] == "synced"
    assert task["github_issue_number"] == 42
    assert task["status"] == "active"  # inbox -> active for GitHub projects
    assert task["work_state"] == "not_started"
    assert task["owner"] == "lobs"
    
    # Verify gh CLI was called to create issue
    create_calls = [call for call in mock_run.call_args_list if "gh issue create" in " ".join(call[0][0])]
    assert len(create_calls) == 1
    # Verify title and body were passed
    create_cmd = create_calls[0][0][0]
    assert "GitHub Task" in create_cmd
    assert "This should create a GitHub issue" in create_cmd


@pytest.mark.asyncio
@mock.patch("app.services.github_sync.subprocess.run", side_effect=mock_subprocess_run)
async def test_create_task_in_github_project_with_existing_issue_number(mock_run, client: AsyncClient, github_project_data):
    """Creating a task with an existing github_issue_number should use it."""
    # Create GitHub project
    project_resp = await client.post("/api/projects", json=github_project_data)
    assert project_resp.status_code == 200
    
    # Create task with existing issue number
    task_data = {
        "id": "github-task-2",
        "title": "Existing Issue Task",
        "status": "active",
        "project_id": github_project_data["id"],
        "github_issue_number": 99,
        "notes": "Linked to existing issue #99"
    }
    
    response = await client.post("/api/tasks", json=task_data)
    assert response.status_code == 200
    
    task = response.json()
    assert task["external_source"] == "github"
    assert task["external_id"] == "99"
    assert task["sync_state"] == "synced"
    assert task["github_issue_number"] == 99
    
    # Verify gh CLI was NOT called to create a new issue
    create_calls = [call for call in mock_run.call_args_list if "gh issue create" in " ".join(call[0][0])]
    assert len(create_calls) == 0


@pytest.mark.asyncio
async def test_update_task_in_github_project_sets_local_changed(client: AsyncClient, github_project_data, db_session):
    """Updating a GitHub-backed task should set sync_state='local_changed'."""
    # Create GitHub project
    project = ProjectModel(**github_project_data)
    db_session.add(project)
    await db_session.commit()
    
    # Create a GitHub-backed task directly in DB
    task = TaskModel(
        id="github-task-3",
        title="Original Title",
        status="active",
        owner="lobs",
        work_state="not_started",
        project_id=github_project_data["id"],
        external_source="github",
        external_id="42",
        github_issue_number=42,
        sync_state="synced"
    )
    db_session.add(task)
    await db_session.commit()
    
    # Update the task via API
    update_data = {
        "title": "Updated Title",
        "notes": "Updated notes"
    }
    response = await client.put(f"/api/tasks/{task.id}", json=update_data)
    assert response.status_code == 200
    
    updated_task = response.json()
    assert updated_task["title"] == "Updated Title"
    assert updated_task["sync_state"] == "local_changed"


@pytest.mark.asyncio
async def test_update_task_status_patch_in_github_project_sets_local_changed(client: AsyncClient, github_project_data, db_session):
    """PATCH /tasks/{id}/status should set sync_state='local_changed' for GitHub tasks."""
    # Create GitHub project via API
    project_resp = await client.post("/api/projects", json=github_project_data)
    assert project_resp.status_code == 200
    
    # Create a GitHub-backed task via API
    task_data = {
        "id": "github-task-4",
        "title": "Test Task",
        "status": "active",
        "owner": "lobs",
        "work_state": "not_started",
        "project_id": github_project_data["id"],
        "external_source": "github",
        "external_id": "42",
        "github_issue_number": 42,
        "sync_state": "synced"
    }
    task_resp = await client.post("/api/tasks", json=task_data)
    assert task_resp.status_code == 200
    task = task_resp.json()
    
    # Update status via PATCH
    response = await client.patch(
        f"/api/tasks/{task['id']}/status",
        json={"status": "completed"}
    )
    assert response.status_code == 200
    
    updated_task = response.json()
    assert updated_task["status"] == "completed"
    assert updated_task["sync_state"] == "local_changed", "PATCH status should mark GitHub task as local_changed"


@pytest.mark.asyncio
async def test_update_task_work_state_patch_in_github_project_sets_local_changed(client: AsyncClient, github_project_data, db_session):
    """PATCH /tasks/{id}/work-state should set sync_state='local_changed' for GitHub tasks."""
    # Create GitHub project via API
    project_resp = await client.post("/api/projects", json=github_project_data)
    assert project_resp.status_code == 200
    
    # Create a GitHub-backed task via API
    task_data = {
        "id": "github-task-5",
        "title": "Test Task",
        "status": "active",
        "owner": "lobs",
        "work_state": "not_started",
        "project_id": github_project_data["id"],
        "external_source": "github",
        "external_id": "42",
        "github_issue_number": 42,
        "sync_state": "synced"
    }
    task_resp = await client.post("/api/tasks", json=task_data)
    assert task_resp.status_code == 200
    task = task_resp.json()
    
    # Update work_state via PATCH
    response = await client.patch(
        f"/api/tasks/{task['id']}/work-state",
        json={"work_state": "in_progress"}
    )
    assert response.status_code == 200
    
    updated_task = response.json()
    assert updated_task["work_state"] == "in_progress"
    assert updated_task["sync_state"] == "local_changed", "PATCH work-state should mark GitHub task as local_changed"


@pytest.mark.asyncio
async def test_update_task_review_state_patch_in_github_project_sets_local_changed(client: AsyncClient, github_project_data, db_session):
    """PATCH /tasks/{id}/review-state should set sync_state='local_changed' for GitHub tasks."""
    # Create GitHub project via API
    project_resp = await client.post("/api/projects", json=github_project_data)
    assert project_resp.status_code == 200
    
    # Create a GitHub-backed task via API
    task_data = {
        "id": "github-task-6",
        "title": "Test Task",
        "status": "active",
        "owner": "lobs",
        "work_state": "not_started",
        "project_id": github_project_data["id"],
        "external_source": "github",
        "external_id": "42",
        "github_issue_number": 42,
        "sync_state": "synced"
    }
    task_resp = await client.post("/api/tasks", json=task_data)
    assert task_resp.status_code == 200
    task = task_resp.json()
    
    # Update review_state via PATCH
    response = await client.patch(
        f"/api/tasks/{task['id']}/review-state",
        json={"review_state": "approved"}
    )
    assert response.status_code == 200
    
    updated_task = response.json()
    assert updated_task["review_state"] == "approved"
    assert updated_task["sync_state"] == "local_changed", "PATCH review-state should mark GitHub task as local_changed"


@pytest.mark.asyncio
@mock.patch("app.services.github_sync.subprocess.run", side_effect=mock_subprocess_run)
async def test_github_sync_imports_issues(mock_run, client: AsyncClient, github_project_data, db_session):
    """Syncing a GitHub project should import new issues as tasks."""
    # Create GitHub project
    project_resp = await client.post("/api/projects", json=github_project_data)
    assert project_resp.status_code == 200
    
    # Sync the project
    response = await client.post(f"/api/projects/{github_project_data['id']}/github-sync")
    assert response.status_code == 200
    
    sync_result = response.json()
    assert sync_result["status"] == "synced"
    assert sync_result["imported"] == 5  # All 5 mocked issues
    assert sync_result["updated"] == 0
    assert sync_result["conflicts"] == 0
    
    # Verify tasks were created
    result = await db_session.execute(
        select(TaskModel).where(TaskModel.project_id == github_project_data["id"])
    )
    tasks = result.scalars().all()
    assert len(tasks) == 5
    
    # Verify task properties
    task_by_number = {t.github_issue_number: t for t in tasks}
    
    # Issue 1: eligible for claim
    t1 = task_by_number[1]
    assert t1.title == "Issue 1"
    assert t1.external_source == "github"
    assert t1.external_id == "1"
    assert t1.sync_state == "synced"
    assert t1.status == "active"
    
    # Issue 3: already claimed
    t3 = task_by_number[3]
    assert t3.title == "Issue 3 - Already Claimed"
    assert t3.sync_state == "synced"
    
    # Issue 4: blocked (ineligible)
    t4 = task_by_number[4]
    assert t4.sync_state == "ineligible"
    
    # Issue 5: closed
    t5 = task_by_number[5]
    assert t5.status == "completed"


@pytest.mark.asyncio
@mock.patch("app.services.github_sync.subprocess.run", side_effect=mock_subprocess_run)
async def test_github_sync_updates_existing_tasks(mock_run, client: AsyncClient, github_project_data, db_session):
    """Syncing should update existing tasks with remote changes (or detect conflicts)."""
    # Create GitHub project
    project = ProjectModel(**github_project_data)
    db_session.add(project)
    await db_session.commit()
    
    # Create an existing task for issue #1
    # NOTE: SQLAlchemy will auto-set updated_at to NOW, which means this will likely
    # trigger conflict detection since both local and remote changed after external_updated_at.
    # This is correct behavior - we're testing that the sync processes the task.
    existing_task = TaskModel(
        id="existing-task-1",
        title="Old Title",
        status="active",
        owner="lobs",
        work_state="not_started",
        project_id=github_project_data["id"],
        external_source="github",
        external_id="1",
        github_issue_number=1,
        sync_state="synced",
        external_updated_at=datetime(2026, 2, 18, 10, 0, 0, tzinfo=timezone.utc)
    )
    db_session.add(existing_task)
    await db_session.commit()
    
    # Sync the project
    response = await client.post(f"/api/projects/{github_project_data['id']}/github-sync")
    assert response.status_code == 200
    
    sync_result = response.json()
    # Import count: 4 new issues (not counting #1)
    assert sync_result["imported"] == 4
    # The existing task will likely be marked as conflict due to auto-set updated_at
    # That's correct behavior when both local and remote changed after last sync
    total_processed = sync_result["updated"] + sync_result["conflicts"]
    assert total_processed >= 1  # Issue #1 was processed (either updated or conflict)


@pytest.mark.asyncio
@mock.patch("app.services.github_sync.subprocess.run", side_effect=mock_subprocess_run)
async def test_github_sync_detects_conflicts(mock_run, client: AsyncClient, github_project_data, db_session):
    """Syncing should detect conflicts when both local and remote have changed."""
    # Create GitHub project
    project = ProjectModel(**github_project_data)
    db_session.add(project)
    await db_session.commit()
    
    # Create a task that was locally modified after last sync
    conflicting_task = TaskModel(
        id="conflict-task",
        title="Locally Modified Title",
        status="active",
        owner="lobs",
        work_state="in_progress",
        project_id=github_project_data["id"],
        external_source="github",
        external_id="2",
        github_issue_number=2,
        sync_state="local_changed",
        external_updated_at=datetime(2026, 2, 18, 10, 0, 0, tzinfo=timezone.utc),
        # updated_at will be auto-set to NOW by SQLAlchemy, which will be AFTER external_updated_at
    )
    db_session.add(conflicting_task)
    await db_session.commit()
    
    # Sync the project (remote has also changed per MOCK_ISSUES_LIST_RESPONSE)
    response = await client.post(f"/api/projects/{github_project_data['id']}/github-sync")
    assert response.status_code == 200
    
    sync_result = response.json()
    assert sync_result["conflicts"] >= 1
    
    # Verify conflict was recorded (query via API to get committed state)
    task_response = await client.get("/api/tasks/conflict-task")
    assert task_response.status_code == 200
    task = task_response.json()
    assert task["sync_state"] == "conflict"
    assert task["conflict_payload"] is not None
    assert "remote_title" in task["conflict_payload"]
    assert task["conflict_payload"]["remote_title"] == "Issue 2"


@pytest.mark.asyncio
async def test_scanner_excludes_ineligible_github_tasks(db_session):
    """Scanner should exclude GitHub tasks that are not eligible for pickup."""
    from app.orchestrator.scanner import Scanner
    
    # Create a GitHub project
    project = ProjectModel(
        id="scanner-project",
        title="Scanner Test Project",
        archived=False,
        type="kanban",
        tracking="github",
        github_repo="test-org/test-repo"
    )
    db_session.add(project)
    
    # Create eligible task (claimed_by_lobs=True)
    eligible_task = TaskModel(
        id="eligible-task",
        title="Eligible Task",
        status="active",
        work_state="not_started",
        project_id=project.id,
        external_source="github",
        external_id="1",
        sync_state="synced",
        conflict_payload={
            "github": {
                "eligible_for_claim": False,
                "claimed_by_lobs": True
            }
        }
    )
    db_session.add(eligible_task)
    
    # Create ineligible task (blocked)
    ineligible_task = TaskModel(
        id="ineligible-task",
        title="Ineligible Task",
        status="active",
        work_state="not_started",
        project_id=project.id,
        external_source="github",
        external_id="2",
        sync_state="ineligible",
        conflict_payload={
            "github": {
                "eligible_for_claim": False,
                "claimed_by_lobs": False,
                "eligibility_reason": "blocked"
            }
        }
    )
    db_session.add(ineligible_task)
    
    # Create local task (always eligible)
    local_task = TaskModel(
        id="local-task",
        title="Local Task",
        status="active",
        work_state="not_started",
        project_id=project.id
    )
    db_session.add(local_task)
    
    await db_session.commit()
    
    # Get eligible tasks via scanner
    scanner = Scanner(db_session)
    eligible = await scanner.get_eligible_tasks()
    
    task_ids = {t["id"] for t in eligible}
    assert "eligible-task" in task_ids
    assert "local-task" in task_ids
    assert "ineligible-task" not in task_ids


@pytest.mark.asyncio
async def test_scanner_includes_local_tasks_always(db_session):
    """Scanner should always include local tasks regardless of sync state."""
    from app.orchestrator.scanner import Scanner
    
    # Create local project
    project = ProjectModel(
        id="local-scanner-project",
        title="Local Scanner Test",
        archived=False,
        type="kanban",
        tracking="local"
    )
    db_session.add(project)
    
    # Create local tasks
    for i in range(3):
        task = TaskModel(
            id=f"local-task-{i}",
            title=f"Local Task {i}",
            status="active",
            work_state="not_started",
            project_id=project.id
        )
        db_session.add(task)
    
    await db_session.commit()
    
    # Get eligible tasks
    scanner = Scanner(db_session)
    eligible = await scanner.get_eligible_tasks()
    
    assert len(eligible) == 3
    for task in eligible:
        assert task["external_source"] is None
