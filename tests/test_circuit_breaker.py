"""Tests for circuit breaker functionality."""

import pytest
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestrator.circuit_breaker import CircuitBreaker, INFRA_FAILURE_PATTERNS


class TestCircuitBreaker:
    """Test circuit breaker infrastructure failure detection."""
    
    @pytest.mark.asyncio
    async def test_classify_infrastructure_failure(self, db_session: AsyncSession):
        """Test that infrastructure failures are correctly classified."""
        circuit = CircuitBreaker(db_session)
        
        # Test gateway auth failure
        is_infra, infra_type = circuit.classify_failure(
            "Error: gateway token mismatch - unauthorized"
        )
        assert is_infra is True
        assert infra_type == "gateway_auth"
        
        # Test session lock
        is_infra, infra_type = circuit.classify_failure(
            "Error: session file locked by another process"
        )
        assert is_infra is True
        assert infra_type == "session_lock"
        
        # Test missing API key
        is_infra, infra_type = circuit.classify_failure(
            "No API key found for provider anthropic"
        )
        assert is_infra is True
        assert infra_type == "missing_api_key"
        
        # Test rate limiting
        is_infra, infra_type = circuit.classify_failure(
            "Error 429: too many requests, rate limit exceeded"
        )
        assert is_infra is True
        assert infra_type == "rate_limited"
    
    @pytest.mark.asyncio
    async def test_classify_task_failure(self, db_session: AsyncSession):
        """Test that task-level failures are not classified as infrastructure."""
        circuit = CircuitBreaker(db_session)
        
        # Test normal task failure
        is_infra, infra_type = circuit.classify_failure(
            "Error: syntax error in code\nTypeError: cannot read property"
        )
        assert is_infra is False
        assert infra_type == ""
    
    @pytest.mark.asyncio
    async def test_circuit_opens_after_threshold(self, db_session: AsyncSession):
        """Test that circuit opens after threshold failures."""
        circuit = CircuitBreaker(db_session, threshold=3, cooldown_seconds=60)
        
        # Record 3 infrastructure failures
        for i in range(3):
            await circuit.record_failure(
                task_id=f"task_{i}",
                project_id="test-project",
                agent_type="programmer",
                error_log="Error: gateway token mismatch",
                failure_reason=""
            )
        
        # Circuit should be open
        assert circuit.is_open is True
        assert circuit.global_circuit["is_open"] is True
        
        # Should not allow spawning
        allowed, reason = await circuit.should_allow_spawn("test-project", "programmer")
        assert allowed is False
        assert "Circuit breaker OPEN" in reason or "GLOBAL CIRCUIT BREAKER OPEN" in reason
    
    @pytest.mark.asyncio
    async def test_circuit_resets_on_success(self, db_session: AsyncSession):
        """Test that circuit resets after successful task."""
        circuit = CircuitBreaker(db_session, threshold=3)
        
        # Record 2 infrastructure failures
        for i in range(2):
            await circuit.record_failure(
                task_id=f"task_{i}",
                project_id="test-project",
                agent_type="programmer",
                error_log="Error: gateway token mismatch",
                failure_reason=""
            )
        
        # Record success
        await circuit.record_success("test-project", "programmer")
        
        # Circuit should be closed
        assert circuit.is_open is False
        
        # Should allow spawning
        allowed, reason = await circuit.should_allow_spawn("test-project", "programmer")
        assert allowed is True
    
    @pytest.mark.asyncio
    async def test_project_level_circuit(self, db_session: AsyncSession):
        """Test that project-level circuits work independently."""
        # Use high threshold (100) globally, so only project/agent circuits open with 2 failures
        circuit = CircuitBreaker(db_session, threshold=100, cooldown_seconds=60)
        
        # Fail project A 2 times
        for i in range(2):
            await circuit.record_failure(
                task_id=f"task_a_{i}",
                project_id="project-a",
                agent_type="programmer",
                error_log="Error: session file locked",
                failure_reason=""
            )
        
        # Manually open the project-a circuit (since threshold is 100, it won't auto-open)
        circuit.project_circuits["project-a"]["consecutive_failures"] = 100
        circuit._open_project_circuit("project-a", "session_lock")
        
        # Project A should be blocked
        allowed, reason = await circuit.should_allow_spawn("project-a", "programmer")
        assert allowed is False
        
        # Project B should still be allowed (global circuit not open, no project-b circuit)
        allowed, reason = await circuit.should_allow_spawn("project-b", "programmer")
        assert allowed is True
    
    @pytest.mark.asyncio
    async def test_agent_level_circuit(self, db_session: AsyncSession):
        """Test that agent-level circuits work independently."""
        # Use high threshold (100) globally, so only agent circuits open with 2 failures
        circuit = CircuitBreaker(db_session, threshold=100, cooldown_seconds=60)
        
        # Fail programmer agent 2 times
        for i in range(2):
            await circuit.record_failure(
                task_id=f"task_{i}",
                project_id="test-project",
                agent_type="programmer",
                error_log="Error: missing API key",
                failure_reason=""
            )
        
        # Manually open the programmer circuit
        circuit.agent_circuits["programmer"]["consecutive_failures"] = 100
        circuit._open_agent_circuit("programmer", "missing_api_key")
        
        # Programmer should be blocked
        allowed, reason = await circuit.should_allow_spawn("test-project", "programmer")
        assert allowed is False
        
        # Researcher should still be allowed (no researcher circuit, global not open)
        allowed, reason = await circuit.should_allow_spawn("test-project", "researcher")
        assert allowed is True
    
    @pytest.mark.asyncio
    async def test_circuit_status(self, db_session: AsyncSession):
        """Test that circuit status is reported correctly."""
        circuit = CircuitBreaker(db_session, threshold=2)
        
        # Record failure
        await circuit.record_failure(
            task_id="task_1",
            project_id="test-project",
            agent_type="programmer",
            error_log="Error: gateway auth failed",
            failure_reason=""
        )
        
        status = circuit.get_status()
        
        assert "global" in status
        assert status["global"]["consecutive_failures"] == 1
        assert "projects" in status
        assert "programmer" in status["agents"] or len(status["agents"]) == 1
