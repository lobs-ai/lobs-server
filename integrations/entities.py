"""Normalized entity types for the Integration Contract v1.

All connectors exchange data using these shared dataclasses.  No connector
may expose raw provider objects to callers — everything goes through these
types.  See integrations/contract_v1.md for the full specification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ---------------------------------------------------------------------------
# Inbound / read entities
# ---------------------------------------------------------------------------


@dataclass
class NormalizedMessage:
    """A message received from a connector (email, chat, SMS, …)."""

    id: str
    """Provider-scoped unique ID."""

    connector: str
    """Name of the connector that produced this message (e.g. 'email')."""

    subject: str
    body: str
    sender: str
    """'Name <email>' or platform handle."""

    recipients: list[str]
    timestamp: datetime

    is_unread: bool = True
    labels: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    """Original provider payload, kept for debugging and future fields."""


@dataclass
class NormalizedEvent:
    """A calendar event produced by a connector."""

    id: str
    connector: str
    title: str
    description: str
    start: datetime
    end: datetime
    attendees: list[str]

    location: str = ""
    is_all_day: bool = False
    status: str = "confirmed"
    """confirmed | tentative | cancelled"""

    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedTask:
    """A task / to-do item produced by a connector (e.g. GitHub issue, Jira ticket)."""

    id: str
    connector: str
    title: str
    description: str
    due: datetime | None

    status: str = "open"
    """open | in_progress | done | cancelled"""

    assignee: str = ""
    priority: str = "normal"
    """low | normal | high | urgent"""

    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedContact:
    """A contact / person record produced by a connector."""

    id: str
    connector: str
    name: str

    email: str = ""
    phone: str = ""
    organization: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Outbound / write types
# ---------------------------------------------------------------------------


@dataclass
class OutboundMessage:
    """Payload for send_message() calls."""

    to: list[str]
    subject: str
    body: str

    html: bool = False
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)


@dataclass
class EventDraft:
    """Payload for create_event() calls."""

    title: str
    start: datetime
    end: datetime

    attendees: list[str] = field(default_factory=list)
    description: str = ""
    location: str = ""
    is_all_day: bool = False


# ---------------------------------------------------------------------------
# Result / status types
# ---------------------------------------------------------------------------


@dataclass
class ActionResult:
    """Result returned by any act() operation."""

    success: bool
    connector: str

    resource_id: str = ""
    """Provider-assigned ID of the created or updated resource."""

    detail: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectorHealth:
    """Health-check result returned by health_check()."""

    connector: str
    status: str
    """ok | degraded | error | not_configured"""

    latency_ms: float = 0.0
    detail: str = ""


# ---------------------------------------------------------------------------
# Webhook types
# ---------------------------------------------------------------------------


@dataclass
class WebhookEvent:
    """A parsed, normalized inbound webhook from a provider."""

    connector: str
    event_type: str
    """Dot-namespaced event type, e.g. 'message.received', 'event.updated'."""

    resource_id: str
    payload: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class IntegrationError(Exception):
    """Base class for all integration-layer errors."""

    def __init__(self, connector: str, message: str) -> None:
        self.connector = connector
        super().__init__(f"[{connector}] {message}")


class ConnectorError(IntegrationError):
    """Recoverable provider-side error (rate limit, temporary unavailability, …)."""


class ConnectorAuthError(IntegrationError):
    """Authentication / credential error."""


class ConnectorNotImplementedError(IntegrationError):
    """Raised when a connector does not support a given capability."""
