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
