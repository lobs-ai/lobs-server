"""API contract smoke tests for Swift clients.

This test suite validates that lobs-server API responses match the data shapes
expected by Swift clients (lobs-mission-control and lobs-mobile).

Key validations:
- Field names match Swift's JSONDecoder.KeyDecodingStrategy.convertFromSnakeCase expectations
- All required fields are present
- Field types match Swift's Codable expectations
- Optional fields are properly nullable
- Date fields are ISO 8601 strings

Run this in CI to catch breaking API changes before they reach clients.
"""

import pytest
from httpx import AsyncClient
from datetime import datetime
from typing import Any, Optional


# ============================================================================
# Schema Validators
# ============================================================================

def validate_iso8601_date(value: Any) -> None:
    """Validate value is a valid ISO 8601 datetime string."""
    assert isinstance(value, str), f"Expected string for date field, got {type(value)}"
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as e:
        pytest.fail(f"Invalid ISO 8601 date: {value} - {e}")


def validate_task_shape(task: dict) -> None:
    """Validate task matches Swift DashboardTask schema.
    
    Swift model expects (with .convertFromSnakeCase):
    - id: String
    - title: String
    - status: TaskStatus (string enum)
    - owner: TaskOwner? (optional string enum)
    - createdAt: Date (snake_case: created_at)
    - updatedAt: Date (snake_case: updated_at)
    - workState: WorkState? (optional, snake_case: work_state)
    - reviewState: ReviewState? (optional, snake_case: review_state)
    - projectId: String? (optional, snake_case: project_id)
    - artifactPath: String? (optional, snake_case: artifact_path)
    - notes: String? (optional)
    - startedAt: Date? (optional, snake_case: started_at)
    - finishedAt: Date? (optional, snake_case: finished_at)
    - sortOrder: Int? (optional, snake_case: sort_order)
    - blockedBy: [String]? (optional, snake_case: blocked_by)
    - pinned: Bool? (optional)
    - shape: TaskShape? (optional string enum)
    - agent: String? (optional)
    - trackingMode: TaskTrackingMode? (optional, snake_case: tracking_mode)
    - githubIssueNumber: Int? (optional, snake_case: github_issue_number)
    - githubIssueUrl: String? (optional, snake_case: github_issue_url)
    - githubIssueState: String? (optional, snake_case: github_issue_state)
    - githubSyncedAt: Date? (optional, snake_case: github_synced_at)
    - workspaceContext: String? (optional, snake_case: workspace_context)
    - userContext: String? (optional, snake_case: user_context)
    - modelTier: String? (optional, snake_case: model_tier)
    """
    # Required fields
    assert "id" in task, "Missing required field: id"
    assert isinstance(task["id"], str), f"id must be string, got {type(task['id'])}"
    
    assert "title" in task, "Missing required field: title"
    assert isinstance(task["title"], str), f"title must be string, got {type(task['title'])}"
    
    assert "status" in task, "Missing required field: status"
    assert isinstance(task["status"], str), f"status must be string, got {type(task['status'])}"
    
    assert "created_at" in task, "Missing required field: created_at"
    validate_iso8601_date(task["created_at"])
    
    assert "updated_at" in task, "Missing required field: updated_at"
    validate_iso8601_date(task["updated_at"])
    
    # Optional string fields
    optional_strings = ["owner", "work_state", "review_state", "project_id", "artifact_path", 
                       "notes", "shape", "agent", "tracking_mode", "github_issue_url", 
                       "github_issue_state", "workspace_context", "user_context", "model_tier"]
    for field in optional_strings:
        if field in task and task[field] is not None:
            assert isinstance(task[field], str), f"{field} must be string or null, got {type(task[field])}"
    
    # Optional date fields
    optional_dates = ["started_at", "finished_at", "github_synced_at"]
    for field in optional_dates:
        if field in task and task[field] is not None:
            validate_iso8601_date(task[field])
    
    # Optional integer fields
    optional_ints = ["sort_order", "github_issue_number"]
    for field in optional_ints:
        if field in task and task[field] is not None:
            assert isinstance(task[field], int), f"{field} must be int or null, got {type(task[field])}"
    
    # Optional boolean fields
    if "pinned" in task and task["pinned"] is not None:
        assert isinstance(task["pinned"], bool), f"pinned must be bool or null, got {type(task['pinned'])}"
    
    # Optional array fields
    if "blocked_by" in task and task["blocked_by"] is not None:
        assert isinstance(task["blocked_by"], list), f"blocked_by must be array or null, got {type(task['blocked_by'])}"
        for item in task["blocked_by"]:
            assert isinstance(item, str), f"blocked_by items must be strings, got {type(item)}"


