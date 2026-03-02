"""Provider health tracking and cooldown management.

Tracks provider/model health based on recent errors and success rates.
Implements automatic cooldown periods per error type to avoid wasting
retries on temporarily unavailable providers.

Error types and cooldown policies:
- rate_limit: 60s backoff, exponential up to 15min
- auth_error: mark disabled until manual intervention or config change
- quota_exceeded: disabled for 24h (simulates billing period)
- timeout: 30s backoff
- server_error: 120s backoff, exponential up to 30min
- unknown: 60s backoff

Health scores (0.0-1.0) are computed from:
- Recent success rate (last 50 attempts)
- Active cooldown penalty
- Error type severity weighting
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrchestratorSetting
from app.services.usage import infer_provider

logger = logging.getLogger(__name__)

ErrorType = Literal[
    "rate_limit",
    "auth_error",
    "quota_exceeded",
    "timeout",
    "server_error",
    "unknown",
]

# Cooldown policies: (initial_seconds, max_seconds, multiplier)
COOLDOWN_POLICIES: dict[ErrorType, tuple[float, float, float]] = {
    "rate_limit": (60.0, 900.0, 2.0),  # 1min -> 15min
    "auth_error": (86400.0, 86400.0, 1.0),  # 24h (manual reset required)
    "quota_exceeded": (86400.0, 86400.0, 1.0),  # 24h
    "timeout": (600.0, 3600.0, 1.5),  # 10min -> 1hr (ensures retries use cloud)
    "server_error": (120.0, 1800.0, 2.0),  # 2min -> 30min
    "unknown": (60.0, 600.0, 1.5),  # 1min -> 10min
}

# History window for health scoring (number of recent events)
HEALTH_WINDOW_SIZE = 50

# Persistence interval (seconds)
PERSISTENCE_INTERVAL = 300.0  # 5 minutes

PROVIDER_HEALTH_CONFIG_KEY = "provider_health.config"


@dataclass
class CooldownState:
    """Cooldown state for a provider/model."""
    error_type: ErrorType
    started_at: float
    duration: float  # current cooldown duration
    consecutive_failures: int = 0

    def is_active(self) -> bool:
        """Check if cooldown is still active."""
        return time.time() < (self.started_at + self.duration)

    def remaining_seconds(self) -> float:
        """Get remaining cooldown time."""
        remaining = (self.started_at + self.duration) - time.time()
        return max(0.0, remaining)


@dataclass
class ProviderHealthStats:
    """Health statistics for a provider."""
    provider: str
    total_attempts: int = 0
    successful_attempts: int = 0
    recent_history: deque = field(default_factory=lambda: deque(maxlen=HEALTH_WINDOW_SIZE))
    cooldowns: dict[ErrorType, CooldownState] = field(default_factory=dict)
    disabled: bool = False
    disabled_reason: str = ""

    def get_success_rate(self) -> float:
        """Calculate success rate from recent history."""
        if not self.recent_history:
            return 1.0  # Optimistic for new providers
        successes = sum(1 for success in self.recent_history if success)
        return successes / len(self.recent_history)

    def get_health_score(self) -> float:
        """
        Calculate health score (0.0-1.0).
        
        Factors:
        - Success rate (70% weight)
        - Active cooldown penalty (30% weight)
        - Disabled = 0.0
        """
        if self.disabled:
            return 0.0

        success_rate = self.get_success_rate()
        
        # Cooldown penalty: max 0.3 deduction based on most severe active cooldown
        cooldown_penalty = 0.0
        for error_type, cooldown in self.cooldowns.items():
            if cooldown.is_active():
                # More severe errors = higher penalty
                severity = {
                    "auth_error": 0.3,
                    "quota_exceeded": 0.3,
                    "rate_limit": 0.25,
                    "server_error": 0.2,
                    "timeout": 0.15,
                    "unknown": 0.1,
                }.get(error_type, 0.1)
                cooldown_penalty = max(cooldown_penalty, severity)

        health = (success_rate * 0.7) + ((1.0 - cooldown_penalty) * 0.3)
        return max(0.0, min(1.0, health))


class ProviderHealthRegistry:
    """
    Global provider health registry.
    
    Tracks health per provider and per model. Provides fast availability
    checks and records outcomes for health scoring.
    
    State is maintained in-memory with periodic DB persistence.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        
        # Provider-level health
        self.provider_health: dict[str, ProviderHealthStats] = {}
        
        # Model-level health (provider/model key)
        self.model_health: dict[str, ProviderHealthStats] = {}
        
        # Last persistence timestamp
        self.last_persist: float = time.time()
        
        # Manual disable list (persisted via config)
        self.disabled_providers: set[str] = set()
        self.disabled_models: set[str] = set()

    async def initialize(self) -> None:
        """Load persisted state from DB."""
        result = await self.db.execute(
            select(OrchestratorSetting).where(
                OrchestratorSetting.key == PROVIDER_HEALTH_CONFIG_KEY
            )
        )
        row = result.scalar_one_or_none()
        
        if row and isinstance(row.value, dict):
            config = row.value
            self.disabled_providers = set(config.get("disabled_providers", []))
            self.disabled_models = set(config.get("disabled_models", []))
            logger.info(
                f"[PROVIDER_HEALTH] Loaded config: "
                f"{len(self.disabled_providers)} disabled providers, "
                f"{len(self.disabled_models)} disabled models"
            )

    def is_available(self, provider_or_model: str) -> bool:
        """
        Quick availability check.
        
        Args:
            provider_or_model: Provider name (e.g. "openai-codex") or 
                             full model spec (e.g. "openai-codex/gpt-5.3-codex")
        
        Returns:
            True if provider/model is currently usable
        """
        # Check if it's a full model spec or just provider
        if "/" in provider_or_model:
            provider = infer_provider(provider_or_model)
            model_key = provider_or_model
            
            # Check model-specific disable/cooldown
            if model_key in self.disabled_models:
                return False
            
            if model_key in self.model_health:
                stats = self.model_health[model_key]
                if stats.disabled:
                    return False
                if self._has_active_cooldown(stats):
                    return False
        else:
            provider = provider_or_model
            model_key = None
        
        # Check provider-level disable/cooldown
        if provider in self.disabled_providers:
            return False
        
        if provider in self.provider_health:
            stats = self.provider_health[provider]
            if stats.disabled:
                return False
            if self._has_active_cooldown(stats):
                return False
        
        return True

    def _has_active_cooldown(self, stats: ProviderHealthStats) -> bool:
        """Check if any cooldown is active."""
        for cooldown in stats.cooldowns.values():
            if cooldown.is_active():
                return True
        return False

    def record_outcome(
        self,
        provider: str,
        model: str,
        success: bool,
        error_type: ErrorType | None = None,
    ) -> None:
        """
        Record an outcome (success or failure).
        
        Args:
            provider: Provider name (e.g. "openai-codex")
            model: Full model spec (e.g. "openai-codex/gpt-5.3-codex")
            success: Whether the attempt succeeded
            error_type: Type of error if not successful
        """
        now = time.time()
        
        # Get or create provider stats
        if provider not in self.provider_health:
            self.provider_health[provider] = ProviderHealthStats(provider=provider)
        provider_stats = self.provider_health[provider]
        
        # Get or create model stats
        if model not in self.model_health:
            self.model_health[model] = ProviderHealthStats(provider=model)
        model_stats = self.model_health[model]
        
        # Update stats
        for stats in [provider_stats, model_stats]:
            stats.total_attempts += 1
            if success:
                stats.successful_attempts += 1
                stats.recent_history.append(True)
            else:
                stats.recent_history.append(False)
        
        # Handle failure with cooldown
        if not success and error_type:
            self._apply_cooldown(provider_stats, error_type, now)
            self._apply_cooldown(model_stats, error_type, now)
            
            # Auto-disable on auth errors or quota exceeded
            if error_type in ("auth_error", "quota_exceeded"):
                provider_stats.disabled = True
                provider_stats.disabled_reason = error_type
                model_stats.disabled = True
                model_stats.disabled_reason = error_type
                logger.warning(
                    f"[PROVIDER_HEALTH] Auto-disabled {model} due to {error_type}"
                )
        
        # Log health update
        if not success:
            logger.info(
                f"[PROVIDER_HEALTH] Recorded failure for {model} "
                f"(error={error_type}, health={model_stats.get_health_score():.2f}, "
                f"cooldown={self._get_max_cooldown_remaining(model_stats):.0f}s)"
            )
        
        # Periodic persistence
        if now - self.last_persist > PERSISTENCE_INTERVAL:
            # Use async task to avoid blocking
            import asyncio
            asyncio.create_task(self._persist_state())

    def _apply_cooldown(
        self,
        stats: ProviderHealthStats,
        error_type: ErrorType,
        now: float,
    ) -> None:
        """Apply or extend cooldown for an error type."""
        policy = COOLDOWN_POLICIES[error_type]
        initial, max_duration, multiplier = policy
        
        if error_type in stats.cooldowns:
            # Extend existing cooldown (exponential backoff)
            cooldown = stats.cooldowns[error_type]
            cooldown.consecutive_failures += 1
            cooldown.duration = min(
                cooldown.duration * multiplier,
                max_duration
            )
            cooldown.started_at = now
            logger.info(
                f"[PROVIDER_HEALTH] Extended {error_type} cooldown for {stats.provider} "
                f"to {cooldown.duration:.0f}s (attempt {cooldown.consecutive_failures})"
            )
        else:
            # New cooldown
            stats.cooldowns[error_type] = CooldownState(
                error_type=error_type,
                started_at=now,
                duration=initial,
                consecutive_failures=1,
            )
            logger.info(
                f"[PROVIDER_HEALTH] Started {error_type} cooldown for {stats.provider} "
                f"({initial:.0f}s)"
            )

    def _get_max_cooldown_remaining(self, stats: ProviderHealthStats) -> float:
        """Get the maximum remaining cooldown time."""
        max_remaining = 0.0
        for cooldown in stats.cooldowns.values():
            if cooldown.is_active():
                max_remaining = max(max_remaining, cooldown.remaining_seconds())
        return max_remaining

    def get_health_report(self) -> dict[str, Any]:
        """
        Get full health report for API exposure.
        
        Returns:
            Dict with provider and model health statistics
        """
        providers = {}
        for provider, stats in self.provider_health.items():
            providers[provider] = {
                "health_score": stats.get_health_score(),
                "success_rate": stats.get_success_rate(),
                "total_attempts": stats.total_attempts,
                "successful_attempts": stats.successful_attempts,
                "disabled": stats.disabled,
                "disabled_reason": stats.disabled_reason,
                "active_cooldowns": {
                    error_type: {
                        "remaining_seconds": cooldown.remaining_seconds(),
                        "consecutive_failures": cooldown.consecutive_failures,
                    }
                    for error_type, cooldown in stats.cooldowns.items()
                    if cooldown.is_active()
                },
            }
        
        models = {}
        for model, stats in self.model_health.items():
            models[model] = {
                "health_score": stats.get_health_score(),
                "success_rate": stats.get_success_rate(),
                "total_attempts": stats.total_attempts,
                "successful_attempts": stats.successful_attempts,
                "disabled": stats.disabled,
                "disabled_reason": stats.disabled_reason,
                "active_cooldowns": {
                    error_type: {
                        "remaining_seconds": cooldown.remaining_seconds(),
                        "consecutive_failures": cooldown.consecutive_failures,
                    }
                    for error_type, cooldown in stats.cooldowns.items()
                    if cooldown.is_active()
                },
            }
        
        return {
            "providers": providers,
            "models": models,
            "disabled_providers": sorted(list(self.disabled_providers)),
            "disabled_models": sorted(list(self.disabled_models)),
        }

    def reset_provider(self, provider: str) -> bool:
        """
        Reset health state for a provider.
        
        Args:
            provider: Provider name
        
        Returns:
            True if reset successful
        """
        if provider in self.provider_health:
            self.provider_health[provider] = ProviderHealthStats(provider=provider)
        
        # Re-enable if manually disabled
        if provider in self.disabled_providers:
            self.disabled_providers.discard(provider)
        
        logger.info(f"[PROVIDER_HEALTH] Reset health for provider: {provider}")
        return True

    def reset_model(self, model: str) -> bool:
        """
        Reset health state for a specific model.
        
        Args:
            model: Full model spec
        
        Returns:
            True if reset successful
        """
        if model in self.model_health:
            self.model_health[model] = ProviderHealthStats(provider=model)
        
        # Re-enable if manually disabled
        if model in self.disabled_models:
            self.disabled_models.discard(model)
        
        logger.info(f"[PROVIDER_HEALTH] Reset health for model: {model}")
        return True

    def toggle_provider(self, provider: str, enabled: bool) -> bool:
        """
        Manually enable/disable a provider.
        
        Args:
            provider: Provider name
            enabled: True to enable, False to disable
        
        Returns:
            True if toggled successfully
        """
        if enabled:
            self.disabled_providers.discard(provider)
            if provider in self.provider_health:
                self.provider_health[provider].disabled = False
                self.provider_health[provider].disabled_reason = ""
            logger.info(f"[PROVIDER_HEALTH] Enabled provider: {provider}")
        else:
            self.disabled_providers.add(provider)
            if provider in self.provider_health:
                self.provider_health[provider].disabled = True
                self.provider_health[provider].disabled_reason = "manual_disable"
            logger.info(f"[PROVIDER_HEALTH] Disabled provider: {provider}")
        
        # Persist immediately
        import asyncio
        asyncio.create_task(self._persist_state())
        
        return True

    async def _persist_state(self) -> None:
        """Persist current state to DB.
        
        Uses its own DB session to avoid corrupting the caller's transaction
        when fired via asyncio.create_task().
        """
        from app.database import AsyncSessionLocal
        try:
            config = {
                "disabled_providers": sorted(list(self.disabled_providers)),
                "disabled_models": sorted(list(self.disabled_models)),
                "persisted_at": datetime.now(timezone.utc).isoformat(),
            }
            
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(OrchestratorSetting).where(
                        OrchestratorSetting.key == PROVIDER_HEALTH_CONFIG_KEY
                    )
                )
                row = result.scalar_one_or_none()
                
                if row:
                    row.value = config
                else:
                    row = OrchestratorSetting(
                        key=PROVIDER_HEALTH_CONFIG_KEY,
                        value=config,
                    )
                    db.add(row)
                
                # Commit with retry-on-lock (exponential backoff: 0.5, 1.0, 1.5, 2.0s)
                for _attempt in range(5):
                    try:
                        if _attempt > 0:
                            await asyncio.sleep(min(_attempt * 0.5, 2.0))
                        await db.commit()
                        break  # Success - exit retry loop
                    except Exception as _e:
                        if _attempt < 4:
                            await db.rollback()
                        else:
                            logger.error(f"[PROVIDER_HEALTH] Failed to persist state after 5 attempts: {_e}")
                            try:
                                await db.rollback()
                            except Exception:
                                pass
                            raise
            self.last_persist = time.time()
            
            logger.debug("[PROVIDER_HEALTH] Persisted state to DB")
        except Exception as e:
            logger.error(f"[PROVIDER_HEALTH] Failed to persist state: {e}")
            try:
                await self.db.rollback()
            except Exception:
                pass
