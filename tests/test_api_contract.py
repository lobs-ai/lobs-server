"""API contract tests - validate server responses match OpenAPI spec."""

import pytest
from httpx import AsyncClient
from datetime import datetime
from typing import Any, Dict

# Schema validators are defined in conftest.py and available globally
from .conftest import (
    validate_task_schema,
    validate_chat_session_schema,
    validate_chat_message_schema,
    validate_memory_schema,
    validate_project_schema,
)


class TestAPIContract:
    """
    Validate critical endpoints against OpenAPI spec.
    
    These tests ensure that:
    1. Response schemas match documented OpenAPI spec
    2. Required fields are always present
    3. Field types are correct
    4. Swift clients won't break on schema changes
    """
    
    # ============================================================================
    # TASKS
    # ============================================================================
    
    @pytest.mark.asyncio
    async def test_tasks_list_schema(self, client: AsyncClient):
        """Validate tasks list response schema."""
        response = await client.get("/api/tasks")
        assert response.status_code == 200
        
        tasks = response.json()
        assert isinstance(tasks, list)
        
        # If tasks exist, validate schema
        if len(tasks) > 0:
            task = tasks[0]
            validate_task_schema(task)
    
    @pytest.mark.asyncio
    async def test_task_create_schema(self, client: AsyncClient):
        """Validate task creation response schema."""
        task_data = {
            "id": "contract-test-task-1",
            "title": "Contract Test Task",
            "status": "inbox",
            "notes": "Testing API contract",
        }
        
        response = await client.post("/api/tasks", json=task_data)
        assert response.status_code == 200
        
        task = response.json()
        validate_task_schema(task)
        
        # Verify input fields are preserved
        assert task["id"] == task_data["id"]
        assert task["title"] == task_data["title"]
        assert task["status"] == task_data["status"]
        assert task["notes"] == task_data["notes"]
    
    @pytest.mark.asyncio
    async def test_task_update_schema(self, client: AsyncClient, sample_task):
        """Validate task update response schema."""
        update_data = {
            "title": "Updated Title",
            "status": "active",
        }
        
        response = await client.put(
            f"/api/tasks/{sample_task['id']}", 
            json=update_data
        )
        assert response.status_code == 200
        
        task = response.json()
        validate_task_schema(task)
        
        # Verify updates were applied
        assert task["title"] == update_data["title"]
        assert task["status"] == update_data["status"]
    
    # ============================================================================
    # CHAT
    # ============================================================================
    
    @pytest.mark.asyncio
    async def test_chat_sessions_list_schema(self, client: AsyncClient):
        """Validate chat sessions list response schema."""
        response = await client.get("/api/chat/sessions")
        assert response.status_code == 200
        
        sessions = response.json()
        assert isinstance(sessions, list)
        
        # If sessions exist, validate schema
        if len(sessions) > 0:
            session = sessions[0]
            validate_chat_session_schema(session)
    
    @pytest.mark.asyncio
    async def test_chat_session_create_schema(self, client: AsyncClient):
        """Validate chat session creation response schema."""
        session_data = {
            "session_key": "contract-test-session",
            "label": "Contract Test Session",
        }
        
        response = await client.post("/api/chat/sessions", json=session_data)
        assert response.status_code == 200
        
        session = response.json()
        validate_chat_session_schema(session)
        
        # Verify input fields are preserved
        assert session["session_key"] == session_data["session_key"]
        assert session["label"] == session_data["label"]
    
    @pytest.mark.asyncio
    async def test_chat_message_send_schema(self, client: AsyncClient):
        """Validate chat message send response schema."""
        # Create session first
        await client.post(
            "/api/chat/sessions",
            json={"session_key": "msg-contract-test"}
        )
        
        # Send message
        response = await client.post(
            "/api/chat/sessions/msg-contract-test/send",
            json={"content": "Test message"}
        )
        assert response.status_code == 200
        
        message = response.json()
        validate_chat_message_schema(message)
        
        # Verify message content
        assert message["content"] == "Test message"
        assert message["role"] == "user"
    
    @pytest.mark.asyncio
    async def test_chat_messages_list_schema(self, client: AsyncClient):
        """Validate chat messages list response schema."""
        session_key = "list-contract-test"
        
        # Create session and send message
        await client.post(
            "/api/chat/sessions",
            json={"session_key": session_key}
        )
        await client.post(
            f"/api/chat/sessions/{session_key}/send",
            json={"content": "Test"}
        )
        
        # Get messages
        response = await client.get(f"/api/chat/sessions/{session_key}/messages")
        assert response.status_code == 200
        
        messages = response.json()
        assert isinstance(messages, list)
        assert len(messages) > 0
        
        for message in messages:
            validate_chat_message_schema(message)
    
            pytest.fail(f"Invalid datetime format: {e}")
    
    # ============================================================================
    # MEMORY
    # ============================================================================
    
    @pytest.mark.asyncio
    async def test_memories_list_schema(self, client: AsyncClient):
        """Validate memories list response schema."""
        response = await client.get("/api/memories")
        assert response.status_code == 200
        
        memories = response.json()
        assert isinstance(memories, list)
        
        # If memories exist, validate schema
        if len(memories) > 0:
            memory = memories[0]
            validate_memory_schema(memory)
    
    @pytest.mark.asyncio
    async def test_memory_create_schema(self, client: AsyncClient):
        """Validate memory creation response schema."""
        memory_data = {
            "id": "contract-test-memory-1",
            "category": "daily",
            "title": "Contract Test Memory",
            "content": "Testing memory API contract",
        }
        
        response = await client.post("/api/memories", json=memory_data)
        assert response.status_code == 200
        
        memory = response.json()
        validate_memory_schema(memory)
        
        # Verify input fields are preserved
        assert memory["id"] == memory_data["id"]
        assert memory["category"] == memory_data["category"]
        assert memory["title"] == memory_data["title"]
        assert memory["content"] == memory_data["content"]
    
        # Validate datetime format
        try:
            datetime.fromisoformat(memory["created_at"].replace("Z", "+00:00"))
        except ValueError as e:
            pytest.fail(f"Invalid datetime format: {e}")
    
    # ============================================================================
    # OPENAPI SPEC VALIDATION
    # ============================================================================
    
    @pytest.mark.asyncio
    async def test_openapi_spec_available(self, client: AsyncClient):
        """Ensure OpenAPI spec is accessible."""
        # FastAPI auto-generates OpenAPI spec at /openapi.json
        response = await client.get("/openapi.json")
        assert response.status_code == 200
        
        spec = response.json()
        
        # Validate basic OpenAPI structure
        assert "openapi" in spec
        assert "info" in spec
        assert "paths" in spec
        
        # Validate critical endpoints are documented
        paths = spec["paths"]
        assert "/api/tasks" in paths
        assert "/api/chat/sessions" in paths
        assert "/api/memories" in paths
    
    @pytest.mark.asyncio
    async def test_critical_endpoints_in_spec(self, client: AsyncClient):
        """Validate all critical endpoints are in OpenAPI spec."""
        response = await client.get("/openapi.json")
        spec = response.json()
        paths = spec["paths"]
        
        critical_endpoints = [
            # Tasks
            "/api/tasks",
            "/api/tasks/{task_id}",
            
            # Chat
            "/api/chat/sessions",
            "/api/chat/sessions/{session_key}/messages",
            "/api/chat/sessions/{session_key}/send",
            
            # Memory
            "/api/memories",
            "/api/memories/{memory_id}",
            
            # Projects
            "/api/projects",
            "/api/projects/{project_id}",
            
            # Health
            "/api/health",
        ]
        
        for endpoint in critical_endpoints:
            assert endpoint in paths, f"Critical endpoint {endpoint} missing from OpenAPI spec"
    
    @pytest.mark.asyncio
    async def test_response_schemas_defined(self, client: AsyncClient):
        """Validate response schemas are properly defined in spec."""
        response = await client.get("/openapi.json")
        spec = response.json()
        
        # Check that schemas are defined
        assert "components" in spec
        assert "schemas" in spec["components"]
        
        schemas = spec["components"]["schemas"]
        
        # Validate critical schemas exist
        critical_schemas = [
            "Task",
            "Project",
        ]
        
        for schema_name in critical_schemas:
            assert schema_name in schemas, f"Schema {schema_name} missing from OpenAPI spec"
    
    # ============================================================================
    # BACKWARD COMPATIBILITY
    # ============================================================================
    
    @pytest.mark.asyncio
    async def test_task_fields_backward_compatible(self, client: AsyncClient):
        """Ensure task fields remain backward compatible."""
        # These fields must always exist in task responses to avoid breaking Swift clients
        required_fields = [
            "id",
            "title",
            "status",
            "created_at",
            "updated_at",
        ]
        
        # Create a task
        task_data = {
            "id": "compat-test",
            "title": "Compatibility Test",
            "status": "inbox",
        }
        response = await client.post("/api/tasks", json=task_data)
        task = response.json()
        
        # Verify all required fields exist
        for field in required_fields:
            assert field in task, f"Required field '{field}' missing from task response"
    
    @pytest.mark.asyncio
    async def test_chat_message_fields_backward_compatible(self, client: AsyncClient):
        """Ensure chat message fields remain backward compatible."""
        required_fields = [
            "id",
            "session_id",
            "role",
            "content",
            "created_at",
        ]
        
        # Create session and message
        await client.post(
            "/api/chat/sessions",
            json={"session_key": "compat-test"}
        )
        response = await client.post(
            "/api/chat/sessions/compat-test/send",
            json={"content": "Test"}
        )
        message = response.json()
        
        # Verify all required fields exist
        for field in required_fields:
            assert field in message, f"Required field '{field}' missing from message response"