def validate_project_shape(project: dict) -> None:
    """Validate project matches Swift Project schema.
    
    Swift model expects (with .convertFromSnakeCase):
    - id: String
    - title: String
    - createdAt: Date (snake_case: created_at)
    - updatedAt: Date (snake_case: updated_at)
    - notes: String? (optional)
    - archived: Bool? (optional)
    - type: ProjectType? (optional string enum: kanban/research/tracker)
    - sortOrder: Int? (optional, snake_case: sort_order)
    """
    # Required fields
    assert "id" in project, "Missing required field: id"
    assert isinstance(project["id"], str), f"id must be string, got {type(project['id'])}"
    
    assert "title" in project, "Missing required field: title"
    assert isinstance(project["title"], str), f"title must be string, got {type(project['title'])}"
    
    assert "created_at" in project, "Missing required field: created_at"
    validate_iso8601_date(project["created_at"])
    
    assert "updated_at" in project, "Missing required field: updated_at"
    validate_iso8601_date(project["updated_at"])
    
    # Optional fields
    if "notes" in project and project["notes"] is not None:
        assert isinstance(project["notes"], str), f"notes must be string or null, got {type(project['notes'])}"
    
    if "archived" in project and project["archived"] is not None:
        assert isinstance(project["archived"], bool), f"archived must be bool or null, got {type(project['archived'])}"
    
    if "type" in project and project["type"] is not None:
        assert isinstance(project["type"], str), f"type must be string or null, got {type(project['type'])}"
        assert project["type"] in ["kanban", "research", "tracker"], f"type must be kanban/research/tracker, got {project['type']}"
    
    if "sort_order" in project and project["sort_order"] is not None:
        assert isinstance(project["sort_order"], int), f"sort_order must be int or null, got {type(project['sort_order'])}"


def validate_chat_session_shape(session: dict) -> None:
    """Validate chat session matches Swift ChatSession schema.
    
    Swift model expects (with .convertFromSnakeCase):
    - id: String
    - label: String?
    - createdAt: Date (snake_case: created_at)
    - updatedAt: Date (snake_case: updated_at)
    """
    assert "id" in session, "Missing required field: id"
    assert isinstance(session["id"], str), f"id must be string, got {type(session['id'])}"
    
    assert "created_at" in session, "Missing required field: created_at"
    validate_iso8601_date(session["created_at"])
    
    assert "updated_at" in session, "Missing required field: updated_at"
    validate_iso8601_date(session["updated_at"])
    
    if "label" in session and session["label"] is not None:
        assert isinstance(session["label"], str), f"label must be string or null, got {type(session['label'])}"


def validate_chat_message_shape(message: dict) -> None:
    """Validate chat message matches Swift ChatMessage schema.
    
    Swift model expects (with .convertFromSnakeCase):
    - id: String
    - sessionId: String (snake_case: session_id)
    - role: String
    - content: String
    - createdAt: Date (snake_case: created_at)
    - modelUsed: String? (optional, snake_case: model_used)
    """
    assert "id" in message, "Missing required field: id"
    assert isinstance(message["id"], str), f"id must be string, got {type(message['id'])}"
    
    assert "session_id" in message, "Missing required field: session_id"
    assert isinstance(message["session_id"], str), f"session_id must be string, got {type(message['session_id'])}"
    
    assert "role" in message, "Missing required field: role"
    assert isinstance(message["role"], str), f"role must be string, got {type(message['role'])}"
    
    assert "content" in message, "Missing required field: content"
    assert isinstance(message["content"], str), f"content must be string, got {type(message['content'])}"
    
    assert "created_at" in message, "Missing required field: created_at"
    validate_iso8601_date(message["created_at"])
    
    if "model_used" in message and message["model_used"] is not None:
        assert isinstance(message["model_used"], str), f"model_used must be string or null, got {type(message['model_used'])}"


