#!/usr/bin/env python3
"""
Database Migration Validator: validates migration scripts for correctness and safety.

Validates:
1. Python syntax is valid
2. Naming convention (YYYYMMDD_description.py or similar)
3. Contains proper migration functions
4. No dangerous operations without safeguards
5. Imports are valid
6. No hardcoded credentials or secrets

Usage:
    python bin/validate_migrations.py
    python bin/validate_migrations.py --check  # Exit 1 if validation fails
    python bin/validate_migrations.py --dir migrations/  # Specify directory
"""

import sys
import ast
import re
from pathlib import Path
from typing import List, Set, Dict, Any
from dataclasses import dataclass

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


@dataclass
class MigrationIssue:
    """Represents a migration validation issue."""
    severity: str  # error, warning, info
    file: str
    issue_type: str
    description: str
    line: int = 0


class MigrationValidator:
    """Validates database migration scripts."""
    
    # Dangerous operations that require extra scrutiny
    DANGEROUS_OPERATIONS = {
        "DROP TABLE": "Dropping tables is irreversible",
        "DROP COLUMN": "Dropping columns is irreversible",
        "TRUNCATE": "Truncating tables deletes all data",
        "DELETE FROM": "Unqualified DELETE can remove all data",
        "UPDATE": "Unqualified UPDATE can modify all rows",
    }
    
    # Required function patterns
    MIGRATION_FUNCTIONS = [
        "main",    # async def main()
        "migrate", # Custom lobs-server style
        "up",      # def up(conn)
        "upgrade", # Alembic-style
        "apply",   # Custom style
    ]
    
    def __init__(self, migrations_dir: str = "migrations"):
        self.migrations_dir = Path(migrations_dir)
        self.issues: List[MigrationIssue] = []
    
    def validate(self) -> List[MigrationIssue]:
        """Run full migration validation."""
        if not self.migrations_dir.exists():
            self.issues.append(MigrationIssue(
                severity="error",
                file="migrations",
                issue_type="directory_not_found",
                description=f"Migration directory not found: {self.migrations_dir}"
            ))
            return self.issues
        
        # Get all Python files in migrations directory
        migration_files = sorted(self.migrations_dir.glob("*.py"))
        
        if not migration_files:
            self.issues.append(MigrationIssue(
                severity="warning",
                file="migrations",
                issue_type="no_migrations",
                description="No migration files found"
            ))
            return self.issues
        
        # Validate each migration
        for filepath in migration_files:
            # Skip __init__.py and __pycache__
            if filepath.name.startswith("__"):
                continue
            
            self._validate_migration_file(filepath)
        
        return self.issues
    
    def _validate_migration_file(self, filepath: Path):
        """Validate a single migration file."""
        filename = filepath.name
        
        # Check naming convention
        self._validate_naming(filename, filepath)
        
        # Read and parse file
        try:
            with open(filepath, 'r') as f:
                content = f.read()
        except Exception as e:
            self.issues.append(MigrationIssue(
                severity="error",
                file=filename,
                issue_type="read_error",
                description=f"Cannot read file: {e}"
            ))
            return
        
        # Validate Python syntax
        try:
            tree = ast.parse(content, filename=filename)
        except SyntaxError as e:
            self.issues.append(MigrationIssue(
                severity="error",
                file=filename,
                issue_type="syntax_error",
                description=f"Syntax error at line {e.lineno}: {e.msg}",
                line=e.lineno or 0
            ))
            return
        
        # Validate AST
        self._validate_ast(filename, tree, content)
    
    def _validate_naming(self, filename: str, filepath: Path):
        """Validate migration file naming convention."""
        # Expected patterns:
        # - YYYYMMDD_description.py
        # - add_something.py
        # - create_table_name.py
        
        # Check for reasonable naming (no spaces, special chars except underscore)
        if not re.match(r'^[a-z0-9_]+\.py$', filename):
            self.issues.append(MigrationIssue(
                severity="warning",
                file=filename,
                issue_type="naming_convention",
                description="Migration filename should use lowercase and underscores only"
            ))
        
        # Check for descriptive name (at least 5 chars before .py)
        if len(filename) < 8:  # x.py is 4 chars, need at least xxx_.py
            self.issues.append(MigrationIssue(
                severity="warning",
                file=filename,
                issue_type="name_too_short",
                description="Migration filename should be descriptive"
            ))
    
    def _validate_ast(self, filename: str, tree: ast.AST, content: str):
        """Validate the AST of a migration file."""
        # Check for migration functions
        has_migration_function = False
        functions = []
        
        for node in ast.walk(tree):
            # Check for function definitions
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node.name)
                if node.name in self.MIGRATION_FUNCTIONS:
                    has_migration_function = True
            
            # Check for dangerous string operations (SQL)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                self._check_sql_safety(filename, node.value, getattr(node, 'lineno', 0))
            
            # Check for dangerous imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._check_import_safety(filename, alias.name, getattr(node, 'lineno', 0))
            
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    self._check_import_safety(filename, node.module, getattr(node, 'lineno', 0))
        
        # Check that migration has a proper entry point
        if not has_migration_function:
            self.issues.append(MigrationIssue(
                severity="error",
                file=filename,
                issue_type="no_migration_function",
                description=f"No migration function found. Expected one of: {', '.join(self.MIGRATION_FUNCTIONS)}. "
                           f"Found functions: {', '.join(functions) or 'none'}"
            ))
        
        # Check for docstring
        docstring = ast.get_docstring(tree)
        if not docstring or not docstring.strip():
            self.issues.append(MigrationIssue(
                severity="info",
                file=filename,
                issue_type="missing_docstring",
                description="Migration has no docstring - consider documenting what it does"
            ))
    
    def _check_sql_safety(self, filename: str, sql: str, line: int):
        """Check SQL string for dangerous operations."""
        sql_upper = sql.upper()
        
        # Check for dangerous operations
        for operation, reason in self.DANGEROUS_OPERATIONS.items():
            if operation in sql_upper:
                # Check if it's in a WHERE clause (safer)
                if operation.startswith("DELETE") or operation.startswith("UPDATE"):
                    if "WHERE" not in sql_upper:
                        # In migrations, UPDATE without WHERE is often used for backfilling data
                        # So make this a warning instead of an error
                        severity = "warning" if operation.startswith("UPDATE") else "error"
                        self.issues.append(MigrationIssue(
                            severity=severity,
                            file=filename,
                            issue_type="unsafe_operation",
                            description=f"{operation} without WHERE clause detected: {reason}. "
                                      "Ensure this is intentional (e.g., backfilling data).",
                            line=line
                        ))
                else:
                    self.issues.append(MigrationIssue(
                        severity="warning",
                        file=filename,
                        issue_type="dangerous_operation",
                        description=f"{operation} detected: {reason}",
                        line=line
                    ))
        
        # Check for hardcoded credentials (basic check)
        suspicious_patterns = [
            (r'password\s*=\s*["\'][^"\']+["\']', "password"),
            (r'api[_-]?key\s*=\s*["\'][^"\']+["\']', "API key"),
            (r'secret\s*=\s*["\'][^"\']+["\']', "secret"),
            (r'token\s*=\s*["\'][^"\']+["\']', "token"),
        ]
        
        for pattern, name in suspicious_patterns:
            if re.search(pattern, sql, re.IGNORECASE):
                self.issues.append(MigrationIssue(
                    severity="error",
                    file=filename,
                    issue_type="hardcoded_credential",
                    description=f"Possible hardcoded {name} detected in SQL",
                    line=line
                ))
    
    def _check_import_safety(self, filename: str, module: str, line: int):
        """Check for dangerous imports."""
        # Warn about uncommon imports that might indicate issues
        suspicious_imports = [
            "os.system",
            "subprocess",
            "eval",
            "exec",
            "__import__",
        ]
        
        for sus in suspicious_imports:
            if sus in module:
                self.issues.append(MigrationIssue(
                    severity="warning",
                    file=filename,
                    issue_type="suspicious_import",
                    description=f"Suspicious import '{module}' - migrations should focus on schema changes",
                    line=line
                ))


