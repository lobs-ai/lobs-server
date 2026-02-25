"""Runtime setting keys for orchestrator control-plane behavior."""

# Loop intervals
SETTINGS_KEY_REFLECTION_INTERVAL_SECONDS = "orchestrator.interval.reflection_seconds"
SETTINGS_KEY_SWEEP_INTERVAL_SECONDS = "orchestrator.interval.sweep_seconds"
SETTINGS_KEY_DIAGNOSTIC_INTERVAL_SECONDS = "orchestrator.interval.diagnostic_seconds"
SETTINGS_KEY_GITHUB_SYNC_INTERVAL_SECONDS = "orchestrator.interval.github_sync_seconds"
SETTINGS_KEY_OPENCLAW_MODEL_SYNC_INTERVAL_SECONDS = "orchestrator.interval.openclaw_model_sync_seconds"
SETTINGS_KEY_REFLECTION_LAST_RUN_AT = "orchestrator.reflection.last_run_at"
SETTINGS_KEY_DAILY_COMPRESSION_HOUR_UTC = "orchestrator.daily_compression.hour_utc"
# Preferred semantic key (America/New_York local hour). Keep UTC key for backwards compatibility.
SETTINGS_KEY_DAILY_COMPRESSION_HOUR_ET = "orchestrator.daily_compression.hour_et"
# Persisted marker so restarts don't cause repeated daily runs and so status can report last run.
SETTINGS_KEY_DAILY_COMPRESSION_LAST_DATE_ET = "orchestrator.daily_compression.last_date_et"

# Daily Ops Brief — posted to chat at _brief_hour_et (default 8am ET)
SETTINGS_KEY_DAILY_BRIEF_HOUR_ET = "orchestrator.daily_brief.hour_et"
SETTINGS_KEY_DAILY_BRIEF_LAST_DATE_ET = "orchestrator.daily_brief.last_date_et"

# Reactive diagnostic trigger thresholds/policy
SETTINGS_KEY_DIAG_STALL_HOURS = "orchestrator.diagnostics.stalled_task.hours"
SETTINGS_KEY_DIAG_FAILURE_RETRY_COUNT = "orchestrator.diagnostics.repeated_failure.retry_count"
SETTINGS_KEY_DIAG_PR_REJECTION_HOURS = "orchestrator.diagnostics.pr_rejection.hours"
SETTINGS_KEY_DIAG_IDLE_HOURS = "orchestrator.diagnostics.idle_agent.hours"
SETTINGS_KEY_DIAG_PERF_DROP_PERCENT = "orchestrator.diagnostics.performance_drop.percent"
SETTINGS_KEY_DIAG_REPO_DRIFT_COUNT = "orchestrator.diagnostics.repo_drift.count"
SETTINGS_KEY_DIAG_DEBOUNCE_SECONDS = "orchestrator.diagnostics.debounce_seconds"
SETTINGS_KEY_DIAG_AUTO_REMEDIATION = "orchestrator.diagnostics.auto_remediation.enabled"
SETTINGS_KEY_DIAG_REMEDIATION_MAX_TASKS = "orchestrator.diagnostics.auto_remediation.max_tasks"

# Model routing policy
SETTINGS_KEY_MODEL_ROUTER_STRICT_CODING_TIER = "model_router.strict_coding_tier"
SETTINGS_KEY_MODEL_ROUTER_DEGRADE_ON_QUOTA = "model_router.degrade_on_quota"

DEFAULT_RUNTIME_SETTINGS: dict[str, object] = {
    SETTINGS_KEY_REFLECTION_INTERVAL_SECONDS: 10800,
    SETTINGS_KEY_SWEEP_INTERVAL_SECONDS: 900,
    SETTINGS_KEY_DIAGNOSTIC_INTERVAL_SECONDS: 600,
    SETTINGS_KEY_GITHUB_SYNC_INTERVAL_SECONDS: 120,
    SETTINGS_KEY_OPENCLAW_MODEL_SYNC_INTERVAL_SECONDS: 900,
    SETTINGS_KEY_DAILY_COMPRESSION_HOUR_UTC: 3,
    SETTINGS_KEY_DAILY_COMPRESSION_HOUR_ET: 3,
    SETTINGS_KEY_DAILY_COMPRESSION_LAST_DATE_ET: "",
    SETTINGS_KEY_DAILY_BRIEF_HOUR_ET: 8,
    SETTINGS_KEY_DAILY_BRIEF_LAST_DATE_ET: "",
    SETTINGS_KEY_DIAG_STALL_HOURS: 2,
    SETTINGS_KEY_DIAG_FAILURE_RETRY_COUNT: 2,
    SETTINGS_KEY_DIAG_PR_REJECTION_HOURS: 24,
    SETTINGS_KEY_DIAG_IDLE_HOURS: 8,
    SETTINGS_KEY_DIAG_PERF_DROP_PERCENT: 30,
    SETTINGS_KEY_DIAG_REPO_DRIFT_COUNT: 2,
    SETTINGS_KEY_DIAG_DEBOUNCE_SECONDS: 7200,
    SETTINGS_KEY_DIAG_AUTO_REMEDIATION: True,
    SETTINGS_KEY_DIAG_REMEDIATION_MAX_TASKS: 3,
    SETTINGS_KEY_MODEL_ROUTER_STRICT_CODING_TIER: True,
    SETTINGS_KEY_MODEL_ROUTER_DEGRADE_ON_QUOTA: False,
}
