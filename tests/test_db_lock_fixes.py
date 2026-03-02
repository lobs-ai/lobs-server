"""Tests for DB lock retry logic in orchestrator modules - syntax and import checks."""

import pytest

# Just verify the modules can be imported and don't have syntax errors
from app.orchestrator.auto_assigner import TaskAutoAssigner, AutoAssignResult
from app.orchestrator.capability_registry import CapabilityRegistrySync
from app.orchestrator.diagnostic_triggers import DiagnosticTriggerEngine
from app.orchestrator.engine import OrchestratorEngine


class TestDBLockFixesSyntax:
    """Verify DB lock fixes have correct syntax and structure."""

    def test_auto_assigner_has_asyncio_import(self):
        """Verify auto_assigner imports asyncio."""
        import inspect
        source = inspect.getsource(TaskAutoAssigner)
        # Verify retry loop is present in the source
        assert "for _attempt in range(5)" in source, "auto_assigner should have retry loop"
        assert "await asyncio.sleep" in source, "auto_assigner should have sleep"

    def test_capability_registry_has_asyncio_import(self):
        """Verify capability_registry imports asyncio."""
        import inspect
        source = inspect.getsource(CapabilityRegistrySync)
        assert "for _attempt in range(5)" in source, "capability_registry should have retry loop"
        assert "await asyncio.sleep" in source, "capability_registry should have sleep"

    def test_diagnostic_triggers_has_retry_logic(self):
        """Verify diagnostic_triggers has retry logic."""
        import inspect
        source = inspect.getsource(DiagnosticTriggerEngine)
        assert "for _attempt in range(5)" in source, "diagnostic_triggers should have retry loop"

    def test_engine_has_retry_logic(self):
        """Verify engine has retry logic for commits."""
        import inspect
        source = inspect.getsource(OrchestratorEngine)
        assert "for _attempt in range(5)" in source, "engine should have retry loop"

    def test_auto_assigner_imports(self):
        """Verify auto_assigner can be imported."""
        assert TaskAutoAssigner is not None
        assert AutoAssignResult is not None

    def test_capability_registry_imports(self):
        """Verify capability_registry can be imported."""
        assert CapabilityRegistrySync is not None

    def test_diagnostic_engine_imports(self):
        """Verify diagnostic_engine can be imported."""
        assert DiagnosticTriggerEngine is not None

    def test_orchestrator_engine_imports(self):
        """Verify orchestrator_engine can be imported."""
        assert OrchestratorEngine is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
