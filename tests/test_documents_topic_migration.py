"""Tests to verify topic migration from string to FK relationship."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_document_with_topic_id(client: AsyncClient):
    """Test that documents can be created with topic_id (FK relationship)."""
    # First create a topic
    topic_response = await client.post(
        "/api/topics",
        json={
            "id": "test-topic-migration-1",
            "title": "Migration Test Topic",
            "description": "Testing topic FK relationship"
        }
    )
    assert topic_response.status_code == 200
    topic_id = topic_response.json()["id"]
    
    # Create a document with topic_id
    doc_response = await client.post(
        "/api/documents",
        json={
            "id": "test-doc-migration-1",
            "title": "Test Document with Topic",
            "topic_id": topic_id,
            "source": "test"
        }
    )
    assert doc_response.status_code == 200
    
    doc = doc_response.json()
    assert doc["id"] == "test-doc-migration-1"
    assert doc["topic_id"] == topic_id
    assert "topic" not in doc  # Ensure old 'topic' field doesn't exist
    
    # Verify we can retrieve it
    get_response = await client.get(f"/api/documents/{doc['id']}")
    assert get_response.status_code == 200
    retrieved_doc = get_response.json()
    assert retrieved_doc["topic_id"] == topic_id


@pytest.mark.asyncio
async def test_list_documents_no_topic_field(client: AsyncClient):
    """Test that listing documents doesn't include old 'topic' string field."""
    # Create a document without topic
    doc_response = await client.post(
        "/api/documents",
        json={
            "id": "test-doc-migration-2",
            "title": "Document without Topic",
            "source": "test"
        }
    )
    assert doc_response.status_code == 200
    
    # List documents
    list_response = await client.get("/api/documents")
    assert list_response.status_code == 200
    
    documents = list_response.json()
    assert isinstance(documents, list)
    
    # Verify none of the documents have 'topic' field (only topic_id)
    for doc in documents:
        assert "topic" not in doc or doc.get("topic") is None
        # topic_id is allowed (can be None or a valid FK)


@pytest.mark.asyncio
async def test_topic_documents_endpoint(client: AsyncClient):
    """Test GET /api/topics/{id}/documents uses topic_id correctly."""
    # Create topic
    topic_response = await client.post(
        "/api/topics",
        json={
            "id": "test-topic-migration-3",
            "title": "Topic Documents Test"
        }
    )
    assert topic_response.status_code == 200
    topic_id = topic_response.json()["id"]
    
    # Create document linked to topic
    doc_response = await client.post(
        "/api/documents",
        json={
            "id": "test-doc-migration-3",
            "title": "Linked Document",
            "topic_id": topic_id
        }
    )
    assert doc_response.status_code == 200
    
    # Get documents for this topic
    topic_docs_response = await client.get(f"/api/topics/{topic_id}/documents")
    assert topic_docs_response.status_code == 200
    
    topic_docs = topic_docs_response.json()
    assert isinstance(topic_docs, list)
    assert len(topic_docs) >= 1
    
    # Verify our document is in the list
    doc_ids = [d["id"] for d in topic_docs]
    assert "test-doc-migration-3" in doc_ids
    
    # Verify all documents have topic_id set correctly
    for doc in topic_docs:
        assert doc["topic_id"] == topic_id


@pytest.mark.asyncio
async def test_update_document_topic_id(client: AsyncClient):
    """Test that updating a document's topic_id works correctly."""
    # Create two topics
    topic1_response = await client.post(
        "/api/topics",
        json={"id": "test-topic-migration-4a", "title": "Topic A"}
    )
    assert topic1_response.status_code == 200
    topic1_id = topic1_response.json()["id"]
    
    topic2_response = await client.post(
        "/api/topics",
        json={"id": "test-topic-migration-4b", "title": "Topic B"}
    )
    assert topic2_response.status_code == 200
    topic2_id = topic2_response.json()["id"]
    
    # Create document with topic1
    doc_response = await client.post(
        "/api/documents",
        json={
            "id": "test-doc-migration-4",
            "title": "Document to Update",
            "topic_id": topic1_id
        }
    )
    assert doc_response.status_code == 200
    
    # Update to topic2
    update_response = await client.put(
        f"/api/documents/test-doc-migration-4",
        json={"topic_id": topic2_id}
    )
    assert update_response.status_code == 200
    
    updated_doc = update_response.json()
    assert updated_doc["topic_id"] == topic2_id
    
    # Verify it's now in topic2's documents
    topic2_docs_response = await client.get(f"/api/topics/{topic2_id}/documents")
    assert topic2_docs_response.status_code == 200
    doc_ids = [d["id"] for d in topic2_docs_response.json()]
    assert "test-doc-migration-4" in doc_ids


@pytest.mark.asyncio
async def test_document_without_topic_allowed(client: AsyncClient):
    """Test that documents can exist without a topic_id (nullable FK)."""
    doc_response = await client.post(
        "/api/documents",
        json={
            "id": "test-doc-migration-5",
            "title": "Document without Topic",
            "source": "test"
        }
    )
    assert doc_response.status_code == 200
    
    doc = doc_response.json()
    assert doc["topic_id"] is None
    
    # Verify it can be retrieved
    get_response = await client.get("/api/documents/test-doc-migration-5")
    assert get_response.status_code == 200
    assert get_response.json()["topic_id"] is None
