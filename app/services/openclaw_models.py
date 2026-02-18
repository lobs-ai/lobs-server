"""Sync model/auth/billing metadata from OpenClaw Gateway."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp

from app.orchestrator.config import GATEWAY_TOKEN, GATEWAY_URL


@dataclass
class OpenClawModelInfo:
    model: str
    provider: str
    auth_type: str
    billing_type: str
    alias: str | None = None


def _infer_provider(model: str) -> str:
    m = (model or "").lower()
    if "/" in m:
        return m.split("/", 1)[0]
    if "claude" in m or "anthropic" in m:
        return "anthropic"
    if "gemini" in m or "google" in m:
        return "google"
    if "gpt" in m or "openai" in m or "o1" in m or "o3" in m:
        return "openai"
    if "kimi" in m or "moonshot" in m:
        return "moonshotai"
    if "minimax" in m:
        return "minimax"
    return "unknown"


def _infer_auth_billing(model: str) -> tuple[str, str]:
    m = (model or "").lower()
    if "gemini-cli" in m:
        return ("subscription", "subscription")
    return ("api_key", "api")


def _extract_models(obj: Any, out: dict[str, OpenClawModelInfo]) -> None:
    if isinstance(obj, dict):
        model = obj.get("model") or obj.get("id")
        alias = obj.get("alias") or obj.get("name")
        if isinstance(model, str) and ("/" in model or model.startswith(("gpt", "claude", "o1", "o3"))):
            provider = str(obj.get("provider") or _infer_provider(model))
            auth_type = str(obj.get("auth") or obj.get("auth_type") or obj.get("authType") or _infer_auth_billing(model)[0])
            billing_type = str(obj.get("billing") or obj.get("billing_type") or obj.get("billingType") or _infer_auth_billing(model)[1])
            out[model] = OpenClawModelInfo(
                model=model,
                provider=provider,
                auth_type=auth_type,
                billing_type=billing_type,
                alias=alias if isinstance(alias, str) else None,
            )

        for v in obj.values():
            _extract_models(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _extract_models(item, out)


async def fetch_openclaw_model_catalog() -> dict[str, Any]:
    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            f"{GATEWAY_URL}/tools/invoke",
            headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
            json={
                "tool": "gateway",
                "args": {"action": "config.get"},
            },
            timeout=aiohttp.ClientTimeout(total=30),
        )
        data = await resp.json()

    result = data.get("result", {}) if isinstance(data, dict) else {}
    details = result.get("details", result) if isinstance(result, dict) else {}

    models: dict[str, OpenClawModelInfo] = {}
    _extract_models(details, models)

    entries = [
        {
            "model": m.model,
            "provider": m.provider,
            "auth_type": m.auth_type,
            "billing_type": m.billing_type,
            "alias": m.alias,
        }
        for m in sorted(models.values(), key=lambda x: (x.provider, x.model))
    ]

    return {
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "source": "openclaw.gateway.config.get",
        "count": len(entries),
        "models": entries,
        "raw": details,
    }
