"""Data factories for creating test objects.

Provides simple factory functions to create test data with sensible defaults
and optional overrides. Reduces duplication across test files.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
import secrets


def create_project_data(
    id: Optional[str] = None,
    title: str = "Test Project",
    **overrides
) -> Dict[str, Any]:
    """Create test project data.
    
    Args:
        id: Project ID (auto-generated if not provided)
        title: Project title
        **overrides: Additional fields to override defaults
        
    Returns:
        Dictionary of project data suitable for API requests or DB models
    """
    data = {
        "id": id or f"test-project-{secrets.token_hex(4)}",
        "title": title,
        "notes": overrides.pop("notes", "Test project notes"),
        "archived": overrides.pop("archived", False),
        "type": overrides.pop("type", "kanban"),
        "sort_order": overrides.pop("sort_order", 0),
    }
    data.update(overrides)
    return data


def create_task_data(
    id: Optional[str] = None,
    title: str = "Test Task",
    project_id: Optional[str] = None,
    **overrides
) -> Dict[str, Any]:
    """Create test task data.
    
    Args:
        id: Task ID (auto-generated if not provided)
        title: Task title
        project_id: Project ID to associate with
        **overrides: Additional fields to override defaults
        
    Returns:
        Dictionary of task data suitable for API requests or DB models
    """
    data = {
        "id": id or f"test-task-{secrets.token_hex(4)}",
        "title": title,
        "status": overrides.pop("status", "inbox"),
        "project_id": project_id,
        "notes": overrides.pop("notes", "Test task notes"),
        "sort_order": overrides.pop("sort_order", 0),
        "pinned": overrides.pop("pinned", False),
    }
    # Add optional fields if provided in overrides
    if "agent" in overrides:
        data["agent"] = overrides.pop("agent")
    if "delegated_to" in overrides:
        data["delegated_to"] = overrides.pop("delegated_to")
    if "deadline" in overrides:
        data["deadline"] = overrides.pop("deadline")
    if "tags" in overrides:
        data["tags"] = overrides.pop("tags")
    
    data.update(overrides)
    return data


def create_inbox_data(
    id: Optional[str] = None,
    title: str = "Test Inbox Item",
    **overrides
) -> Dict[str, Any]:
    """Create test inbox item data.
    
    Args:
        id: Inbox item ID (auto-generated if not provided)
        title: Item title
        **overrides: Additional fields to override defaults
        
    Returns:
        Dictionary of inbox data suitable for API requests or DB models
    """
    data = {
        "id": id or f"test-inbox-{secrets.token_hex(4)}",
        "title": title,
        "filename": overrides.pop("filename", "test.txt"),
        "content": overrides.pop("content", "Test inbox content"),
        "is_read": overrides.pop("is_read", False),
    }
    data.update(overrides)
    return data


def create_document_data(
    id: Optional[str] = None,
    title: str = "Test Document",
    **overrides
) -> Dict[str, Any]:
    """Create test document data.
    
    Args:
        id: Document ID (auto-generated if not provided)
        title: Document title
        **overrides: Additional fields to override defaults
        
    Returns:
        Dictionary of document data suitable for API requests or DB models
    """
    data = {
        "id": id or f"test-doc-{secrets.token_hex(4)}",
        "title": title,
        "content": overrides.pop("content", "Test document content"),
        "source": overrides.pop("source", "writer"),
        "status": overrides.pop("status", "pending"),
        "is_read": overrides.pop("is_read", False),
        "content_is_truncated": overrides.pop("content_is_truncated", False),
    }
    data.update(overrides)
    return data


def create_agent_data(
    agent_id: str = "programmer",
    **overrides
) -> Dict[str, Any]:
    """Create test agent status data.
    
    Args:
        agent_id: Agent identifier
        **overrides: Additional fields to override defaults
        
    Returns:
        Dictionary of agent status data suitable for API requests or DB models
    """
    data = {
        "agent_id": agent_id,
        "status": overrides.pop("status", "idle"),
        "current_task": overrides.pop("current_task", None),
        "last_activity": overrides.pop("last_activity", datetime.now(timezone.utc).isoformat()),
    }
    data.update(overrides)
    return data


def create_memory_data(
    id: Optional[str] = None,
    title: str = "Test Memory",
    **overrides
) -> Dict[str, Any]:
    """Create test memory data.
    
    Args:
        id: Memory ID (auto-generated if not provided)
        title: Memory title
        **overrides: Additional fields to override defaults
        
    Returns:
        Dictionary of memory data suitable for API requests or DB models
    """
    data = {
        "id": id or f"test-memory-{secrets.token_hex(4)}",
        "title": title,
        "content": overrides.pop("content", "Test memory content"),
        "type": overrides.pop("type", "note"),
        "tags": overrides.pop("tags", []),
    }
    data.update(overrides)
    return data


def create_topic_data(
    id: Optional[str] = None,
    title: str = "Test Topic",
    **overrides
) -> Dict[str, Any]:
    """Create test topic data.
    
    Args:
        id: Topic ID (auto-generated if not provided)
        title: Topic title
        **overrides: Additional fields to override defaults
        
    Returns:
        Dictionary of topic data suitable for API requests or DB models
    """
    data = {
        "id": id or f"test-topic-{secrets.token_hex(4)}",
        "title": title,
        "description": overrides.pop("description", "Test topic description"),
        "icon": overrides.pop("icon", "📝"),
        "auto_created": overrides.pop("auto_created", False),
    }
    data.update(overrides)
    return data


def create_calendar_event_data(
    id: Optional[int] = None,
    title: str = "Test Event",
    **overrides
) -> Dict[str, Any]:
    """Create test calendar event data.
    
    Args:
        id: Event ID (auto-generated if not provided)
        title: Event title
        **overrides: Additional fields to override defaults
        
    Returns:
        Dictionary of event data suitable for API requests or DB models
    """
    now = datetime.now(timezone.utc)
    data = {
        "title": title,
        "start_time": overrides.pop("start_time", now.isoformat()),
        "end_time": overrides.pop("end_time", (now.replace(hour=now.hour + 1)).isoformat()),
        "description": overrides.pop("description", "Test event description"),
        "location": overrides.pop("location", None),
        "all_day": overrides.pop("all_day", False),
    }
    if id is not None:
        data["id"] = id
    data.update(overrides)
    return data


def create_template_data(
    id: Optional[str] = None,
    name: str = "Test Template",
    **overrides
) -> Dict[str, Any]:
    """Create test template data.
    
    Args:
        id: Template ID (auto-generated if not provided)
        name: Template name
        **overrides: Additional fields to override defaults
        
    Returns:
        Dictionary of template data suitable for API requests or DB models
    """
    data = {
        "id": id or f"test-template-{secrets.token_hex(4)}",
        "name": name,
        "description": overrides.pop("description", "Test template description"),
        "items": overrides.pop("items", [
            {"title": "Task 1"},
            {"title": "Task 2"}
        ]),
    }
    data.update(overrides)
    return data
