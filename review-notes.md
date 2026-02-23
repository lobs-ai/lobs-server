# Security and Error Handling Review: Mission Control API Endpoints

**Reviewed by:** reviewer  
**Date:** 2026-02-22  
**Scope:** `app/routers/inbox.py`, `app/routers/orchestrator_reflections.py`  
**Focus:** Authorization, input validation, error handling, data integrity

---

## Executive Summary

Reviewed recently added Mission Control endpoints for security vulnerabilities and error handling issues. Found **4 critical security issues**, **6 important bugs**, and **3 suggestions for improvement**.

**Critical findings:**
- Missing commit/flush calls can cause silent data loss in batch operations
- SQL injection vulnerability via unvalidated limit/offset parameters  
- Thread ID override vulnerability in inbox message creation
- Missing validation on state transitions in initiative decisions

**Recommendation:** Address critical issues before next release.

---

## 🔴 Critical Issues

### 1. **Missing database commits in `inbox.py`**

**Severity:** 🔴 Critical — Data loss  
**Location:** `app/routers/inbox.py` lines 34-36, 62-69, 81-87

**Issue:**  
Multiple endpoints modify database state (`flush()`) but never `commit()`. Without commit, changes are lost when the request ends.

**Affected endpoints:**
- `POST /api/inbox` (create_inbox_item) — line 36
- `PUT /api/inbox/{item_id}` (update_inbox_item) — lines 68-69
- `DELETE /api/inbox/{item_id}` (delete_inbox_item) — line 86

**Example:**
```python
@router.post("")
async def create_inbox_item(item: InboxItemCreate, db: AsyncSession = Depends(get_db)) -> InboxItem:
    db_item = InboxItemModel(**item.model_dump())
    db.add(db_item)
    await db.flush()  # ❌ Flushed but never committed
    await db.refresh(db_item)
    return InboxItem.model_validate(db_item)
```

**Impact:**  
Users create/update/delete inbox items, see success responses, but changes disappear. This breaks user trust and causes confusion.

**Fix:**  
Add `await db.commit()` before returning in all endpoints that modify data.

```python
await db.flush()
await db.commit()  # ✅ Add this
await db.refresh(db_item)
```

---

### 2. **SQL injection via unvalidated limit/offset**

**Severity:** 🔴 Critical — Security vulnerability  
**Location:** `app/routers/orchestrator_reflections.py` lines 177-178, 437-438

**Issue:**  
`limit` and `offset` query parameters are passed to SQL queries without proper validation. While SQLAlchemy provides some protection, the explicit `int()` cast can fail with non-numeric input.

**Example:**
```python
@router.get("/intelligence/initiatives")
async def list_initiatives(
    status: str | None = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    query = select(AgentInitiative).order_by(AgentInitiative.created_at.desc())
    if status:
        query = query.where(AgentInitiative.status == status)
    
    result = await db.execute(query.limit(max(1, min(1000, int(limit)))))  # ❌ int() can raise ValueError
```

**Attack vector:**
```bash
curl "/api/orchestrator/intelligence/initiatives?limit='; DROP TABLE agent_initiatives;--"
```

This will cause `ValueError: invalid literal for int()` instead of SQL injection (SQLAlchemy prevents the injection), but:
1. **Causes 500 errors** instead of proper 400 validation errors
2. **Leaks implementation details** in error messages
3. **No input sanitization** for the `status` parameter (string concatenation risk)

**Fix:**  
Add Pydantic validation in the route parameters and proper error handling:

```python
from pydantic import Field

@router.get("/intelligence/initiatives")
async def list_initiatives(
    status: str | None = None,
    limit: int = Field(default=200, ge=1, le=1000),  # ✅ Pydantic validates
    offset: int = Field(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
```

---

### 3. **Thread ID override vulnerability in inbox message creation**

**Severity:** 🔴 Critical — Authorization bypass  
**Location:** `app/routers/inbox.py` lines 117-143

