"""Tests for the test helpers library.

Ensures factories, mocks, and assertions work correctly.
"""

import pytest
from unittest.mock import AsyncMock, Mock
from httpx import Response
from datetime import datetime

from tests.helpers import (
    # Factories
    create_project_data,
    create_task_data,
    create_inbox_data,
    create_document_data,
    create_agent_data,
    create_memory_data,
    create_topic_data,
    create_calendar_event_data,
    # Mocks
    mock_worker_manager,
    mock_scanner,
    mock_monitor,
    mock_scheduler,
    mock_reflection_manager,
    mock_openclaw_bridge,
    # Assertions
    assert_response_success,
    assert_response_error,
    assert_has_fields,
    assert_task_status,
    assert_list_response,
    assert_timestamp_fields,
    assert_json_schema,
)


class TestFactories:
    """Test data factory functions."""
    
    def test_create_project_data_defaults(self):
        """Test project factory with defaults."""
        data = create_project_data()
        
        assert "id" in data
        assert data["title"] == "Test Project"
        assert data["type"] == "kanban"
        assert data["archived"] is False
        assert data["sort_order"] == 0
    
    def test_create_project_data_overrides(self):
        """Test project factory with overrides."""
        data = create_project_data(
            id="custom-id",
            title="Custom Project",
            archived=True,
            custom_field="extra"
        )
        
        assert data["id"] == "custom-id"
        assert data["title"] == "Custom Project"
        assert data["archived"] is True
        assert data["custom_field"] == "extra"
    
    def test_create_task_data_defaults(self):
        """Test task factory with defaults."""
        data = create_task_data()
        
        assert "id" in data
        assert data["title"] == "Test Task"
        assert data["status"] == "inbox"
        assert data["pinned"] is False
    
    def test_create_task_data_with_optional_fields(self):
        """Test task factory with optional fields."""
        data = create_task_data(
            agent="programmer",
            deadline="2024-12-31",
            tags=["urgent", "bug"]
        )
        
        assert data["agent"] == "programmer"
        assert data["deadline"] == "2024-12-31"
        assert data["tags"] == ["urgent", "bug"]
    
    def test_create_inbox_data(self):
        """Test inbox factory."""
        data = create_inbox_data(title="Custom Inbox Item", filename="custom.md")
        
        assert data["title"] == "Custom Inbox Item"
        assert data["filename"] == "custom.md"
        assert data["is_read"] is False
    
    def test_create_document_data(self):
        """Test document factory."""
        data = create_document_data(source="researcher", status="approved")
        
        assert data["source"] == "researcher"
        assert data["status"] == "approved"
        assert data["content_is_truncated"] is False
    
    def test_create_agent_data(self):
        """Test agent status factory."""
        data = create_agent_data(agent_id="writer", status="busy")
        
        assert data["agent_id"] == "writer"
        assert data["status"] == "busy"
        assert "last_activity" in data
    
    def test_create_memory_data(self):
        """Test memory factory."""
        data = create_memory_data(type="lesson", tags=["testing", "python"])
        
        assert data["type"] == "lesson"
        assert data["tags"] == ["testing", "python"]
    
    def test_create_topic_data(self):
        """Test topic factory."""
        data = create_topic_data(icon="🧪", auto_created=True)
        
        assert data["icon"] == "🧪"
        assert data["auto_created"] is True
    
    def test_create_calendar_event_data(self):
        """Test calendar event factory."""
        data = create_calendar_event_data(all_day=True, location="Office")
        
        assert data["all_day"] is True
        assert data["location"] == "Office"
        assert "start_time" in data
        assert "end_time" in data