def validate_memory_shape(memory: dict) -> None:
    """Validate memory matches Swift Memory schema.
    
    Swift model expects (with .convertFromSnakeCase):
    - id: String
    - type: String
    - content: String
    - createdAt: Date (snake_case: created_at)
    - updatedAt: Date (snake_case: updated_at)
    - metadata: dict? (optional, JSON object)
    """
    assert "id" in memory, "Missing required field: id"
    assert isinstance(memory["id"], str), f"id must be string, got {type(memory['id'])}"
    
    assert "type" in memory, "Missing required field: type"
    assert isinstance(memory["type"], str), f"type must be string, got {type(memory['type'])}"
    
    assert "content" in memory, "Missing required field: content"
    assert isinstance(memory["content"], str), f"content must be string, got {type(memory['content'])}"
    
    assert "created_at" in memory, "Missing required field: created_at"
    validate_iso8601_date(memory["created_at"])
    
    assert "updated_at" in memory, "Missing required field: updated_at"
    validate_iso8601_date(memory["updated_at"])
    
    if "metadata" in memory and memory["metadata"] is not None:
        assert isinstance(memory["metadata"], dict), f"metadata must be dict or null, got {type(memory['metadata'])}"


def validate_agent_shape(agent: dict) -> None:
    """Validate agent matches Swift Agent schema.
    
    Swift model expects (with .convertFromSnakeCase):
    - id: String
    - agentType: String (snake_case: agent_type)
    - status: String
    - currentTask: String? (optional, snake_case: current_task)
    - lastActive: Date? (optional, snake_case: last_active)
    """
    assert "id" in agent, "Missing required field: id"
    assert isinstance(agent["id"], str), f"id must be string, got {type(agent['id'])}"
    
    assert "agent_type" in agent, "Missing required field: agent_type"
    assert isinstance(agent["agent_type"], str), f"agent_type must be string, got {type(agent['agent_type'])}"
    
    assert "status" in agent, "Missing required field: status"
    assert isinstance(agent["status"], str), f"status must be string, got {type(agent['status'])}"
    
    if "current_task" in agent and agent["current_task"] is not None:
        assert isinstance(agent["current_task"], str), f"current_task must be string or null, got {type(agent['current_task'])}"
    
    if "last_active" in agent and agent["last_active"] is not None:
        validate_iso8601_date(agent["last_active"])


def validate_inbox_item_shape(item: dict) -> None:
    """Validate inbox item matches Swift InboxItem schema.
    
    Swift model expects (with .convertFromSnakeCase):
    - id: String
    - title: String
    - content: String?
    - filename: String?
    - isRead: Bool (snake_case: is_read)
    - createdAt: Date (snake_case: created_at)
    - processedAt: Date? (optional, snake_case: processed_at)
    """
    assert "id" in item, "Missing required field: id"
    assert isinstance(item["id"], str), f"id must be string, got {type(item['id'])}"
    
    assert "title" in item, "Missing required field: title"
    assert isinstance(item["title"], str), f"title must be string, got {type(item['title'])}"
    
    assert "is_read" in item, "Missing required field: is_read"
    assert isinstance(item["is_read"], bool), f"is_read must be bool, got {type(item['is_read'])}"
    
    assert "created_at" in item, "Missing required field: created_at"
    validate_iso8601_date(item["created_at"])
    
    if "content" in item and item["content"] is not None:
        assert isinstance(item["content"], str), f"content must be string or null, got {type(item['content'])}"
    
    if "filename" in item and item["filename"] is not None:
        assert isinstance(item["filename"], str), f"filename must be string or null, got {type(item['filename'])}"
    
    if "processed_at" in item and item["processed_at"] is not None:
        validate_iso8601_date(item["processed_at"])


