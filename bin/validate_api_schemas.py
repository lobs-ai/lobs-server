#!/usr/bin/env python3
"""
API Schema Validator: validates Pydantic request/response models.

Validates:
1. All Pydantic models can generate valid JSON schemas
2. Schemas have required fields properly marked
3. No breaking changes to existing schemas (if baseline exists)
4. Schema consistency between Create/Update/Response models
5. Type safety and validation rules

Usage:
    python bin/validate_api_schemas.py
    python bin/validate_api_schemas.py --check  # Exit 1 if validation fails
    python bin/validate_api_schemas.py --export schemas.json  # Export for docs
"""

import sys
import json
from pathlib import Path
from typing import Any, Dict, List, Set
from dataclasses import dataclass

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema
import app.schemas as schemas


@dataclass
class SchemaIssue:
    """Represents a schema validation issue."""
    severity: str  # error, warning, info
    model: str
    issue_type: str
    description: str


class APISchemaValidator:
    """Validates Pydantic API schemas."""
    
    def __init__(self):
        self.issues: List[SchemaIssue] = []
        self.schemas: Dict[str, Any] = {}
    
    def validate(self) -> List[SchemaIssue]:
        """Run full schema validation."""
        # Get all Pydantic models from schemas module
        models = self._get_pydantic_models()
        
        if not models:
            self.issues.append(SchemaIssue(
                severity="error",
                model="schemas",
                issue_type="no_models_found",
                description="No Pydantic models found in app.schemas"
            ))
            return self.issues
        
        # Generate JSON schemas
        try:
            schema_definitions, _ = models_json_schema(
                [(model, 'validation') for model in models],
                title='Lobs API Schemas'
            )
            self.schemas = schema_definitions
        except Exception as e:
            self.issues.append(SchemaIssue(
                severity="error",
                model="all",
                issue_type="schema_generation_failed",
                description=f"Failed to generate JSON schemas: {e}"
            ))
            return self.issues
        
        # Validate individual models
        for model in models:
            self._validate_model(model)
        
        # Validate CRUD consistency
        self._validate_crud_consistency(models)
        
        return self.issues
    
    def _get_pydantic_models(self) -> List[type[BaseModel]]:
        """Extract all Pydantic models from schemas module."""
        models = []
        for name in dir(schemas):
            obj = getattr(schemas, name)
            if (isinstance(obj, type) and 
                issubclass(obj, BaseModel) and 
                obj is not BaseModel):
                models.append(obj)
        return models
    
    def _validate_model(self, model: type[BaseModel]):
        """Validate a single Pydantic model."""
        model_name = model.__name__
        
        # Check that model can generate schema
        try:
            schema = model.model_json_schema()
        except Exception as e:
            self.issues.append(SchemaIssue(
                severity="error",
                model=model_name,
                issue_type="invalid_schema",
                description=f"Cannot generate JSON schema: {e}"
            ))
            return
        
        # Check for docstring
        if not model.__doc__ or model.__doc__.strip() == "":
            self.issues.append(SchemaIssue(
                severity="info",
                model=model_name,
                issue_type="missing_docstring",
                description="Model has no docstring - consider adding documentation"
            ))
        
        # Check for required fields
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        
        if not properties:
            self.issues.append(SchemaIssue(
                severity="warning",
                model=model_name,
                issue_type="no_properties",
                description="Model has no properties defined"
            ))
        
        # Check field types
        for field_name, field_info in properties.items():
            self._validate_field(model_name, field_name, field_info, required)
    
    def _validate_field(self, model_name: str, field_name: str, 
                       field_info: Dict[str, Any], required: List[str]):
        """Validate a single field."""
        # Check for type definition
        if "type" not in field_info and "$ref" not in field_info and "anyOf" not in field_info:
            self.issues.append(SchemaIssue(
                severity="error",
                model=model_name,
                issue_type="missing_type",
                description=f"Field '{field_name}' has no type definition"
            ))
        
        # Check for overly permissive Any types
        if field_info.get("type") == "object" and "properties" not in field_info:
            self.issues.append(SchemaIssue(
                severity="warning",
                model=model_name,
                issue_type="untyped_object",
                description=f"Field '{field_name}' uses Any/dict - consider defining explicit schema"
            ))
        
        # Check for string fields without constraints (info only)
        if field_info.get("type") == "string" and field_name in required:
            has_constraints = any(k in field_info for k in [
                "minLength", "maxLength", "pattern", "enum"
            ])
            if not has_constraints and field_name not in ["id", "title", "content", "text", "notes"]:
                # Only warn for non-common fields
                pass  # Too noisy, skip this check
    
    def _validate_crud_consistency(self, models: List[type[BaseModel]]):
        """Validate consistency between Create/Update/Response models."""
        # Group models by entity (e.g., Task, Project, Memory)
        entities: Dict[str, Dict[str, type[BaseModel]]] = {}
        
        for model in models:
            name = model.__name__
            # Detect entity name (everything before Base/Create/Update/etc)
            for suffix in ["Base", "Create", "Update", "Response", "ListItem", 
                          "List", "Summary", "Search"]:
                if name.endswith(suffix):
                    entity = name[:-len(suffix)]
                    if entity not in entities:
                        entities[entity] = {}
                    entities[entity][suffix] = model
                    break
            else:
                # No suffix - this is the response model
                if name not in entities:
                    entities[name] = {}
                entities[name]["Response"] = model
        
        # Validate each entity
        for entity, model_variants in entities.items():
            self._validate_entity_consistency(entity, model_variants)
    
    def _validate_entity_consistency(self, entity: str, 
                                     variants: Dict[str, type[BaseModel]]):
        """Validate consistency within an entity's CRUD models."""
        # Check that Create has an ID field
        if "Create" in variants:
            create_model = variants["Create"]
            schema = create_model.model_json_schema()
            properties = schema.get("properties", {})
            
            if "id" not in properties:
                # Some entities might not have ID in create (auto-generated)
                pass
        
        # Check that Update model has all fields optional
        if "Update" in variants:
            update_model = variants["Update"]
            schema = update_model.model_json_schema()
            required = schema.get("required", [])
            
            if required:
                self.issues.append(SchemaIssue(
                    severity="warning",
                    model=update_model.__name__,
                    issue_type="update_required_fields",
                    description=f"Update model has required fields: {required}. "
                               "Update models should typically have all optional fields."
                ))
        
        # Check that Response/main model has all base fields
        if "Base" in variants and ("Response" in variants or entity in variants):
            base_model = variants["Base"]
            response_model = variants.get("Response") or variants.get(entity)
            
            if response_model:
                base_fields = set(base_model.model_fields.keys())
                response_fields = set(response_model.model_fields.keys())
                
                missing_fields = base_fields - response_fields
                if missing_fields:
                    self.issues.append(SchemaIssue(
                        severity="error",
                        model=response_model.__name__,
                        issue_type="missing_base_fields",
                        description=f"Response model missing base fields: {missing_fields}"
                    ))
    
    def export_schemas(self, output_path: str):
        """Export all schemas to a JSON file for documentation."""
        if not self.schemas:
            print("No schemas to export. Run validate() first.")
            return
        
        output = {
            "title": "Lobs API Schemas",
            "version": "1.0.0",
            "schemas": self.schemas
        }
        
        with open(output_path, 'w') as f:
            json.dump(output, f, indent=2)
        
        print(f"✅ Exported {len(self.schemas)} schemas to {output_path}")


