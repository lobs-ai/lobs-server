"""Shared model chooser for orchestrator flows.

Centralizes model selection using:
- task difficulty/criticality (via model_router policy)
- routing policy chain (subscription/API/provider preferences)
- provider budget caps
- provider health tracking (cooldowns, error rates, availability)
- recent provider error rates
- model pricing (when available)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ModelPricing, ModelUsageEvent, OrchestratorSetting
from app.orchestrator.model_router import (
    MODEL_ROUTER_AVAILABLE_MODELS_KEY,
    MODEL_ROUTER_TIER_CHEAP_KEY,
    MODEL_ROUTER_TIER_STANDARD_KEY,
    MODEL_ROUTER_TIER_STRONG_KEY,
    decide_models,
)
from app.orchestrator.runtime_settings import (
    SETTINGS_KEY_MODEL_ROUTER_DEGRADE_ON_QUOTA,
    SETTINGS_KEY_MODEL_ROUTER_STRICT_CODING_TIER,
)
from app.services.usage import infer_provider, resolve_route_type

USAGE_BUDGETS_KEY = "usage.budgets"
USAGE_ROUTING_POLICY_KEY = "usage.routing_policy"
OPENCLAW_MODEL_CATALOG_KEY = "usage.openclaw_model_catalog"


@dataclass(frozen=True, slots=True)
class ModelChoice:
    model: str
    candidates: list[str]
    routing_policy: dict[str, Any]
    strict_coding_tier: bool
    degrade_on_quota: bool
    audit: dict[str, Any]


class ModelChooser:
    """Shared chooser used by worker execution and reflection/diagnostic jobs."""

    def __init__(self, db: AsyncSession, provider_health: Optional[Any] = None):
        self.db = db
        self.provider_health = provider_health  # ProviderHealthRegistry instance

    async def choose(
        self,
        *,
        agent_type: str,
        task: dict[str, Any],
        purpose: str = "execution",
    ) -> ModelChoice:
        cfg = await self._load_runtime_config()
        decision = decide_models(
            agent_type,
            task,
            tier_overrides=cfg.get("tiers"),
            available_models=cfg.get("available_models"),
        )

        candidates = list(decision.models)
        candidates = self._apply_routing_policy(candidates=candidates, task=task, policy=cfg["routing_policy"])
        candidates = await self._apply_budget_guards(candidates=candidates, budgets=cfg["budgets"])
        candidates = await self._rank_by_health_and_cost(
            candidates=candidates,
            routing_policy=cfg["routing_policy"],
            task=task,
            complexity=decision.complexity,
            purpose=purpose,
        )

        if agent_type == "programmer" and cfg["strict_coding_tier"] and candidates:
            candidates = [candidates[0]]

        if not candidates:
            candidates = list(decision.models)

        return ModelChoice(
            model=candidates[0],
            candidates=candidates,
            routing_policy=cfg["routing_policy"],
            strict_coding_tier=cfg["strict_coding_tier"],
            degrade_on_quota=cfg["degrade_on_quota"],
            audit={
                **decision.audit,
                "purpose": purpose,
                "chosen_model": candidates[0],
                "candidates": candidates,
                "strict_coding_tier": cfg["strict_coding_tier"],
                "degrade_on_quota": cfg["degrade_on_quota"],
                "routing_policy_present": bool(cfg["routing_policy"]),
                "budgets_present": bool(cfg["budgets"]),
            },
        )

    async def _load_runtime_config(self) -> dict[str, Any]:
        keys = (
            MODEL_ROUTER_TIER_CHEAP_KEY,
            MODEL_ROUTER_TIER_STANDARD_KEY,
            MODEL_ROUTER_TIER_STRONG_KEY,
            MODEL_ROUTER_AVAILABLE_MODELS_KEY,
            OPENCLAW_MODEL_CATALOG_KEY,
            SETTINGS_KEY_MODEL_ROUTER_STRICT_CODING_TIER,
            SETTINGS_KEY_MODEL_ROUTER_DEGRADE_ON_QUOTA,
            USAGE_BUDGETS_KEY,
            USAGE_ROUTING_POLICY_KEY,
        )
        result = await self.db.execute(
            select(OrchestratorSetting).where(OrchestratorSetting.key.in_(keys))
        )
        rows = {row.key: row.value for row in result.scalars().all()}

        def _list_value(key: str) -> list[str] | None:
            val = rows.get(key)
            if isinstance(val, list):
                return [str(v).strip() for v in val if str(v).strip()]
            return None

        explicit_tiers = {
            "cheap": _list_value(MODEL_ROUTER_TIER_CHEAP_KEY),
            "standard": _list_value(MODEL_ROUTER_TIER_STANDARD_KEY),
            "strong": _list_value(MODEL_ROUTER_TIER_STRONG_KEY),
        }
        explicit_available_models = _list_value(MODEL_ROUTER_AVAILABLE_MODELS_KEY)
        catalog_raw = rows.get(OPENCLAW_MODEL_CATALOG_KEY)
        derived = self._derive_tiers_from_catalog(catalog_raw if isinstance(catalog_raw, dict) else {})
        tiers = {
            "cheap": explicit_tiers["cheap"] or derived["cheap"],
            "standard": explicit_tiers["standard"] or derived["standard"],
            "strong": explicit_tiers["strong"] or derived["strong"],
        }
        available_models = explicit_available_models or derived["available_models"]

        return {
            "tiers": tiers,
            "available_models": available_models,
            "strict_coding_tier": bool(rows.get(SETTINGS_KEY_MODEL_ROUTER_STRICT_CODING_TIER, True)),
            "degrade_on_quota": bool(rows.get(SETTINGS_KEY_MODEL_ROUTER_DEGRADE_ON_QUOTA, False)),
            "routing_policy": rows.get(USAGE_ROUTING_POLICY_KEY) if isinstance(rows.get(USAGE_ROUTING_POLICY_KEY), dict) else {},
            "budgets": rows.get(USAGE_BUDGETS_KEY) if isinstance(rows.get(USAGE_BUDGETS_KEY), dict) else {},
        }

    @staticmethod
    def _derive_tiers_from_catalog(catalog: dict[str, Any]) -> dict[str, list[str] | None]:
        models = catalog.get("models") if isinstance(catalog.get("models"), list) else []
        if not models:
            return {"cheap": None, "standard": None, "strong": None, "available_models": None}

        available: list[str] = []
        cheap: list[str] = []
        standard: list[str] = []
        strong: list[str] = []

        for item in models:
            if not isinstance(item, dict):
                continue
            model = item.get("model")
            if not isinstance(model, str) or not model.strip():
                continue
            name = model.strip()
            available.append(name)
            lower = name.lower()
            billing = str(item.get("billing_type", "")).lower()

            if billing == "subscription":
                cheap.append(name)

            if any(k in lower for k in ("opus", "gpt-5", "o3", "sonnet-4-5", "claude-4-6", "ultra")):
                strong.append(name)
            else:
                standard.append(name)

        # Keep order, remove duplicates.
        def _dedupe(values: list[str]) -> list[str]:
            out: list[str] = []
            seen: set[str] = set()
            for v in values:
                if v in seen:
                    continue
                seen.add(v)
                out.append(v)
            return out

        available = _dedupe(available)
        cheap = _dedupe(cheap)
        standard = _dedupe(standard)
        strong = _dedupe(strong)

        if not standard:
            standard = available[:]
        if not strong:
            strong = standard[:]
        if not cheap:
            cheap = standard[:]

        return {
            "cheap": cheap or None,
            "standard": standard or None,
            "strong": strong or None,
            "available_models": available or None,
        }

    @staticmethod
    def _task_type(task: dict[str, Any]) -> str:
        status = str(task.get("status") or "").lower()
        title = str(task.get("title") or "").lower()
        notes = str(task.get("notes") or "").lower()
        if status == "inbox":
            return "inbox"
        if "summary" in title or "summary" in notes:
            return "quick_summary"
        if "triage" in title or "triage" in notes:
            return "triage"
        return "default"

    def _apply_routing_policy(
        self,
        *,
        candidates: list[str],
        task: dict[str, Any],
        policy: dict[str, Any],
    ) -> list[str]:
        if not candidates or not policy:
            return candidates

        task_type = self._task_type(task)
        chains = policy.get("fallback_chains") if isinstance(policy.get("fallback_chains"), dict) else {}
        chain = chains.get(task_type) or chains.get("default")
        if not isinstance(chain, list) or not chain:
            return candidates

        order = {str(p).lower(): i for i, p in enumerate(chain)}
        subscription_models = policy.get("subscription_models") if isinstance(policy.get("subscription_models"), list) else []
        subscription_providers = policy.get("subscription_providers") if isinstance(policy.get("subscription_providers"), list) else []

        def provider_rank(model_name: str) -> int:
            route_type = resolve_route_type(
                model_name,
                subscription_models=subscription_models,
                subscription_providers=subscription_providers,
            )
            if route_type == "subscription":
                return order.get("subscription", len(order) + 5)
            return order.get(infer_provider(model_name), len(order) + 10)

        return sorted(candidates, key=provider_rank)

    async def _provider_month_cost(self, provider: str, now: datetime) -> float:
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        result = await self.db.execute(
            select(func.coalesce(func.sum(ModelUsageEvent.estimated_cost_usd), 0.0)).where(
                ModelUsageEvent.provider == provider,
                ModelUsageEvent.timestamp >= month_start,
            )
        )
        return float(result.scalar() or 0.0)

    async def _provider_recent_error_rate(self, provider: str, minutes: int = 30) -> float:
        since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        total_q = await self.db.execute(
            select(func.coalesce(func.sum(ModelUsageEvent.requests), 0)).where(
                ModelUsageEvent.provider == provider,
                ModelUsageEvent.timestamp >= since,
            )
        )
        err_q = await self.db.execute(
            select(func.coalesce(func.sum(ModelUsageEvent.requests), 0)).where(
                ModelUsageEvent.provider == provider,
                ModelUsageEvent.timestamp >= since,
                ModelUsageEvent.status != "success",
            )
        )
        total = int(total_q.scalar() or 0)
        errs = int(err_q.scalar() or 0)
        return (errs / total) if total > 0 else 0.0

    async def _apply_budget_guards(self, *, candidates: list[str], budgets: dict[str, Any]) -> list[str]:
        per_provider = budgets.get("per_provider_monthly_usd") if isinstance(budgets.get("per_provider_monthly_usd"), dict) else {}
        if not per_provider:
            return candidates

        now = datetime.now(timezone.utc)
        allowed: list[str] = []
        blocked: list[str] = []
        for model_name in candidates:
            provider = infer_provider(model_name)
            cap = per_provider.get(provider)
            if cap is None:
                allowed.append(model_name)
                continue
            spent = await self._provider_month_cost(provider, now)
            if float(spent) >= float(cap):
                blocked.append(model_name)
            else:
                allowed.append(model_name)

        if allowed:
            return allowed
        return blocked or candidates

    async def _latest_unit_price(
        self,
        *,
        provider: str,
        model: str,
        route_type: str,
    ) -> float:
        if route_type == "subscription":
            return 0.0

        result = await self.db.execute(
            select(ModelPricing)
            .where(
                ModelPricing.provider == provider,
                ModelPricing.model == model,
                ModelPricing.route_type == route_type,
                ModelPricing.active.is_(True),
            )
            .order_by(ModelPricing.effective_date.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return 9999.0
        return float(row.input_per_1m_usd or 0.0) + float(row.output_per_1m_usd or 0.0)

    async def _rank_by_health_and_cost(
        self,
        *,
        candidates: list[str],
        routing_policy: dict[str, Any],
        task: dict[str, Any],
        complexity: str,
        purpose: str,
    ) -> list[str]:
        if not candidates:
            return candidates

        subscription_models = routing_policy.get("subscription_models") if isinstance(routing_policy.get("subscription_models"), list) else []
        subscription_providers = routing_policy.get("subscription_providers") if isinstance(routing_policy.get("subscription_providers"), list) else []
        now = datetime.now(timezone.utc)

        # Filter out unavailable providers/models first (cooldowns, disabled)
        available_candidates: list[str] = []
        unavailable_candidates: list[str] = []
        
        if self.provider_health:
            for model_name in candidates:
                if self.provider_health.is_available(model_name):
                    available_candidates.append(model_name)
                else:
                    unavailable_candidates.append(model_name)
        else:
            available_candidates = list(candidates)
        
        # If all candidates are unavailable, use them anyway as fallback
        # (let worker handle the failure with proper error recording)
        if not available_candidates:
            available_candidates = list(candidates)

        provider_cost_cache: dict[str, float] = {}
        provider_err_cache: dict[str, float] = {}
        provider_health_cache: dict[str, float] = {}

        async def provider_cost(provider: str) -> float:
            if provider not in provider_cost_cache:
                provider_cost_cache[provider] = await self._provider_month_cost(provider, now)
            return provider_cost_cache[provider]

        async def provider_err(provider: str) -> float:
            if provider not in provider_err_cache:
                provider_err_cache[provider] = await self._provider_recent_error_rate(provider, minutes=30)
            return provider_err_cache[provider]

        def model_health_score(model_name: str) -> float:
            """Get health score from provider health registry."""
            if not self.provider_health:
                return 1.0
            
            if model_name not in provider_health_cache:
                # Check model-specific health
                if model_name in self.provider_health.model_health:
                    stats = self.provider_health.model_health[model_name]
                    provider_health_cache[model_name] = stats.get_health_score()
                else:
                    # Fall back to provider-level health
                    provider = infer_provider(model_name)
                    if provider in self.provider_health.provider_health:
                        stats = self.provider_health.provider_health[provider]
                        provider_health_cache[model_name] = stats.get_health_score()
                    else:
                        provider_health_cache[model_name] = 1.0  # Optimistic for new providers
            
            return provider_health_cache[model_name]

        # Priority model:
        # 1) Health score (primary ranking signal - replaces error rate guard)
        # 2) Explicit provider preference (routing_policy.quality_preference)
        # 3) Candidate order from tier routing (user-controlled via tier lists)
        # 4) Cost/spend as tie-breakers only
        quality_pref = routing_policy.get("quality_preference") if isinstance(routing_policy.get("quality_preference"), list) else []
        quality_rank = {str(p).lower(): i for i, p in enumerate(quality_pref)}

        scored: list[tuple[tuple[float, ...], str]] = []
        for idx, model_name in enumerate(available_candidates):
            provider = infer_provider(model_name)
            route_type = resolve_route_type(
                model_name,
                subscription_models=subscription_models,
                subscription_providers=subscription_providers,
            )
            
            # Get health score (higher is better, so invert for sorting)
            health_score = model_health_score(model_name)
            health_penalty = 1.0 - health_score  # Convert to penalty (0.0 = best, 1.0 = worst)
            
            err_rate = await provider_err(provider)
            spend = await provider_cost(provider)
            unit_price = await self._latest_unit_price(provider=provider, model=model_name, route_type=route_type)

            high_error_penalty = 1.0 if err_rate >= 0.5 else 0.0
            # Keep reflection/diagnostic cheaper by default when safe.
            cost_weight = 0.2 if purpose in {"reflection", "diagnostic"} else (0.15 if complexity == "light" else 0.05)
            provider_pref = quality_rank.get(str(provider).lower(), 999)

            score = (
                high_error_penalty,
                round(health_penalty, 4),  # Use health score as primary signal
                round(err_rate, 4),  # Keep err_rate as secondary signal
                float(provider_pref),
                float(idx),
                round(spend, 4),
                round(unit_price * cost_weight, 4),
            )
            scored.append((score, model_name))

        scored.sort(key=lambda x: x[0])
        return [m for _, m in scored]