def validate_document_shape(doc: dict) -> None:
    """Validate document matches Swift AgentDocument schema.
    
    Swift model expects (with .convertFromSnakeCase):
    - id: String
    - title: String
    - content: String
    - source: String?
    - status: String
    - isRead: Bool (snake_case: is_read)
    - contentIsTruncated: Bool (snake_case: content_is_truncated)
    - createdAt: Date (snake_case: created_at)
    - updatedAt: Date (snake_case: updated_at)
    """
    assert "id" in doc, "Missing required field: id"
    assert isinstance(doc["id"], str), f"id must be string, got {type(doc['id'])}"
    
    assert "title" in doc, "Missing required field: title"
    assert isinstance(doc["title"], str), f"title must be string, got {type(doc['title'])}"
    
    assert "content" in doc, "Missing required field: content"
    assert isinstance(doc["content"], str), f"content must be string, got {type(doc['content'])}"
    
    assert "status" in doc, "Missing required field: status"
    assert isinstance(doc["status"], str), f"status must be string, got {type(doc['status'])}"
    
    assert "is_read" in doc, "Missing required field: is_read"
    assert isinstance(doc["is_read"], bool), f"is_read must be bool, got {type(doc['is_read'])}"
    
    assert "content_is_truncated" in doc, "Missing required field: content_is_truncated"
    assert isinstance(doc["content_is_truncated"], bool), f"content_is_truncated must be bool, got {type(doc['content_is_truncated'])}"
    
    assert "created_at" in doc, "Missing required field: created_at"
    validate_iso8601_date(doc["created_at"])
    
    assert "updated_at" in doc, "Missing required field: updated_at"
    validate_iso8601_date(doc["updated_at"])
    
    if "source" in doc and doc["source"] is not None:
        assert isinstance(doc["source"], str), f"source must be string or null, got {type(doc['source'])}"


# ============================================================================
# API Contract Tests
# ============================================================================

@pytest.mark.asyncio
async def test_tasks_list_endpoint_contract(client: AsyncClient, sample_project):
    """Verify /api/tasks list endpoint returns correct shape."""
    # Create a task with full field coverage
    task_data = {
        "id": "contract-test-task-1",
        "title": "Contract Test Task",
        "status": "active",
        "owner": "lobs",
        "project_id": sample_project["id"],
        "notes": "Test notes",
        "work_state": "in_progress",
        "review_state": "pending",
        "sort_order": 5,
        "pinned": True,
        "shape": "deep",
        "agent": "programmer",
        "tracking_mode": "inbox",
        "workspace_context": "test-workspace",
        "user_context": "test-user",
        "model_tier": "standard",
    }
    create_response = await client.post("/api/tasks", json=task_data)
    assert create_response.status_code == 200
    
    # Fetch tasks list
    response = await client.get("/api/tasks")
    assert response.status_code == 200
    
    tasks = response.json()
    assert isinstance(tasks, list), "Expected list of tasks"
    assert len(tasks) > 0, "Expected at least one task"
    
    # Validate each task shape
    for task in tasks:
        validate_task_shape(task)


@pytest.mark.asyncio
async def test_tasks_create_endpoint_contract(client: AsyncClient, sample_project):
    """Verify /api/tasks POST endpoint returns correct shape."""
    task_data = {
        "id": "contract-test-task-2",
        "title": "Another Test Task",
        "status": "inbox",
        "project_id": sample_project["id"],
    }
    
    response = await client.post("/api/tasks", json=task_data)
    assert response.status_code == 200
    
    task = response.json()
    validate_task_shape(task)
    
    # Verify created task has expected values
    assert task["id"] == "contract-test-task-2"
    assert task["title"] == "Another Test Task"
    assert task["status"] == "inbox"


