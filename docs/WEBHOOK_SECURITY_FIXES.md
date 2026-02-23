# Webhook Security Fixes - Implementation Guide

**Status:** 🔴 In Progress  
**Target:** Mitigate critical security issues identified in security audit  
**Related:** `WEBHOOK_SECURITY_AUDIT.md`

---

## Fix #1: Payload Size Limiting

### Implementation

**File:** `app/middleware.py` (new class)

```python
class PayloadSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests with body size > max_bytes."""
    
    def __init__(self, app, max_bytes: int = 1_048_576):  # 1MB default
        super().__init__(app)
        self.max_bytes = max_bytes
    
    async def dispatch(self, request: Request, call_next):
        """Check Content-Length before reading body."""
        content_length = request.headers.get("content-length")
        
        if content_length and int(content_length) > self.max_bytes:
            logger.warning(
                f"Rejected oversized request: {content_length} bytes "
                f"from {request.client.host} to {request.url.path}"
            )
            return JSONResponse(
                {"detail": "Payload too large"},
                status_code=413
            )
        
        return await call_next(request)
```

**File:** `app/main.py`

```python
from app.middleware import PayloadSizeLimitMiddleware

app.add_middleware(
    PayloadSizeLimitMiddleware,
    max_bytes=1_048_576  # 1MB for webhooks
)
```

**File:** `app/config.py` (add setting)

```python
MAX_WEBHOOK_PAYLOAD_BYTES: int = Field(
    default=1_048_576,  # 1MB
    description="Maximum webhook payload size in bytes"
)
```

**Test Update:**
```python
@pytest.mark.asyncio
async def test_webhook_rejects_oversized_payload(client: AsyncClient, db_session):
    # ... existing setup ...
    
    response = await client.post(
        "/api/webhooks/receive/custom",
        json=huge_payload
    )
    
    assert response.status_code == 413  # ✅ Now enforced
    assert "too large" in response.json()["detail"].lower()
```

---

## Fix #2: Rate Limiting

### Implementation

**Dependencies:**
```bash
pip install slowapi
```

**File:** `requirements.txt`
```
slowapi==0.1.9
```

**File:** `app/rate_limit.py` (new)

```python
"""Rate limiting for webhook endpoints."""

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request
from starlette.responses import JSONResponse

# Global limiter instance
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/hour"],  # Default for all endpoints
    storage_uri="memory://",  # Use memory backend (or redis:// for production)
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Custom handler for rate limit errors."""
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Rate limit exceeded. Please try again later.",
            "retry_after": exc.detail.split("Retry after ")[1] if "Retry after" in exc.detail else "60 seconds"
        }
    )
```

**File:** `app/main.py`

```python
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.rate_limit import limiter, rate_limit_exceeded_handler

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
```

**File:** `app/routers/webhooks.py`

```python
from app.rate_limit import limiter

@router.post("/webhooks/receive/{provider}")
@limiter.limit("10/minute")  # Per-IP rate limit
async def receive_webhook(
    request: Request,  # Required for limiter
    provider: str,
    # ... rest of params ...
):
    # ... existing code ...
```

**Test Update:**
```python
@pytest.mark.asyncio
async def test_webhook_rate_limiting_per_ip(client: AsyncClient, db_session):
    # ... existing setup ...
    
    for i in range(15):
        response = await client.post(
            "/api/webhooks/receive/custom",
            json=payload
        )
        
        if i < 10:
            assert response.status_code == 200  # ✅ First 10 allowed
        else:
            assert response.status_code == 429  # ✅ Rest rate-limited
```

---

## Fix #3: HMAC for Custom Webhooks

### Implementation

**File:** `app/routers/webhooks.py`

Update signature verification logic:

```python
def verify_custom_signature(
    body: bytes,
    secret: str,
    signature_header: Optional[str]
) -> bool:
    """
    Verify custom webhook signature using HMAC SHA256.
    
    Expected header format: X-Webhook-Signature: sha256=<hex_digest>
    
    For backward compatibility, also accepts secret in payload (deprecated).
    """
    if not signature_header:
        # Backward compatibility: check payload (log warning)
        logger.warning("Custom webhook using deprecated payload-based auth")
        return False  # Or keep old behavior with deprecation notice
    
    try:
        # Parse signature header
        if not signature_header.startswith('sha256='):
            return False
        
        expected_sig = hmac.new(
            secret.encode('utf-8'),
            body,
            hashlib.sha256
        ).hexdigest()
        
        received_sig = signature_header.replace('sha256=', '')
        
        return hmac.compare_digest(expected_sig, received_sig)
    except Exception as e:
        logger.error(f"Custom webhook signature verification failed: {e}")
        return False


@router.post("/webhooks/receive/{provider}")
async def receive_webhook(
    # ... existing params ...
    x_webhook_signature: Optional[str] = Header(None),
    # ...
):
    # ... existing code ...
    
    # In signature verification section:
    elif provider == "custom":
        # Try HMAC signature first (preferred)
        signature_valid = verify_custom_signature(
            body, webhook.secret, x_webhook_signature
        )
        
        # Fallback to payload-based auth (deprecated)
        if not signature_valid:
            signature_valid = payload.get("secret") == webhook.secret
            if signature_valid:
                logger.warning(
                    f"Webhook {webhook.id} using deprecated payload-based auth. "
                    "Please migrate to HMAC signatures."
                )
```

**Documentation:** Add to `docs/WEBHOOKS.md`:

```markdown
### Custom Webhook Authentication

**Recommended (HMAC Signature):**

1. Generate HMAC-SHA256 signature of request body:
   ```python
   import hmac
   import hashlib
   
   signature = hmac.new(
       secret.encode('utf-8'),
       body.encode('utf-8'),
       hashlib.sha256
   ).hexdigest()
   ```

2. Send signature in header:
   ```bash
   curl -X POST http://server/api/webhooks/receive/custom \
     -H "Content-Type: application/json" \
     -H "X-Webhook-Signature: sha256=${signature}" \
     -d '{"title": "My Task"}'
   ```

**Deprecated (Payload Secret):**
```json
{"secret": "my-secret", "title": "Task"}
```
This method will be removed in v2.0.
```

---

## Fix #4: HTML Sanitization

### Implementation

**Dependencies:**
```bash
pip install bleach
```

**File:** `requirements.txt`
```
bleach==6.1.0
```

**File:** `app/utils/sanitize.py` (new)

```python
"""Input sanitization utilities."""

import bleach
from typing import Optional


def sanitize_html(text: Optional[str]) -> str:
    """
    Remove all HTML tags and dangerous content from text.
    
    Uses bleach to strip tags while preserving text content.
    Safe for storing user input that will be rendered in web UIs.
    """
    if text is None:
        return ""
    
    # Strip all HTML tags (no tags allowed)
    # Alternatively, use allowed_tags for rich text (e.g., ['b', 'i', 'a'])
    cleaned = bleach.clean(
        text,
        tags=[],  # No tags allowed
        strip=True,  # Remove tags, keep text
        strip_comments=True
    )
    
    return cleaned


def sanitize_task_data(data: dict) -> dict:
    """Sanitize task-related fields from webhook payloads."""
    if "title" in data:
        data["title"] = sanitize_html(data["title"])
    if "description" in data:
        data["description"] = sanitize_html(data["description"])
    if "notes" in data:
        data["notes"] = sanitize_html(data["notes"])
    if "body" in data:
        data["body"] = sanitize_html(data["body"])
    
    return data
```

**File:** `app/routers/webhooks.py`

```python
from app.utils.sanitize import sanitize_html, sanitize_task_data

async def create_task_from_webhook(
    db: AsyncSession,
    webhook: WebhookRegistrationModel,
    payload: dict,
    config: dict
) -> dict:
    """Create a task from webhook payload."""
    
    # Sanitize payload before processing
    payload = sanitize_task_data(payload.copy())
    
    # ... rest of existing code ...
    
    # When creating task:
    task = TaskModel(
        id=task_id,
        title=sanitize_html(title),  # Double-check
        notes=sanitize_html(description),
        # ... rest ...
    )
```

