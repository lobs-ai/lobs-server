"""Tests for memory API endpoints."""

import pytest
from datetime import datetime, date
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_long_term_memory(client: AsyncClient):
    """Test creating a long-term memory (MEMORY.md)."""
    memory_data = {
        "title": "Main Memory",
        "content": "# Long-term Memory\n\nImportant stuff here.",
        "memory_type": "long_term",
    }
    response = await client.post("/api/memories", json=memory_data)
    assert response.status_code == 200
    data = response.json()
    assert data["path"] == "MEMORY.md"
    assert data["agent"] == "main"  # default
    assert data["title"] == "Main Memory"
    assert data["memory_type"] == "long_term"
    assert data["date"] is None
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_create_daily_memory(client: AsyncClient):
    """Test creating a daily memory."""
    memory_date = datetime(2026, 2, 12)
    memory_data = {
        "title": "Daily Memory - 2026-02-12",
        "content": "# 2026-02-12\n\nWhat happened today.",
        "memory_type": "daily",
        "date": memory_date.isoformat(),
    }
    response = await client.post("/api/memories", json=memory_data)
    assert response.status_code == 200
    data = response.json()
    assert data["path"] == "memory/2026-02-12.md"
    assert data["title"] == "Daily Memory - 2026-02-12"
    assert data["memory_type"] == "daily"
    assert data["date"].startswith("2026-02-12")


@pytest.mark.asyncio
async def test_create_custom_memory(client: AsyncClient):
    """Test creating a custom memory with explicit path."""
    memory_data = {
        "title": "Custom Memory",
        "content": "Custom content",
        "memory_type": "custom",
        "path": "custom/my-memory.md",
    }
    response = await client.post("/api/memories", json=memory_data)
    assert response.status_code == 200
    data = response.json()
    assert data["path"] == "custom/my-memory.md"
    assert data["title"] == "Custom Memory"
    assert data["memory_type"] == "custom"


@pytest.mark.asyncio
async def test_create_custom_memory_without_path_fails(client: AsyncClient):
    """Test that custom memory without path fails."""
    memory_data = {
        "title": "Custom Memory",
        "content": "Custom content",
        "memory_type": "custom",
    }
    response = await client.post("/api/memories", json=memory_data)
    assert response.status_code == 400
    assert "path is required" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_daily_memory_without_date_fails(client: AsyncClient):
    """Test that daily memory without date fails."""
    memory_data = {
        "title": "Daily Memory",
        "content": "Content",
        "memory_type": "daily",
    }
    response = await client.post("/api/memories", json=memory_data)
    assert response.status_code == 400
    assert "date is required" in response.json()["detail"]


@pytest.mark.asyncio
async def test_duplicate_path_fails(client: AsyncClient):
    """Test that duplicate path returns 409."""
    memory_data = {
        "title": "First",
        "content": "Content 1",
        "memory_type": "custom",
        "path": "duplicate/path.md",
    }
    response1 = await client.post("/api/memories", json=memory_data)
    assert response1.status_code == 200
    
    # Try to create another with same path
    memory_data2 = {
        "title": "Second",
        "content": "Content 2",
        "memory_type": "custom",
        "path": "duplicate/path.md",
    }
    response2 = await client.post("/api/memories", json=memory_data2)
    assert response2.status_code == 409
    assert "already exists" in response2.json()["detail"]


@pytest.mark.asyncio
async def test_list_memories(client: AsyncClient):
    """Test listing memories."""
    # Create a few memories
    await client.post("/api/memories", json={
        "title": "Memory 1",
        "content": "Content 1",
        "memory_type": "custom",
        "path": "mem1.md",
    })
    await client.post("/api/memories", json={
        "title": "Memory 2",
        "content": "Content 2",
        "memory_type": "custom",
        "path": "mem2.md",
    })
    
    response = await client.get("/api/memories")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    # Check that content is NOT included in list view
    assert "content" not in data[0]
    assert "title" in data[0]
    assert "path" in data[0]
    assert "memory_type" in data[0]


