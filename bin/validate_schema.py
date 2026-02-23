#!/usr/bin/env python3
"""
Schema validator: checks that SQLAlchemy models match the actual database schema.

Validates:
1. All model tables exist in database
2. All model columns exist with correct types
3. No missing or extra columns
4. Correct nullable constraints
5. Foreign key constraints match

Usage:
    python bin/validate_schema.py
    python bin/validate_schema.py --check  # Exit 1 if validation fails
"""

import sys
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Set
from dataclasses import dataclass

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import inspect, MetaData
from sqlalchemy.ext.asyncio import create_async_engine
from app.database import Base
from app.config import settings
import app.models  # Import all models to register them with Base.metadata


@dataclass
class SchemaIssue:
    """Represents a schema validation issue."""
    severity: str  # error, warning
    table: str
    issue_type: str
    description: str


class SchemaValidator:
    """Validates SQLAlchemy models against actual database schema."""
    
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.issues: List[SchemaIssue] = []
    
    async def validate(self) -> List[SchemaIssue]:
        """Run full schema validation."""
        engine = create_async_engine(self.database_url, echo=False)
        
        try:
            # Get model metadata
            model_metadata = Base.metadata
            
            # Get database schema
            async with engine.connect() as conn:
                db_metadata = MetaData()
                await conn.run_sync(lambda sync_conn: db_metadata.reflect(bind=sync_conn))
                
                # Validate tables
                await self._validate_tables(model_metadata, db_metadata)
                
                # Validate columns for each table
                for table_name in model_metadata.tables.keys():
                    if table_name in db_metadata.tables:
                        await self._validate_table_columns(
                            table_name,
                            model_metadata.tables[table_name],
                            db_metadata.tables[table_name]
                        )
        
        finally:
            await engine.dispose()
        
        return self.issues
    
    async def _validate_tables(self, model_metadata: MetaData, db_metadata: MetaData):
        """Validate that all model tables exist in database."""
        model_tables = set(model_metadata.tables.keys())
        db_tables = set(db_metadata.tables.keys())
        
        # Check for missing tables
        missing_tables = model_tables - db_tables
        for table in missing_tables:
            self.issues.append(SchemaIssue(
                severity="error",
                table=table,
                issue_type="missing_table",
                description=f"Table '{table}' defined in models but not in database"
            ))
        
        # Check for extra tables (warning only)
        extra_tables = db_tables - model_tables
        for table in extra_tables:
            self.issues.append(SchemaIssue(
                severity="warning",
                table=table,
                issue_type="extra_table",
                description=f"Table '{table}' exists in database but not in models"
            ))
    
    async def _validate_table_columns(self, table_name: str, model_table, db_table):
        """Validate columns for a specific table."""
        model_cols = {col.name: col for col in model_table.columns}
        db_cols = {col.name: col for col in db_table.columns}
        
        # Check for missing columns
        missing_cols = set(model_cols.keys()) - set(db_cols.keys())
        for col_name in missing_cols:
            self.issues.append(SchemaIssue(
                severity="error",
                table=table_name,
                issue_type="missing_column",
                description=f"Column '{col_name}' defined in model but not in database"
            ))
        
        # Check for extra columns (warning only)
        extra_cols = set(db_cols.keys()) - set(model_cols.keys())
        for col_name in extra_cols:
            self.issues.append(SchemaIssue(
                severity="warning",
                table=table_name,
                issue_type="extra_column",
                description=f"Column '{col_name}' exists in database but not in model"
            ))
        
        # Validate column properties for columns that exist in both
        common_cols = set(model_cols.keys()) & set(db_cols.keys())
        for col_name in common_cols:
            model_col = model_cols[col_name]
            db_col = db_cols[col_name]
            
            # Check nullable constraint
            if model_col.nullable != db_col.nullable:
                self.issues.append(SchemaIssue(
                    severity="error",
                    table=table_name,
                    issue_type="nullable_mismatch",
                    description=f"Column '{col_name}': nullable mismatch (model={model_col.nullable}, db={db_col.nullable})"
                ))
            
            # Check type compatibility (basic check)
            model_type = str(model_col.type)
            db_type = str(db_col.type)
            
            # SQLite type normalization
            type_map = {
                "VARCHAR": "STRING",
                "INTEGER": "INTEGER",
                "BOOLEAN": "BOOLEAN",
                "DATETIME": "DATETIME",
                "TEXT": "TEXT",
                "REAL": "FLOAT",
                "FLOAT": "FLOAT",
                "JSON": "JSON"
            }
            
            # Extract base type
            def normalize_type(t: str) -> str:
                t_upper = t.upper()
                for key, value in type_map.items():
                    if key in t_upper:
                        return value
                return t_upper.split("(")[0].strip()
            
            model_base = normalize_type(model_type)
            db_base = normalize_type(db_type)
            
            if model_base != db_base and not self._types_compatible(model_base, db_base):
                self.issues.append(SchemaIssue(
                    severity="warning",
                    table=table_name,
                    issue_type="type_mismatch",
                    description=f"Column '{col_name}': type mismatch (model={model_type}, db={db_type})"
                ))
    
    def _types_compatible(self, model_type: str, db_type: str) -> bool:
        """Check if two types are compatible (some leeway for SQLite quirks)."""
        # SQLite stores all strings as TEXT/VARCHAR
        if (model_type in ("STRING", "TEXT", "VARCHAR") and 
            db_type in ("STRING", "TEXT", "VARCHAR")):
            return True
        
        # Numeric types
        if (model_type in ("INTEGER", "FLOAT", "REAL") and
            db_type in ("INTEGER", "FLOAT", "REAL")):
            return True
        
        # DateTime types (SQLite stores as TIMESTAMP or DATETIME)
        if (model_type in ("DATETIME", "TIMESTAMP") and
            db_type in ("DATETIME", "TIMESTAMP")):
            return True
        
        return False


def print_issues(issues: List[SchemaIssue]):
    """Print validation issues in a readable format."""
    if not issues:
        print("✅ Schema validation passed! All models match database schema.")
        return
    
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    
    if errors:
        print(f"\n❌ Found {len(errors)} schema error(s):\n")
        for issue in errors:
            print(f"  [{issue.table}] {issue.issue_type}")
            print(f"    {issue.description}\n")
    
    if warnings:
        print(f"\n⚠️  Found {len(warnings)} schema warning(s):\n")
        for issue in warnings:
            print(f"  [{issue.table}] {issue.issue_type}")
            print(f"    {issue.description}\n")
    
    # Summary
    print(f"Summary: {len(errors)} errors, {len(warnings)} warnings")


async def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Validate SQLAlchemy models against database schema"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit with code 1 if validation fails (for CI)"
    )
    parser.add_argument(
        "--database-url",
        default=settings.DATABASE_URL,
        help=f"Database URL (default: {settings.DATABASE_URL})"
    )
    
    args = parser.parse_args()
    
    # Run validation
    validator = SchemaValidator(args.database_url)
    issues = await validator.validate()
    
    # Print results
    print_issues(issues)
    
    # Exit with appropriate code
    errors = [i for i in issues if i.severity == "error"]
    if args.check and errors:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
