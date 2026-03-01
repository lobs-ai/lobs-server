"""Shared model chooser for orchestrator flows.

Centralizes model selection using:
- task difficulty/criticality (via model_router policy)
- routing policy chain (subscription/API/provider preferences)
- per-provider monthly budget caps (usage.budgets.per_provider_monthly_usd)
- per-lane daily spend caps with auto-downgrade (budget_guard.lane_policy):
    critical / standard / background lanes — enforced by BudgetGuard
- global daily hard cap (usage.budgets.daily_hard_cap_usd):
    last-resort guardrail restricting to micro/small when total daily spend exceeded
- provider health tracking (cooldowns, error rates, availability)
- recent provider error rates
- model pricing (when available)
- local model availability (Ollama health check with TTL cache)

Budget enforcement order in choose():
  1. Per-provider monthly caps (_apply_budget_guards)
  2. Per-lane daily caps (_apply_lane_budget_guard via BudgetGuard)
  3. Global daily hard cap (_apply_global_daily_cap via apply_daily_hard_cap)
  4. Health + cost ranking (_rank_by_health_and_cost)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import aiohttp
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# --- Ollama auto-detection cache ---
# Discovers running Ollama models and classifies them into tiers.
# Cached for 60s. Returns empty dict when Ollama is unreachable.

import os
import re

_ollama_models_cache: dict[str, list[str]] | None = None
_ollama_cache_time: float = 0.0
_OLLAMA_CACHE_TTL = 60.0
_OLLAMA_BASE_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# --- LM Studio auto-detection cache ---
_lmstudio_models_cache: dict[str, list[str]] | None = None
_lmstudio_cache_time: float = 0.0
_LMSTUDIO_CACHE_TTL = 60.0
_LMSTUDIO_BASE_URL = os.environ.get("LMSTUDIO_HOST", "http://localhost:1234")

# ── Project-level model policy ────────────────────────────────────────────
# Controls whether local (LM Studio/Ollama) models are allowed per project.
# Format: {"<project_id>": {"local": "never"|"preferred"|"allowed"}}
# "never"     — strip all local models; use cloud only
# "preferred" — local models stay at front (default behavior)
# "allowed"   — local models allowed but ranked normally (not pinned to front)
#
# Set via orchestrator_settings key: model_router.project_policy
# Can be overridden per-task via task.model_tier = "standard" or higher.
PROJECT_LOCAL_POLICY_SETTINGS_KEY = "model_router.project_policy"

# Built-in defaults (can be overridden via DB settings)
_DEFAULT_PROJECT_POLICY: dict[str, str] = {
    # Core infra — always use cloud models for reliability
    "lobs-server": "never",
    "lobs-mission-control": "never",
    "lobs-mobile": "never",
    "lobs-sail": "never",
    # App projects — allow local but not pinned first (cloud preferred for iOS code quality)
    "grandmas-stories": "allowed",
    "flock-master": "preferred",
}

# Name-based parameter size hints for LM Studio models (no metadata from API).
# Map substring → estimated billions of parameters.
_LMSTUDIO_PARAM_HINTS: dict[str, float] = {
    "0.5b": 0.5, "1b": 1, "1.5b": 1.5, "3b": 3, "4b": 4, "7b": 7, "8b": 8,
    "13b": 13, "14b": 14, "22b": 22, "30b": 30, "32b": 32, "35b": 35,
    "70b": 70, "72b": 72, "110b": 110, "405b": 405,
    # MoE active param hints — classify by effective quality, not raw active count.
    # e.g. Qwen 35B-A3B performs like a ~20B dense model, not a 3B.
    "a3b": 20, "a14b": 30, "a22b": 35, "a35b": 40,
}

# Parameter size → tier mapping.
# Models are inserted at the FRONT of their tier (preferred because free).
_PARAM_TIER_THRESHOLDS: list[tuple[float, str]] = [
    # (max_billions, tier)
    # Local models NEVER go into standard or strong — those are cloud-quality.
    (15.0, "micro"),      # ≤15B → micro (Phi, Qwen 7B, routing/classification)
    (40.0, "small"),      # ≤40B → small (Qwen 30B, Mistral 22B, summaries/drafts)
    (float("inf"), "medium"),  # >40B → medium (Llama 70B+, alongside cheap cloud)
]

# Override specific model families to tiers regardless of param count.
_MODEL_TIER_OVERRIDES: dict[str, str] = {
    # Add overrides here as needed, e.g.:
    # "deepseek-coder-v2": "standard",
}


def _classify_ollama_model(name: str, param_size: str | None, size_bytes: int | None) -> str:
    """Classify an Ollama model into cheap/standard/strong based on parameter count."""
    # Check family overrides first
    base_name = name.split(":")[0].lower()
    if base_name in _MODEL_TIER_OVERRIDES:
        return _MODEL_TIER_OVERRIDES[base_name]
    
    # Parse parameter size (e.g., "30B", "7.6B", "70B")
    billions = _parse_param_billions(param_size)
    if billions is None and size_bytes:
        # Rough estimate: ~0.5 bytes per parameter for Q4 quantization
        billions = size_bytes / (0.5 * 1e9)
    
    if billions is not None:
        for threshold, tier in _PARAM_TIER_THRESHOLDS:
            if billions <= threshold:
                return tier
    
    # Default: cheap (conservative — don't waste API money when unsure)
    return "cheap"


def _parse_param_billions(param_size: str | None) -> float | None:
    """Parse '30B', '7.6B', '405B' etc. into float billions."""
    if not param_size:
        return None
    match = re.match(r"([\d.]+)\s*[bB]", param_size.strip())
    if match:
        return float(match.group(1))
    return None


async def discover_ollama_models() -> dict[str, list[str]]:
    """Discover Ollama models and classify into tiers.
    
    Returns dict like {"cheap": ["ollama/qwen3:30b"], "standard": ["ollama/llama3:70b"]}.
    Returns empty dict when Ollama is unreachable.
    Cached for 60s.
    """
    global _ollama_models_cache, _ollama_cache_time
    
    now = time.monotonic()
    if _ollama_models_cache is not None and (now - _ollama_cache_time) < _OLLAMA_CACHE_TTL:
        return _ollama_models_cache
    
    result: dict[str, list[str]] = {}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_OLLAMA_BASE_URL}/api/tags",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                if resp.status != 200:
                    _ollama_models_cache = {}
                    _ollama_cache_time = now
                    return {}
                
                data = await resp.json()
    except Exception:
        logger.debug("[MODEL_CHOOSER] Ollama not reachable — no local models")
        _ollama_models_cache = {}
        _ollama_cache_time = now
        return {}
    
    models = data.get("models", [])
    for m in models:
        name = m.get("name", "")
        if not name:
            continue
        
        details = m.get("details", {})
        param_size = details.get("parameter_size")
        size_bytes = m.get("size")
        
        tier = _classify_ollama_model(name, param_size, size_bytes)
        ollama_model_id = f"ollama/{name}"
        
        result.setdefault(tier, []).append(ollama_model_id)
    
    if result:
        logger.info(
            "[MODEL_CHOOSER] Discovered Ollama models: %s",
            {k: v for k, v in result.items()},
        )
    
    _ollama_models_cache = result
    _ollama_cache_time = now
    return result


def _estimate_lmstudio_param_billions(model_id: str) -> float | None:
    """Estimate parameter count from model ID string."""
    lower = model_id.lower().replace("-", "").replace("_", "")
    # Check MoE active-param patterns first (aXb), then total params by length
    moe_hints = {k: v for k, v in _LMSTUDIO_PARAM_HINTS.items() if k.startswith("a")}
    other_hints = {k: v for k, v in _LMSTUDIO_PARAM_HINTS.items() if not k.startswith("a")}
    for hint, billions in sorted(moe_hints.items(), key=lambda x: -len(x[0])):
        if hint in lower:
            return billions
    for hint, billions in sorted(other_hints.items(), key=lambda x: -len(x[0])):
        if hint in lower:
            return billions
    return None


async def discover_lmstudio_models() -> dict[str, list[str]]:
    """Discover LM Studio models and classify into tiers.

    Returns dict like {"small": ["lmstudio/qwen3.5-35b-a3b"]}.
    Returns empty dict when LM Studio is unreachable.
    Cached for 60s.
    """
    global _lmstudio_models_cache, _lmstudio_cache_time

    now = time.monotonic()
    if _lmstudio_models_cache is not None and (now - _lmstudio_cache_time) < _LMSTUDIO_CACHE_TTL:
        return _lmstudio_models_cache

    result: dict[str, list[str]] = {}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_LMSTUDIO_BASE_URL}/v1/models",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                if resp.status != 200:
                    _lmstudio_models_cache = {}
                    _lmstudio_cache_time = now
                    return {}

                data = await resp.json()
    except Exception:
        logger.debug("[MODEL_CHOOSER] LM Studio not reachable — no local models")
        _lmstudio_models_cache = {}
        _lmstudio_cache_time = now
        return {}

    models = data.get("data", [])
    for m in models:
        model_id = m.get("id", "")
        if not model_id:
            continue
        # Skip embedding models
        if "embed" in model_id.lower():
            continue

        billions = _estimate_lmstudio_param_billions(model_id)
        if billions is not None:
            for threshold, tier in _PARAM_TIER_THRESHOLDS:
                if billions <= threshold:
                    break
        else:
            tier = "small"  # Conservative default

        lmstudio_model_id = f"lmstudio/{model_id}"
        result.setdefault(tier, []).append(lmstudio_model_id)

    if result:
        logger.info(
            "[MODEL_CHOOSER] Discovered LM Studio models: %s",
            {k: v for k, v in result.items()},
        )

    _lmstudio_models_cache = result
    _lmstudio_cache_time = now
    return result

from app.models import ModelPricing, ModelUsageEvent, OrchestratorSetting
from app.orchestrator.budget_guard import BudgetGuard
from app.orchestrator.budget_guardrails import (
    apply_daily_hard_cap,
    get_today_total_spend,
    append_override_log,
)
from app.orchestrator.model_router import (
    MODEL_ROUTER_AVAILABLE_MODELS_KEY,
    MODEL_ROUTER_TIER_MICRO_KEY,
    MODEL_ROUTER_TIER_SMALL_KEY,
    MODEL_ROUTER_TIER_MEDIUM_KEY,
    MODEL_ROUTER_TIER_STANDARD_KEY,
    MODEL_ROUTER_TIER_STRONG_KEY,
    TIER_ORDER,
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
    # budget_lane: classified lane for this task (critical|standard|background).
    # Set at model-selection time; used to tag ModelUsageEvents for accurate
    # per-lane spend tracking.
    budget_lane: str = "standard"


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
        tiers = dict(cfg.get("tiers") or {})
        
        # Auto-discover Ollama models and inject into tiers.
        # Local models are prepended (preferred because free/fast).
        # Auto-discover local models (Ollama + LM Studio) and prepend (free → preferred)
        for discover_fn in (discover_ollama_models, discover_lmstudio_models):
            local_tiers = await discover_fn()
            if local_tiers:
                for tier_name, local_models in local_tiers.items():
                    existing = list(tiers.get(tier_name) or [])
                    tiers[tier_name] = local_models + [m for m in existing if m not in local_models]
        
        decision = decide_models(
            agent_type,
            task,
            tier_overrides=tiers,
            available_models=cfg.get("available_models"),
            purpose=purpose,
        )

        candidates = list(decision.models)
        candidates = self._apply_routing_policy(candidates=candidates, task=task, policy=cfg["routing_policy"])
        candidates = await self._apply_budget_guards(candidates=candidates, budgets=cfg["budgets"])

        # --- Lane-based daily spend cap (budget guardrail) ---
        tier_map: dict[str, list[str]] = {
            tier: list(models or [])
            for tier, models in (decision.audit.get("tier_models") or {}).items()
        }
        lane_guard_decision = await self._apply_lane_budget_guard(
            candidates=candidates,
            task=task,
            agent_type=agent_type,
            criticality=str(decision.criticality),
            tier_map=tier_map,
        )
        candidates = lane_guard_decision["effective_candidates"]

        # --- Global daily hard cap (last-resort guardrail across all lanes) ---
        hard_cap_decision = await self._apply_global_daily_cap(
            candidates=candidates,
            budgets=cfg["budgets"],
            tier_map=tier_map,
            task=task,
            agent_type=agent_type,
        )
        candidates = hard_cap_decision["effective_candidates"]

        candidates = await self._rank_by_health_and_cost(
            candidates=candidates,
            routing_policy=cfg["routing_policy"],
            task=task,
            complexity=decision.complexity,
            purpose=purpose,
        )

        # Apply project-level local model policy
        project_id = (task or {}).get("project_id", "")
        local_policy = await self._get_project_local_policy(project_id, cfg)
        local_prefixes = ("lmstudio/", "ollama/")
        local_models = [m for m in candidates if m.startswith(local_prefixes)]
        cloud_models = [m for m in candidates if not m.startswith(local_prefixes)]

        if local_policy == "never":
            # Strip all local models — cloud only
            candidates = cloud_models
        elif local_policy == "preferred":
            # Pin local to front (original behavior)
            if local_models:
                candidates = local_models + cloud_models
        else:  # "allowed" — don't pin, let health ranking decide
            pass  # candidates already ranked by health

        if agent_type == "programmer" and cfg["strict_coding_tier"] and candidates:
            candidates = [candidates[0]]

        if not candidates:
            candidates = list(decision.models)

        detected_lane: str = lane_guard_decision.get("lane", "standard") or "standard"

        return ModelChoice(
            model=candidates[0],
            candidates=candidates,
            routing_policy=cfg["routing_policy"],
            strict_coding_tier=cfg["strict_coding_tier"],
            degrade_on_quota=cfg["degrade_on_quota"],
            budget_lane=detected_lane,
            audit={
                **decision.audit,
                "purpose": purpose,
                "chosen_model": candidates[0],
                "candidates": candidates,
                "strict_coding_tier": cfg["strict_coding_tier"],
                "degrade_on_quota": cfg["degrade_on_quota"],
                "routing_policy_present": bool(cfg["routing_policy"]),
                "budgets_present": bool(cfg["budgets"]),
                "lane_guard": lane_guard_decision,
                "hard_cap_guard": hard_cap_decision,
            },
        )

    async def _load_runtime_config(self) -> dict[str, Any]:
        keys = (
            MODEL_ROUTER_TIER_MICRO_KEY,
            MODEL_ROUTER_TIER_SMALL_KEY,
            MODEL_ROUTER_TIER_MEDIUM_KEY,
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
            "micro": _list_value(MODEL_ROUTER_TIER_MICRO_KEY),
            "small": _list_value(MODEL_ROUTER_TIER_SMALL_KEY),
            "medium": _list_value(MODEL_ROUTER_TIER_MEDIUM_KEY),
            "standard": _list_value(MODEL_ROUTER_TIER_STANDARD_KEY),
            "strong": _list_value(MODEL_ROUTER_TIER_STRONG_KEY),
        }
        explicit_available_models = _list_value(MODEL_ROUTER_AVAILABLE_MODELS_KEY)
        catalog_raw = rows.get(OPENCLAW_MODEL_CATALOG_KEY)
        derived = self._derive_tiers_from_catalog(catalog_raw if isinstance(catalog_raw, dict) else {})
        tiers = {
            tier: explicit_tiers[tier] or derived.get(tier)
            for tier in TIER_ORDER
        }
        available_models = explicit_available_models or derived["available_models"]

        # Load project-level model policy from DB (runtime override of defaults)
        project_policy: dict[str, str] = {}
        try:
            pp_result = await self.db.execute(
                select(OrchestratorSetting).where(
                    OrchestratorSetting.key == PROJECT_LOCAL_POLICY_SETTINGS_KEY
                )
            )
            pp_row = pp_result.scalar_one_or_none()
            if pp_row and isinstance(pp_row.value, dict):
                project_policy = pp_row.value
            elif pp_row and isinstance(pp_row.value, str):
                import json as _json
                project_policy = _json.loads(pp_row.value)
        except Exception:
            pass

        return {
            "tiers": tiers,
            "available_models": available_models,
            "strict_coding_tier": bool(rows.get(SETTINGS_KEY_MODEL_ROUTER_STRICT_CODING_TIER, True)),
            "degrade_on_quota": bool(rows.get(SETTINGS_KEY_MODEL_ROUTER_DEGRADE_ON_QUOTA, False)),
            "routing_policy": rows.get(USAGE_ROUTING_POLICY_KEY) if isinstance(rows.get(USAGE_ROUTING_POLICY_KEY), dict) else {},
            "budgets": rows.get(USAGE_BUDGETS_KEY) if isinstance(rows.get(USAGE_BUDGETS_KEY), dict) else {},
            "project_policy": project_policy,
        }

    @staticmethod
    def _derive_tiers_from_catalog(catalog: dict[str, Any]) -> dict[str, list[str] | None]:
        models = catalog.get("models") if isinstance(catalog.get("models"), list) else []
        if not models:
            return {tier: None for tier in TIER_ORDER} | {"available_models": None}

        available: list[str] = []
        medium: list[str] = []
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
                medium.append(name)

            if any(k in lower for k in ("opus", "gpt-5", "o3", "claude-4-6", "ultra")):
                strong.append(name)
            elif any(k in lower for k in ("sonnet", "codex", "gpt-4")):
                standard.append(name)
            else:
                medium.append(name)

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
        medium = _dedupe(medium)
        standard = _dedupe(standard)
        strong = _dedupe(strong)

        if not standard:
            standard = available[:]
        if not strong:
            strong = standard[:]
        if not medium:
            medium = standard[:]

        return {
            "micro": None,   # Only populated by Ollama auto-discovery
            "small": None,   # Only populated by Ollama auto-discovery
            "medium": medium or None,
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

    async def _get_project_local_policy(self, project_id: str, cfg: dict) -> str:
        """Return local model policy: 'never' | 'preferred' | 'allowed'."""
        raw = cfg.get("project_policy") or {}
        if project_id and project_id in raw:
            return raw[project_id]
        return _DEFAULT_PROJECT_POLICY.get(project_id or "", "allowed")

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

    async def _apply_lane_budget_guard(
        self,
        *,
        candidates: list[str],
        task: dict[str, Any],
        agent_type: str,
        criticality: str,
        tier_map: dict[str, list[str]],
    ) -> dict[str, Any]:
        """Apply daily lane-based spend cap via BudgetGuard.

        Returns a dict with ``effective_candidates`` and audit metadata.
        Never raises — failures fall back to original candidates.
        """
        try:
            guard = BudgetGuard(self.db)
            decision = await guard.apply(
                task=task,
                agent_type=agent_type,
                criticality=criticality,
                candidates=candidates,
                tier_map=tier_map,
            )
            return {
                "effective_candidates": decision.effective_candidates,
                "lane": decision.lane,
                "over_budget": decision.over_budget,
                "downgraded": decision.downgraded,
                "reason": decision.reason,
                "cap_usd": decision.cap_usd,
                "spent_usd": decision.spent_usd,
            }
        except Exception as exc:
            logger.warning("[MODEL_CHOOSER] BudgetGuard failed (non-fatal): %s", exc)
            return {
                "effective_candidates": candidates,
                "lane": "unknown",
                "over_budget": False,
                "downgraded": False,
                "reason": f"guard_error: {exc}",
                "cap_usd": None,
                "spent_usd": 0.0,
            }

    async def _apply_global_daily_cap(
        self,
        *,
        candidates: list[str],
        budgets: dict[str, Any],
        tier_map: dict[str, list[str]],
        task: dict[str, Any],
        agent_type: str,
    ) -> dict[str, Any]:
        """Enforce the global daily hard cap across all lanes.

        This is a last-resort guardrail: when the total daily spend across *all*
        lanes reaches ``daily_hard_cap_usd`` (from usage.budgets), all candidates
        are restricted to micro/small tier models.  Per-lane caps (handled by
        ``_apply_lane_budget_guard``) run first; this cap runs second.

        Returns a dict with ``effective_candidates`` and audit metadata.
        Never raises — failures fall back to original candidates.
        """
        try:
            daily_hard_cap = float(budgets.get("daily_hard_cap_usd") or 0.0)
            if daily_hard_cap <= 0.0:
                # Hard cap not configured — pass through
                return {
                    "effective_candidates": candidates,
                    "hard_cap_usd": None,
                    "daily_spend_usd": 0.0,
                    "hard_cap_exceeded": False,
                    "downgraded": False,
                    "reason": "no_hard_cap",
                }

            daily_spend = await get_today_total_spend(self.db)
            filtered, reason = apply_daily_hard_cap(
                candidates,
                daily_spend=daily_spend,
                daily_hard_cap=daily_hard_cap,
                tier_map=tier_map,
            )
            downgraded = reason is not None and filtered != candidates

            if downgraded:
                logger.warning(
                    "[MODEL_CHOOSER] Global daily hard cap exceeded: "
                    "spend=%.4f cap=%.2f task=%s agent=%s",
                    daily_spend,
                    daily_hard_cap,
                    task.get("id"),
                    agent_type,
                )
                # Append to guardrails override log for audit trail
                try:
                    await append_override_log(
                        self.db,
                        lane=None,  # global cap, not lane-specific
                        reason=reason or "daily_hard_cap_exceeded",
                        original_model=candidates[0] if candidates else None,
                        downgraded_model=filtered[0] if filtered else None,
                        task_id=task.get("id"),
                        agent_type=agent_type,
                    )
                except Exception as log_exc:
                    logger.debug(
                        "[MODEL_CHOOSER] Failed to append hard-cap override log: %s", log_exc
                    )

            return {
                "effective_candidates": filtered,
                "hard_cap_usd": daily_hard_cap,
                "daily_spend_usd": round(daily_spend, 4),
                "hard_cap_exceeded": daily_spend >= daily_hard_cap,
                "downgraded": downgraded,
                "reason": reason or "within_hard_cap",
            }
        except Exception as exc:
            logger.warning("[MODEL_CHOOSER] Global daily hard cap check failed (non-fatal): %s", exc)
            return {
                "effective_candidates": candidates,
                "hard_cap_usd": None,
                "daily_spend_usd": 0.0,
                "hard_cap_exceeded": False,
                "downgraded": False,
                "reason": f"hard_cap_guard_error: {exc}",
            }

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
