"""Custom assertion helpers for common test patterns.

Provides readable assertion functions that encapsulate common validation logic.
"""

from typing import Any, Dict, List, Optional, Union
from httpx import Response


def assert_response_success(
    response: Response,
    expected_status: int = 200,
    message: Optional[str] = None
) -> None:
    """Assert that a response is successful.
    
    Args:
        response: HTTP response object
        expected_status: Expected status code (default: 200)
        message: Optional custom error message
        
    Raises:
        AssertionError: If response status doesn't match expected
    """
    error_msg = message or f"Expected status {expected_status}, got {response.status_code}"
    if response.status_code != expected_status:
        # Include response body for debugging
        try:
            body = response.json()
            error_msg += f"\nResponse body: {body}"
        except Exception:
            error_msg += f"\nResponse text: {response.text}"
    
    assert response.status_code == expected_status, error_msg


def assert_response_error(
    response: Response,
    expected_status: int = 400,
    expected_detail: Optional[str] = None
) -> None:
    """Assert that a response is an error with expected details.
    
    Args:
        response: HTTP response object
        expected_status: Expected error status code (default: 400)
        expected_detail: Optional substring expected in error detail
        
    Raises:
        AssertionError: If response doesn't match expected error
    """
    assert response.status_code == expected_status, \
        f"Expected error status {expected_status}, got {response.status_code}"
    
    if expected_detail:
        try:
            body = response.json()
            detail = body.get("detail", "")
            assert expected_detail in str(detail), \
                f"Expected detail to contain '{expected_detail}', got: {detail}"
        except Exception as e:
            raise AssertionError(f"Could not parse error response: {e}")


def assert_has_fields(
    data: Dict[str, Any],
    required_fields: List[str],
    message: Optional[str] = None
) -> None:
    """Assert that a dictionary contains all required fields.
    
    Args:
        data: Dictionary to check
        required_fields: List of field names that must be present
        message: Optional custom error message
        
    Raises:
        AssertionError: If any required field is missing
    """
    missing = [f for f in required_fields if f not in data]
    if missing:
        error_msg = message or f"Missing required fields: {missing}"
        error_msg += f"\nAvailable fields: {list(data.keys())}"
        raise AssertionError(error_msg)


def assert_task_status(
    task_data: Dict[str, Any],
    expected_status: str,
    message: Optional[str] = None
) -> None:
    """Assert that a task has the expected status.
    
    Args:
        task_data: Task dictionary
        expected_status: Expected status value
        message: Optional custom error message
        
    Raises:
        AssertionError: If task status doesn't match expected
    """
    actual = task_data.get("status")
    error_msg = message or f"Expected task status '{expected_status}', got '{actual}'"
    assert actual == expected_status, error_msg


