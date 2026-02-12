"""Circuit breaker - prevents cascading failures.

Ported from ~/lobs-orchestrator/orchestrator/core/circuit_breaker.py
Adapted to use SQLAlchemy and async DB operations.

Detects infrastructure failures (gateway auth, session locks, missing API keys,
service unavailable) and pauses task spawning until the issue is resolved.
Prevents wasting retries on tasks that fail due to infrastructure, not task logic.
"""

import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task

logger = logging.getLogger(__name__)

# Patterns that indicate infrastructure failure, not task failure
INFRA_FAILURE_PATTERNS = [
    (re.compile(r"gateway.*token.*mismatch|unauthorized.*gateway", re.IGNORECASE), "gateway_auth"),
    (re.compile(r"session file locked", re.IGNORECASE), "session_lock"),
    (re.compile(r"No API key found for provider", re.IGNORECASE), "missing_api_key"),
    (re.compile(r"connect failed.*unauthorized", re.IGNORECASE), "gateway_auth"),
    (re.compile(r"ECONNREFUSED|ETIMEDOUT|ENOTFOUND", re.IGNORECASE), "service_unavailable"),
    (re.compile(r"All models failed", re.IGNORECASE), "all_models_failed"),
    (re.compile(r"FailoverError", re.IGNORECASE), "failover_exhausted"),
    (re.compile(r"rate.?limit|429|too many requests", re.IGNORECASE), "rate_limited"),
]