**Issue:**  
The endpoint accepts a `thread_id` from the client, then **silently overrides it** with the actual thread ID. This is confusing and creates a security issue: clients can attempt to write to any thread, and the server just ignores the request parameter.

**Code:**
```python
@router.post("/{item_id}/thread/messages")
async def create_inbox_message(
    item_id: str,
    message: InboxMessageCreate,  # ❌ Contains thread_id from client
    db: AsyncSession = Depends(get_db)
) -> InboxMessage:
    result = await db.execute(select(InboxThreadModel).where(InboxThreadModel.doc_id == item_id))
    thread = result.scalar_one_or_none()
    
    if not thread:
        thread = InboxThreadModel(id=str(uuid4()), doc_id=item_id, triage_status="needs_response")
        db.add(thread)
        await db.flush()
    
    # Use the actual thread ID, not the one from the request
    message_data = message.model_dump()
    message_data["thread_id"] = thread.id  # ❌ Silently overwrites client input
    db_message = InboxMessageModel(**message_data)
```

**Problems:**
1. **API contract violation** — endpoint accepts `thread_id` but ignores it
2. **Potential for confusion** — client thinks they're posting to thread X, server posts to thread Y
3. **Missing validation** — no check if client-provided thread_id matches the actual thread

**Fix:**  
Remove `thread_id` from the `InboxMessageCreate` schema and derive it server-side only:

```python
class InboxMessageCreate(BaseModel):
    id: str
    author: str
    text: str
    # thread_id removed - server controls this

@router.post("/{item_id}/thread/messages")
async def create_inbox_message(
    item_id: str,
    message: InboxMessageCreate,
    db: AsyncSession = Depends(get_db)
) -> InboxMessage:
    # Find or create thread
    result = await db.execute(select(InboxThreadModel).where(InboxThreadModel.doc_id == item_id))
    thread = result.scalar_one_or_none()
    
    if not thread:
        thread = InboxThreadModel(id=str(uuid4()), doc_id=item_id, triage_status="needs_response")
        db.add(thread)
        await db.flush()
    
    # Create message with server-controlled thread_id
    db_message = InboxMessageModel(
        id=message.id,
        thread_id=thread.id,  # ✅ Server controls association
        author=message.author,
        text=message.text
    )
```

---

### 4. **No validation on initiative status transitions**

**Severity:** 🔴 Critical — Data integrity  
**Location:** `app/orchestrator/initiative_decisions.py` line 77

**Issue:**  
The decision engine only validates that approved initiatives are in `lobs_review` or `proposed` status, but **no validation** for defer/reject decisions. This allows invalid state transitions.

**Code:**
```python
if decision == "approve" and initiative.status not in {"lobs_review", "proposed"}:
    raise ValueError(f"initiative in status '{initiative.status}' is not approvable")
# ❌ No checks for defer/reject
```

**Attack scenario:**
1. Initiative is already `approved` with a task_id
2. Lobs decides to "reject" it
3. Status changes to `rejected` but task still exists
4. Orphaned task executes despite rejection

**Fix:**  
Add status transition validation for all decision types:

```python
# Valid state transitions
VALID_TRANSITIONS = {
    "approve": {"proposed", "lobs_review"},
    "defer": {"proposed", "lobs_review"},
    "reject": {"proposed", "lobs_review", "deferred"},
}

if initiative.status not in VALID_TRANSITIONS[decision]:
    raise ValueError(
        f"Cannot {decision} initiative in status '{initiative.status}'. "
        f"Valid states: {VALID_TRANSITIONS[decision]}"
    )
```

---

## 🟡 Important Issues

### 5. **Missing database commit in batch operations**

**Severity:** 🟡 Important — Data loss in batch operations  
**Location:** `app/routers/orchestrator_reflections.py` lines 354-402

**Issue:**  
The batch decision endpoint processes all decisions but only commits **once at the end** (line 402). If any decision fails after some succeed, the entire batch is lost.

