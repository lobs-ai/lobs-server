"""Tests for the time-based test detector."""

import ast
import pytest
from pathlib import Path
import sys

# Add bin to path to import the detector
bin_path = Path(__file__).parent.parent / "bin"
sys.path.insert(0, str(bin_path))

from detect_time_based_tests import TimeBasedTestDetector, scan_file


@pytest.fixture
def temp_test_file(tmp_path):
    """Create a temporary Python file for testing."""
    def _create(content: str) -> Path:
        file_path = tmp_path / "test_sample.py"
        file_path.write_text(content)
        return file_path
    return _create


def test_detect_datetime_now_without_freeze_time(temp_test_file):
    """Test detection of datetime.now() without @freeze_time."""
    code = """
from datetime import datetime

def test_something():
    now = datetime.now()
    assert now is not None
"""
    file_path = temp_test_file(code)
    issues = scan_file(file_path)
    
    assert len(issues) == 1
    assert issues[0].issue_type == "datetime_now_without_freeze_time"
    assert issues[0].line_number == 5


def test_detect_date_today_without_mocking(temp_test_file):
    """Test detection of date.today() without mocking."""
    code = """
from datetime import date

def test_something():
    today = date.today()
    assert today is not None
"""
    file_path = temp_test_file(code)
    issues = scan_file(file_path)
    
    assert len(issues) == 1
    assert issues[0].issue_type == "date_today_without_mocking"
    assert issues[0].line_number == 5


def test_detect_datetime_utcnow_without_freeze_time(temp_test_file):
    """Test detection of datetime.utcnow() without @freeze_time."""
    code = """
from datetime import datetime

def test_something():
    now = datetime.utcnow()
    assert now is not None
"""
    file_path = temp_test_file(code)
    issues = scan_file(file_path)
    
    assert len(issues) == 1
    assert issues[0].issue_type == "datetime_utcnow_without_freeze_time"
    assert issues[0].line_number == 5


def test_no_issue_with_freeze_time_decorator(temp_test_file):
    """Test that @freeze_time decorator prevents detection."""
    code = """
from datetime import datetime
from freezegun import freeze_time

@freeze_time("2024-01-01 12:00:00")
def test_something():
    now = datetime.now()
    assert now is not None
"""
    file_path = temp_test_file(code)
    issues = scan_file(file_path)
    
    assert len(issues) == 0


def test_no_issue_with_freeze_time_call_decorator(temp_test_file):
    """Test that @freeze_time("...") decorator prevents detection."""
    code = """
from datetime import datetime
from freezegun import freeze_time

@freeze_time("2024-01-01")
def test_something():
    now = datetime.now()
    today = date.today()
    assert now is not None
"""
    file_path = temp_test_file(code)
    issues = scan_file(file_path)
    
    assert len(issues) == 0


def test_detect_multiple_issues_in_one_function(temp_test_file):
    """Test detection of multiple issues in a single function."""
    code = """
from datetime import datetime, date

def test_something():
    now = datetime.now()
    today = date.today()
    utc = datetime.utcnow()
    assert True
"""
    file_path = temp_test_file(code)
    issues = scan_file(file_path)
    
    assert len(issues) == 3
    issue_types = {issue.issue_type for issue in issues}
    assert "datetime_now_without_freeze_time" in issue_types
    assert "date_today_without_mocking" in issue_types
    assert "datetime_utcnow_without_freeze_time" in issue_types


def test_freeze_time_only_applies_to_decorated_function(temp_test_file):
    """Test that @freeze_time only applies to the decorated function."""
    code = """
from datetime import datetime
from freezegun import freeze_time

@freeze_time("2024-01-01")
def test_with_freeze():
    now = datetime.now()  # OK - has decorator
    assert now is not None

def test_without_freeze():
    now = datetime.now()  # Should be detected
    assert now is not None
"""
    file_path = temp_test_file(code)
    issues = scan_file(file_path)
    
    assert len(issues) == 1
    assert issues[0].line_number == 11  # In test_without_freeze


def test_detect_datetime_now_with_timezone(temp_test_file):
    """Test detection of datetime.now(tz=...) without @freeze_time."""
    code = """
from datetime import datetime, timezone

def test_something():
    now = datetime.now(timezone.utc)
    assert now is not None
"""
    file_path = temp_test_file(code)
    issues = scan_file(file_path)
    
    # Should detect datetime.now with timezone arg
    assert len(issues) == 1
    assert "datetime_now" in issues[0].issue_type


def test_real_test_file_detection():
    """Test detection on actual test file (test_memories.py)."""
    test_file = Path(__file__).parent / "test_memories.py"
    if test_file.exists():
        issues = scan_file(test_file)
        # We know there's at least one date.today() in test_memories.py
        assert len(issues) >= 1
        assert any("date_today" in issue.issue_type for issue in issues)


def test_detector_handles_syntax_errors_gracefully(temp_test_file):
    """Test that detector handles invalid Python gracefully."""
    code = """
def test_invalid(
    # Missing closing paren
"""
    file_path = temp_test_file(code)
    issues = scan_file(file_path)
    
    # Should return empty list, not crash
    assert issues == []


def test_detector_with_nested_functions(temp_test_file):
    """Test detection in nested functions."""
    code = """
from datetime import datetime

def test_outer():
    def inner():
        now = datetime.now()
        return now
    result = inner()
    assert result is not None
"""
    file_path = temp_test_file(code)
    issues = scan_file(file_path)
    
    assert len(issues) == 1
    assert issues[0].line_number == 6


def test_issue_contains_helpful_information(temp_test_file):
    """Test that detected issues contain helpful information."""
    code = """
from datetime import datetime

def test_something():
    now = datetime.now()
"""
    file_path = temp_test_file(code)
    issues = scan_file(file_path)
    
    assert len(issues) == 1
    issue = issues[0]
    
    # Check all fields are populated
    assert issue.file_path
    assert issue.line_number > 0
    assert issue.column >= 0
    assert issue.issue_type
    assert issue.code_snippet
    assert issue.suggestion
    assert "freeze_time" in issue.suggestion.lower()


# Clean up sys.path
sys.path.remove(str(bin_path))