class CircuitBreaker:
    """
    Detects infrastructure failures and pauses spawning to avoid wasting retries.
    
    States:
    - CLOSED: Normal operation, tasks can spawn
    - OPEN: Infrastructure failure detected, spawning paused
    - HALF_OPEN: Cooldown elapsed, allowing probe spawn
    
    Tracks state per project and per agent type to isolate failures.
    """

    def __init__(
        self,
        db: AsyncSession,
        threshold: int = 3,
        cooldown_seconds: float = 300.0  # 5 minutes
    ):
        """
        Initialize circuit breaker.
        
        Args:
            db: Database session
            threshold: Number of consecutive infra failures before opening circuit
            cooldown_seconds: Seconds to wait before allowing probe spawn
        """
        self.db = db
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        
        # Circuit state per project
        self.project_circuits: dict[str, dict] = {}
        
        # Circuit state per agent type
        self.agent_circuits: dict[str, dict] = {}
        
        # Global circuit state
        self.global_circuit = {
            "is_open": False,
            "reason": "",
            "opened_at": 0.0,
            "consecutive_failures": 0,
            "last_failure_at": 0.0,
            "last_failure_type": "",
        }

    def classify_failure(self, error_log: str, failure_reason: str = "") -> tuple[bool, str]:
        """
        Classify whether a failure is infrastructure-related.
        
        Args:
            error_log: Error log from worker
            failure_reason: Optional failure reason from task
            
        Returns:
            (is_infra, infra_type) tuple
        """
        text = f"{failure_reason}\n{error_log}"
        for pattern, infra_type in INFRA_FAILURE_PATTERNS:
            if pattern.search(text):
                return True, infra_type
        return False, ""

    async def record_failure(
        self,
        task_id: str,
        project_id: str,
        agent_type: str,
        error_log: str,
        failure_reason: str = ""
    ) -> bool:
        """
        Record a task failure and update circuit state.
        
        Args:
            task_id: Failed task ID
            project_id: Project ID
            agent_type: Agent type that failed
            error_log: Error log from worker
            failure_reason: Optional failure reason
            
        Returns:
            True if classified as infrastructure failure
        """
        is_infra, infra_type = self.classify_failure(error_log, failure_reason)
        
        if not is_infra:
            # Task-level failure — reset consecutive counters
            await self._reset_circuit(project_id, agent_type)
            return False
        
        # Update global circuit
        self.global_circuit["consecutive_failures"] += 1
        self.global_circuit["last_failure_at"] = time.time()
        self.global_circuit["last_failure_type"] = infra_type
        
        # Update project circuit
        if project_id not in self.project_circuits:
            self.project_circuits[project_id] = {
                "is_open": False,
                "consecutive_failures": 0,
                "last_failure_type": "",
                "opened_at": 0.0,
            }
        
        self.project_circuits[project_id]["consecutive_failures"] += 1
        self.project_circuits[project_id]["last_failure_type"] = infra_type
        
        # Update agent circuit
        if agent_type not in self.agent_circuits:
            self.agent_circuits[agent_type] = {
                "is_open": False,
                "consecutive_failures": 0,
                "last_failure_type": "",
                "opened_at": 0.0,
            }
        
        self.agent_circuits[agent_type]["consecutive_failures"] += 1
        self.agent_circuits[agent_type]["last_failure_type"] = infra_type
        
        logger.warning(
            f"[CIRCUIT] Infrastructure failure detected: {infra_type} "
            f"(project={project_id}, agent={agent_type}, "
            f"global={self.global_circuit['consecutive_failures']}/{self.threshold})"
        )
        
        # Check thresholds and open circuits if needed
        if self.global_circuit["consecutive_failures"] >= self.threshold:
            self._open_global_circuit(infra_type)
        
        if self.project_circuits[project_id]["consecutive_failures"] >= self.threshold:
            self._open_project_circuit(project_id, infra_type)
        
        if self.agent_circuits[agent_type]["consecutive_failures"] >= self.threshold:
            self._open_agent_circuit(agent_type, infra_type)
        
        return True

    async def record_success(self, project_id: str, agent_type: str) -> None:
        """
        Record a successful task completion. Resets circuit state.
        
        Args:
            project_id: Project ID
            agent_type: Agent type that succeeded
        """
        await self._reset_circuit(project_id, agent_type)
        
        if self.global_circuit["is_open"]:
            logger.info("[CIRCUIT] Global circuit closing — task succeeded")
            self.global_circuit = {
                "is_open": False,
                "reason": "",
                "opened_at": 0.0,
                "consecutive_failures": 0,
                "last_failure_at": 0.0,
                "last_failure_type": "",
            }

    async def should_allow_spawn(
        self,
        project_id: str,
        agent_type: str
    ) -> tuple[bool, str]:
        """
        Check if spawning is allowed for this project/agent combination.
        
        Args:
            project_id: Project ID to check
            agent_type: Agent type to check
            
        Returns:
            (allowed, reason) tuple
        """
        # Check global circuit
        if self.global_circuit["is_open"]:
            elapsed = time.time() - self.global_circuit["opened_at"]
            if elapsed >= self.cooldown_seconds:
                logger.info(
                    f"[CIRCUIT] Global cooldown elapsed ({elapsed:.0f}s), allowing probe spawn"
                )
                return True, ""
            
            remaining = self.cooldown_seconds - elapsed
            return False, (
                f"Global circuit breaker OPEN: {self.global_circuit['reason']}. "
                f"Paused for {remaining:.0f}s more."
            )
        
        # Check project circuit
        project_circuit = self.project_circuits.get(project_id)
        if project_circuit and project_circuit["is_open"]:
            elapsed = time.time() - project_circuit["opened_at"]
            if elapsed >= self.cooldown_seconds:
                logger.info(
                    f"[CIRCUIT] Project {project_id} cooldown elapsed, allowing probe spawn"
                )
                return True, ""
            
            remaining = self.cooldown_seconds - elapsed
            return False, (
                f"Circuit breaker OPEN for project {project_id}: "
                f"{project_circuit['last_failure_type']}. "
                f"Paused for {remaining:.0f}s more."
            )
        
        # Check agent circuit
        agent_circuit = self.agent_circuits.get(agent_type)
        if agent_circuit and agent_circuit["is_open"]:
            elapsed = time.time() - agent_circuit["opened_at"]
            if elapsed >= self.cooldown_seconds:
                logger.info(
                    f"[CIRCUIT] Agent {agent_type} cooldown elapsed, allowing probe spawn"
                )
                return True, ""
            
            remaining = self.cooldown_seconds - elapsed
            return False, (
                f"Circuit breaker OPEN for {agent_type} agent: "
                f"{agent_circuit['last_failure_type']}. "
                f"Paused for {remaining:.0f}s more."
            )
        
        return True, ""

    def _open_global_circuit(self, infra_type: str) -> None:
        """Open the global circuit breaker — pause all spawning."""
        self.global_circuit["is_open"] = True
        self.global_circuit["opened_at"] = time.time()
        self.global_circuit["reason"] = infra_type
        
        logger.error(
            f"[CIRCUIT] ⚠️ GLOBAL CIRCUIT BREAKER OPEN — {infra_type}. "
            f"Pausing all task spawning for {self.cooldown_seconds}s. "
            f"({self.global_circuit['consecutive_failures']} consecutive infrastructure failures)"
        )

    def _open_project_circuit(self, project_id: str, infra_type: str) -> None:
        """Open circuit for a specific project."""
        self.project_circuits[project_id]["is_open"] = True
        self.project_circuits[project_id]["opened_at"] = time.time()
        
        logger.error(
            f"[CIRCUIT] ⚠️ PROJECT CIRCUIT BREAKER OPEN — {project_id}: {infra_type}. "
            f"Pausing spawning for this project for {self.cooldown_seconds}s."
        )

    def _open_agent_circuit(self, agent_type: str, infra_type: str) -> None:
        """Open circuit for a specific agent type."""
        self.agent_circuits[agent_type]["is_open"] = True
        self.agent_circuits[agent_type]["opened_at"] = time.time()
        
        logger.error(
            f"[CIRCUIT] ⚠️ AGENT CIRCUIT BREAKER OPEN — {agent_type}: {infra_type}. "
            f"Pausing spawning for this agent type for {self.cooldown_seconds}s."
        )

    async def _reset_circuit(self, project_id: str, agent_type: str) -> None:
        """Reset circuit state for project and agent."""
        # Reset project circuit
        if project_id in self.project_circuits:
            was_open = self.project_circuits[project_id]["is_open"]
            self.project_circuits[project_id] = {
                "is_open": False,
                "consecutive_failures": 0,
                "last_failure_type": "",
                "opened_at": 0.0,
            }
            if was_open:
                logger.info(f"[CIRCUIT] Project circuit closed for {project_id}")
        
        # Reset agent circuit
        if agent_type in self.agent_circuits:
            was_open = self.agent_circuits[agent_type]["is_open"]
            self.agent_circuits[agent_type] = {
                "is_open": False,
                "consecutive_failures": 0,
                "last_failure_type": "",
                "opened_at": 0.0,
            }
            if was_open:
                logger.info(f"[CIRCUIT] Agent circuit closed for {agent_type}")

    def get_status(self) -> dict:
        """Get current circuit breaker status."""
        return {
            "global": {
                "is_open": self.global_circuit["is_open"],
                "reason": self.global_circuit["reason"],
                "consecutive_failures": self.global_circuit["consecutive_failures"],
                "last_failure_type": self.global_circuit["last_failure_type"],
                "cooldown_remaining": max(
                    0,
                    self.cooldown_seconds - (time.time() - self.global_circuit["opened_at"])
                ) if self.global_circuit["is_open"] else 0,
            },
            "projects": {
                pid: {
                    "is_open": circuit["is_open"],
                    "consecutive_failures": circuit["consecutive_failures"],
                    "last_failure_type": circuit["last_failure_type"],
                }
                for pid, circuit in self.project_circuits.items()
                if circuit["consecutive_failures"] > 0
            },
            "agents": {
                agent: {
                    "is_open": circuit["is_open"],
                    "consecutive_failures": circuit["consecutive_failures"],
                    "last_failure_type": circuit["last_failure_type"],
                }
                for agent, circuit in self.agent_circuits.items()
                if circuit["consecutive_failures"] > 0
            },
        }

    @property
    def is_open(self) -> bool:
        """Check if any circuit is open."""
        if self.global_circuit["is_open"]:
            return True
        
        for circuit in self.project_circuits.values():
            if circuit["is_open"]:
                return True
        
        for circuit in self.agent_circuits.values():
            if circuit["is_open"]:
                return True
        
        return False
