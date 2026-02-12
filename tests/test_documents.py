"""Tests for documents API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_document(client: AsyncClient):
    """Test creating a document."""
    doc_data = {
        "id": "doc-1",
        "title": "Test Document",
        "content": "Document content",
        "source": "writer",
        "status": "pending",
        "is_read": False,
        "content_is_truncated": False
    }
    response = await client.post("/api/documents", json=doc_data)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "doc-1"
    assert data["title"] == "Test Document"
    assert data["content"] == "Document content"
    assert data["source"] == "writer"
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_list_documents(client: AsyncClient, sample_document):
    """Test listing documents."""
    response = await client.get("/api/documents")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == sample_document["id"]


@pytest.mark.asyncio
async def test_list_documents_pagination(client: AsyncClient):
    """Test document pagination."""
    # Create multiple documents
    for i in range(5):
        await client.post("/api/documents", json={
            "id": f"doc-{i}",
            "title": f"Document {i}",
            "source": "writer",
            "is_read": False,
            "content_is_truncated": False
        })
    
    # Test limit
    response = await client.get("/api/documents?limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2
    
    # Test offset
    response = await client.get("/api/documents?offset=2&limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_get_document(client: AsyncClient, sample_document):
    """Test getting a single document."""
    response = await client.get(f"/api/documents/{sample_document['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == sample_document["id"]
    assert data["title"] == sample_document["title"]


@pytest.mark.asyncio
async def test_get_document_not_found(client: AsyncClient):
    """Test getting a non-existent document returns 404."""
    response = await client.get("/api/documents/nonexistent")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_document(client: AsyncClient, sample_document):
    """Test updating a document."""
    update_data = {
        "title": "Updated Document",
        "status": "approved",
        "is_read": True
    }
    response = await client.put(
        f"/api/documents/{sample_document['id']}",
        json=update_data
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Document"
    assert data["status"] == "approved"
    assert data["is_read"] is True


@pytest.mark.asyncio
async def test_update_document_not_found(client: AsyncClient):
    """Test updating a non-existent document returns 404."""
    response = await client.put(
        "/api/documents/nonexistent",
        json={"title": "Updated"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_document(client: AsyncClient, sample_document):
    """Test deleting a document."""
    response = await client.delete(f"/api/documents/{sample_document['id']}")
    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    
    # Verify it's deleted
    get_response = await client.get(f"/api/documents/{sample_document['id']}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_delete_document_not_found(client: AsyncClient):
    """Test deleting a non-existent document returns 404."""
    response = await client.delete("/api/documents/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_archive_document(client: AsyncClient, sample_document):
    """Test archiving a document."""
    response = await client.post(f"/api/documents/{sample_document['id']}/archive")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "archived"
    assert data["id"] == sample_document["id"]


@pytest.mark.asyncio
async def test_archive_document_not_found(client: AsyncClient):
    """Test archiving a non-existent document returns 404."""
    response = await client.post("/api/documents/nonexistent/archive")
    assert response.status_code == 404