**Code:**
```python
@router.post("/intelligence/initiatives/batch-decide")
async def batch_decide_initiatives(...):
    for d in payload.decisions:
        # ... process each decision
        try:
            r = await engine.decide(...)  # This calls commit internally!
            results.append(r)
        except (ValueError, PermissionError) as e:
            errors.append(...)
    
    # ... create new tasks
    
    await db.commit()  # ❌ Redundant - each engine.decide() already commits
```

**Problem:**  
Looking at `initiative_decisions.py` line 139, `engine.decide()` calls `await self.db.commit()` at the end. This means:
1. Each decision commits individually (good for atomicity per decision)
2. But the batch endpoint commits again at the end (redundant)
3. If task creation fails, decisions are already committed (partial success)

**Impact:**  
This is actually **safer** than expected (each decision is atomic), but the final commit is misleading and the error handling doesn't account for partial success.

**Fix:**  
Document this behavior and handle partial success:

```python
@router.post("/intelligence/initiatives/batch-decide")
async def batch_decide_initiatives(...):
    """
    Process multiple initiative decisions in a single batch.
    
    NOTE: Each decision commits independently. If later steps fail,
    earlier decisions are already persisted. Check 'results' and 'errors'
    to see what succeeded vs. failed.
    """
    # ... existing code
    
    # Final commit is only for new tasks
    await db.commit()
    
    return {
        "total": total,
        "processed": processed,
        # Add warning if partial success
        "partial_success": processed > 0 and failed > 0,
        ...
    }
```

---

### 6. **Missing validation on triage_status values**

**Severity:** 🟡 Important — Invalid state  
**Location:** `app/routers/inbox.py` line 154

**Issue:**  
The `triage_status` field accepts any string value. The schema has no validation for allowed values.

**Code:**
```python
class InboxTriageUpdate(BaseModel):
    triage_status: str  # ❌ No validation

@router.patch("/{item_id}/triage")
async def update_inbox_triage(
    item_id: str,
    triage_update: InboxTriageUpdate,
    db: AsyncSession = Depends(get_db)
) -> InboxThread:
    thread.triage_status = triage_update.triage_status  # ❌ Accepts anything
```

**Impact:**  
Database gets polluted with invalid values like `"foo"`, `"RESOLVED"` (wrong case), `""` (empty string).

**Fix:**  
Add Pydantic enum validation:

```python
from enum import Enum

class TriageStatus(str, Enum):
    NEEDS_RESPONSE = "needs_response"
    PENDING = "pending"
    RESOLVED = "resolved"

class InboxTriageUpdate(BaseModel):
    triage_status: TriageStatus  # ✅ Only allows valid values
```

---

### 7. **No input validation on initiative category/risk_tier in batch decisions**

**Severity:** 🟡 Important — Data integrity  
**Location:** `app/routers/orchestrator_reflections.py` lines 36-47

**Issue:**  
The `BatchInitiativeDecision` schema accepts optional fields like `selected_agent`, `selected_project_id` but doesn't validate:
- Agent names (could be `"hackerman"` instead of a real agent)
- Project IDs (could reference deleted/non-existent projects)
- Decision values (accepts any string, not just `approve|defer|reject`)

**Code:**
```python
class InitiativeDecisionRequest(BaseModel):
    decision: str  # ❌ Should be Literal["approve", "defer", "reject"]
    revised_title: str | None = None
    revised_description: str | None = None
    selected_agent: str | None = None  # ❌ No validation
    selected_project_id: str | None = None  # ❌ No FK validation
```

**Fix:**
```python
from typing import Literal

class InitiativeDecisionRequest(BaseModel):
    decision: Literal["approve", "defer", "reject"]  # ✅ Validated at schema level
    revised_title: str | None = Field(None, min_length=1, max_length=500)
    revised_description: str | None = Field(None, max_length=10000)
    selected_agent: str | None = Field(None, pattern=r"^[a-z_]+$")  # ✅ Validate agent names
    selected_project_id: str | None = None  # Validated in business logic
```

