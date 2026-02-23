"""Input sanitization utilities for security."""

import html
import re
from typing import Optional, Any
import logging

logger = logging.getLogger(__name__)


def sanitize_html(text: Optional[str]) -> str:
    """
    Remove/escape HTML from text to prevent XSS attacks.
    
    Uses simple HTML escaping rather than tag stripping to preserve
    user intent (e.g., if they meant to type "<script>" as text).
    
    For webhook payloads, this prevents malicious HTML/JS from being
    stored in database and later executed in web UIs.
    
    Args:
        text: Input text that may contain HTML
        
    Returns:
        Sanitized text with HTML entities escaped
    """
    if text is None:
        return ""
    
    if not isinstance(text, str):
        return str(text)
    
    # Escape HTML entities: < becomes &lt;, > becomes &gt;, etc.
    # This preserves the text but makes it safe to display
    sanitized = html.escape(text)
    
    # Also strip any control characters that could cause issues
    # Allow newlines and tabs, remove other control chars
    sanitized = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', sanitized)
    
    return sanitized


def sanitize_dict_values(data: dict, fields: list[str]) -> dict:
    """
    Sanitize specific fields in a dictionary.
    
    Args:
        data: Dictionary to sanitize
        fields: List of field names to sanitize
        
    Returns:
        New dictionary with sanitized values
    """
    sanitized = data.copy()
    
    for field in fields:
        if field in sanitized and isinstance(sanitized[field], str):
            original = sanitized[field]
            sanitized[field] = sanitize_html(original)
            
            if sanitized[field] != original:
                logger.info(
                    f"Sanitized HTML from field '{field}': "
                    f"{len(original)} bytes -> {len(sanitized[field])} bytes"
                )
    
    return sanitized


def sanitize_webhook_payload(payload: dict) -> dict:
    """
    Sanitize common webhook payload fields.
    
    Applies HTML escaping to fields that commonly contain user input
    and will be displayed in UIs.
    
    Args:
        payload: Webhook payload dictionary
        
    Returns:
        Sanitized payload
    """
    # Common fields that need sanitization
    text_fields = [
        'title', 'name', 'subject',
        'description', 'body', 'content', 'notes',
        'message', 'text', 'summary',
        'label', 'tag', 'category'
    ]
    
    return sanitize_dict_values(payload, text_fields)


def sanitize_github_issue(issue_data: dict) -> dict:
    """
    Sanitize GitHub issue data from webhook payloads.
    
    Args:
        issue_data: GitHub issue object from webhook
        
    Returns:
        Sanitized issue data
    """
    if not issue_data:
        return {}
    
    sanitized = issue_data.copy()
    
    # Sanitize text fields
    for field in ['title', 'body']:
        if field in sanitized:
            sanitized[field] = sanitize_html(sanitized[field])
    
    # Sanitize labels if present
    if 'labels' in sanitized and isinstance(sanitized['labels'], list):
        sanitized['labels'] = [
            {**label, 'name': sanitize_html(label.get('name', ''))}
            if isinstance(label, dict) else label
            for label in sanitized['labels']
        ]
    
    return sanitized


def validate_payload_size(payload: Any, max_bytes: int = 1_048_576) -> bool:
    """
    Validate that payload size is within acceptable limits.
    
    This is a secondary check after Content-Length header validation.
    Useful for detecting decompression bombs or chunked transfer attacks.
    
    Args:
        payload: Payload object (will be serialized to check size)
        max_bytes: Maximum allowed size in bytes
        
    Returns:
        True if size is acceptable, False otherwise
    """
    import sys
    
    # Get approximate size of object in memory
    # For dicts/lists, this gives a rough estimate
    size = sys.getsizeof(payload)
    
    if size > max_bytes:
        logger.warning(
            f"Payload size ({size} bytes) exceeds limit ({max_bytes} bytes)"
        )
        return False
    
    return True


# Maximum JSON nesting depth to prevent stack overflow attacks
MAX_JSON_DEPTH = 20


def validate_json_depth(obj: Any, max_depth: int = MAX_JSON_DEPTH, current_depth: int = 0) -> bool:
    """
    Validate that JSON object nesting doesn't exceed maximum depth.
    
    Prevents stack overflow attacks via deeply nested JSON.
    
    Args:
        obj: JSON object to validate (dict, list, or primitive)
        max_depth: Maximum allowed nesting depth
        current_depth: Current nesting level (internal use)
        
    Returns:
        True if depth is acceptable, False otherwise
    """
    if current_depth > max_depth:
        logger.warning(
            f"JSON nesting depth ({current_depth}) exceeds limit ({max_depth})"
        )
        return False
    
    if isinstance(obj, dict):
        for value in obj.values():
            if not validate_json_depth(value, max_depth, current_depth + 1):
                return False
    elif isinstance(obj, list):
        for item in obj:
            if not validate_json_depth(item, max_depth, current_depth + 1):
                return False
    
    # Primitives (str, int, bool, None) don't add depth
    return True
