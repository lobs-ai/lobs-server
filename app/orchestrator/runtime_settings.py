"""Runtime setting keys for orchestrator control-plane behavior."""

# Loop intervals
SETTINGS_KEY_REFLECTION_INTERVAL_SECONDS = "orchestrator.interval.reflection_seconds"
SETTINGS_KEY_SWEEP_INTERVAL_SECONDS = "orchestrator.interval.sweep_seconds"
SETTINGS_KEY_DIAGNOSTIC_INTERVAL_SECONDS = "orchestrator.interval.diagnostic_seconds"
SETTINGS_KEY_GITHUB_SYNC_INTERVAL_SECONDS = "orchestrator.interval.github_sync_seconds"
SETTINGS_KEY_OPENCLAW_MODEL_SYNC_INTERVAL_SECONDS = "orchestrator.interval.openclaw_model_sync_seconds"
SETTINGS_KEY_REFLECTION_LAST_RUN_AT = "orchestrator.reflection.last_run_at"

# Model routing policy
SETTINGS_KEY_MODEL_ROUTER_STRICT_CODING_TIER = "model_router.strict_coding_tier"
SETTINGS_KEY_MODEL_ROUTER_DEGRADE_ON_QUOTA = "model_router.degrade_on_quota"

DEFAULT_RUNTIME_SETTINGS: dict[str, object] = {
    SETTINGS_KEY_REFLECTION_INTERVAL_SECONDS: 21600,
    SETTINGS_KEY_SWEEP_INTERVAL_SECONDS: 900,
    SETTINGS_KEY_DIAGNOSTIC_INTERVAL_SECONDS: 600,
    SETTINGS_KEY_GITHUB_SYNC_INTERVAL_SECONDS: 120,
    SETTINGS_KEY_OPENCLAW_MODEL_SYNC_INTERVAL_SECONDS: 900,
    SETTINGS_KEY_MODEL_ROUTER_STRICT_CODING_TIER: True,
    SETTINGS_KEY_MODEL_ROUTER_DEGRADE_ON_QUOTA: False,
}