The engine validates project existence in `_create_task_from_initiative`, but this fails **after** the decision is recorded. Better to validate upfront.

---

### 8. **Bulk update endpoint returns count without verifying success**

**Severity:** 🟡 Important — Misleading response  
**Location:** `app/routers/inbox.py` lines 183-197

**Issue:**  
The bulk read state update returns `count: len(item_ids)` even if some items don't exist.

**Code:**
```python
@router.post("/read-state")
async def update_inbox_read_state(
    item_ids: list[str],
    db: AsyncSession = Depends(get_db)
):
    for item_id in item_ids:
        result = await db.execute(select(InboxItemModel).where(InboxItemModel.id == item_id))
        item = result.scalar_one_or_none()
        if item:  # ❌ Silently skips missing items
            item.is_read = True
    
    await db.flush()
    return {"status": "updated", "count": len(item_ids)}  # ❌ Claims all were updated
```

**Impact:**  
Client sends 10 IDs, 3 don't exist, but response says `"count": 10`. Client thinks all succeeded.

**Fix:**
```python
@router.post("/read-state")
async def update_inbox_read_state(
    item_ids: list[str],
    db: AsyncSession = Depends(get_db)
):
    updated = 0
    missing = []
    
    for item_id in item_ids:
        result = await db.execute(select(InboxItemModel).where(InboxItemModel.id == item_id))
        item = result.scalar_one_or_none()
        if item:
            item.is_read = True
            updated += 1
        else:
            missing.append(item_id)
    
    await db.commit()
    return {
        "status": "updated",
        "updated": updated,
        "requested": len(item_ids),
        "missing": missing  # ✅ Tell client what failed
    }
```

---

### 9. **No rate limiting or max array size for batch operations**

**Severity:** 🟡 Important — DoS vulnerability  
**Location:** `app/routers/orchestrator_reflections.py` line 260, `app/routers/inbox.py` line 184

**Issue:**  
Batch endpoints accept unbounded arrays. An attacker can send 1 million initiative decisions or inbox item IDs, causing:
- Memory exhaustion (loading all initiatives into memory)
- Database overload (N+1 query problem)
- Request timeout

**Code:**
```python
class BatchInitiativeDecisionRequest(BaseModel):
    decisions: list[BatchInitiativeDecision] = Field(default_factory=list)  # ❌ No max_length
    new_tasks: list[LobsNewTask] = Field(default_factory=list)  # ❌ No max_length

@router.post("/read-state")
async def update_inbox_read_state(
    item_ids: list[str],  # ❌ No max_length
```

**Fix:**
```python
from pydantic import Field

class BatchInitiativeDecisionRequest(BaseModel):
    decisions: list[BatchInitiativeDecision] = Field(default_factory=list, max_length=100)  # ✅ Limit batch size
    new_tasks: list[LobsNewTask] = Field(default_factory=list, max_length=50)

@router.post("/read-state")
async def update_inbox_read_state(
    item_ids: list[str] = Field(..., max_length=1000),  # ✅ Prevent abuse
```

---

### 10. **Missing error handling for concurrent modifications**

**Severity:** 🟡 Important — Race conditions  
**Location:** `app/routers/inbox.py` line 62, `app/routers/orchestrator_reflections.py` line 221

**Issue:**  
No optimistic locking or version checks. Two requests updating the same inbox item or initiative simultaneously can cause last-write-wins data loss.

**Scenario:**
1. User A fetches inbox item: `{"title": "Old", "summary": null}`
2. User B fetches same item: `{"title": "Old", "summary": null}`
3. User A updates: `PATCH /inbox/123 {"title": "Updated by A"}`
4. User B updates: `PATCH /inbox/123 {"summary": "Added summary"}`
5. **Result:** User A's title change is lost (B overwrites with old data)

