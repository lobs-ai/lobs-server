# Agent Code Review Workflow

**Date:** 2026-02-22  
**Status:** Proposed (pending ADR approval)

Defines when and how the **reviewer agent** is triggered for agent-produced code changes. Replaces ad-hoc reviewer invocation with systematic quality gates.

---

## Quick Reference

**Do I need a code review?**

```
>500 lines changed?           → YES (Large Refactor)
New /api/* route?             → YES (API Endpoint)
Touched models.py or DB?      → YES (State Management)
Auth/token/permission code?   → YES (Security)
Added >20 tests?              → YES (Test Quality)
Otherwise?                    → NO (auto-merge)
```

See [Review Triggers](#review-triggers) for full criteria and checklists.

---

## Review Triggers

A reviewer **must** be involved when any agent produces changes matching these criteria:

| Category | Trigger Threshold | Rationale |
|----------|------------------|-----------|
| Large refactors | >500 lines changed | High blast radius; needs structural review |
| New API endpoints | Any new route | Contract stability; auth/validation coverage |
| State management | Any change to models, migrations, DB schema | Data integrity risk |
| Security-sensitive code | Auth, tokens, permissions, crypto, input validation | Security is non-negotiable |
| Test suite additions | >20 new tests | Test quality matters; bad tests give false confidence |

### Additional Triggers (Recommended)

- **Cross-agent changes** — Code touching multiple agent workspaces
- **Orchestrator logic** — Changes to scanner, router, engine, or monitor
- **Configuration changes** — Environment variables, feature flags, model routing

---

## Workflow

```
Agent completes work
       │
       ▼
  Orchestrator checks triggers ──── No trigger matched ──► Auto-merge
       │
   Trigger matched
       │
       ▼
  Create review task (assigned to reviewer agent)
       │
       ▼
  Reviewer runs checklist for category
       │
       ▼
  Pass ──► Approve + merge
  Fail ──► Block + create fix task (assigned to original agent)
```

### Task Handoff

When a review is triggered, the orchestrator creates a task with:

- **Title:** `Review: [original task title]`
- **Agent:** `reviewer`
- **Priority:** Same as original task
- **Context fields:**
  - `review_category` — Which trigger(s) matched
  - `original_task_id` — Link to the originating task
  - `changed_files` — List of files modified
  - `diff_summary` — Line counts by file

---

## Review Checklists

### Large Refactors (>500 lines)

- [ ] No functional behavior changes unless intentional
- [ ] All existing tests still pass
- [ ] No dead code introduced
- [ ] Import structure is clean (no circular deps)
- [ ] Commit messages explain *why*, not just *what*
- [ ] Related docs updated

### New API Endpoints

- [ ] Auth decorator applied (`Depends(get_current_user)`)
  - Example: `@router.post("/items", dependencies=[Depends(get_current_user)])`
- [ ] Pydantic schemas defined for request/response
  - Request: `ItemCreate`, Response: `ItemResponse` in `schemas.py`
- [ ] Input validation covers edge cases
  - Empty strings, null values, negative numbers, SQL injection patterns
- [ ] Error responses follow existing patterns (4xx with detail)
  - `400: {"detail": "Invalid input"}`, `404: {"detail": "Not found"}`
- [ ] Endpoint added to AGENTS.md API reference
  - Include method, path, parameters, response schema, example curl
- [ ] Tests cover happy path + auth + validation errors
  - At minimum: `test_create_success`, `test_create_unauthorized`, `test_create_invalid_input`
- [ ] Rate limiting considered (if applicable)

### State Management Changes

- [ ] Migration script included (if schema change)
  - Alembic migration with `upgrade()` and `downgrade()` functions
- [ ] Backward compatibility preserved (or migration path documented)
  - New columns have defaults; existing queries still work
- [ ] Indexes added for new query patterns
  - Example: `Index('ix_tasks_status_priority', 'status', 'priority')` for common filters
- [ ] No N+1 query patterns introduced
  - Use `joinedload()` or `selectinload()` for related entities
- [ ] WAL mode compatibility verified
  - No `PRAGMA` statements that conflict with Write-Ahead Logging
- [ ] Rollback plan documented
  - What happens if migration fails halfway? Data loss risk?

### Security-Sensitive Code

- [ ] No secrets in code or logs
  - Tokens loaded from env vars; no API keys in source or git history
- [ ] Auth checks cannot be bypassed
  - Verify dependency injection is required (can't skip with direct function call)
- [ ] Input sanitized before DB queries
  - SQLAlchemy ORM used (not raw SQL); Pydantic validation on all inputs
- [ ] Token expiry/rotation handled
  - Tokens have expiration; refresh mechanism exists
- [ ] Error messages don't leak internals
  - `401: "Unauthorized"` not `"Invalid token: eyJhbGc..."`
- [ ] Follows OWASP Top 10 relevant controls
  - Injection, Broken Auth, Sensitive Data Exposure, XXE, Broken Access Control

### Test Suite Additions (>20 tests)

- [ ] Tests are independent (no shared mutable state)
- [ ] Assertions are specific (not just `assert response.status_code == 200`)
- [ ] Edge cases covered, not just happy paths
- [ ] No time-dependent flakiness (see [TIME_BASED_TEST_DETECTION.md](../TIME_BASED_TEST_DETECTION.md))
- [ ] Test names describe the scenario
- [ ] Fixtures are reusable, not duplicated

---

## Handoff Template

When creating a review task, use this template for the task description:

```markdown
## Code Review Request

**Original Task:** [task-id] — [title]
**Agent:** [agent that produced the code]
**Review Category:** [trigger category]

### Changes Summary
- Files changed: [count]
- Lines added: [count]
- Lines removed: [count]

### Changed Files
- `path/to/file1.py` — [brief description]
- `path/to/file2.py` — [brief description]

### Review Focus
[What specifically needs attention based on the trigger category]

### Checklist
[Paste the relevant checklist from the workflow guide]
```

---

## Implementation Notes

**Where this lives in the codebase:**
- Trigger detection: `app/orchestrator/engine.py` (post-task completion hook)
- Review task creation: `app/orchestrator/router.py` (new review routing logic)
- Checklists: This document (referenced by reviewer agent prompt)

**Orchestrator integration:** After an agent task completes, the orchestrator should:
1. Analyze the diff (file count, line count, file paths)
2. Match against trigger rules
3. If matched, create a review task before marking the original as complete
4. Original task stays in `review` status until reviewer approves

---

## Common Review Issues (By Category)

These are the most frequent problems caught during code reviews. Check these first:

### API Endpoints
- Missing auth dependency (public endpoint when it should be protected)
- No validation on string length (potential memory exhaustion)
- 500 errors instead of proper 4xx with detail
- Missing pagination on list endpoints

### State Management
- Adding NOT NULL column without default or migration data backfill
- Foreign key constraints missing (orphaned records possible)
- Unique constraints not enforced at DB level (only in code)
- Transaction boundaries incorrect (partial updates on failure)

### Security
- JWT secret in environment but not required (crashes if missing)
- Permissions checked after action (should be before)
- User input in log messages (PII leakage)
- Password comparison using `==` instead of constant-time compare

### Large Refactors
- Import cycles introduced by moving code
- Dead code left behind (old implementations not removed)
- Tests still pass but test wrong thing (mocks not updated)
- Environment variable names changed but not documented

### Test Suites
- Tests pass but don't clean up DB (future test pollution)
- Mocking at wrong level (testing mocks, not real code)
- Time-dependent assertions (`datetime.now()` causing flakes)
- Tests require specific execution order (not isolated)

---

## Related

- [ADR-0013: Systematic Agent Code Review](../decisions/0013-systematic-agent-code-review.md) (proposed)
- [ADR-0008: Agent Specialization Model](../decisions/0008-agent-specialization-model.md)
- [ADR-0011: Handoff Protocol](../decisions/0011-handoff-protocol.md)
- [TIME_BASED_TEST_DETECTION.md](../TIME_BASED_TEST_DETECTION.md) — Detecting and fixing time-dependent test issues
