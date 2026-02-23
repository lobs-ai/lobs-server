# Contract Testing Guide

**Purpose:** Validate agent input/output interfaces remain stable and parseable.

**When to use:** Any change to agent prompts, result parsing, or state transitions.

---

## Overview

Contract tests ensure that:
1. **Prompts we send** match what agents expect
2. **Results we parse** match what agents actually return
3. **Schema changes** don't break existing integrations

Unlike unit tests (which mock agent responses), contract tests use **real agent output samples** to validate parsing logic.

---

## Contract Structure

Each agent type has a contract defining:
- **Input schema** — Prompt structure, context packet format
- **Output schema** — Expected result structure, error format
- **State transitions** — Valid status changes
- **Sample outputs** — Real responses captured from production/dev

### Directory Layout

```
tests/
└── contracts/
    ├── __init__.py
    ├── fixtures/                  # Versioned agent output samples
    │   ├── programmer/
    │   │   ├── v1_success.json
    │   │   ├── v1_failure.json
    │   │   ├── v1_blocked.json
    │   │   └── v2_success.json    # New schema version
    │   ├── project_manager/
    │   │   ├── v1_routing.json
    │   │   └── v1_approval.json
    │   └── researcher/
    │       ├── v1_findings.json
    │       └── v1_no_results.json
    ├── schemas/                   # JSON schemas for validation
    │   ├── programmer.schema.json
    │   ├── project_manager.schema.json
    │   └── researcher.schema.json
    ├── test_programmer_contract.py
    ├── test_project_manager_contract.py
    └── test_researcher_contract.py
```

---

## Writing Contract Tests

### Step 1: Capture Real Agent Output

When an agent completes a task, capture the output:

```bash
# From orchestrator logs or worker result files
cat data/worker-results/task-123-transcript.jsonl | tail -1 > \
  tests/contracts/fixtures/programmer/v1_success.json
```

**What to capture:**
- Successful completion
- Failure with error
- Blocked state
- Edge cases (empty results, large output, special characters)

### Step 2: Define the Schema

Create a JSON schema describing the expected structure:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "status": {
      "type": "string",
      "enum": ["completed", "failed", "blocked"]
    },
    "summary": {
      "type": "string",
      "minLength": 1,
      "maxLength": 5000
    },
    "files_changed": {
      "type": "array",
      "items": { "type": "string" }
    },
    "next_steps": {
      "type": ["array", "null"],
      "items": { "type": "string" }
    }
  },
  "required": ["status", "summary"]
}
```

### Step 3: Write the Contract Test

```python
"""Contract tests for programmer agent."""
import json
import pytest
from pathlib import Path
from jsonschema import validate, ValidationError

from app.orchestrator.worker import WorkerManager
from app.orchestrator.prompter import Prompter

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "programmer"
SCHEMA_PATH = Path(__file__).parent / "schemas" / "programmer.schema.json"


def load_fixture(name: str) -> dict:
    """Load a fixture file."""
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


def load_schema() -> dict:
    """Load the programmer agent schema."""
    with open(SCHEMA_PATH) as f:
        return json.load(f)


@pytest.mark.contract
class TestProgrammerContract:
    """Contract tests for programmer agent interface."""
    
    def test_success_output_parses(self):
        """Verify successful task output matches schema."""
        output = load_fixture("v1_success.json")
        schema = load_schema()
        
        # Schema validation
        validate(instance=output, schema=schema)
        
        # Semantic validation
        assert output["status"] == "completed"
        assert len(output["summary"]) > 0
        assert isinstance(output["files_changed"], list)
    
    def test_failure_output_parses(self):
        """Verify failure output includes error details."""
        output = load_fixture("v1_failure.json")
        schema = load_schema()
        
        validate(instance=output, schema=schema)
        
        assert output["status"] == "failed"
        assert "error" in output or "reason" in output
    
    def test_blocked_output_parses(self):
        """Verify blocked output includes blocker info."""
        output = load_fixture("v1_blocked.json")
        schema = load_schema()
        
        validate(instance=output, schema=schema)
        
        assert output["status"] == "blocked"
        assert "blocker" in output or "summary" in output
    
    def test_parsing_with_real_worker_logic(self):
        """Verify WorkerManager can actually parse the output."""
        output = load_fixture("v1_success.json")
        
        # This tests the actual parsing code used in production
        # (Mock just enough to isolate the parsing logic)
        # Example:
        status = output.get("status")
        summary = output.get("summary", "")
        files = output.get("files_changed", [])
        
        assert status in ["completed", "failed", "blocked"]
        assert isinstance(summary, str)
        assert isinstance(files, list)
    
    def test_prompt_structure(self):
        """Verify prompts we send match expected format."""
        # Build a task prompt
        task_data = {
            "id": 123,
            "title": "Fix bug in router",
            "description": "Router crashes on invalid input",
            "project_id": 1,
            "agent": "programmer"
        }
        
        prompter = Prompter()
        prompt = prompter.build_task_prompt(task_data)
        
        # Validate prompt structure
        assert "## Task" in prompt
        assert "## Project" in prompt or "## Context" in prompt
        assert task_data["title"] in prompt
        assert task_data["description"] in prompt
