#!/usr/bin/env python3
"""Quick test for inbox processor logic."""

import sys
sys.path.insert(0, '.')

from app.orchestrator.inbox_processor import InboxProcessor


class MockDB:
    """Mock database for testing."""
    pass


def test_analyze_response():
    """Test the response analysis logic."""
    processor = InboxProcessor(MockDB())
    
    # Test approval
    result = processor._analyze_response(
        user_message="Yes, do it!",
        inbox_content="1. Fix the bug\n2. Add tests",
        inbox_title="Bug fixes needed"
    )
    assert result["type"] == "create_tasks", f"Expected create_tasks, got {result['type']}"
    print("✓ Approval detection works")
    
    # Test rejection
    result = processor._analyze_response(
        user_message="No, skip this",
        inbox_content="1. Fix the bug",
        inbox_title="Bug fixes needed"
    )
    assert result["type"] == "resolve", f"Expected resolve, got {result['type']}"
    print("✓ Rejection detection works")
    
    # Test pending
    result = processor._analyze_response(
        user_message="Maybe later, need to think about it",
        inbox_content="1. Fix the bug",
        inbox_title="Bug fixes needed"
    )
    assert result["type"] == "pending", f"Expected pending, got {result['type']}"
    print("✓ Pending detection works")
    
    # Test specific instructions
    result = processor._analyze_response(
        user_message="Create a task to fix the authentication bug in the login flow",
        inbox_content="Various bugs reported",
        inbox_title="Bug triage"
    )
    assert result["type"] == "create_tasks", f"Expected create_tasks, got {result['type']}"
    print("✓ Specific instructions detection works")


def test_parse_tasks():
    """Test task parsing from content."""
    processor = InboxProcessor(MockDB())
    
    content = """
    Project: test-project
    
    Here are the suggestions:
    1. Fix the authentication bug
    2. Add error handling
    3. Update documentation
    
    Notes: These are high priority
    """
    
    tasks = processor._parse_tasks_from_content(content, "Bug fixes")
    assert len(tasks) == 3, f"Expected 3 tasks, got {len(tasks)}"
    assert tasks[0]["project_id"] == "test-project", f"Expected test-project, got {tasks[0]['project_id']}"
    print(f"✓ Parsed {len(tasks)} tasks correctly")
    
    # Test bullet points
    content2 = """
    - Refactor the database layer
    - Add caching layer
    * Implement rate limiting
    """
    
    tasks2 = processor._parse_tasks_from_content(content2, "Performance improvements")
    assert len(tasks2) == 3, f"Expected 3 tasks, got {len(tasks2)}"
    print(f"✓ Bullet point parsing works")


def test_project_extraction():
    """Test project ID extraction."""
    processor = InboxProcessor(MockDB())
    
    # Test "Project: xxx" format
    content1 = "Project: my-awesome-project\n\nSome content"
    project_id = processor._extract_project_id(content1)
    assert project_id == "my-awesome-project", f"Expected my-awesome-project, got {project_id}"
    print("✓ Project extraction (Project: format) works")
    
    # Test hashtag format
    content2 = "Working on #lobs-server improvements"
    project_id = processor._extract_project_id(content2)
    assert project_id == "lobs-server", f"Expected lobs-server, got {project_id}"
    print("✓ Project extraction (hashtag format) works")
    
    # Test default
    content3 = "Random content with no project"
    project_id = processor._extract_project_id(content3)
    assert project_id == "default", f"Expected default, got {project_id}"
    print("✓ Default project fallback works")


if __name__ == "__main__":
    print("Testing inbox processor logic...\n")
    
    test_analyze_response()
    print()
    
    test_parse_tasks()
    print()
    
    test_project_extraction()
    print()
    
    print("✅ All tests passed!")
