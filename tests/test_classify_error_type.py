"""Tests for classify_error_type function."""

import pytest

from app.orchestrator.worker import classify_error_type


def test_classify_error_type_none_status():
    """Test that None status doesn't cause TypeError."""
    # This was the bug: status=None caused '>=' comparison to fail
    result = classify_error_type(
        "Some generic error",
        {"error": "generic error", "status": None}
    )
    assert result == "unknown"


def test_classify_error_type_missing_status():
    """Test when status key is missing entirely."""
    result = classify_error_type(
        "Some generic error",
        {"error": "generic error"}
    )
    assert result == "unknown"


def test_classify_error_type_server_error_with_status():
    """Test server error classification with valid status code."""
    result = classify_error_type(
        "Internal server error",
        {"error": "server_error", "status": 500}
    )
    assert result == "server_error"


def test_classify_error_type_server_error_status_502():
    """Test server error with 502 status."""
    result = classify_error_type(
        "Bad gateway",
        {"status": 502}
    )
    assert result == "server_error"


def test_classify_error_type_server_error_status_503():
    """Test server error with 503 status."""
    result = classify_error_type(
        "Service unavailable",
        {"status": 503}
    )
    assert result == "server_error"


def test_classify_error_type_server_error_in_message():
    """Test server error detection from error message when status is None."""
    result = classify_error_type(
        "500 internal server error occurred",
        {"status": None}
    )
    assert result == "server_error"


def test_classify_error_type_server_error_in_error_code():
    """Test server error detection from error code when status is None."""
    result = classify_error_type(
        "Something went wrong",
        {"error": "internal_error", "status": None}
    )
    assert result == "server_error"


def test_classify_error_type_rate_limit():
    """Test rate limit classification."""
    result = classify_error_type(
        "Rate limit exceeded",
        {"status": 429}
    )
    assert result == "rate_limit"


def test_classify_error_type_rate_limit_none_status():
    """Test rate limit from message when status is None."""
    result = classify_error_type(
        "429 too many requests",
        {"status": None}
    )
    assert result == "rate_limit"


def test_classify_error_type_auth_error():
    """Test auth error classification."""
    result = classify_error_type(
        "Unauthorized",
        {"status": 401}
    )
    assert result == "auth_error"


def test_classify_error_type_auth_error_403():
    """Test auth error with 403 status."""
    result = classify_error_type(
        "Forbidden",
        {"status": 403}
    )
    assert result == "auth_error"


def test_classify_error_type_auth_error_none_status():
    """Test auth error from message when status is None."""
    result = classify_error_type(
        "unauthorized - invalid api key",
        {"status": None}
    )
    assert result == "auth_error"


def test_classify_error_type_quota_exceeded():
    """Test quota exceeded classification."""
    result = classify_error_type(
        "Quota limit exceeded",
        {"status": None}
    )
    assert result == "quota_exceeded"


def test_classify_error_type_timeout():
    """Test timeout classification."""
    result = classify_error_type(
        "Request timed out",
        {"status": None}
    )
    assert result == "timeout"


def test_classify_error_type_no_response_data():
    """Test classification with no response data."""
    result = classify_error_type("Rate limit exceeded")
    assert result == "rate_limit"
    
    result = classify_error_type("500 server error")
    assert result == "server_error"
    
    result = classify_error_type("unauthorized access")
    assert result == "auth_error"


def test_classify_error_type_empty_response_data():
    """Test classification with empty response data dict."""
    result = classify_error_type("Some error", {})
    assert result == "unknown"


def test_classify_error_type_unknown():
    """Test unknown error classification."""
    result = classify_error_type(
        "Some weird error",
        {"error": "weird", "status": 418}  # I'm a teapot
    )
    assert result == "unknown"