**Fix:**  
Add optimistic locking with version field or timestamp:

```python
class InboxItemUpdate(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    updated_at: Optional[datetime] = None  # ✅ Client sends last-known timestamp

@router.put("/{item_id}")
async def update_inbox_item(
    item_id: str,
    item_update: InboxItemUpdate,
    db: AsyncSession = Depends(get_db)
) -> InboxItem:
    result = await db.execute(select(InboxItemModel).where(InboxItemModel.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Inbox item not found")
    
    # Check if item was modified since client last saw it
    if item_update.updated_at and item.updated_at > item_update.updated_at:
        raise HTTPException(
            status_code=409,
            detail="Item was modified by another request. Refresh and try again."
        )
    
    # ... apply updates
```

---

## 🔵 Suggestions

### 11. **Inconsistent error responses**

**Severity:** 🔵 Suggestion — API consistency  
**Location:** Multiple endpoints

**Issue:**  
404 errors return different message formats:
- `"Inbox item not found"` (inbox.py:50)
- `"Thread not found"` (inbox.py:156)
- `"Initiative not found"` (orchestrator_reflections.py:228)

Some return just a string detail, others might return structured errors.

**Fix:**  
Standardize error responses:

```python
# Create a shared error response helper
def not_found_error(resource: str, resource_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "error": "not_found",
            "resource": resource,
            "id": resource_id,
            "message": f"{resource.title()} not found"
        }
    )

# Usage
if not item:
    raise not_found_error("inbox_item", item_id)
```

---

### 12. **Missing request/response logging for audit trail**

**Severity:** 🔵 Suggestion — Observability  
**Location:** All endpoints, especially batch operations

**Issue:**  
No logging of who made initiative decisions, what they decided, or when. This is critical for:
- Debugging ("why did this initiative get rejected?")
- Audit compliance ("who approved this?")
- Analytics ("how many initiatives are typically deferred?")

**Fix:**  
Add structured logging to decision endpoints:

```python
import logging

logger = logging.getLogger(__name__)

@router.post("/intelligence/initiatives/{initiative_id}/decide")
async def decide_initiative(
    initiative_id: str,
    payload: InitiativeDecisionRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    initiative = await db.get(AgentInitiative, initiative_id)
    if initiative is None:
        raise HTTPException(status_code=404, detail="Initiative not found")
    
    logger.info(
        "Initiative decision",
        extra={
            "initiative_id": initiative_id,
            "decision": payload.decision,
            "decided_by": "lobs",
            "title": initiative.title,
            "category": initiative.category,
        }
    )
    
    # ... process decision
```

---

### 13. **No pagination metadata in list responses**

**Severity:** 🔵 Suggestion — API usability  
**Location:** `app/routers/inbox.py` line 19

**Issue:**  
The inbox list endpoint returns just an array, no pagination metadata. Clients can't tell:
- Total number of items
- Whether there are more pages
- What the current offset is

**Current:**
```python
@router.get("")
async def list_inbox_items(...) -> list[InboxItem]:  # ❌ Just an array
    return [InboxItem.model_validate(i) for i in items]
```

**Better:**
```python
@router.get("")
async def list_inbox_items(...) -> dict[str, Any]:  # ✅ Structured response
    # Get total count
    count_result = await db.execute(select(func.count()).select_from(InboxItemModel))
    total = count_result.scalar_one()
    
    return {
        "items": [InboxItem.model_validate(i) for i in items],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
    }
```

**Note:** The reflections endpoint already does this correctly (line 530).

---

## Test Coverage Analysis

Reviewed existing tests:
- ✅ `tests/test_inbox.py` — Good coverage of happy paths
- ✅ `tests/test_reflections_api.py` — Good coverage of list/filter functionality

