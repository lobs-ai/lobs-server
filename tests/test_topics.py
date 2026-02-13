"""Tests for topics API endpoints."""

import pytest
import pytest_asyncio
from httpx import AsyncClient


class TestTopicsList:
    """Tests for GET /api/topics endpoint."""
    
    @pytest.mark.asyncio
    async def test_list_topics_empty(self, client: AsyncClient):
        """Test listing topics when database is empty."""
        response = await client.get("/api/topics")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
    
    @pytest.mark.asyncio
    async def test_list_topics_pagination(self, client: AsyncClient):
        """Test topic list pagination."""
        response = await client.get("/api/topics?limit=5&offset=0")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) <= 5


class TestTopicsCreate:
    """Tests for POST /api/topics endpoint."""
    
    @pytest.mark.asyncio
    async def test_create_topic_success(self, client: AsyncClient):
        """Test creating a new topic successfully."""
        topic_data = {
            "id": "topic-create-test",
            "title": "Create Test Topic",
            "description": "Testing topic creation",
            "icon": "🧪",
            "auto_created": False
        }
        response = await client.post("/api/topics", json=topic_data)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == topic_data["id"]
        assert data["title"] == topic_data["title"]
        assert data["icon"] == topic_data["icon"]
        assert "created_at" in data
        assert "updated_at" in data
    
    @pytest.mark.asyncio
    async def test_create_topic_duplicate_title(self, client: AsyncClient):
        """Test that creating a topic with duplicate title fails."""
        # Create first topic
        topic1 = {
            "id": "topic-dup-1",
            "title": "Duplicate Test"
        }
        await client.post("/api/topics", json=topic1)
        
        # Try to create second with same title
        topic2 = {
            "id": "topic-dup-2",
            "title": "Duplicate Test"
        }
        response = await client.post("/api/topics", json=topic2)
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]
    
    @pytest.mark.asyncio
    async def test_create_topic_minimal(self, client: AsyncClient):
        """Test creating a topic with minimal required fields."""
        topic_data = {
            "id": "topic-minimal-test",
            "title": "Minimal Topic"
        }
        response = await client.post("/api/topics", json=topic_data)
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Minimal Topic"
        assert data["auto_created"] == False  # default value


class TestTopicsGet:
    """Tests for GET /api/topics/{topic_id} endpoint."""
    
    @pytest.mark.asyncio
    async def test_get_topic_success(self, client: AsyncClient):
        """Test getting a specific topic."""
        # Create a topic first
        topic_data = {"id": "topic-get-test", "title": "Get Test Topic"}
        await client.post("/api/topics", json=topic_data)
        
        # Get it
        response = await client.get(f"/api/topics/{topic_data['id']}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == topic_data["id"]
        assert data["title"] == topic_data["title"]
    
    @pytest.mark.asyncio
    async def test_get_topic_not_found(self, client: AsyncClient):
        """Test getting a non-existent topic."""
        response = await client.get("/api/topics/nonexistent-id")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]


class TestTopicsUpdate:
    """Tests for PUT /api/topics/{topic_id} endpoint."""
    
    @pytest.mark.asyncio
    async def test_update_topic_success(self, client: AsyncClient):
        """Test updating a topic."""
        # Create a topic first
        topic_data = {"id": "topic-update-test", "title": "Update Test Topic"}
        await client.post("/api/topics", json=topic_data)
        
        # Update it
        update_data = {
            "description": "Updated description",
            "icon": "📝"
        }
        response = await client.put(f"/api/topics/{topic_data['id']}", json=update_data)
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "Updated description"
        assert data["icon"] == "📝"
        assert data["title"] == "Update Test Topic"  # Should remain unchanged
    
    @pytest.mark.asyncio
    async def test_update_topic_title(self, client: AsyncClient):
        """Test updating a topic's title."""
        # Create a topic first
        topic_data = {"id": "topic-title-update-test", "title": "Original Title"}
        await client.post("/api/topics", json=topic_data)
        
        # Update title
        update_data = {"title": "Updated Topic Title"}
        response = await client.put(f"/api/topics/{topic_data['id']}", json=update_data)
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Updated Topic Title"
    
    @pytest.mark.asyncio
    async def test_update_topic_not_found(self, client: AsyncClient):
        """Test updating a non-existent topic."""
        update_data = {"description": "Test"}
        response = await client.put("/api/topics/nonexistent-id", json=update_data)
        assert response.status_code == 404


class TestTopicsDelete:
    """Tests for DELETE /api/topics/{topic_id} endpoint."""
    
    @pytest.mark.asyncio
    async def test_delete_topic_success(self, client: AsyncClient):
        """Test deleting a topic."""
        # Create a topic to delete
        topic_data = {"id": "topic-delete-test", "title": "Topic to Delete"}
        await client.post("/api/topics", json=topic_data)
        
        # Delete it
        response = await client.delete(f"/api/topics/{topic_data['id']}")
        assert response.status_code == 200
        assert response.json()["status"] == "deleted"
        
        # Verify it's gone
        get_response = await client.get(f"/api/topics/{topic_data['id']}")
        assert get_response.status_code == 404
    
    @pytest.mark.asyncio
    async def test_delete_topic_not_found(self, client: AsyncClient):
        """Test deleting a non-existent topic."""
        response = await client.delete("/api/topics/nonexistent-id")
        assert response.status_code == 404


class TestTopicsDocuments:
    """Tests for GET /api/topics/{topic_id}/documents endpoint."""
    
    @pytest.mark.asyncio
    async def test_get_topic_documents_empty(self, client: AsyncClient):
        """Test getting documents for a topic with no documents."""
        # Create a topic first
        topic_data = {"id": "topic-docs-test", "title": "Docs Test Topic"}
        await client.post("/api/topics", json=topic_data)
        
        # Get documents (should be empty)
        response = await client.get(f"/api/topics/{topic_data['id']}/documents")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0
    
    @pytest.mark.asyncio
    async def test_get_topic_documents_not_found(self, client: AsyncClient):
        """Test getting documents for a non-existent topic."""
        response = await client.get("/api/topics/nonexistent-id/documents")
        assert response.status_code == 404


class TestTopicsIntegration:
    """Integration tests for topics."""
    
    @pytest.mark.asyncio
    async def test_auto_created_flag(self, client: AsyncClient):
        """Test that auto_created flag works correctly."""
        # Create manual topic
        manual_topic = {
            "id": "manual-topic-test",
            "title": "Manual Topic",
            "auto_created": False
        }
        response = await client.post("/api/topics", json=manual_topic)
        assert response.status_code == 200
        assert response.json()["auto_created"] == False
        
        # Create auto topic
        auto_topic = {
            "id": "auto-topic-test",
            "title": "Auto Topic",
            "auto_created": True
        }
        response = await client.post("/api/topics", json=auto_topic)
        assert response.status_code == 200
        assert response.json()["auto_created"] == True
