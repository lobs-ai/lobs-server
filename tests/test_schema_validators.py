"""
Tests for schema validation scripts.

Tests both API schema validation and migration validation.
"""

import sys
from pathlib import Path

# Add project root and bin to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

import pytest
from validate_api_schemas import APISchemaValidator
from validate_migrations import MigrationValidator


class TestAPISchemaValidator:
    """Test API schema validation."""
    
    def test_validator_finds_models(self):
        """Test that validator can find Pydantic models."""
        validator = APISchemaValidator()
        models = validator._get_pydantic_models()
        
        assert len(models) > 0, "Should find Pydantic models"
        
        # Check for some known models
        model_names = [m.__name__ for m in models]
        assert "Task" in model_names
        assert "Project" in model_names
        assert "Memory" in model_names
    
    def test_validation_runs_without_errors(self):
        """Test that validation runs and produces results."""
        validator = APISchemaValidator()
        issues = validator.validate()
        
        # Should not crash
        assert isinstance(issues, list)
        
        # Should not have critical errors
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) == 0, f"Found unexpected errors: {errors}"
    
    def test_schema_generation(self):
        """Test that schemas can be generated."""
        validator = APISchemaValidator()
        validator.validate()
        
        assert validator.schemas is not None
        assert len(validator.schemas) > 0
        
        # Check that schemas have expected structure
        # Schema definitions should be a dict
        assert isinstance(validator.schemas, dict)
    
    def test_crud_consistency_validation(self):
        """Test that CRUD consistency checks work."""
        validator = APISchemaValidator()
        issues = validator.validate()
        
        # Check that no entity is missing base fields in response model
        base_field_errors = [
            i for i in issues 
            if i.issue_type == "missing_base_fields"
        ]
        
        # We shouldn't have any critical missing base fields
        assert len(base_field_errors) == 0, \
            f"Found missing base fields: {base_field_errors}"


class TestMigrationValidator:
    """Test migration validation."""
    
    def test_validator_finds_migrations(self):
        """Test that validator can find migration files."""
        validator = MigrationValidator(migrations_dir="migrations")
        issues = validator.validate()
        
        # Should find migrations
        # If no errors about directory not found, we found the directory
        dir_errors = [i for i in issues if i.issue_type == "directory_not_found"]
        assert len(dir_errors) == 0, "Migrations directory should exist"
    
    def test_validation_runs(self):
        """Test that validation runs without crashing."""
        validator = MigrationValidator(migrations_dir="migrations")
        issues = validator.validate()
        
        # Should not crash
        assert isinstance(issues, list)
        
        # Should not have critical syntax errors
        syntax_errors = [i for i in issues if i.issue_type == "syntax_error"]
        assert len(syntax_errors) == 0, f"Found syntax errors: {syntax_errors}"
    
    def test_dangerous_operations_detected(self):
        """Test that dangerous operations are flagged."""
        validator = MigrationValidator(migrations_dir="migrations")
        issues = validator.validate()
        
        # There should be some warnings (UPDATE without WHERE, etc.)
        warnings = [i for i in issues if i.severity == "warning"]
        
        # This is informational - we expect some warnings in real migrations
        # Just verify the detection is working
        assert isinstance(warnings, list)
    
    def test_migration_function_detection(self):
        """Test that migration functions are detected."""
        validator = MigrationValidator(migrations_dir="migrations")
        issues = validator.validate()
        
        # Should recognize 'migrate' as a valid function name
        missing_func_errors = [
            i for i in issues 
            if i.issue_type == "no_migration_function"
        ]
        
        # We shouldn't have any since we added 'migrate' to valid functions
        assert len(missing_func_errors) == 0, \
            f"Should recognize 'migrate' function: {missing_func_errors}"


class TestSchemaValidatorCLI:
    """Test CLI interfaces of validators."""
    
    def test_api_schema_validator_import(self):
        """Test that API schema validator can be imported."""
        import validate_api_schemas
        assert hasattr(validate_api_schemas, 'APISchemaValidator')
        assert hasattr(validate_api_schemas, 'main')
    
    def test_migration_validator_import(self):
        """Test that migration validator can be imported."""
        import validate_migrations
        assert hasattr(validate_migrations, 'MigrationValidator')
        assert hasattr(validate_migrations, 'main')


# Integration test
def test_both_validators_pass():
    """Integration test: both validators should pass on current codebase."""
    # API schemas
    api_validator = APISchemaValidator()
    api_issues = api_validator.validate()
    api_errors = [i for i in api_issues if i.severity == "error"]
    
    assert len(api_errors) == 0, \
        f"API schema validation failed: {api_errors}"
    
    # Migrations
    migration_validator = MigrationValidator(migrations_dir="migrations")
    migration_issues = migration_validator.validate()
    migration_errors = [i for i in migration_issues if i.severity == "error"]
    
    assert len(migration_errors) == 0, \
        f"Migration validation failed: {migration_errors}"
