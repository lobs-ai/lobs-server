# Design: “Lobs, Handle This” Universal Intake Pipeline

**Date:** 2026-02-24  
**Author:** Architect  
**Scope:** lobs-server + lobs-mission-control integration

## 1) Problem Statement

Rafe has high-value, unstructured inputs in Chat (messages, pasted links, context snippets), but converting those into executable work still requires manual reformulation and task creation.

We need a fast, low-friction path from **chat message → structured execution plan** so cognitive load is delegated immediately.

Specifically:
- Add a chat-level **“Handle This”** action in Mission Control (`Sources/LobsMissionControl/Chat/`)
- Send payload to a new backend endpoint: `POST /v1/intake/handle-this`
- Return a structured plan:
  - `task`
  - `subtasks`
  - `owner_agent`
  - `confidence`
  - `clarifying_questions`
- Show a compact review UI before creating downstream tasks

---

## 2) Proposed Solution

### 2.1 End-to-end flow

```text
Chat message bubble
  -> “Handle This” action
  -> Review sheet prefilled with message + optional URL/context
  -> POST /v1/intake/handle-this
  -> server generates IntakePlan
  -> user reviews/edits
  -> create task(s) via existing /api/tasks endpoints
```

### 2.2 API contract (server)

Create new router: `app/routers/intake.py` with route:
- `POST /intake/handle-this` (mounted under API prefix)

**Versioned path requirement:** expose `POST /v1/intake/handle-this` by setting `API_PREFIX=/v1` in deployments that use this flow. For compatibility with existing clients currently calling `/api/*`, keep route prefix-driven and avoid hardcoding `v1` in router code.

#### Request model
`HandleThisRequest`
- `message_text: str` (required)
- `source_url: str | None` (optional)
- `context: str | None` (optional freeform context)
- `session_key: str | None` (optional chat session correlation)
- `message_id: str | None` (optional chat message correlation)

Validation:
- non-empty `message_text`
- max lengths (defensive caps)
- `source_url` must be valid URL when present

#### Response model
`HandleThisResponse`
- `task: str`
- `subtasks: list[str]`
- `owner_agent: str` (`programmer|researcher|writer|architect|reviewer|project-manager`)
- `confidence: float` (0.0–1.0)
- `clarifying_questions: list[str]`
- `normalization_notes: list[str] | None` (optional, for explainability)

### 2.3 Planning strategy (server internals)

Implement in two stages for low risk:

**Stage A (MVP deterministic planner):**
- Reuse heuristics pattern from `intent.py` for agent recommendation
- Use simple extraction for URL/context cues
- Generate concise task + subtasks template
- Generate clarifying questions only when confidence < threshold (e.g., 0.72)

**Stage B (LLM-backed planner, optional behind flag):**
- Add service `app/services/intake_planner.py`
- Inject model routing later via existing orchestrator routing policy
- Keep response schema unchanged

Recommendation: ship Stage A first to unblock UX and contract, then iterate quality.

### 2.4 Mission Control changes

#### Models (`Sources/LobsMissionControl/Models.swift`)
Add:
- `HandleThisRequest`
- `HandleThisResponse`
- `HandleThisDraft` (UI local state)

#### API (`Sources/LobsMissionControl/APIService.swift`)
Add:
- `submitHandleThis(_ request: HandleThisRequest) async throws -> HandleThisResponse`

Keep endpoint path configurable via baseURL + path, consistent with current `request(...)` helper.

#### Chat UI (`Sources/LobsMissionControl/Chat/`)
- Add per-message action in `ChatMessageView` context menu / trailing action: **Handle This**
- Add compact review sheet component (new file recommended: `HandleThisReviewSheet.swift`)
  - Prefill from selected message text
  - Editable optional URL/context
  - “Generate Plan” button
  - Show returned structured plan
  - CTA: “Create Task” (single task path first), optional “Create Task + subtasks” as follow-up
- Wire state through `ChatView` and/or `ChatViewModel`

### 2.5 Observability and operations

Server logging (structured):
- `intake.handle_this.requested`
- `intake.handle_this.planned`
- `intake.handle_this.failed`

Metrics (initial counters in logs acceptable):
- requests count
- mean confidence
- % with clarifying questions
- p95 latency

### 2.6 Security / abuse limits

- Existing bearer auth applies (same as other API routes)
- Input size caps to avoid prompt/resource abuse
- URL is treated as plain text metadata at MVP (no auto-fetch in endpoint)

---

## 3) Tradeoffs

### Chosen: deterministic MVP first, stable schema
**Pros**
- Fastest path to usable feature
- Predictable latency and behavior
- Lower implementation risk
- Lets client/UI integrate now