@pytest.mark.asyncio
async def test_tasks_get_endpoint_contract(client: AsyncClient, sample_task):
    """Verify /api/tasks/{id} GET endpoint returns correct shape."""
    response = await client.get(f"/api/tasks/{sample_task['id']}")
    assert response.status_code == 200
    
    task = response.json()
    validate_task_shape(task)


@pytest.mark.asyncio
async def test_projects_list_endpoint_contract(client: AsyncClient):
    """Verify /api/projects endpoint returns correct shape."""
    # Create a project with full field coverage
    project_data = {
        "id": "contract-test-project-1",
        "title": "Contract Test Project",
        "notes": "Test project notes",
        "archived": False,
        "type": "kanban",
        "sort_order": 10,
    }
    create_response = await client.post("/api/projects", json=project_data)
    assert create_response.status_code == 200
    
    # Fetch projects list
    response = await client.get("/api/projects")
    assert response.status_code == 200
    
    projects = response.json()
    assert isinstance(projects, list), "Expected list of projects"
    assert len(projects) > 0, "Expected at least one project"
    
    # Validate each project shape
    for project in projects:
        validate_project_shape(project)


@pytest.mark.asyncio
async def test_projects_create_endpoint_contract(client: AsyncClient):
    """Verify /api/projects POST endpoint returns correct shape."""
    project_data = {
        "id": "contract-test-project-2",
        "title": "Another Test Project",
        "type": "research",
    }
    
    response = await client.post("/api/projects", json=project_data)
    assert response.status_code == 200
    
    project = response.json()
    validate_project_shape(project)
    
    # Verify created project has expected values
    assert project["id"] == "contract-test-project-2"
    assert project["title"] == "Another Test Project"
    assert project["type"] == "research"


@pytest.mark.asyncio
async def test_chat_sessions_endpoint_contract(client: AsyncClient):
    """Verify /api/chat/sessions endpoint returns correct shape."""
    # Create a chat session
    session_data = {"label": "Test Chat Session"}
    create_response = await client.post("/api/chat/sessions", json=session_data)
    assert create_response.status_code == 200
    
    # Fetch sessions list
    response = await client.get("/api/chat/sessions")
    assert response.status_code == 200
    
    sessions = response.json()
    assert isinstance(sessions, list), "Expected list of sessions"
    assert len(sessions) > 0, "Expected at least one session"
    
    # Validate each session shape
    for session in sessions:
        validate_chat_session_shape(session)


@pytest.mark.asyncio
async def test_chat_messages_endpoint_contract(client: AsyncClient):
    """Verify /api/chat/sessions/{id}/messages endpoint returns correct shape."""
    # Create a chat session
    session_data = {"label": "Test Chat for Messages"}
    session_response = await client.post("/api/chat/sessions", json=session_data)
    assert session_response.status_code == 200
    session = session_response.json()
    session_id = session["id"]
    
    # Create a message
    message_data = {
        "role": "user",
        "content": "Test message",
    }
    create_response = await client.post(
        f"/api/chat/sessions/{session_id}/messages",
        json=message_data
    )
    assert create_response.status_code == 200
    
    # Fetch messages
    response = await client.get(f"/api/chat/sessions/{session_id}/messages")
    assert response.status_code == 200
    
    messages = response.json()
    assert isinstance(messages, list), "Expected list of messages"
    assert len(messages) > 0, "Expected at least one message"
    
    # Validate each message shape
    for message in messages:
        validate_chat_message_shape(message)


@pytest.mark.asyncio
async def test_memories_endpoint_contract(client: AsyncClient):
    """Verify /api/memories endpoint returns correct shape."""
    # Create a memory
    memory_data = {
        "type": "note",
        "content": "Test memory content",
    }
    create_response = await client.post("/api/memories", json=memory_data)
    assert create_response.status_code == 200
    
    # Fetch memories
    response = await client.get("/api/memories")
    assert response.status_code == 200
    
    memories = response.json()
    assert isinstance(memories, list), "Expected list of memories"
    assert len(memories) > 0, "Expected at least one memory"
    
    # Validate each memory shape
    for memory in memories:
        validate_memory_shape(memory)