```

### Step 4: Add to CI

```bash
# pytest.ini or pyproject.toml
[tool.pytest.ini_options]
markers = [
    "unit: Unit tests (fast, isolated)",
    "contract: Contract tests (agent I/O validation)",
    "integration: Integration tests (multi-component)",
    "chaos: Chaos tests (failure injection)",
]

# Run only contract tests
pytest -m contract -v
```

---

## Contract Evolution

### When Schemas Change

1. **Backward-compatible change** (add optional field):
   - Update schema with new field (not required)
   - Add new fixture with new field
   - Keep old fixtures (verify they still validate)

2. **Breaking change** (rename field, change type):
   - Create new schema version (`programmer.v2.schema.json`)
   - Add migration logic in parsing code
   - Keep old fixtures and schema for regression tests
   - Mark old schema as deprecated after migration period

### Version Management

```python
# Example: Multi-version parsing
def parse_programmer_output(raw_output: dict) -> ProgrammerResult:
    """Parse programmer output, supporting multiple schema versions."""
    # Detect version (by field presence or explicit version marker)
    if "schema_version" in raw_output:
        version = raw_output["schema_version"]
    elif "files_changed" in raw_output:
        version = "v1"
    else:
        version = "v2"
    
    if version == "v1":
        return _parse_v1(raw_output)
    elif version == "v2":
        return _parse_v2(raw_output)
    else:
        raise ValueError(f"Unsupported schema version: {version}")
```

---

## Best Practices

### ✅ Do

- **Capture real outputs** — Use actual agent responses, not hand-written examples
- **Test edge cases** — Empty results, very long outputs, unicode/special characters
- **Version fixtures** — Keep old versions when schema evolves
- **Test parsing code** — Not just schema validation, but actual parsing logic
- **Update on schema changes** — Add contract test before changing agent interface

### ❌ Don't

- **Don't hand-write outputs** — Use real agent responses to avoid drift
- **Don't skip failures** — Capture failure cases, not just successes
- **Don't test business logic** — Contract tests validate structure, not correctness
- **Don't make tests brittle** — Allow flexible ordering, optional fields where appropriate

---

## Example: Full Contract Test Suite

```python
"""Complete contract test example for programmer agent."""
import json
import pytest
from pathlib import Path
from jsonschema import validate

FIXTURES = Path(__file__).parent / "fixtures" / "programmer"
SCHEMA = Path(__file__).parent / "schemas" / "programmer.schema.json"


class TestProgrammerContractV1:
    """Contract tests for programmer agent v1 interface."""
    
    @pytest.fixture
    def schema(self):
        with open(SCHEMA) as f:
            return json.load(f)
    
    @pytest.fixture(params=[
        "v1_success.json",
        "v1_failure.json",
        "v1_blocked.json"
    ])
    def output_sample(self, request):
        """Parameterized fixture for all output types."""
        with open(FIXTURES / request.param) as f:
            return json.load(f)
    
    def test_all_outputs_validate(self, output_sample, schema):
        """All captured outputs validate against schema."""
        validate(instance=output_sample, schema=schema)
    
    def test_status_is_valid_enum(self, output_sample):
        """Status field is one of the allowed values."""
        assert output_sample["status"] in ["completed", "failed", "blocked"]
    
    def test_summary_is_nonempty_string(self, output_sample):
        """Summary field exists and is not empty."""
        summary = output_sample.get("summary")
        assert summary is not None
        assert isinstance(summary, str)
        assert len(summary) > 0
    
    def test_files_changed_is_list_of_strings(self, output_sample):
        """Files changed is a list of path strings (if present)."""
        if "files_changed" in output_sample:
            files = output_sample["files_changed"]
            assert isinstance(files, list)
            for f in files:
                assert isinstance(f, str)
    
    def test_parsing_matches_worker_logic(self, output_sample):
        """Output can be parsed by actual WorkerManager logic."""
        # Import the actual parsing function from worker.py
        from app.orchestrator.worker import parse_agent_result
        
        # This should not raise
        result = parse_agent_result(output_sample)
        
        # Verify result structure
        assert hasattr(result, "status")
        assert hasattr(result, "summary")
```

---

## Maintaining Contracts

### Quarterly Review

- **Audit fixture freshness** — Are samples from recent agent runs?
- **Check schema coverage** — Do we have samples for all edge cases?
- **Validate parsing code** — Does the parsing logic match schemas?

### On Schema Changes

1. Announce breaking change in team chat
2. Update schema and fixtures
3. Add migration code if needed
4. Run full contract suite
5. Update CHANGELOG.md

---

## Related

- [ADR 0006: Distributed Testing Architecture](../decisions/0006-distributed-testing-architecture.md)
- [Integration Testing Guide](integration-testing.md)
- [Chaos Testing Guide](chaos-testing.md)