**Missing test coverage:**
1. **No auth tests** — Tests don't verify that endpoints require authentication
2. **No authorization tests** — Tests don't verify users can't access others' data
3. **No error path tests for batch operations** — What happens when batch has mix of valid/invalid items?
4. **No concurrency tests** — What happens with simultaneous updates?
5. **No input validation tests** — Tests don't send malformed data (negative limits, SQL injection attempts, etc.)
6. **No state transition tests** — Can you approve an already-approved initiative?

**Recommended tests to add:**

```python
# tests/test_inbox_security.py
@pytest.mark.asyncio
async def test_inbox_requires_auth(client_no_auth: AsyncClient):
    """Verify inbox endpoints return 401 without auth."""
    response = await client_no_auth.get("/api/inbox")
    assert response.status_code == 401

@pytest.mark.asyncio
async def test_update_inbox_item_missing_commit(client: AsyncClient, db_session):
    """Verify that updates are actually persisted (regression test for missing commit)."""
    # Create item
    item = await client.post("/api/inbox", json={"id": "test", "title": "Original"})
    
    # Update it
    await client.put("/api/inbox/test", json={"title": "Updated"})
    
    # Fetch from DB directly (bypassing cache)
    result = await db_session.execute(select(InboxItemModel).where(InboxItemModel.id == "test"))
    item = result.scalar_one()
    assert item.title == "Updated"  # ❌ Currently fails - no commit!

@pytest.mark.asyncio
async def test_batch_decide_with_invalid_items(client: AsyncClient):
    """Verify batch handles mix of valid and invalid initiative IDs."""
    payload = {
        "decisions": [
            {"initiative_id": "real-id", "decision": "approve"},
            {"initiative_id": "fake-id", "decision": "approve"},
        ]
    }
    response = await client.post("/api/orchestrator/intelligence/initiatives/batch-decide", json=payload)
    data = response.json()
    assert data["failed"] == 1
    assert data["processed"] == 1
    assert len(data["errors"]) == 1
    assert "fake-id" in data["errors"][0]["initiative_id"]
```

---

## Summary of Findings

| Priority | Count | Category |
|----------|-------|----------|
| 🔴 Critical | 4 | Data loss, security, data integrity |
| 🟡 Important | 6 | Missing validation, race conditions, DoS |
| 🔵 Suggestion | 3 | API consistency, observability |
| **Total** | **13** | |

**Must fix before release:**
1. Add missing database commits (Issue #1)
2. Fix SQL injection vulnerability (Issue #2)
3. Fix thread ID override (Issue #3)
4. Add state transition validation (Issue #4)

**Should fix soon:**
5. Document batch operation behavior (Issue #5)
6. Validate triage status enum (Issue #6)
7. Validate decision inputs (Issue #7)
8. Fix bulk update response (Issue #8)

**Nice to have:**
9. Add batch size limits (Issue #9)
10. Add optimistic locking (Issue #10)
11-13. API improvements (consistency, logging, pagination)

---

## Recommended Actions

### For Programmer

Create handoffs for:
1. **Database commit fixes** — Add commits to inbox endpoints
2. **Input validation** — Add Pydantic Field constraints and enums
3. **Security hardening** — Remove thread_id from schema, validate state transitions
4. **Test coverage** — Add security and error path tests

### For Architect

Consider:
- **Authorization model** — Current system has authentication but no user-level authorization (all authenticated users can access all data)
- **Transaction boundaries** — Clarify where commits should happen (per-operation vs. batch)
- **Optimistic locking strategy** — Add versioning to prevent lost updates

---

## Code Quality Notes

**What's done well:**
- ✅ Good use of Pydantic schemas for type safety
- ✅ Consistent error handling with HTTPException
- ✅ Good separation of concerns (decision engine in separate module)
- ✅ Reflections endpoint has good pagination implementation

**What needs improvement:**
- ❌ Inconsistent transaction management (some endpoints commit, others don't)
- ❌ Missing input validation on critical fields
- ❌ No authorization checks beyond authentication
- ❌ Error messages leak implementation details

---

**Review complete.** See handoff for fixes.
