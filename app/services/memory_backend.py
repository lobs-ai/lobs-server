"""Memory backend resolution utilities (sqlite/qmd with fallback)."""

from __future__ import annotations

from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrchestratorSetting
from app.orchestrator.runtime_settings import (
    DEFAULT_RUNTIME_SETTINGS,
    SETTINGS_KEY_MEMORY_BACKEND,
    SETTINGS_KEY_MEMORY_QMD_CONFIG,
    SETTINGS_KEY_MEMORY_SEARCH_PATHS,
)


async def get_memory_runtime_config(db: AsyncSession) -> dict[str, Any]:
    backend_row = await db.get(OrchestratorSetting, SETTINGS_KEY_MEMORY_BACKEND)
    qmd_row = await db.get(OrchestratorSetting, SETTINGS_KEY_MEMORY_QMD_CONFIG)
    paths_row = await db.get(OrchestratorSetting, SETTINGS_KEY_MEMORY_SEARCH_PATHS)

    backend = str((backend_row.value if backend_row else DEFAULT_RUNTIME_SETTINGS[SETTINGS_KEY_MEMORY_BACKEND])).lower()
    qmd = dict(DEFAULT_RUNTIME_SETTINGS[SETTINGS_KEY_MEMORY_QMD_CONFIG])
    if isinstance(qmd_row.value if qmd_row else None, dict):
        qmd.update(qmd_row.value)
    extra_paths = paths_row.value if paths_row and isinstance(paths_row.value, list) else list(DEFAULT_RUNTIME_SETTINGS[SETTINGS_KEY_MEMORY_SEARCH_PATHS])

    if backend not in {"sqlite", "qmd"}:
        backend = "sqlite"

    resolved = backend
    fallback_reason = None
    if backend == "qmd" and not qmd.get("enabled", False):
        resolved = str(qmd.get("fallbackBackend") or "sqlite")
        fallback_reason = "qmd_disabled"

    return {
        "backend": backend,
        "resolved_backend": resolved,
        "fallback_reason": fallback_reason,
        "qmd": qmd,
        "extra_paths": [str(p) for p in extra_paths if str(p).strip()],
    }
