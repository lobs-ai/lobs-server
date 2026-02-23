"""Error classification and handling for worker operations."""

from typing import Any


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
    
    return "unknown"
