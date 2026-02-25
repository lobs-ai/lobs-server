"""Tests for intent routing API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_route_intent_research(client: AsyncClient):
    """Test intent routing for research-related tasks."""
    test_cases = [
        "Research the best database options for our project",
        "Investigate why the API is slow",
        "Compare React vs Vue frameworks",
        "Analyze user behavior patterns",
    ]
    
    for text in test_cases:
        response = await client.post("/api/intent/route", json={"text": text})
        assert response.status_code == 200
        data = response.json()
        assert data["intent"] == "research"
        assert data["recommended_agent"] == "researcher"
        assert data["confidence"] >= 0.8


@pytest.mark.asyncio
async def test_route_intent_writing(client: AsyncClient):
    """Test intent routing for writing-related tasks."""
    test_cases = [
        "Write documentation for the API",
        "Draft a blog post about our new feature",
        "Edit content for the landing page",
        "Create copy for the marketing email",
    ]
    
    for text in test_cases:
        response = await client.post("/api/intent/route", json={"text": text})
        assert response.status_code == 200
        data = response.json()
        assert data["intent"] == "writing"
        assert data["recommended_agent"] == "writer"
        assert data["confidence"] >= 0.75


@pytest.mark.asyncio
async def test_route_intent_review(client: AsyncClient):
    """Test intent routing for review-related tasks."""
    test_cases = [
        "Review the pull request for bugs",
        "QA the new authentication flow",
        "Test the payment integration",
        "Validate the API responses",
    ]
    
    for text in test_cases:
        response = await client.post("/api/intent/route", json={"text": text})
        assert response.status_code == 200
        data = response.json()
        assert data["intent"] == "review"
        assert data["recommended_agent"] == "reviewer"
        assert data["confidence"] >= 0.75


@pytest.mark.asyncio
async def test_route_intent_architecture(client: AsyncClient):
    """Test intent routing for architecture-related tasks."""
    test_cases = [
        "Design the authentication system",
        "Create architecture for microservices",
        "We need a refactor plan for the data layer",
    ]
    
    for text in test_cases:
        response = await client.post("/api/intent/route", json={"text": text})
        assert response.status_code == 200
        data = response.json()
        assert data["intent"] == "architecture"
        assert data["recommended_agent"] == "architect"
        assert data["confidence"] >= 0.7


@pytest.mark.asyncio
async def test_route_intent_implementation_default(client: AsyncClient):
    """Test intent routing defaults to implementation for ambiguous tasks."""
    test_cases = [
        "Fix the login button",
        "Add error handling to the API",
        "Update the database schema",
        "Implement the new feature",
    ]
    
    for text in test_cases:
        response = await client.post("/api/intent/route", json={"text": text})
        assert response.status_code == 200
        data = response.json()
        assert data["intent"] == "implementation"
        assert data["recommended_agent"] == "programmer"
        assert data["confidence"] >= 0.7


@pytest.mark.asyncio
async def test_route_intent_case_insensitive(client: AsyncClient):
    """Test that intent routing is case-insensitive."""
    test_cases = [
        "RESEARCH the database options",
        "Research the database options",
        "research the database options",
    ]
    
    for text in test_cases:
        response = await client.post("/api/intent/route", json={"text": text})
        assert response.status_code == 200
        data = response.json()
        assert data["intent"] == "research"
        assert data["recommended_agent"] == "researcher"


@pytest.mark.asyncio
async def test_route_intent_response_structure(client: AsyncClient):
    """Test that the response has the correct structure."""
    response = await client.post("/api/intent/route", json={"text": "Write docs"})
    assert response.status_code == 200
    data = response.json()
    
    # Check all required fields are present
    assert "intent" in data
    assert "recommended_agent" in data
    assert "confidence" in data
    
    # Check field types
    assert isinstance(data["intent"], str)
    assert isinstance(data["recommended_agent"], str)
    assert isinstance(data["confidence"], float)
    assert 0.0 <= data["confidence"] <= 1.0


@pytest.mark.asyncio
async def test_route_intent_empty_text(client: AsyncClient):
    """Test routing with empty text defaults to implementation."""
    response = await client.post("/api/intent/route", json={"text": ""})
    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "implementation"
    assert data["recommended_agent"] == "programmer"


@pytest.mark.asyncio
async def test_route_intent_multiple_keywords(client: AsyncClient):
    """Test that the first matching keyword wins."""
    # "research" appears before "write"
    response = await client.post("/api/intent/route", json={
        "text": "Research and write a report"
    })
    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "research"
    assert data["recommended_agent"] == "researcher"


# ===========================================================================
# Capture classifier tests
# ===========================================================================

@pytest.mark.asyncio
async def test_capture_task_intent(client: AsyncClient):
    """Task-flavoured text should rank 'task' first with high confidence."""
    texts = [
        "Implement the new authentication middleware",
        "Fix the login bug on the dashboard",
        "Build the notification service",
        "Deploy the API to production",
    ]
    for text in texts:
        resp = await client.post("/api/intent/capture", json={"text": text})
        assert resp.status_code == 200
        data = resp.json()
        assert data["intents"], "Should return at least one intent"
        top = data["intents"][0]
        assert top["intent_type"] == "task", f"Expected task for: {text!r}, got: {top}"
        assert top["confidence"] >= 0.55


@pytest.mark.asyncio
async def test_capture_reminder_intent(client: AsyncClient):
    """Reminder-flavoured text should rank 'reminder' first."""
    texts = [
        "Remind me to follow up with Alice next week",
        "Don't forget the deployment deadline on Friday",
        "Follow up with the client by tomorrow",
    ]
    for text in texts:
        resp = await client.post("/api/intent/capture", json={"text": text})
        assert resp.status_code == 200
        data = resp.json()
        top = data["intents"][0]
        assert top["intent_type"] == "reminder", f"Expected reminder for: {text!r}, got: {top}"
        assert top["confidence"] >= 0.55


@pytest.mark.asyncio
async def test_capture_research_intent(client: AsyncClient):
    """Research-flavoured text should rank 'research' first."""
    texts = [
        "Research the best caching strategies for FastAPI",
        "Compare Postgres vs SQLite for our use case",
        "What is the best way to handle rate limiting?",
    ]
    for text in texts:
        resp = await client.post("/api/intent/capture", json={"text": text})
        assert resp.status_code == 200
        data = resp.json()
        top = data["intents"][0]
        assert top["intent_type"] == "research", f"Expected research for: {text!r}, got: {top}"
        assert top["confidence"] >= 0.55


@pytest.mark.asyncio
async def test_capture_reply_needed_intent(client: AsyncClient):
    """Reply-needed text should rank 'reply_needed' first."""
    texts = [
        "Reply to John's email about the API changes",
        "Get back to the design team about the mockups",
        "Respond to the client's question about billing",
    ]
    for text in texts:
        resp = await client.post("/api/intent/capture", json={"text": text})
        assert resp.status_code == 200
        data = resp.json()
        top = data["intents"][0]
        assert top["intent_type"] == "reply_needed", f"Expected reply_needed for: {text!r}, got: {top}"
        assert top["confidence"] >= 0.55


@pytest.mark.asyncio
async def test_capture_response_structure(client: AsyncClient):
    """Verify the capture response has the correct schema."""
    resp = await client.post("/api/intent/capture", json={"text": "Fix the auth bug"})
    assert resp.status_code == 200
    data = resp.json()

    # Top-level fields
    assert "intents" in data
    assert "raw_text" in data
    assert "word_count" in data
    assert isinstance(data["intents"], list)
    assert len(data["intents"]) == 4  # always returns all 4 intent types

    # Each intent
    for intent in data["intents"]:
        assert "intent_type" in intent
        assert "confidence" in intent
        assert "suggested_title" in intent
        assert "proposed_action" in intent
        assert intent["intent_type"] in {"task", "reminder", "research", "reply_needed"}
        assert 0.0 <= intent["confidence"] <= 1.0


@pytest.mark.asyncio
async def test_capture_url_detection(client: AsyncClient):
    """URLs in text should be detected and returned."""
    resp = await client.post("/api/intent/capture", json={
        "text": "Look into this: https://example.com/api-docs"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["detected_url"] == "https://example.com/api-docs"


@pytest.mark.asyncio
async def test_capture_empty_text(client: AsyncClient):
    """Empty text should still return a valid response."""
    resp = await client.post("/api/intent/capture", json={"text": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["intents"], list)
    assert data["word_count"] == 0


@pytest.mark.asyncio
async def test_capture_word_count(client: AsyncClient):
    """Word count should reflect the input text."""
    text = "This is a five word sentence"
    resp = await client.post("/api/intent/capture", json={"text": text})
    assert resp.status_code == 200
    data = resp.json()
    assert data["word_count"] == 6


@pytest.mark.asyncio
async def test_capture_intents_sorted_by_confidence(client: AsyncClient):
    """Returned intents should be in descending confidence order."""
    resp = await client.post("/api/intent/capture", json={"text": "Implement the new feature"})
    assert resp.status_code == 200
    data = resp.json()
    confidences = [i["confidence"] for i in data["intents"]]
    assert confidences == sorted(confidences, reverse=True)


@pytest.mark.asyncio
async def test_capture_suggested_title_from_first_line(client: AsyncClient):
    """Suggested title should come from the first non-empty line."""
    resp = await client.post("/api/intent/capture", json={
        "text": "Fix the login bug\nMore details below\nAnd more"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["intents"][0]["suggested_title"] == "Fix the login bug"


@pytest.mark.asyncio
async def test_capture_confirm_task(client: AsyncClient):
    """Confirming a task intent should create a Task entity."""
    resp = await client.post("/api/intent/capture/confirm", json={
        "text": "Implement the new auth middleware",
        "intent_type": "task",
        "suggested_title": "Implement auth middleware",
        "agent": "programmer",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["entity_type"] == "task"
    assert data["entity_id"]
    assert data["nav_path"] == "tasks"
    assert "programmer" in data["message"]
    assert data["title"] == "Implement auth middleware"


@pytest.mark.asyncio
async def test_capture_confirm_reminder(client: AsyncClient):
    """Confirming a reminder intent should create an InboxItem."""
    resp = await client.post("/api/intent/capture/confirm", json={
        "text": "Remind me to follow up with Alice next week",
        "intent_type": "reminder",
        "suggested_title": "Follow up with Alice",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["entity_type"] == "inbox_item"
    assert data["entity_id"]
    assert data["nav_path"] == "inbox"
    assert "Reminder" in data["message"]


@pytest.mark.asyncio
async def test_capture_confirm_research(client: AsyncClient):
    """Confirming a research intent should create an InboxItem."""
    resp = await client.post("/api/intent/capture/confirm", json={
        "text": "Research the best caching strategies",
        "intent_type": "research",
        "suggested_title": "Best caching strategies",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["entity_type"] == "inbox_item"
    assert data["nav_path"] == "inbox"
    assert "Research" in data["message"]


@pytest.mark.asyncio
async def test_capture_confirm_reply_needed(client: AsyncClient):
    """Confirming a reply_needed intent should create an InboxItem."""
    resp = await client.post("/api/intent/capture/confirm", json={
        "text": "Reply to John's email about the contract",
        "intent_type": "reply_needed",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["entity_type"] == "inbox_item"
    assert data["nav_path"] == "inbox"


@pytest.mark.asyncio
async def test_capture_confirm_task_auto_agent(client: AsyncClient):
    """Task confirm should auto-select agent when not provided."""
    resp = await client.post("/api/intent/capture/confirm", json={
        "text": "Research the best monitoring tools",
        "intent_type": "task",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["entity_type"] == "task"
    # Research text → researcher agent
    assert "researcher" in data["message"]


@pytest.mark.asyncio
async def test_capture_confirm_with_project_id(client: AsyncClient):
    """Task confirm should accept and store project_id."""
    from app.models import Project as ProjectModel
    from sqlalchemy.ext.asyncio import AsyncSession

    # We just verify the endpoint accepts project_id without error
    resp = await client.post("/api/intent/capture/confirm", json={
        "text": "Build the new onboarding flow",
        "intent_type": "task",
        "suggested_title": "New onboarding flow",
        "project_id": "nonexistent-project",
    })
    # FKs are not enforced in SQLite by default so it should still succeed
    assert resp.status_code == 200
    data = resp.json()
    assert data["entity_type"] == "task"
