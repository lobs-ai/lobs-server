# Integration Contract v1

**Status:** Canonical  
**Created:** 2026-02-24  
**Owner:** lobs-server integrations platform

---

## Purpose

This document defines the canonical interface every external integration connector **must** implement. Connectors are thin adapters between lobs-server and a third-party service (email, calendar, CRM, project tool, etc.). The contract prevents one-off adapter sprawl and ensures all connectors can be:

- Tested uniformly with contract tests
- Swapped transparently (e.g. Gmail → Microsoft 365) without touching call sites
- Monitored and health-checked via a single endpoint

---

## Connector Interface

Every connector extends `BaseConnector` (see `integrations/base_connector.py`) and must implement the following five capability groups:

### 1. `auth` — Authentication & Configuration

| Method | Signature | Description |
|--------|-----------|-------------|
| `is_configured()` | `() → bool` | Returns `True` if all required credentials/env vars are present and non-empty. Must not make network calls. |
| `health_check()` | `async () → ConnectorHealth` | Makes a cheap live call to verify connectivity. Returns `ConnectorHealth` with `status`, `latency_ms`, and optional `detail`. |

### 2. `fetch` — Read Data

| Method | Signature | Description |
|--------|-----------|-------------|
| `fetch_messages()` | `async (limit, filter_unread) → list[NormalizedMessage]` | Return normalized messages (emails, chat, etc.). |
| `fetch_events()` | `async (start, end) → list[NormalizedEvent]` | Return calendar events in the given time window. |
| `search()` | `async (query, limit) → list[NormalizedMessage \| NormalizedEvent]` | Free-text search over the connector's data. |

Connectors that do not support a `fetch_*` method should raise `NotImplementedError` (default in base class).

### 3. `act` — Write / Mutate

| Method | Signature | Description |
|--------|-----------|-------------|
| `send_message()` | `async (msg: OutboundMessage) → ActionResult` | Send a message (email, SMS, chat). |
| `create_event()` | `async (event: EventDraft) → ActionResult` | Create a calendar event. |
| `update_event()` | `async (event_id, patch: dict) → ActionResult` | Patch an existing event. |
| `delete_event()` | `async (event_id) → ActionResult` | Delete/cancel an event. |
| `mark_read()` | `async (message_id) → ActionResult` | Mark a message as read. |

### 4. `webhook_in` — Receive Webhooks

| Method | Signature | Description |
|--------|-----------|-------------|
| `verify_webhook()` | `(headers, body_bytes) → bool` | Verify HMAC/signature of incoming webhook. |
| `parse_webhook()` | `(headers, body_bytes) → WebhookEvent` | Parse provider payload into normalized `WebhookEvent`. |

### 5. `webhook_out` — Register / Deregister Webhooks

| Method | Signature | Description |
|--------|-----------|-------------|
| `register_webhook()` | `async (callback_url) → str` | Register a webhook with the provider. Returns provider-assigned webhook ID. |
| `deregister_webhook()` | `async (webhook_id) → bool` | Remove the webhook registration. |

---

## Normalized Entities

All connectors exchange data via these shared entities (see `integrations/entities.py`):

### `NormalizedMessage`

```python
@dataclass
class NormalizedMessage:
    id: str                     # Provider-scoped unique ID
    connector: str              # Connector name (e.g. "email", "calendar")
    subject: str
    body: str
    sender: str                 # "Name <email>" or handle
    recipients: list[str]
    timestamp: datetime
    is_unread: bool = True
    labels: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)   # Original provider payload
```

### `NormalizedEvent`

```python
@dataclass
class NormalizedEvent:
    id: str
    connector: str
    title: str
    description: str
    start: datetime
    end: datetime
    attendees: list[str]
    location: str = ""
    is_all_day: bool = False
    status: str = "confirmed"   # confirmed | tentative | cancelled
    raw: dict = field(default_factory=dict)
```

### `NormalizedTask`

```python
@dataclass
class NormalizedTask:
    id: str
    connector: str
    title: str
    description: str
    due: datetime | None
    status: str                 # open | in_progress | done | cancelled
    assignee: str = ""
    priority: str = "normal"    # low | normal | high | urgent
    raw: dict = field(default_factory=dict)
```

### `NormalizedContact`

```python
@dataclass
class NormalizedContact:
    id: str
    connector: str
    name: str
    email: str = ""
    phone: str = ""
    organization: str = ""
    raw: dict = field(default_factory=dict)
```

### `ConnectorHealth`

```python
@dataclass
class ConnectorHealth:
    connector: str
    status: str                 # ok | degraded | error | not_configured
    latency_ms: float = 0.0
    detail: str = ""
```

### `ActionResult`

```python
@dataclass
class ActionResult:
    success: bool
    connector: str
    resource_id: str = ""       # Provider-assigned ID of created/updated resource
    detail: str = ""
    raw: dict = field(default_factory=dict)
```

### `WebhookEvent`

```python
@dataclass
class WebhookEvent:
    connector: str
    event_type: str             # e.g. "message.received", "event.updated"
    resource_id: str
    payload: dict               # Normalized data
    raw: dict = field(default_factory=dict)
```

### `OutboundMessage`

```python
@dataclass
class OutboundMessage:
    to: list[str]
    subject: str
    body: str
    html: bool = False
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
```

### `EventDraft`

```python
@dataclass
class EventDraft:
    title: str
    start: datetime
    end: datetime
    attendees: list[str] = field(default_factory=list)
    description: str = ""
    location: str = ""
    is_all_day: bool = False
```

---

## Rules for New Connectors

1. **Extend `BaseConnector`** from `integrations/base_connector.py`.
2. **Implement `is_configured()` and `health_check()`** — required for the integration status endpoint.
3. **Use normalized entities** for all data exchange. Never expose raw provider objects to callers.
4. **Pass contract tests** in `tests/test_integration_contract.py` by registering your connector in `ALL_CONNECTORS`.
5. **One connector file per integration** under `integrations/<name>_connector.py`.
6. **No business logic in connectors** — they are pure adapters. Route logic lives in services/routers.
7. **Raise `ConnectorError`** (subclass of `IntegrationError`) for recoverable provider errors. Let stdlib exceptions propagate for programming errors.

---

## Adding a New Connector — Checklist

- [ ] Create `integrations/<name>_connector.py`
- [ ] Extend `BaseConnector`, set `name = "<name>"`
- [ ] Implement all five capability groups (raise `NotImplementedError` for unsupported ops)
- [ ] Add to `ALL_CONNECTORS` in `tests/test_integration_contract.py`
- [ ] Run `python -m pytest tests/test_integration_contract.py -v` — all contract tests must pass
- [ ] Update this document's **Connector Registry** table below

---

## Connector Registry

| Name | Module | Supported Capabilities |
|------|--------|----------------------|
| `email` | `integrations/email_connector.py` | auth, fetch (messages, search), act (send, mark_read), webhook_in (partial) |
| `calendar` | `integrations/calendar_connector.py` | auth, fetch (events), act (create, update, delete event) |

---

## Versioning

This is **Contract v1**. Breaking changes (removing/renaming methods, changing entity fields) require a new version document (`contract_v2.md`) and a migration period. Additive changes (new optional fields, new methods) are non-breaking and do not require a version bump.
