"""Tests for topics API endpoints."""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from tests.helpers import (
    create_topic_data,
    assert_created,
    assert_list_response,
    assert_response_success,
    assert_response_error,
    assert_not_found,
    assert_updated,
    assert_deleted,
)


class TestTopicsList:
    """Tests for GET /api/topics endpoint."""
    
    @pytest.mark.asyncio
    async def test_list_topics_empty(self, client: AsyncClient):
        """Test listing topics when database is empty."""
        response = await client.get("/api/topics")
        assert_list_response(response, min_length=0)
    
    @pytest.mark.asyncio
    async def test_list_topics_pagination(self, client: AsyncClient):
        """Test topic list pagination."""
        response = await client.get("/api/topics?limit=5&offset=0")
        topics = assert_list_response(response, max_length=5)


class TestTopicsCreate:
    """Tests for POST /api/topics endpoint."""
    
    @pytest.mark.asyncio
    async def test_create_topic_success(self, client: AsyncClient):
        """Test creating a new topic successfully."""
        topic_data = create_topic_data(
            id="topic-create-test",
            title="Create Test Topic",
            description="Testing topic creation",
            icon="🧪",
            auto_created=False
        )
        response = await client.post("/api/topics", json=topic_data)
        data = assert_created(response, expected_fields=["title", "icon"])
        assert data["id"] == topic_data["id"]
        assert data["title"] == topic_data["title"]
        assert data["icon"] == topic_data["icon"]
    
    @pytest.mark.asyncio
    async def test_create_topic_duplicate_title(self, client: AsyncClient):
        """Test that creating a topic with duplicate title fails."""
        # Create first topic
        topic1 = create_topic_data(id="topic-dup-1", title="Duplicate Test")
        await client.post("/api/topics", json=topic1)
        
        # Try to create second with same title
        topic2 = create_topic_data(id="topic-dup-2", title="Duplicate Test")
        response = await client.post("/api/topics", json=topic2)
        assert_response_error(response, expected_status=400, expected_detail="already exists")
    
    @pytest.mark.asyncio
    async def test_create_topic_minimal(self, client: AsyncClient):
        """Test creating a topic with minimal required fields."""
        topic_data = create_topic_data(id="topic-minimal-test", title="Minimal Topic")
        response = await client.post("/api/topics", json=topic_data)
        data = assert_created(response)
        assert data["title"] == "Minimal Topic"
        assert data["auto_created"] == False  # default value


class TestTopicsGet:
    """Tests for GET /api/topics/{topic_id} endpoint."""
    
    @pytest.mark.asyncio
    async def test_get_topic_success(self, client: AsyncClient):
        """Test getting a specific topic."""
        # Create a topic first
        topic_data = create_topic_data(id="topic-get-test", title="Get Test Topic")
        await client.post("/api/topics", json=topic_data)
        
        # Get it
        response = await client.get(f"/api/topics/{topic_data['id']}")
        assert_response_success(response)
        data = response.json()
        assert data["id"] == topic_data["id"]
        assert data["title"] == topic_data["title"]
    
    @pytest.mark.asyncio
    async def test_get_topic_not_found(self, client: AsyncClient):
        """Test getting a non-existent topic."""
        response = await client.get("/api/topics/nonexistent-id")
        assert_not_found(response, resource_type="topic")


class TestTopicsUpdate:
    """Tests for PUT /api/topics/{topic_id} endpoint."""
    
    @pytest.mark.asyncio
    async def test_update_topic_success(self, client: AsyncClient):
        """Test updating a topic."""
        # Create a topic first
        topic_data = create_topic_data(id="topic-update-test", title="Update Test Topic")
        await client.post("/api/topics", json=topic_data)
        
        # Update it
        update_data = {
            "description": "Updated description",
            "icon": "📝"
        }
        response = await client.put(f"/api/topics/{topic_data['id']}", json=update_data)
        data = assert_updated(response, expected_changes={
            "description": "Updated description",
            "icon": "📝"
        })
        assert data["title"] == "Update Test Topic"  # Should remain unchanged
    
    @pytest.mark.asyncio
    async def test_update_topic_title(self, client: AsyncClient):
        """Test updating a topic's title."""
        # Create a topic first
        topic_data = create_topic_data(id="topic-title-update-test", title="Original Title")
        await client.post("/api/topics", json=topic_data)
        
        # Update title
        update_data = {"title": "Updated Topic Title"}
        response = await client.put(f"/api/topics/{topic_data['id']}", json=update_data)
        data = assert_updated(response, expected_changes={"title": "Updated Topic Title"})
    
    @pytest.mark.asyncio
    async def test_update_topic_not_found(self, client: AsyncClient):
        """Test updating a non-existent topic."""
        update_data = {"description": "Test"}
        response = await client.put("/api/topics/nonexistent-id", json=update_data)
        assert_not_found(response)


class TestTopicsDelete:
    """Tests for DELETE /api/topics/{topic_id} endpoint."""
    
    @pytest.mark.asyncio
    async def test_delete_topic_success(self, client: AsyncClient):
        """Test deleting a topic."""
        # Create a topic to delete
        topic_data = create_topic_data(id="topic-delete-test", title="Topic to Delete")
        await client.post("/api/topics", json=topic_data)
        
        # Delete it
        response = await client.delete(f"/api/topics/{topic_data['id']}")
        assert_deleted(response)
        
        # Verify it's gone
        get_response = await client.get(f"/api/topics/{topic_data['id']}")
        assert_not_found(get_response)
    
    @pytest.mark.asyncio
    async def test_delete_topic_not_found(self, client: AsyncClient):
        """Test deleting a non-existent topic."""
        response = await client.delete("/api/topics/nonexistent-id")
        assert_not_found(response)