@pytest.mark.asyncio
async def test_list_memories_filter_by_type(client: AsyncClient):
    """Test filtering memories by type."""
    # Create different types
    await client.post("/api/memories", json={
        "title": "Long-term",
        "content": "Content",
        "memory_type": "long_term",
    })
    await client.post("/api/memories", json={
        "title": "Daily",
        "content": "Content",
        "memory_type": "daily",
        "date": datetime(2026, 2, 12).isoformat(),
    })
    
    # Filter for daily only
    response = await client.get("/api/memories?type=daily")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["memory_type"] == "daily"


@pytest.mark.asyncio
async def test_get_memory_by_id(client: AsyncClient):
    """Test getting a memory by ID."""
    create_response = await client.post("/api/memories", json={
        "title": "Test Memory",
        "content": "Full content here",
        "memory_type": "custom",
        "path": "test.md",
    })
    created = create_response.json()
    memory_id = created["id"]
    
    response = await client.get(f"/api/memories/{memory_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == memory_id
    assert data["title"] == "Test Memory"
    assert data["content"] == "Full content here"


@pytest.mark.asyncio
async def test_get_memory_by_path(client: AsyncClient):
    """Test getting a memory by path."""
    await client.post("/api/memories", json={
        "title": "Path Test",
        "content": "Content",
        "memory_type": "daily",
        "date": datetime(2026, 2, 12).isoformat(),
    })
    
    response = await client.get("/api/memories/by-path/memory/2026-02-12.md")
    assert response.status_code == 200
    data = response.json()
    assert data["path"] == "memory/2026-02-12.md"
    assert data["title"] == "Path Test"


@pytest.mark.asyncio
async def test_get_memory_not_found(client: AsyncClient):
    """Test getting non-existent memory returns 404."""
    response = await client.get("/api/memories/99999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_memory(client: AsyncClient):
    """Test updating a memory."""
    create_response = await client.post("/api/memories", json={
        "title": "Original",
        "content": "Original content",
        "memory_type": "custom",
        "path": "update-test.md",
    })
    memory_id = create_response.json()["id"]
    
    # Update title and content
    update_response = await client.put(f"/api/memories/{memory_id}", json={
        "title": "Updated Title",
        "content": "Updated content",
    })
    assert update_response.status_code == 200
    data = update_response.json()
    assert data["title"] == "Updated Title"
    assert data["content"] == "Updated content"


@pytest.mark.asyncio
async def test_update_memory_partial(client: AsyncClient):
    """Test partial update (only title)."""
    create_response = await client.post("/api/memories", json={
        "title": "Original",
        "content": "Original content",
        "memory_type": "custom",
        "path": "partial-update.md",
    })
    memory_id = create_response.json()["id"]
    
    # Update only title
    update_response = await client.put(f"/api/memories/{memory_id}", json={
        "title": "New Title",
    })
    assert update_response.status_code == 200
    data = update_response.json()
    assert data["title"] == "New Title"
    assert data["content"] == "Original content"  # unchanged


@pytest.mark.asyncio
async def test_delete_memory(client: AsyncClient):
    """Test deleting a memory."""
    create_response = await client.post("/api/memories", json={
        "title": "To Delete",
        "content": "Content",
        "memory_type": "custom",
        "path": "delete-me.md",
    })
    memory_id = create_response.json()["id"]
    
    # Delete
    delete_response = await client.delete(f"/api/memories/{memory_id}")
    assert delete_response.status_code == 200
    
    # Verify it's gone
    get_response = await client.get(f"/api/memories/{memory_id}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_search_memories(client: AsyncClient):
    """Test searching memories."""
    # Create some memories with searchable content
    await client.post("/api/memories", json={
        "title": "Python Tutorial",
        "content": "Learn Python programming with examples",
        "memory_type": "custom",
        "path": "python.md",
    })
    await client.post("/api/memories", json={
        "title": "JavaScript Guide",
        "content": "JavaScript is a programming language for web",
        "memory_type": "custom",
        "path": "js.md",
    })
    await client.post("/api/memories", json={
        "title": "Cooking Recipe",
        "content": "How to make pasta",
        "memory_type": "custom",
        "path": "cooking.md",
    })
    
    # Search for "programming"
    response = await client.get("/api/memories/search?q=programming")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2  # Python and JavaScript
    # Results should have snippets
    assert "snippet" in data[0]
    assert "score" in data[0]
    # Higher score should come first (title match vs content match)
    titles = [r["title"] for r in data]
    assert "Python Tutorial" in titles or "JavaScript Guide" in titles


@pytest.mark.asyncio
async def test_search_empty_query(client: AsyncClient):
    """Test search with empty query returns empty results."""
    response = await client.get("/api/memories/search?q=")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_quick_capture(client: AsyncClient):
    """Test quick capture endpoint."""
    # First capture creates today's memory
    response1 = await client.post("/api/memories/capture", json={
        "content": "First note of the day"
    })
    assert response1.status_code == 200
    data1 = response1.json()
    today = date.today()
    expected_path = f"memory/{today.isoformat()}.md"
    assert data1["path"] == expected_path
    assert data1["memory_type"] == "daily"
    assert "First note of the day" in data1["content"]
    
    # Second capture appends to same memory
    response2 = await client.post("/api/memories/capture", json={
        "content": "Second note of the day"
    })
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["path"] == expected_path
    assert "First note of the day" in data2["content"]
    assert "Second note of the day" in data2["content"]
    # Should have timestamp headers
    assert "##" in data2["content"]


@pytest.mark.asyncio
async def test_list_pagination(client: AsyncClient):
    """Test memory list pagination."""
    # Create 5 memories
    for i in range(5):
        await client.post("/api/memories", json={
            "title": f"Memory {i}",
            "content": f"Content {i}",
            "memory_type": "custom",
            "path": f"mem-{i}.md",
        })
    
    # Get first 3
    response = await client.get("/api/memories?limit=3&offset=0")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    
    # Get next 2
    response = await client.get("/api/memories?limit=3&offset=3")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2


@pytest.mark.asyncio
async def test_create_memory_for_different_agents(client: AsyncClient):
    """Test creating memories for different agents."""
    # Main agent
    response1 = await client.post("/api/memories", json={
        "title": "Main Memory",
        "content": "Main agent content",
        "memory_type": "custom",
        "path": "test.md",
        "agent": "main",
    })
    assert response1.status_code == 200
    assert response1.json()["agent"] == "main"
    
    # Programmer agent - same path, different agent
    response2 = await client.post("/api/memories", json={
        "title": "Programmer Memory",
        "content": "Programmer agent content",
        "memory_type": "custom",
        "path": "test.md",
        "agent": "programmer",
    })
    assert response2.status_code == 200
    assert response2.json()["agent"] == "programmer"


@pytest.mark.asyncio
async def test_duplicate_path_same_agent_fails(client: AsyncClient):
    """Test that duplicate path for same agent fails."""
    await client.post("/api/memories", json={
        "title": "First",
        "content": "Content",
        "memory_type": "custom",
        "path": "dup.md",
        "agent": "main",
    })
    
    response = await client.post("/api/memories", json={
        "title": "Second",
        "content": "Content",
        "memory_type": "custom",
        "path": "dup.md",
        "agent": "main",
    })
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_filter_memories_by_agent(client: AsyncClient):
    """Test filtering memories by agent."""
    # Create memories for different agents
    await client.post("/api/memories", json={
        "title": "Main Memory 1",
        "content": "Content",
        "memory_type": "custom",
        "path": "main1.md",
        "agent": "main",
    })
    await client.post("/api/memories", json={
        "title": "Main Memory 2",
        "content": "Content",
        "memory_type": "custom",
        "path": "main2.md",
        "agent": "main",
    })
    await client.post("/api/memories", json={
        "title": "Programmer Memory",
        "content": "Content",
        "memory_type": "custom",
        "path": "prog1.md",
        "agent": "programmer",
    })
    
    # Get all memories
    response_all = await client.get("/api/memories")
    assert response_all.status_code == 200
    assert len(response_all.json()) == 3
    
    # Filter by main agent
    response_main = await client.get("/api/memories?agent=main")
    assert response_main.status_code == 200
    main_memories = response_main.json()
    assert len(main_memories) == 2
    assert all(m["agent"] == "main" for m in main_memories)
    
    # Filter by programmer agent
    response_prog = await client.get("/api/memories?agent=programmer")
    assert response_prog.status_code == 200
    prog_memories = response_prog.json()
    assert len(prog_memories) == 1
    assert prog_memories[0]["agent"] == "programmer"


@pytest.mark.asyncio
async def test_search_memories_by_agent(client: AsyncClient):
    """Test searching memories filtered by agent."""
    # Create memories with similar content for different agents
    await client.post("/api/memories", json={
        "title": "Main Python",
        "content": "Python code for main agent",
        "memory_type": "custom",
        "path": "main-py.md",
        "agent": "main",
    })
    await client.post("/api/memories", json={
        "title": "Programmer Python",
        "content": "Python code for programmer agent",
        "memory_type": "custom",
        "path": "prog-py.md",
        "agent": "programmer",
    })
    
    # Search all agents
    response_all = await client.get("/api/memories/search?q=Python")
    assert response_all.status_code == 200
    assert len(response_all.json()) == 2
    
    # Search only main agent
    response_main = await client.get("/api/memories/search?q=Python&agent=main")
    assert response_main.status_code == 200
    results = response_main.json()
    assert len(results) == 1
    assert results[0]["agent"] == "main"


@pytest.mark.asyncio
async def test_get_memory_by_path_and_agent(client: AsyncClient):
    """Test getting memory by path requires agent specification."""
    # Create same path for different agents
    await client.post("/api/memories", json={
        "title": "Main Shared",
        "content": "Main agent version",
        "memory_type": "custom",
        "path": "shared.md",
        "agent": "main",
    })
    await client.post("/api/memories", json={
        "title": "Programmer Shared",
        "content": "Programmer agent version",
        "memory_type": "custom",
        "path": "shared.md",
        "agent": "programmer",
    })
    
    # Get main agent's version
    response_main = await client.get("/api/memories/by-path/shared.md?agent=main")
    assert response_main.status_code == 200
    assert response_main.json()["content"] == "Main agent version"
    
    # Get programmer agent's version
    response_prog = await client.get("/api/memories/by-path/shared.md?agent=programmer")
    assert response_prog.status_code == 200
    assert response_prog.json()["content"] == "Programmer agent version"


@pytest.mark.asyncio
async def test_quick_capture_for_different_agents(client: AsyncClient):
    """Test quick capture with agent parameter."""
    # Capture for main agent
    response_main = await client.post("/api/memories/capture", json={
        "content": "Main agent note",
        "agent": "main",
    })
    assert response_main.status_code == 200
    assert response_main.json()["agent"] == "main"
    assert "Main agent note" in response_main.json()["content"]
    
    # Capture for programmer agent
    response_prog = await client.post("/api/memories/capture", json={
        "content": "Programmer agent note",
        "agent": "programmer",
    })
    assert response_prog.status_code == 200
    assert response_prog.json()["agent"] == "programmer"
    assert "Programmer agent note" in response_prog.json()["content"]
    
    # They should be separate memories
    assert response_main.json()["id"] != response_prog.json()["id"]


@pytest.mark.asyncio
async def test_list_agents(client: AsyncClient):
    """Test listing agents with memory counts."""
    # Create memories for different agents
    await client.post("/api/memories", json={
        "title": "Main 1",
        "content": "Content",
        "memory_type": "custom",
        "path": "m1.md",
        "agent": "main",
    })
    await client.post("/api/memories", json={
        "title": "Main 2",
        "content": "Content",
        "memory_type": "custom",
        "path": "m2.md",
        "agent": "main",
    })
    await client.post("/api/memories", json={
        "title": "Programmer 1",
        "content": "Content",
        "memory_type": "custom",
        "path": "p1.md",
        "agent": "programmer",
    })
    
    response = await client.get("/api/memories/agents")
    assert response.status_code == 200
    agents = response.json()
    
    # Should have counts for each agent
    agent_dict = {a["agent"]: a for a in agents}
    assert "main" in agent_dict
    assert agent_dict["main"]["memory_count"] == 2
    assert "programmer" in agent_dict
    assert agent_dict["programmer"]["memory_count"] == 1
    assert "last_updated" in agent_dict["main"]
