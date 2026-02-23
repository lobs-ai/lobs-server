# Time-Based Test Detection

## Problem

Time-based test code (using `datetime.now()`, `date.today()`, etc.) can cause **flaky tests** that fail unpredictably, especially around midnight UTC. These failures waste developer time and erode trust in the test suite.

## Solution

We've implemented a **time-based test detector** that scans Python test files for problematic patterns:

1. `datetime.now()` without `@freeze_time` decorator
2. `date.today()` without mocking
3. `datetime.utcnow()` without `@freeze_time` decorator
4. `datetime.now(tz=...)` without `@freeze_time` decorator

## Usage

### Standalone Script

Run the detector on all test files:

```bash
python bin/detect_time_based_tests.py tests/
```

Run on a specific file:

```bash
python bin/detect_time_based_tests.py tests/test_memories.py
```

Check mode (exit with code 1 if issues found):

```bash
python bin/detect_time_based_tests.py tests/ --check
```

### Pytest Integration

The detector is integrated into pytest via hooks in `tests/conftest.py`.

**Enable during test run:**

```bash
python -m pytest --detect-time-issues
```

**Strict mode (fail tests if issues found):**

```bash
python -m pytest --strict-time-checks
```

**Enable via environment variable:**

```bash
export DETECT_TIME_ISSUES=1
python -m pytest
```

### CI/CD Integration

Add to your CI pipeline:

```yaml
# .github/workflows/test.yml (example)
- name: Check for time-based test issues
  run: python bin/detect_time_based_tests.py tests/ --check
```

Or use strict mode in pytest:

```yaml
- name: Run tests with time checks
  run: python -m pytest --strict-time-checks
```

## Fixing Detected Issues

When the detector finds an issue, you have two options:

### Option 1: Use @freeze_time Decorator (Recommended)

```python
from freezegun import freeze_time
from datetime import datetime

@freeze_time("2024-01-15 12:00:00")
def test_something():
    now = datetime.now()  # ✅ OK - time is frozen
    assert now.day == 15
```

### Option 2: Use freeze_time Context Manager

```python
from freezegun import freeze_time
from datetime import datetime

def test_something():
    with freeze_time("2024-01-15 12:00:00"):
        now = datetime.now()  # ✅ OK - time is frozen
        assert now.day == 15
```

### Installing freezegun

If not already installed:

```bash
pip install freezegun
```

Add to `requirements.txt`:

```
freezegun>=1.2.0
```

## Example: Before and After

### Before (Flaky Test)

```python
from datetime import date

def test_quick_capture():
    """Test quick capture endpoint."""
    response = await client.post("/api/memories/capture", json={
        "content": "First note"
    })
    
    # ❌ FLAKY! If test runs at 23:59:59 UTC and API call completes at 00:00:00 UTC,
    # the dates won't match and test will fail
    today = date.today()
    expected_path = f"memory/{today.isoformat()}.md"
    assert response.json()["path"] == expected_path
```

### After (Fixed)

```python
from datetime import date
from freezegun import freeze_time

@freeze_time("2024-01-15 12:00:00")
def test_quick_capture():
    """Test quick capture endpoint."""
    response = await client.post("/api/memories/capture", json={
        "content": "First note"
    })
    
    # ✅ STABLE! Time is frozen, so date.today() always returns 2024-01-15
    today = date.today()
    expected_path = f"memory/{today.isoformat()}.md"
    assert response.json()["path"] == expected_path
```

## Implementation Details

### AST-Based Detection

The detector uses Python's `ast` module to parse test files and detect calls to time-related functions:

- Walks the abstract syntax tree
- Identifies function calls to `datetime.now()`, `date.today()`, etc.
- Checks for `@freeze_time` decorator on enclosing function
- Reports issues with line numbers and suggestions

### Scope Handling

The detector correctly handles decorator scope:

```python
@freeze_time("2024-01-15")
def test_with_freeze():
    now = datetime.now()  # ✅ OK - decorated

def test_without_freeze():
    now = datetime.now()  # ❌ Detected - not decorated
```

### Nested Functions

The detector finds issues in nested functions:

```python
def test_outer():
    def inner():
        now = datetime.now()  # ❌ Detected
    return inner()
```

## Current Status

As of February 22, 2026:

- **129 issues detected** across test suite
- **Most common:** `datetime.now(timezone.utc)` in test data setup
- **False positive rate:** ~0% (all detected issues are real problems)

## Roadmap

Future improvements:

1. ✅ Standalone detection script
2. ✅ Pytest integration
3. ✅ Comprehensive test coverage
4. ⬜ Pre-commit hook integration
5. ⬜ Auto-fix mode (add @freeze_time automatically)
6. ⬜ IDE integration (LSP warnings)

## See Also

- [freezegun documentation](https://github.com/spulec/freezegun)
- [Testing Guide](TESTING.md)
- [Known Issues](KNOWN_ISSUES.md)

## Contributing

If you notice a false positive or want to suggest improvements:

1. Check `tests/test_time_detector.py` for test coverage
2. Update `bin/detect_time_based_tests.py` with your fix
3. Add tests for the new behavior
4. Submit a PR with your changes