def print_issues(issues: List[MigrationIssue]):
    """Print validation issues in a readable format."""
    if not issues:
        print("✅ Migration validation passed! All migrations are valid.")
        return
    
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    info = [i for i in issues if i.severity == "info"]
    
    if errors:
        print(f"\n❌ Found {len(errors)} migration error(s):\n")
        for issue in errors:
            location = f":{issue.line}" if issue.line else ""
            print(f"  [{issue.file}{location}] {issue.issue_type}")
            print(f"    {issue.description}\n")
    
    if warnings:
        print(f"\n⚠️  Found {len(warnings)} migration warning(s):\n")
        for issue in warnings:
            location = f":{issue.line}" if issue.line else ""
            print(f"  [{issue.file}{location}] {issue.issue_type}")
            print(f"    {issue.description}\n")
    
    if info:
        print(f"\n💡 Found {len(info)} migration suggestion(s):\n")
        for issue in info:
            print(f"  [{issue.file}] {issue.issue_type}")
            print(f"    {issue.description}\n")
    
    # Summary
    print(f"Summary: {len(errors)} errors, {len(warnings)} warnings, {len(info)} suggestions")


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Validate database migration scripts"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit with code 1 if validation fails (for CI)"
    )
    parser.add_argument(
        "--dir",
        type=str,
        default="migrations",
        help="Migrations directory (default: migrations)"
    )
    
    args = parser.parse_args()
    
    # Run validation
    validator = MigrationValidator(migrations_dir=args.dir)
    issues = validator.validate()
    
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