class TestMocks:
    """Test mock helper functions."""
    
    @pytest.mark.asyncio
    async def test_mock_worker_manager(self):
        """Test worker manager mock."""
        mock = mock_worker_manager(spawn_result=True, sweep_requested=True)
        
        assert isinstance(mock, AsyncMock)
        assert mock.sweep_requested is True
        
        result = await mock.spawn_worker()
        assert result is True
        
        workers = await mock.get_active_workers()
        assert workers == []
    
    @pytest.mark.asyncio
    async def test_mock_scanner(self):
        """Test scanner mock."""
        work_items = [{"id": 1, "title": "Task 1"}]
        mock = mock_scanner(work_items=work_items)
        
        result = await mock.scan_for_work()
        assert result == work_items
    
    @pytest.mark.asyncio
    async def test_mock_monitor(self):
        """Test monitor mock."""
        stuck_tasks = [{"id": 123, "status": "in_progress"}]
        mock = mock_monitor(stuck_tasks=stuck_tasks)
        
        result = await mock.check_stuck_tasks()
        assert result == stuck_tasks
    
    @pytest.mark.asyncio
    async def test_mock_scheduler(self):
        """Test scheduler mock."""
        due_events = [{"id": 1, "title": "Event"}]
        mock = mock_scheduler(due_events=due_events)
        
        result = await mock.check_due_events()
        assert result == due_events
    
    @pytest.mark.asyncio
    async def test_mock_reflection_manager(self):
        """Test reflection manager mock."""
        mock = mock_reflection_manager(should_reflect=True)
        
        result = await mock.should_trigger_reflection()
        assert result is True
    
    @pytest.mark.asyncio
    async def test_mock_openclaw_bridge(self):
        """Test openclaw bridge mock."""
        webhook_result = {"status": "processed"}
        mock = mock_openclaw_bridge(webhook_result=webhook_result)
        
        result = await mock.handle_webhook()
        assert result == webhook_result
    
    def test_mock_with_custom_attributes(self):
        """Test setting custom attributes on mocks."""
        mock = mock_worker_manager(custom_attr="custom_value")
        
        assert mock.custom_attr == "custom_value"


class TestAssertions:
    """Test assertion helper functions."""
    
    def test_assert_response_success(self):
        """Test successful response assertion."""
        # Mock successful response
        response = Mock(spec=Response)
        response.status_code = 200
        
        # Should not raise
        assert_response_success(response, expected_status=200)
    
    def test_assert_response_success_fails(self):
        """Test response assertion fails on wrong status."""
        response = Mock(spec=Response)
        response.status_code = 400
        response.json = Mock(return_value={"detail": "Bad request"})
        
        with pytest.raises(AssertionError) as exc_info:
            assert_response_success(response, expected_status=200)
        
        assert "Expected status 200, got 400" in str(exc_info.value)
    
    def test_assert_response_error(self):
        """Test error response assertion."""
        response = Mock(spec=Response)
        response.status_code = 404
        response.json = Mock(return_value={"detail": "Not found"})
        
        # Should not raise
        assert_response_error(response, expected_status=404, expected_detail="Not found")
    
    def test_assert_has_fields_success(self):
        """Test field presence assertion."""
        data = {"id": 1, "title": "Test", "status": "active"}
        
        # Should not raise
        assert_has_fields(data, ["id", "title", "status"])
    
    def test_assert_has_fields_fails(self):
        """Test field assertion fails on missing fields."""
        data = {"id": 1, "title": "Test"}
        
        with pytest.raises(AssertionError) as exc_info:
            assert_has_fields(data, ["id", "title", "status", "created_at"])
        
        assert "Missing required fields" in str(exc_info.value)
        assert "status" in str(exc_info.value)
        assert "created_at" in str(exc_info.value)
    
    def test_assert_task_status(self):
        """Test task status assertion."""
        task = {"id": 1, "status": "completed"}
        
        # Should not raise
        assert_task_status(task, expected_status="completed")
        
        # Should raise
        with pytest.raises(AssertionError) as exc_info:
            assert_task_status(task, expected_status="in_progress")
        
        assert "Expected task status 'in_progress', got 'completed'" in str(exc_info.value)
    
    def test_assert_list_response(self):
        """Test list response assertion."""
        response = Mock(spec=Response)
        response.status_code = 200
        response.json = Mock(return_value=[
            {"id": 1, "title": "Item 1"},
            {"id": 2, "title": "Item 2"}
        ])
        
        result = assert_list_response(
            response,
            min_length=1,
            max_length=5,
            item_schema=["id", "title"]
        )
        
        assert len(result) == 2
        assert result[0]["id"] == 1
    
    def test_assert_list_response_fails_on_length(self):
        """Test list assertion fails on length constraints."""
        response = Mock(spec=Response)
        response.status_code = 200
        response.json = Mock(return_value=[{"id": 1}])
        
        with pytest.raises(AssertionError) as exc_info:
            assert_list_response(response, min_length=5)
        
        assert "Expected at least 5 items" in str(exc_info.value)
    
    def test_assert_timestamp_fields(self):
        """Test timestamp field validation."""
        data = {
            "id": 1,
            "created_at": "2024-01-15T12:00:00Z",
            "updated_at": "2024-01-15T13:00:00Z"
        }
        
        # Should not raise
        assert_timestamp_fields(data)
    
    def test_assert_timestamp_fields_custom(self):
        """Test timestamp validation with custom fields."""
        data = {
            "started_at": "2024-01-15T12:00:00Z",
            "completed_at": "2024-01-15T13:00:00Z"
        }
        
        # Should not raise
        assert_timestamp_fields(data, fields=["started_at", "completed_at"])
    
    def test_assert_json_schema(self):
        """Test JSON schema type validation."""
        data = {
            "id": 123,
            "title": "Test",
            "active": True,
            "count": 5
        }
        
        schema = {
            "id": int,
            "title": str,
            "active": bool,
            "count": int
        }
        
        # Should not raise
        assert_json_schema(data, schema)
    
    def test_assert_json_schema_fails_on_type(self):
        """Test schema validation fails on wrong types."""
        data = {"id": "not-an-int", "title": "Test"}
        schema = {"id": int, "title": str}
        
        with pytest.raises(AssertionError) as exc_info:
            assert_json_schema(data, schema)
        
        assert "expected int, got str" in str(exc_info.value)