@pytest.mark.asyncio
async def test_agents_endpoint_contract(client: AsyncClient):
    """Verify /api/agents endpoint returns correct shape."""
    response = await client.get("/api/agents")
    assert response.status_code == 200
    
    agents = response.json()
    assert isinstance(agents, list), "Expected list of agents"
    
    # If there are agents, validate their shape
    if len(agents) > 0:
        for agent in agents:
            validate_agent_shape(agent)


@pytest.mark.asyncio
async def test_inbox_endpoint_contract(client: AsyncClient):
    """Verify /api/inbox endpoint returns correct shape."""
    # Create an inbox item
    item_data = {
        "id": "contract-test-inbox-1",
        "title": "Test Inbox Item",
        "filename": "test.txt",
        "content": "Test content",
        "is_read": False,
    }
    create_response = await client.post("/api/inbox", json=item_data)
    assert create_response.status_code == 200
    
    # Fetch inbox items
    response = await client.get("/api/inbox")
    assert response.status_code == 200
    
    items = response.json()
    assert isinstance(items, list), "Expected list of inbox items"
    assert len(items) > 0, "Expected at least one inbox item"
    
    # Validate each item shape
    for item in items:
        validate_inbox_item_shape(item)


@pytest.mark.asyncio
async def test_documents_endpoint_contract(client: AsyncClient):
    """Verify /api/documents endpoint returns correct shape."""
    # Create a document
    doc_data = {
        "id": "contract-test-doc-1",
        "title": "Test Document",
        "content": "Test document content",
        "source": "writer",
        "status": "pending",
        "is_read": False,
        "content_is_truncated": False,
    }
    create_response = await client.post("/api/documents", json=doc_data)
    assert create_response.status_code == 200
    
    # Fetch documents
    response = await client.get("/api/documents")
    assert response.status_code == 200
    
    docs = response.json()
    assert isinstance(docs, list), "Expected list of documents"
    assert len(docs) > 0, "Expected at least one document"
    
    # Validate each document shape
    for doc in docs:
        validate_document_shape(doc)


@pytest.mark.asyncio
async def test_health_endpoint_contract(client: AsyncClient):
    """Verify /api/health endpoint returns correct shape."""
    response = await client.get("/api/health")
    assert response.status_code == 200
    
    health = response.json()
    assert isinstance(health, dict), "Expected health object"
    assert "status" in health, "Missing required field: status"
    assert health["status"] in ["healthy", "unhealthy"], f"Invalid status: {health['status']}"


@pytest.mark.asyncio
async def test_status_endpoint_contract(client: AsyncClient):
    """Verify /api/status endpoint returns correct shape."""
    response = await client.get("/api/status")
    assert response.status_code == 200
    
    status = response.json()
    assert isinstance(status, dict), "Expected status object"
    
    # Basic status fields expected by Swift clients
    # Note: exact fields may vary, but should be a dict
    assert len(status) >= 0, "Status should return an object"


# ============================================================================
# Edge Case Tests
# ============================================================================

@pytest.mark.asyncio
async def test_task_with_null_optional_fields(client: AsyncClient, sample_project):
    """Verify tasks with null optional fields don't break Swift clients."""
    task_data = {
        "id": "contract-test-minimal-task",
        "title": "Minimal Task",
        "status": "inbox",
        "project_id": sample_project["id"],
        # All other fields omitted/null
    }
    
    response = await client.post("/api/tasks", json=task_data)
    assert response.status_code == 200
    
    task = response.json()
    validate_task_shape(task)
    
    # Verify minimal fields present
    assert task["id"] == "contract-test-minimal-task"
    assert task["title"] == "Minimal Task"
    assert task["status"] == "inbox"