**Cons**
- Planning quality lower than LLM planner
- May ask fewer nuanced clarifying questions

### Alternative considered: direct LLM planner now
**Why not now**
- More moving parts (prompting, model fallback, reliability)
- Harder to test deterministically
- Slower to ship cross-repo integration

---

## 4) Implementation Plan (Ordered Subtasks)

### Task 1 — Server contract + endpoint (small/medium)
**Owner:** programmer  
**Deliverables:**
- `app/routers/intake.py` with `POST /intake/handle-this`
- Pydantic request/response models
- Router registration in `app/main.py`
- Basic deterministic planner logic

**Acceptance criteria:**
- Valid request returns all required response fields
- Empty `message_text` returns validation error (422)
- Auth enforced like other protected routes

### Task 2 — Mission Control models + API client (small)
**Owner:** programmer  
**Deliverables:**
- New model structs in `Models.swift`
- `APIService.submitHandleThis(...)`

**Acceptance criteria:**
- Request/response decode passes with snake_case conversion
- Network errors and auth errors surface existing APIError behavior

### Task 3 — Chat “Handle This” review UX (medium)
**Owner:** programmer  
**Deliverables:**
- Message-level action in chat UI
- Compact review sheet with request editing + plan display
- Create task action using existing task creation APIs

**Acceptance criteria:**
- From any non-system message, user can launch flow in <=2 interactions
- Successful plan generation renders task/subtasks/agent/confidence/questions
- User can cancel safely without side effects

### Task 4 — Integration and guardrails (small/medium)
**Owner:** programmer  
**Deliverables:**
- Basic event logging in server
- client-side loading/error states in sheet
- docs update (AGENTS.md endpoint listing + changelog note)

**Acceptance criteria:**
- Failures are actionable (error message visible)
- Logs distinguish request vs planner failure

---

## 5) Testing Strategy

### Server tests (`tests/`)
1. **Unit tests** for intake planner mapping:
   - research-like message -> `owner_agent=researcher`
   - implementation-like message -> `owner_agent=programmer`
2. **API tests** for endpoint:
   - 200 happy path with full schema
   - 422 invalid payloads
   - 401 missing/invalid token
3. **Boundary tests**:
   - long message truncation/rejection behavior
   - invalid URL handling

### Mission Control tests
1. **Model decode tests** for `HandleThisResponse`
2. **APIService tests** for request path and error mapping
3. **UI tests/manual QA script**:
   - invoke from chat message
   - edit URL/context
   - generate plan
   - create task
   - verify created task appears in task board

### Rollout validation checklist
- Endpoint reachable from production Mission Control config
- p95 latency acceptable (<1.5s MVP target)
- at least 10 real “Handle This” interactions without crash

---

## Risks & Mitigations

1. **Path mismatch (`/api` vs `/v1`)**  
   Mitigation: keep router prefix-driven; coordinate deployment `API_PREFIX` and client base path together.

2. **Over-automation from low-confidence plan**  
   Mitigation: always show review step; if confidence below threshold, highlight clarifying questions and block one-click creation unless user confirms.

3. **Scope creep into full autonomous delegation**  
   Mitigation: this phase stops at plan + user-confirmed task creation.

---

## Handoffs

```json
[
  {
    "to": "programmer",
    "initiative": "handle-this-intake-pipeline",
    "title": "Implement intake endpoint POST /intake/handle-this with stable response schema",
    "context": "Implement server-side intake endpoint per docs/design/handle-this-intake-pipeline.md. Start with deterministic planner and strict request validation.",
    "acceptance": "Authenticated POST returns {task, subtasks, owner_agent, confidence, clarifying_questions}. Includes 200/401/422 tests and router registration in main.py.",
    "files": [
      "docs/design/handle-this-intake-pipeline.md",
      "app/main.py",
      "app/routers/intake.py",
      "tests/"
    ]
  },
  {
    "to": "programmer",
    "initiative": "handle-this-intake-pipeline",
    "title": "Add Mission Control Handle This models, API call, and compact review sheet in Chat",
    "context": "In lobs-mission-control, add request/response models in Models.swift, APIService method, and message-level Handle This flow in Sources/LobsMissionControl/Chat/.",
    "acceptance": "From a chat message user can open Handle This sheet, submit to endpoint, view structured plan, and create a task via existing task APIs. Includes loading/error states and basic tests/manual QA checklist.",
    "files": [
      "docs/design/handle-this-intake-pipeline.md",
      "Sources/LobsMissionControl/Models.swift",
      "Sources/LobsMissionControl/APIService.swift",
      "Sources/LobsMissionControl/Chat/"
    ]
  }
]
```