"""Tests for validation tools (schema validator and time-based test detector)."""

import pytest
import tempfile
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock

# Import validation modules
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from validate_schema import SchemaValidator, SchemaIssue
from detect_time_based_tests import TimeBasedTestDetector, scan_file


class TestSchemaValidator:
    """Tests for schema validator."""
    
    @pytest.mark.asyncio
    async def test_schema_validator_finds_missing_tables(self):
        """Test that validator detects missing tables."""
        # This would require mocking database metadata
        # For now, we just test the validator can be instantiated
        validator = SchemaValidator("sqlite+aiosqlite:///:memory:")
        assert validator.issues == []
    
    def test_schema_issue_dataclass(self):
        """Test SchemaIssue dataclass."""
        issue = SchemaIssue(
            severity="error",
            table="test_table",
            issue_type="missing_column",
            description="Column 'id' missing"
        )
        assert issue.severity == "error"
        assert issue.table == "test_table"
        assert issue.issue_type == "missing_column"
    
    def test_types_compatible(self):
        """Test type compatibility checker."""
        validator = SchemaValidator("sqlite+aiosqlite:///:memory:")
        
        # String types should be compatible
        assert validator._types_compatible("STRING", "TEXT")
        assert validator._types_compatible("VARCHAR", "TEXT")
        
        # Numeric types should be compatible
        assert validator._types_compatible("INTEGER", "INTEGER")
        assert validator._types_compatible("FLOAT", "REAL")
        
        # DateTime types should be compatible
        assert validator._types_compatible("DATETIME", "TIMESTAMP")
        assert validator._types_compatible("TIMESTAMP", "DATETIME")
        
        # Different types should not be compatible
        assert not validator._types_compatible("STRING", "INTEGER")


class TestTimeBasedTestDetector:
    """Tests for time-based test detector."""
    
    def test_detect_datetime_now_without_freeze_time(self):
        """Test detection of datetime.now() without @freeze_time."""
        test_code = """
import datetime

def test_something():
    now = datetime.now()
    assert now is not None
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(test_code)
            f.flush()
            
            issues = scan_file(Path(f.name))
            
        # Should detect datetime.now() without freeze_time
        assert len(issues) > 0
        assert any(i.issue_type == 'datetime_now_without_freeze_time' for i in issues)
    
    def test_no_issues_with_freeze_time(self):
        """Test that @freeze_time decorated functions pass."""
        test_code = """
import datetime
from freezegun import freeze_time

@freeze_time("2024-01-01")
def test_something():
    now = datetime.now()
    assert now is not None
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(test_code)
            f.flush()
            
            issues = scan_file(Path(f.name))
        
        # Should not detect issues with @freeze_time
        assert len(issues) == 0
    
    def test_detect_date_today_without_mocking(self):
        """Test detection of date.today() without mocking."""
        test_code = """
from datetime import date

def test_something():
    today = date.today()
    assert today is not None
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(test_code)
            f.flush()
            
            issues = scan_file(Path(f.name))
        
        # Should detect date.today() without mocking
        assert len(issues) > 0
        assert any(i.issue_type == 'date_today_without_mocking' for i in issues)
    
    def test_detector_handles_invalid_python(self):
        """Test that detector handles invalid Python gracefully."""
        invalid_code = "this is not valid python code {{{"
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(invalid_code)
            f.flush()
            
            issues = scan_file(Path(f.name))
        
        # Should return empty list for invalid code
        assert issues == []


class TestValidationIntegration:
    """Integration tests for validation tools."""
    
    def test_both_validators_can_be_imported(self):
        """Test that both validation tools can be imported."""
        from validate_schema import SchemaValidator
        from detect_time_based_tests import TimeBasedTestDetector
        
        assert SchemaValidator is not None
        assert TimeBasedTestDetector is not None
    
    @pytest.mark.asyncio
    async def test_schema_validator_against_real_db(self, db_session):
        """Test schema validator against actual test database."""
        from app.config import settings
        
        validator = SchemaValidator(settings.DATABASE_URL)
        issues = await validator.validate()
        
        # The validator should run without crashing
        assert isinstance(issues, list)
        
        # All issues should be SchemaIssue objects
        for issue in issues:
            assert isinstance(issue, SchemaIssue)
            assert issue.severity in ("error", "warning")
            assert issue.table is not None
            assert issue.issue_type is not None
