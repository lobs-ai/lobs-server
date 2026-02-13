#!/usr/bin/env python3
"""Quick test for inbox processor logic."""

import sys
sys.path.insert(0, '.')

from app.orchestrator.inbox_processor import InboxProcessor


class MockDB:
    """Mock database for testing."""
    pass


class MockInboxItem:
    """Mock inbox item for testing."""
    def __init__(self, title, content):
        self.title = title
        self.content = content


def test_analyze_response():
    """Test the response analysis logic (fallback regex method)."""
    processor = InboxProcessor(MockDB())
    
    # Test approval - simple "yes"
    item = MockInboxItem("Bug fixes needed", "1. Fix the bug\n2. Add tests")
    result = processor._analyze_response_fallback(
        user_message="Yes, do it!",
        inbox_item=item
    )
    assert result["type"] == "create_task", f"Expected create_task, got {result['type']}"
    print("✓ Approval detection (yes) works")
    
    # Test approval - "do this"
    result = processor._analyze_response_fallback(
        user_message="do this",
        inbox_item=item
    )
    assert result["type"] == "create_task", f"Expected create_task, got {result['type']}"
    print("✓ Approval detection (do this) works")
    
    # Test approval - "looks good"
    result = processor._analyze_response_fallback(
        user_message="looks good, do that",
        inbox_item=item
    )
    assert result["type"] == "create_task", f"Expected create_task, got {result['type']}"
    print("✓ Approval detection (looks good) works")
    
    # Test approval - conversational with instructions
    result = processor._analyze_response_fallback(
        user_message="yes investigate why this is an issue",
        inbox_item=item
    )
    assert result["type"] == "create_task", f"Expected create_task, got {result['type']}"
    print("✓ Approval detection (conversational) works")
    
    # Test rejection
    item2 = MockInboxItem("Bug fixes needed", "1. Fix the bug")
    result = processor._analyze_response_fallback(
        user_message="No, skip this",
        inbox_item=item2
    )
    assert result["type"] == "resolve", f"Expected resolve, got {result['type']}"
    print("✓ Rejection detection works")
    
    # Test pending
    result = processor._analyze_response_fallback(
        user_message="Maybe later, need to think about it",
        inbox_item=item2
    )
    assert result["type"] == "pending", f"Expected pending, got {result['type']}"
    print("✓ Pending detection works")
    
    # Test specific instructions (action verb)
    item3 = MockInboxItem("Bug triage", "Various bugs reported")
    result = processor._analyze_response_fallback(
        user_message="integrate this with the new calendar feature",
        inbox_item=item3
    )
    assert result["type"] == "create_task", f"Expected create_task, got {result['type']}"
    print("✓ Specific instructions detection works")


def test_agent_type_inference():
    """Test agent type inference."""
    processor = InboxProcessor(MockDB())
    
    # Test researcher
    item = MockInboxItem(
        "Research alternatives",
        "We need to investigate different database options"
    )
    agent_type = processor._infer_agent_type(item, "yes investigate this")
    assert agent_type == "researcher", f"Expected researcher, got {agent_type}"
    print("✓ Agent type inference (researcher) works")
    
    # Test programmer
    item = MockInboxItem(
        "Fix the bug",
        "Implement error handling in the API"
    )
    agent_type = processor._infer_agent_type(item, "yes fix this")
    assert agent_type == "programmer", f"Expected programmer, got {agent_type}"
    print("✓ Agent type inference (programmer) works")
    
    # Test writer
    item = MockInboxItem(
        "Documentation needed",
        "Write a guide for the new API endpoints"
    )
    agent_type = processor._infer_agent_type(item, "yes write the doc")
    assert agent_type == "writer", f"Expected writer, got {agent_type}"
    print("✓ Agent type inference (writer) works")
    
    # Test architect
    item = MockInboxItem(
        "System redesign",
        "Design system for better scalability"
    )
    agent_type = processor._infer_agent_type(item, "yes design this")
    assert agent_type == "architect", f"Expected architect, got {agent_type}"
    print("✓ Agent type inference (architect) works")


def test_project_extraction():
    """Test project ID extraction."""
    processor = InboxProcessor(MockDB())
    
    # Test **Project:** format
    item = MockInboxItem(
        "Bug fixes needed",
        "**Project:** lobs-server\n\nSome content"
    )
    project_id = processor._extract_project_id(item)
    assert project_id == "lobs-server", f"Expected lobs-server, got {project_id}"
    print("✓ Project extraction (**Project:** format) works")
    
    # Test keyword matching
    item = MockInboxItem(
        "Fix server bug",
        "The lobs-server API is returning errors"
    )
    project_id = processor._extract_project_id(item)
    assert project_id == "lobs-server", f"Expected lobs-server, got {project_id}"
    print("✓ Project extraction (keyword matching) works")
    
    # Test default
    item = MockInboxItem(
        "Random task",
        "Random content with no project"
    )
    project_id = processor._extract_project_id(item)
    assert project_id == "default", f"Expected default, got {project_id}"
    print("✓ Default project fallback works")


if __name__ == "__main__":
    print("Testing inbox processor logic...\n")
    
    test_analyze_response()
    print()
    
    test_agent_type_inference()
    print()
    
    test_project_extraction()
    print()
    
    print("✅ All tests passed!")