class TestFixtures:
    """Test additional fixtures from fixtures.py."""
    
    @pytest.mark.asyncio
    async def test_multiple_projects_fixture(self, multiple_projects):
        """Test multiple_projects fixture."""
        assert len(multiple_projects) == 3
        assert all("id" in p for p in multiple_projects)
        assert all("title" in p for p in multiple_projects)
    
    @pytest.mark.asyncio
    async def test_multiple_tasks_fixture(self, multiple_tasks):
        """Test multiple_tasks fixture."""
        assert len(multiple_tasks) == 5
        
        statuses = [t["status"] for t in multiple_tasks]
        assert "inbox" in statuses
        assert "queued" in statuses
        assert "completed" in statuses
    
    @pytest.mark.asyncio
    async def test_agent_status_fixture(self, agent_status):
        """Test agent_status fixture."""
        assert agent_status["agent_id"] == "programmer"
        assert agent_status["status"] == "active"
    
    @pytest.mark.asyncio
    async def test_sample_topic_fixture(self, sample_topic):
        """Test sample_topic fixture."""
        assert "id" in sample_topic
        assert sample_topic["title"] == "Test Topic"
    
    @pytest.mark.asyncio
    async def test_queued_task_fixture(self, queued_task):
        """Test queued_task fixture."""
        assert queued_task["status"] == "queued"
        assert queued_task["agent"] == "programmer"
    
    @pytest.mark.asyncio
    async def test_blocked_task_fixture(self, blocked_task):
        """Test blocked_task fixture."""
        assert blocked_task["status"] == "blocked"
    
    @pytest.mark.asyncio
    async def test_completed_task_fixture(self, completed_task):
        """Test completed_task fixture."""
        assert completed_task["status"] == "completed"
    
    def test_api_base_headers_fixture(self, api_base_headers):
        """Test api_base_headers fixture."""
        assert "Authorization" in api_base_headers
        assert "Content-Type" in api_base_headers
        assert api_base_headers["Content-Type"] == "application/json"