def assert_list_response(
    response: Response,
    min_length: int = 0,
    max_length: Optional[int] = None,
    item_schema: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Assert that response contains a valid list and return it.
    
    Args:
        response: HTTP response object
        min_length: Minimum expected list length (default: 0)
        max_length: Maximum expected list length (optional)
        item_schema: Required fields for each list item (optional)
        
    Returns:
        The parsed list from response
        
    Raises:
        AssertionError: If response is not a valid list or constraints not met
    """
    assert_response_success(response)
    
    data = response.json()
    assert isinstance(data, list), f"Expected list response, got {type(data)}"
    
    if min_length is not None:
        assert len(data) >= min_length, \
            f"Expected at least {min_length} items, got {len(data)}"
    
    if max_length is not None:
        assert len(data) <= max_length, \
            f"Expected at most {max_length} items, got {len(data)}"
    
    if item_schema and data:
        # Check first item has required fields
        assert_has_fields(data[0], item_schema, "List item missing required fields")
    
    return data


def assert_pagination_headers(
    response: Response,
    expected_total: Optional[int] = None
) -> None:
    """Assert that response has valid pagination headers.
    
    Args:
        response: HTTP response object
        expected_total: Expected total count (optional)
        
    Raises:
        AssertionError: If pagination headers are missing or invalid
    """
    headers = response.headers
    
    # Check for common pagination headers
    if "x-total-count" in headers:
        total = int(headers["x-total-count"])
        assert total >= 0, f"Invalid total count: {total}"
        
        if expected_total is not None:
            assert total == expected_total, \
                f"Expected total count {expected_total}, got {total}"


def assert_timestamp_fields(
    data: Dict[str, Any],
    fields: Optional[List[str]] = None
) -> None:
    """Assert that timestamp fields are present and valid.
    
    Args:
        data: Dictionary to check
        fields: List of timestamp field names (default: created_at, updated_at)
        
    Raises:
        AssertionError: If timestamp fields are missing or invalid
    """
    timestamp_fields = fields or ["created_at", "updated_at"]
    
    for field in timestamp_fields:
        assert field in data, f"Missing timestamp field: {field}"
        # Basic validation - should be a string in ISO format
        value = data[field]
        assert isinstance(value, str), \
            f"Timestamp field {field} should be string, got {type(value)}"
        assert "T" in value or value.isdigit(), \
            f"Invalid timestamp format for {field}: {value}"


def assert_db_object_matches(
    db_obj: Any,
    expected_data: Dict[str, Any],
    exclude_fields: Optional[List[str]] = None
) -> None:
    """Assert that a database object matches expected data.
    
    Args:
        db_obj: SQLAlchemy model instance
        expected_data: Dictionary of expected values
        exclude_fields: Fields to skip in comparison
        
    Raises:
        AssertionError: If object data doesn't match expected
    """
    exclude = set(exclude_fields or [])
    exclude.update(["created_at", "updated_at"])  # Usually auto-generated
    
    for key, expected_value in expected_data.items():
        if key in exclude:
            continue
        
        actual_value = getattr(db_obj, key, None)
        assert actual_value == expected_value, \
            f"Field {key}: expected {expected_value}, got {actual_value}"


def assert_json_schema(
    data: Dict[str, Any],
    schema: Dict[str, type]
) -> None:
    """Assert that data matches a simple type schema.
    
    Args:
        data: Dictionary to validate
        schema: Dict mapping field names to expected types
        
    Raises:
        AssertionError: If data doesn't match schema
    """
    for field, expected_type in schema.items():
        assert field in data, f"Missing field: {field}"
        actual_value = data[field]
        
        # Handle Optional types (None is allowed)
        if actual_value is None:
            continue
            
        assert isinstance(actual_value, expected_type), \
            f"Field {field}: expected {expected_type.__name__}, got {type(actual_value).__name__}"


def assert_not_found(
    response: Response,
    resource_type: Optional[str] = None
) -> None:
    """Assert that response is a 404 not found error.
    
    Args:
        response: HTTP response object
        resource_type: Optional resource type to check in error message
        
    Raises:
        AssertionError: If response is not a 404 or message doesn't match
    """
    assert response.status_code == 404, \
        f"Expected 404 Not Found, got {response.status_code}"
    
    try:
        detail = response.json().get("detail", "")
        assert "not found" in detail.lower(), \
            f"Expected 'not found' in error detail, got: {detail}"
        
        if resource_type:
            assert resource_type.lower() in detail.lower(), \
                f"Expected '{resource_type}' in error detail, got: {detail}"
    except Exception as e:
        raise AssertionError(f"Could not parse 404 response: {e}")


def assert_deleted(
    response: Response,
    expected_message: str = "deleted"
) -> None:
    """Assert that response indicates successful deletion.
    
    Args:
        response: HTTP response object from delete request
        expected_message: Expected status message (default: "deleted")
        
    Raises:
        AssertionError: If response doesn't indicate deletion
    """
    assert_response_success(response, expected_status=200)
    
    data = response.json()
    if "status" in data:
        assert data["status"] == expected_message, \
            f"Expected status '{expected_message}', got '{data.get('status')}'"


def assert_created(
    response: Response,
    expected_fields: Optional[List[str]] = None,
    require_timestamps: bool = True
) -> Dict[str, Any]:
    """Assert successful creation and return created object.
    
    Args:
        response: HTTP response object from POST request
        expected_fields: Optional list of fields to verify
        require_timestamps: Whether to require created_at field (default: True)
        
    Returns:
        Created object data
        
    Raises:
        AssertionError: If creation failed or fields missing
    """
    assert_response_success(response, expected_status=200)
    
    data = response.json()
    
    # Check common creation fields
    default_fields = ["id"]
    if require_timestamps:
        default_fields.append("created_at")
    if expected_fields:
        default_fields.extend(expected_fields)
    
    assert_has_fields(data, default_fields)
    
    return data


def assert_updated(
    response: Response,
    expected_changes: Optional[Dict[str, Any]] = None,
    require_timestamps: bool = True
) -> Dict[str, Any]:
    """Assert successful update and optionally verify changes.
    
    Args:
        response: HTTP response object from PUT/PATCH request
        expected_changes: Optional dict of field:value pairs to verify
        require_timestamps: Whether to require updated_at field (default: True)
        
    Returns:
        Updated object data
        
    Raises:
        AssertionError: If update failed or changes not applied
    """
    assert_response_success(response, expected_status=200)
    
    data = response.json()
    
    # Verify has updated_at timestamp if required
    if require_timestamps:
        assert "updated_at" in data, "Updated object should have updated_at field"
    
    # Verify expected changes if provided
    if expected_changes:
        for field, expected_value in expected_changes.items():
            actual_value = data.get(field)
            assert actual_value == expected_value, \
                f"Field {field}: expected {expected_value}, got {actual_value}"
    
    return data