**Test Update:**
```python
@pytest.mark.asyncio
async def test_webhook_sanitizes_input_for_task_creation(client, db_session, sample_project):
    # ... existing setup ...
    
    payload = {
        "secret": "secret",
        "title": "<script>alert('XSS')</script>Test Task",
        "description": "<img src=x onerror=alert('XSS')>Description"
    }
    
    response = await client.post("/api/webhooks/receive/custom", json=payload)
    assert response.status_code == 200
    
    # Verify HTML was stripped
    task_id = response.json()["task_id"]
    task = await db_session.get(TaskModel, task_id)
    
    assert "<script>" not in task.title  # ✅ Sanitized
    assert "<img" not in task.notes
    assert "Test Task" in task.title  # ✅ Text preserved
```

---

## Fix #5: Generic Error Messages

### Implementation

**File:** `app/routers/webhooks.py`

```python
# At the top:
import logging

logger = logging.getLogger(__name__)

# Custom exception for webhook errors
class WebhookError(Exception):
    """Base exception for webhook processing errors."""
    pass


@router.post("/webhooks/receive/{provider}")
async def receive_webhook(...):
    """Receive webhook events from external providers."""
    
    # Read body
    try:
        body = await request.body()
    except Exception as e:
        logger.error(f"Failed to read webhook body: {e}")
        raise HTTPException(
            status_code=400,
            detail="Invalid request"  # ✅ Generic message
        )
    
    # Parse JSON
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON from {provider}: {e}")
        raise HTTPException(
            status_code=400,
            detail="Invalid request format"  # ✅ Generic message
        )
    
    # Find webhook registration
    result = await db.execute(...)
    webhooks = result.scalars().all()
    
    if not webhooks:
        logger.warning(
            f"No active webhook for provider: {provider}, "
            f"event_type: {event_type}"
        )
        # ✅ Don't reveal if provider is configured or not
        raise HTTPException(
            status_code=403,
            detail="Unauthorized"
        )
    
    # Signature verification
    if not matched_webhook:
        logger.warning(
            f"Signature validation failed for {provider} "
            f"event {event_type} from {request.client.host}"
        )
        # ✅ Same error as missing webhook
        raise HTTPException(
            status_code=403,
            detail="Unauthorized"
        )
    
    # Process event
    try:
        result = await process_webhook_event(...)
        # ... success handling ...
        
    except Exception as e:
        # ✅ Log detailed error server-side
        logger.error(
            f"Webhook processing failed: {event_id} ({provider}/{event_type})",
            exc_info=True  # Include stack trace in logs
        )
        
        # ✅ Return generic error to client
        return {
            "status": "error",
            "event_id": event_id,
            "message": "Webhook processing failed"
            # ❌ Don't include: "error": str(e)
        }
```

**Test Update:**
```python
@pytest.mark.asyncio
async def test_webhook_error_messages_dont_leak_internals(client, db_session):
    # Try non-existent provider
    response = await client.post(
        "/api/webhooks/receive/nonexistent",
        json={"data": "test"}
    )
    
    assert response.status_code == 403
    assert response.json()["detail"] == "Unauthorized"  # ✅ Generic
    # ❌ Should NOT contain: "No webhook configured for provider: nonexistent"


@pytest.mark.asyncio
async def test_webhook_processing_errors_dont_expose_details(client, db_session):
    # ... setup with invalid config ...
    
    response = await client.post(
        "/api/webhooks/receive/custom",
        json={"secret": "secret", "title": "Test"}
    )
    
    data = response.json()
    assert data["status"] == "error"
    assert data["message"] == "Webhook processing failed"  # ✅ Generic
    assert "error" not in data  # ✅ No exception details
```

---

## Testing Strategy

### 1. Unit Tests
- Update all tests in `test_webhook_security.py` to enforce new controls
- Add tests for edge cases (exact size limits, boundary conditions)

