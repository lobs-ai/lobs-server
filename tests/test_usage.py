from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_usage_event_ingest_and_summary(client):
    price_payload = {
        "provider": "openai",
        "model": "openai/gpt-4.1-mini",
        "route_type": "api",
        "input_per_1m_usd": 0.2,
        "output_per_1m_usd": 0.8,
        "cached_input_per_1m_usd": 0.1,
    }
    price_res = await client.post("/api/usage/pricing", json=price_payload)
    assert price_res.status_code == 200

    event_payload = {
        "source": "test",
        "model": "openai/gpt-4.1-mini",
        "route_type": "api",
        "task_type": "inbox",
        "input_tokens": 1000,
        "output_tokens": 500,
        "cached_tokens": 0,
        "requests": 1,
        "status": "success",
    }
    create_res = await client.post("/api/usage/events", json=event_payload)
    assert create_res.status_code == 200
    event = create_res.json()
    assert event["provider"] == "openai"
    assert event["estimated_cost_usd"] > 0

    summary_res = await client.get("/api/usage/summary", params={"window": "month"})
    assert summary_res.status_code == 200
    summary = summary_res.json()
    assert summary["total_requests"] >= 1
    assert summary["total_input_tokens"] >= 1000
    assert summary["total_output_tokens"] >= 500
    assert len(summary["by_provider"]) >= 1


@pytest.mark.asyncio
async def test_subscription_usage_cost_defaults_to_zero(client):
    event_payload = {
        "source": "test",
        "provider": "gemini",
        "model": "google-gemini-cli/gemini-3-pro-preview",
        "route_type": "subscription",
        "task_type": "inbox",
        "input_tokens": 2000,
        "output_tokens": 1200,
        "requests": 1,
        "status": "success",
    }
    create_res = await client.post("/api/usage/events", json=event_payload)
    assert create_res.status_code == 200
    event = create_res.json()
    assert event["estimated_cost_usd"] == 0


@pytest.mark.asyncio
async def test_budgets_and_routing_policy_roundtrip(client):
    budgets_res = await client.get("/api/usage/budgets")
    assert budgets_res.status_code == 200
    budgets = budgets_res.json()
    assert "monthly_total_usd" in budgets

    new_budgets = {
        "monthly_total_usd": 300.0,
        "daily_alert_usd": 20.0,
        "per_provider_monthly_usd": {"openai": 100.0, "claude": 100.0, "kimi": 50.0, "minimax": 50.0},
        "per_task_hard_cap_usd": 4.0,
    }
    patch_budgets = await client.patch("/api/usage/budgets", json=new_budgets)
    assert patch_budgets.status_code == 200
    assert patch_budgets.json()["monthly_total_usd"] == 300.0

    policy_res = await client.get("/api/routing/policy")
    assert policy_res.status_code == 200
    policy = policy_res.json()
    assert "subscription_first_task_types" in policy

    new_policy = {
        "subscription_first_task_types": ["inbox", "quick_summary"],
        "subscription_providers": ["gemini", "mistral"],
        "subscription_models": ["google-gemini-cli/gemini-3-pro-preview"],
        "fallback_chains": {"inbox": ["subscription", "openai"]},
        "quality_preference": ["claude", "openai", "kimi", "minimax"],
    }
    patch_policy = await client.patch("/api/routing/policy", json=new_policy)
    assert patch_policy.status_code == 200
    assert patch_policy.json()["fallback_chains"]["inbox"] == ["subscription", "openai"]
