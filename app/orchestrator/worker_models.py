"""Worker data models and utility functions.

Shared data structures and helpers used by worker management components.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.usage import log_usage_event

logger = logging.getLogger(__name__)


@dataclass
class WorkerInfo:
    """Information about an active worker spawned via Gateway API."""
    run_id: str
    child_session_key: str
    task_id: str
    project_id: str
    agent_type: str
    model: str
    start_time: float
    label: str
    model_audit: dict[str, Any] | None = None
    transcript_path: str | None = None


def classify_error_type(error_message: str, response_data: dict | None = None) -> str:
    """
    Classify error type for provider health tracking.
    
    Returns one of: rate_limit, auth_error, quota_exceeded, timeout, 
                    server_error, unknown
    """
    error_lower = error_message.lower()
    
    # Check response data for specific error codes
    if response_data:
        error_code = str(response_data.get("error", "")).lower()
        status = response_data.get("status")
        
        if status == 429 or "429" in error_code:
            return "rate_limit"
        if status in (401, 403) or any(k in error_code for k in ("unauthorized", "forbidden", "auth")):
            return "auth_error"
        if (status is not None and status >= 500) or any(k in error_code for k in ("server_error", "internal_error", "service_unavailable")):
            return "server_error"
    
    # Pattern matching on error message
    if any(k in error_lower for k in ("rate limit", "429", "too many requests", "rate_limit")):
        return "rate_limit"
    if any(k in error_lower for k in ("auth", "unauthorized", "forbidden", "401", "403", "api key")):
        return "auth_error"
    if any(k in error_lower for k in ("quota", "billing", "insufficient_quota", "limit exceeded")):
        return "quota_exceeded"
    if any(k in error_lower for k in ("timeout", "timed out", "etimedout", "deadline")):
        return "timeout"
    if any(k in error_lower for k in ("500", "502", "503", "server error", "internal error", "service unavailable")):
        return "server_error"
    if any(k in error_lower for k in ("connection refused", "cannot connect", "connect call failed",
                                       "connection reset", "clientconnectorerror", "networkerror",
                                       "gateway unavailable", "econnrefused", "no route to host")):
        return "gateway_unavailable"
    
    return "unknown"


async def safe_log_usage_event(db: AsyncSession, **kwargs: Any) -> None:
    """Best-effort usage logging that never poisons the caller DB session."""
    try:
        await log_usage_event(db, **kwargs)
    except Exception as e:
        logger.warning("[USAGE] Skipping usage event due to DB/logging error: %s", e)
        try:
            await db.rollback()
        except Exception:
            pass


def extract_json(text: str) -> dict[str, Any]:
    """Best-effort extraction of JSON object from assistant summary text.
    
    Handles:
    - Plain JSON objects
    - JSON wrapped in markdown code blocks (```json ... ```)
    - Nested braces
    """
    candidate = text.strip()
    
    # Try direct JSON parse first
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else {"raw": candidate}
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code blocks (```json ... ```)
    import re
    json_block_pattern = r"```(?:json)?\s*\n(.*?)\n```"
    matches = re.findall(json_block_pattern, candidate, re.DOTALL | re.IGNORECASE)
    for match in matches:
        try:
            parsed = json.loads(match.strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    # Try finding JSON object boundaries
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = candidate[start : end + 1]
        try:
            parsed = json.loads(snippet)
            return parsed if isinstance(parsed, dict) else {"raw": candidate}
        except json.JSONDecodeError:
            pass

    return {"raw": candidate}


def json_list(value: Any) -> list[str]:
    """Convert value to a list of strings, filtering invalid items."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float))]
