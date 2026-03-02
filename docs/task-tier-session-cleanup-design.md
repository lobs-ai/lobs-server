# Design: Task Tier Classification + Session Cleanup Fix

**Date:** 2026-03-01  
**Status:** Ready for implementation  
**Task ID:** FAD05A1F-5D54-4B27-A89B-BA876BF4AA4C

---

## Problem Statement

Three related improvements to reduce session leaks and make model routing deterministic:

1. **Task tier at creation** — Tier must be classified before any workflow step runs so spawn_agent can use task.model_tier directly without mid-workflow LLM calls.
2. **ModelChooser** — Should be a deterministic tier→model table lookup reading from DB settings; no fuzzy heuristics mid-workflow.
3. **Session cleanup** — NodeHandlers.delete_session() in workflow_nodes.py is a no-op placeholder. Sessions are never actually deleted, causing accumulation.

---

## Current State (Audit)

### Already Done

- Task tier at creation: app/services/task_tier.py + app/routers/tasks.py — complete
- ModelChooser tier→model from DB settings with explicit tier short-circuit — complete
- workflow_seeds.py session_refs wired for task-router, code-task, research-task — complete
- _exec_cleanup handles session_ref and session_refs correctly — complete

### NOT Done

NodeHandlers.delete_session() in workflow_nodes.py:201 is a no-op.
The correct API is sessions.delete via Gateway WebSocket JSON-RPC — same as worker_gateway.py.

---

## Fix

Replace the no-op NodeHandlers.delete_session() with a real WebSocket call:
- WS URL: GATEWAY_URL with http:// -> ws://
- Auth: Authorization: Bearer {GATEWAY_TOKEN}
- Method: sessions.delete, params: {key: session_key}
- Timeout: 10s, best-effort (errors = log warning, continue)

All imports (aiohttp, json, uuid, GATEWAY_URL, GATEWAY_TOKEN) already present in workflow_nodes.py.

---

## Acceptance Criteria

- After task completes, session deleted via Gateway (log shows "Deleted session <key>")
- Session count stays flat over multiple task cycles
- Errors produce WARNING, never fail the cleanup node