class TestTopicsDocuments:
    """Tests for GET /api/topics/{topic_id}/documents endpoint."""
    
    @pytest.mark.asyncio
    async def test_get_topic_documents_empty(self, client: AsyncClient):
        """Test getting documents for a topic with no documents."""
        # Create a topic first
        topic_data = create_topic_data(id="topic-docs-test", title="Docs Test Topic")
        await client.post("/api/topics", json=topic_data)
        
        # Get documents (should be empty)
        response = await client.get(f"/api/topics/{topic_data['id']}/documents")
        docs = assert_list_response(response, min_length=0)
        assert len(docs) == 0
    
    @pytest.mark.asyncio
    async def test_get_topic_documents_not_found(self, client: AsyncClient):
        """Test getting documents for a non-existent topic."""
        response = await client.get("/api/topics/nonexistent-id/documents")
        assert_not_found(response)


class TestTopicsResearchRequests:
    """Tests for topic research request endpoints."""
    
    @pytest.mark.asyncio
    async def test_create_research_request_for_topic(self, client: AsyncClient):
        """Test creating a research request linked to a topic."""
        # Create a topic first
        topic_data = create_topic_data(id="topic-research-test", title="Research Topic")
        await client.post("/api/topics", json=topic_data)
        
        # Create a research request for this topic
        request_data = {
            "id": "req-001",
            "prompt": "Research AI safety",
            "status": "pending",
            "author": "rafe"
        }
        response = await client.post(f"/api/topics/{topic_data['id']}/requests", json=request_data)
        data = assert_created(response, expected_fields=["prompt"])
        assert data["id"] == request_data["id"]
        assert data["prompt"] == request_data["prompt"]
        assert data["topic_id"] == topic_data["id"]  # Verify topic_id is set
    
    @pytest.mark.asyncio
    async def test_create_research_request_topic_not_found(self, client: AsyncClient):
        """Test creating a research request for non-existent topic fails."""
        request_data = {
            "id": "req-002",
            "prompt": "Research something",
            "status": "pending"
        }
        response = await client.post("/api/topics/nonexistent-topic/requests", json=request_data)
        assert_not_found(response, resource_type="topic")
    
    @pytest.mark.asyncio
    async def test_get_topic_research_requests_empty(self, client: AsyncClient):
        """Test getting research requests for a topic with no requests."""
        # Create a topic first
        topic_data = create_topic_data(id="topic-no-requests", title="Empty Topic")
        await client.post("/api/topics", json=topic_data)
        
        # Get requests (should be empty)
        response = await client.get(f"/api/topics/{topic_data['id']}/requests")
        requests = assert_list_response(response, min_length=0)
        assert len(requests) == 0
    
    @pytest.mark.asyncio
    async def test_get_topic_research_requests_with_data(self, client: AsyncClient):
        """Test getting research requests for a topic with existing requests."""
        # Create a topic
        topic_data = create_topic_data(id="topic-with-requests", title="Topic With Requests")
        await client.post("/api/topics", json=topic_data)
        
        # Create two research requests
        request1 = {
            "id": "req-topic-1",
            "prompt": "Research question 1",
            "status": "pending"
        }
        request2 = {
            "id": "req-topic-2",
            "prompt": "Research question 2",
            "status": "pending"
        }
        await client.post(f"/api/topics/{topic_data['id']}/requests", json=request1)
        await client.post(f"/api/topics/{topic_data['id']}/requests", json=request2)
        
        # Get all requests for the topic
        response = await client.get(f"/api/topics/{topic_data['id']}/requests")
        requests = assert_list_response(
            response,
            min_length=2,
            item_schema=["id", "prompt", "topic_id"]
        )
        # Verify both requests are linked to the topic
        for req in requests:
            assert req["topic_id"] == topic_data["id"]
    
    @pytest.mark.asyncio
    async def test_get_topic_research_requests_not_found(self, client: AsyncClient):
        """Test getting research requests for non-existent topic fails."""
        response = await client.get("/api/topics/nonexistent-topic/requests")
        assert_not_found(response)
    
    @pytest.mark.asyncio
    async def test_research_request_with_project_and_topic(self, client: AsyncClient, sample_project):
        """Test creating a research request with both project_id and topic_id."""
        # Create a topic
        topic_data = create_topic_data(id="topic-with-project", title="Project Topic")
        await client.post("/api/topics", json=topic_data)
        
        # Create a research request with both project and topic
        request_data = {
            "id": "req-both",
            "prompt": "Research project-specific topic",
            "status": "pending",
            "project_id": sample_project["id"]
        }
        response = await client.post(f"/api/topics/{topic_data['id']}/requests", json=request_data)
        data = assert_created(response)
        assert data["topic_id"] == topic_data["id"]
        assert data["project_id"] == sample_project["id"]


class TestTopicsIntegration:
    """Integration tests for topics."""
    
    @pytest.mark.asyncio
    async def test_auto_created_flag(self, client: AsyncClient):
        """Test that auto_created flag works correctly."""
        # Create manual topic
        manual_topic = create_topic_data(
            id="manual-topic-test",
            title="Manual Topic",
            auto_created=False
        )
        response = await client.post("/api/topics", json=manual_topic)
        data = assert_created(response)
        assert data["auto_created"] == False
        
        # Create auto topic
        auto_topic = create_topic_data(
            id="auto-topic-test",
            title="Auto Topic",
            auto_created=True
        )
        response = await client.post("/api/topics", json=auto_topic)
        data = assert_created(response)
        assert data["auto_created"] == True