@pytest.mark.asyncio
async def test_project_with_null_optional_fields(client: AsyncClient):
    """Verify projects with null optional fields don't break Swift clients."""
    project_data = {
        "id": "contract-test-minimal-project",
        "title": "Minimal Project",
        # All other fields omitted/null
    }
    
    response = await client.post("/api/projects", json=project_data)
    assert response.status_code == 200
    
    project = response.json()
    validate_project_shape(project)
    
    # Verify minimal fields present
    assert project["id"] == "contract-test-minimal-project"
    assert project["title"] == "Minimal Project"


@pytest.mark.asyncio
async def test_empty_list_responses(client: AsyncClient):
    """Verify empty list responses don't break Swift clients."""
    # Test empty tasks (with filter that matches nothing)
    response = await client.get("/api/tasks?project_id=nonexistent-project")
    assert response.status_code == 200
    assert response.json() == []
    
    # Test empty agents (if no agents running)
    response = await client.get("/api/agents")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_date_field_formats(client: AsyncClient, sample_project):
    """Verify date fields are consistently ISO 8601 formatted."""
    # Create task
    task_data = {
        "id": "contract-test-date-task",
        "title": "Date Test Task",
        "status": "active",
        "project_id": sample_project["id"],
    }
    response = await client.post("/api/tasks", json=task_data)
    assert response.status_code == 200
    
    task = response.json()
    
    # Verify all date fields are valid ISO 8601
    validate_iso8601_date(task["created_at"])
    validate_iso8601_date(task["updated_at"])
    
    # Verify dates can be parsed by Python datetime
    from datetime import datetime
    created = datetime.fromisoformat(task["created_at"].replace("Z", "+00:00"))
    updated = datetime.fromisoformat(task["updated_at"].replace("Z", "+00:00"))
    
    assert created <= updated, "created_at should be before or equal to updated_at"


@pytest.mark.asyncio
async def test_snake_case_consistency(client: AsyncClient, sample_project):
    """Verify all API responses use snake_case (for Swift's .convertFromSnakeCase)."""
    # Create task with fields that should be snake_case
    task_data = {
        "id": "contract-test-snake-case",
        "title": "Snake Case Test",
        "status": "active",
        "project_id": sample_project["id"],
        "work_state": "in_progress",
        "review_state": "pending",
        "sort_order": 1,
        "artifact_path": "/path/to/artifact",
        "started_at": "2024-01-01T00:00:00Z",
        "finished_at": "2024-01-02T00:00:00Z",
        "blocked_by": ["other-task-id"],
        "tracking_mode": "inbox",
        "github_issue_number": 123,
        "github_issue_url": "https://github.com/test/repo/issues/123",
        "github_issue_state": "open",
        "workspace_context": "test-workspace",
        "user_context": "test-user",
        "model_tier": "standard",
    }
    
    response = await client.post("/api/tasks", json=task_data)
    assert response.status_code == 200
    
    task = response.json()
    
    # Verify snake_case fields are present (not camelCase)
    snake_case_fields = [
        "created_at", "updated_at", "project_id", "work_state", "review_state",
        "sort_order", "artifact_path", "started_at", "finished_at", "blocked_by",
        "tracking_mode", "github_issue_number", "github_issue_url", "github_issue_state",
        "workspace_context", "user_context", "model_tier"
    ]
    
    for field in snake_case_fields:
        # Field should exist if it was in the input
        if field in task_data:
            assert field in task, f"Expected snake_case field '{field}' in response"
    
    # Verify camelCase versions DON'T exist (would confuse Swift decoder)
    camel_case_variants = [
        "createdAt", "updatedAt", "projectId", "workState", "reviewState",
        "sortOrder", "artifactPath", "startedAt", "finishedAt", "blockedBy",
        "trackingMode", "githubIssueNumber", "githubIssueUrl", "githubIssueState",
        "workspaceContext", "userContext", "modelTier"
    ]
    
    for field in camel_case_variants:
        assert field not in task, f"Found camelCase field '{field}' - API must use snake_case only"
