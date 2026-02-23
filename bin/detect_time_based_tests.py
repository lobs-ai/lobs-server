#!/usr/bin/env python3
"""
Detect time-based test issues that cause flaky tests.

Scans Python files for patterns that can cause midnight UTC test failures:
1. datetime.now() without @freeze_time decorator
2. date.today() without mocking
3. timedelta calculations relative to unmocked 'now'

Usage:
    python bin/detect_time_based_tests.py [path]
    python bin/detect_time_based_tests.py tests/
    python bin/detect_time_based_tests.py --check  # Exit 1 if issues found
"""

import ast
import sys
from pathlib import Path
from typing import List, Tuple
from dataclasses import dataclass


@dataclass
class TimeIssue:
    """Represents a detected time-based test issue."""
    file_path: str
    line_number: int
    column: int
    issue_type: str
    code_snippet: str
    suggestion: str


class TimeBasedTestDetector(ast.NodeVisitor):
    """AST visitor that detects problematic time-based patterns in tests."""
    
    def __init__(self, file_path: str, source_lines: List[str]):
        self.file_path = file_path
        self.source_lines = source_lines
        self.issues: List[TimeIssue] = []
        self.has_freeze_time = False
        self.freeze_time_lines = set()
        
    def visit_FunctionDef(self, node: ast.FunctionDef):
        """Check function decorators for @freeze_time."""
        # Reset freeze_time tracking for each function
        function_has_freeze_time = False
        
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id == 'freeze_time':
                function_has_freeze_time = True
                self.freeze_time_lines.add(node.lineno)
            elif isinstance(decorator, ast.Call):
                if isinstance(decorator.func, ast.Name) and decorator.func.id == 'freeze_time':
                    function_has_freeze_time = True
                    self.freeze_time_lines.add(node.lineno)
        
        # Store the current state
        prev_has_freeze_time = self.has_freeze_time
        self.has_freeze_time = function_has_freeze_time
        
        # Visit children
        self.generic_visit(node)
        
        # Restore state
        self.has_freeze_time = prev_has_freeze_time
    
    def visit_Call(self, node: ast.Call):
        """Detect calls to datetime.now(), date.today(), etc."""
        # Check for datetime.now()
        if self._is_call_to(node, 'datetime', 'now'):
            if not self.has_freeze_time:
                self._add_issue(
                    node,
                    'datetime_now_without_freeze_time',
                    'datetime.now() called without @freeze_time decorator',
                    'Add @freeze_time decorator or use freezegun.freeze_time context manager'
                )
        
        # Check for date.today()
        elif self._is_call_to(node, 'date', 'today'):
            if not self.has_freeze_time:
                self._add_issue(
                    node,
                    'date_today_without_mocking',
                    'date.today() called without @freeze_time decorator',
                    'Add @freeze_time decorator or use freezegun.freeze_time context manager'
                )
        
        # Check for datetime.utcnow() (deprecated but still used)
        elif self._is_call_to(node, 'datetime', 'utcnow'):
            if not self.has_freeze_time:
                self._add_issue(
                    node,
                    'datetime_utcnow_without_freeze_time',
                    'datetime.utcnow() called without @freeze_time decorator',
                    'Add @freeze_time decorator and consider using datetime.now(timezone.utc) instead'
                )
        
        # Check for datetime.now(tz=...) - still problematic without freeze_time
        elif self._is_call_to(node, 'datetime', 'now') and node.args:
            if not self.has_freeze_time:
                self._add_issue(
                    node,
                    'datetime_now_with_tz_without_freeze_time',
                    'datetime.now(tz=...) called without @freeze_time decorator',
                    'Add @freeze_time decorator or use freezegun.freeze_time context manager'
                )
        
        self.generic_visit(node)
    
    def _is_call_to(self, node: ast.Call, module: str, attr: str) -> bool:
        """Check if a call node is calling module.attr()."""
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                return node.func.value.id == module and node.func.attr == attr
        return False
    
    def _add_issue(self, node: ast.AST, issue_type: str, description: str, suggestion: str):
        """Record a detected issue."""
        line_num = node.lineno
        col = node.col_offset
        
        # Get code snippet
        if 0 <= line_num - 1 < len(self.source_lines):
            code_snippet = self.source_lines[line_num - 1].strip()
        else:
            code_snippet = ""
        
        self.issues.append(TimeIssue(
            file_path=self.file_path,
            line_number=line_num,
            column=col,
            issue_type=issue_type,
            code_snippet=code_snippet,
            suggestion=suggestion
        ))


def scan_file(file_path: Path) -> List[TimeIssue]:
    """Scan a single Python file for time-based test issues."""
    try:
        source = file_path.read_text()
        source_lines = source.splitlines()
        tree = ast.parse(source, filename=str(file_path))
        
        detector = TimeBasedTestDetector(str(file_path), source_lines)
        detector.visit(tree)
        
        return detector.issues
    except Exception as e:
        print(f"Error scanning {file_path}: {e}", file=sys.stderr)
        return []


def scan_directory(directory: Path, pattern: str = "test_*.py") -> List[TimeIssue]:
    """Scan all test files in a directory."""
    all_issues = []
    
    for file_path in directory.rglob(pattern):
        if file_path.is_file():
            issues = scan_file(file_path)
            all_issues.extend(issues)
    
    return all_issues


def print_issues(issues: List[TimeIssue]):
    """Print detected issues in a readable format."""
    if not issues:
        print("✅ No time-based test issues detected!")
        return
    
    print(f"⚠️  Found {len(issues)} time-based test issue(s):\n")
    
    # Group by file
    by_file = {}
    for issue in issues:
        if issue.file_path not in by_file:
            by_file[issue.file_path] = []
        by_file[issue.file_path].append(issue)
    
    for file_path, file_issues in sorted(by_file.items()):
        print(f"📄 {file_path}")
        for issue in file_issues:
            print(f"  Line {issue.line_number}:{issue.column} - {issue.issue_type}")
            print(f"    Code: {issue.code_snippet}")
            print(f"    💡 {issue.suggestion}")
            print()


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Detect time-based test issues that cause flaky tests"
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="tests/",
        help="Path to scan (file or directory, default: tests/)"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit with code 1 if any issues are found"
    )
    parser.add_argument(
        "--pattern",
        default="test_*.py",
        help="File pattern to match (default: test_*.py)"
    )
    
    args = parser.parse_args()
    
    path = Path(args.path)
    
    if not path.exists():
        print(f"Error: Path '{path}' does not exist", file=sys.stderr)
        sys.exit(1)
    
    # Scan files
    if path.is_file():
        issues = scan_file(path)
    else:
        issues = scan_directory(path, args.pattern)
    
    # Print results
    print_issues(issues)
    
    # Exit with appropriate code
    if args.check and issues:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