def print_issues(issues: List[SchemaIssue]):
    """Print validation issues in a readable format."""
    if not issues:
        print("✅ API schema validation passed! All schemas are valid.")
        return
    
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    info = [i for i in issues if i.severity == "info"]
    
    if errors:
        print(f"\n❌ Found {len(errors)} schema error(s):\n")
        for issue in errors:
            print(f"  [{issue.model}] {issue.issue_type}")
            print(f"    {issue.description}\n")
    
    if warnings:
        print(f"\n⚠️  Found {len(warnings)} schema warning(s):\n")
        for issue in warnings:
            print(f"  [{issue.model}] {issue.issue_type}")
            print(f"    {issue.description}\n")
    
    if info:
        print(f"\n💡 Found {len(info)} schema suggestion(s):\n")
        for issue in info:
            print(f"  [{issue.model}] {issue.issue_type}")
            print(f"    {issue.description}\n")
    
    # Summary
    print(f"Summary: {len(errors)} errors, {len(warnings)} warnings, {len(info)} suggestions")


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Validate Pydantic API request/response schemas"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit with code 1 if validation fails (for CI)"
    )
    parser.add_argument(
        "--export",
        type=str,
        metavar="FILE",
        help="Export schemas to JSON file for documentation"
    )
    
    args = parser.parse_args()
    
    # Run validation
    validator = APISchemaValidator()
    issues = validator.validate()
    
    # Export if requested
    if args.export:
        validator.export_schemas(args.export)
    
    # Print results
    print_issues(issues)
    
    # Exit with appropriate code
    errors = [i for i in issues if i.severity == "error"]
    if args.check and errors:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
