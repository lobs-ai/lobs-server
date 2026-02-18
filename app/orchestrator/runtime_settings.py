"""Runtime setting keys for orchestrator control-plane behavior."""

# Loop intervals
SETTINGS_KEY_REFLECTION_INTERVAL_SECONDS = "orchestrator.interval.reflection_seconds"
SETTINGS_KEY_SWEEP_INTERVAL_SECONDS = "orchestrator.interval.sweep_seconds"
SETTINGS_KEY_DIAGNOSTIC_INTERVAL_SECONDS = "orchestrator.interval.diagnostic_seconds"
SETTINGS_KEY_GITHUB_SYNC_INTERVAL_SECONDS = "orchestrator.interval.github_sync_seconds"

# Model routing policy
SETTINGS_KEY_MODEL_ROUTER_STRICT_CODING_TIER = "model_router.strict_coding_tier"
SETTINGS_KEY_MODEL_ROUTER_DEGRADE_ON_QUOTA = "model_router.degrade_on_quota"

# Memory stack policy
SETTINGS_KEY_MEMORY_BACKEND = "memory.backend"
SETTINGS_KEY_MEMORY_QMD_CONFIG = "memory.qmd"
SETTINGS_KEY_MEMORY_SEARCH_PATHS = "memory.search.extra_paths"

DEFAULT_RUNTIME_SETTINGS: dict[str, object] = {
    SETTINGS_KEY_REFLECTION_INTERVAL_SECONDS: 21600,
    SETTINGS_KEY_SWEEP_INTERVAL_SECONDS: 900,
    SETTINGS_KEY_DIAGNOSTIC_INTERVAL_SECONDS: 600,
    SETTINGS_KEY_GITHUB_SYNC_INTERVAL_SECONDS: 120,
    SETTINGS_KEY_MODEL_ROUTER_STRICT_CODING_TIER: True,
    SETTINGS_KEY_MODEL_ROUTER_DEGRADE_ON_QUOTA: False,
    SETTINGS_KEY_MEMORY_BACKEND: "sqlite",
    SETTINGS_KEY_MEMORY_QMD_CONFIG: {
        "enabled": False,
        "includeDefaultMemory": True,
        "syncIntervalSeconds": 300,
        "maxResults": 6,
        "snippetMaxChars": 400,
        "paths": [],
        "fallbackBackend": "sqlite",
    },
    SETTINGS_KEY_MEMORY_SEARCH_PATHS: [],
}