### 2. Integration Tests
- Test full webhook flow with real payloads
- Verify rate limiting doesn't block legitimate traffic
- Test backward compatibility for custom webhook auth

### 3. Manual Testing
- Send webhooks from GitHub test events
- Verify Slack webhook signature validation
- Test oversized payloads (should be rejected)
- Test rapid requests (should be rate-limited)

### 4. Performance Testing
- Verify middleware doesn't add significant latency
- Test rate limiter memory usage with many IPs
- Benchmark sanitization overhead

---

## Deployment Plan

### Phase 1: Add Middleware (Non-Breaking)
1. Deploy payload size limit (won't affect existing webhooks)
2. Deploy rate limiting with generous limits
3. Monitor for false positives

### Phase 2: Add HMAC Support (Backward Compatible)
1. Deploy custom webhook HMAC support
2. Document migration guide
3. Keep payload-based auth working (with warnings)

### Phase 3: Enable Sanitization
1. Deploy HTML sanitization
2. Verify existing tasks render correctly
3. Monitor for legitimate HTML being stripped

### Phase 4: Error Message Updates
1. Deploy generic error responses
2. Verify logging captures enough detail for debugging

### Phase 5: Deprecate Payload Auth
1. Send notifications to custom webhook users
2. Set deprecation timeline (e.g., 90 days)
3. Remove payload-based auth in next major version

---

## Rollback Plan

If issues detected:

1. **Rate limiting too aggressive:**
   - Increase limits via config (no code deploy needed)
   - Or disable via feature flag

2. **Sanitization breaks legitimate data:**
   - Add allowed tags to bleach config
   - Rollback sanitization, audit data

3. **Performance degradation:**
   - Disable middleware causing issues
   - Optimize and redeploy

---

## Configuration

**File:** `app/config.py`

```python
class Settings(BaseSettings):
    # ... existing settings ...
    
    # Webhook security settings
    MAX_WEBHOOK_PAYLOAD_BYTES: int = Field(
        default=1_048_576,  # 1MB
        description="Maximum webhook payload size"
    )
    
    WEBHOOK_RATE_LIMIT_PER_MINUTE: int = Field(
        default=10,
        description="Max webhook requests per IP per minute"
    )
    
    WEBHOOK_RATE_LIMIT_PER_HOUR: int = Field(
        default=100,
        description="Max webhook requests per IP per hour"
    )
    
    WEBHOOK_REQUIRE_HMAC_CUSTOM: bool = Field(
        default=False,  # Start as optional, make required later
        description="Require HMAC signatures for custom webhooks"
    )
    
    WEBHOOK_SANITIZE_HTML: bool = Field(
        default=True,
        description="Sanitize HTML in webhook payloads"
    )
```

---

## Monitoring

Add metrics for:
- Webhook requests per second (by provider, by IP)
- Rate limit rejections
- Signature validation failures
- Payload size distribution
- Processing errors by type

Use structured logging:
```python
logger.info("webhook_received", extra={
    "provider": provider,
    "event_type": event_type,
    "payload_size": len(body),
    "signature_valid": signature_valid,
    "processing_ms": elapsed_ms
})
```

---

## Documentation Updates

1. **API docs:** Update webhook receiver endpoint documentation
2. **Security docs:** Document security controls implemented
3. **Migration guide:** Guide for custom webhook users to add HMAC
4. **Changelog:** Document breaking changes and timelines

---

## Validation Checklist

Before marking as complete:

- [ ] Payload size limit enforced (1MB default)
- [ ] Rate limiting active (10/min per IP)
- [ ] Custom webhooks accept HMAC signatures
- [ ] HTML sanitization applied to task fields
- [ ] Error messages are generic
- [ ] All security tests pass
- [ ] Documentation updated
- [ ] Monitoring/alerting configured
- [ ] Backward compatibility verified
- [ ] Performance impact acceptable (<10ms overhead)

---

**Status:** Ready for implementation  
**Estimated effort:** 4-6 hours (all fixes)  
**Priority:** CRITICAL - Block production deployment until complete
