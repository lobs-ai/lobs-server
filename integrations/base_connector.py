"""Base connector for the Integration Contract v1.

Every external integration must subclass BaseConnector and override the
capability groups it supports.  Unsupported operations raise
ConnectorNotImplementedError by default.

See integrations/contract_v1.md for the full specification.
"""

from __future__ import annotations

import abc
import time
from datetime import datetime
from typing import Any

from integrations.entities import (
    ActionResult,
    ConnectorHealth,
    ConnectorNotImplementedError,
    EventDraft,
    NormalizedEvent,
    NormalizedMessage,
    OutboundMessage,
    WebhookEvent,
)


class BaseConnector(abc.ABC):
    """Abstract base class for all lobs-server integration connectors.

    Subclasses must:
      1. Set the ``name`` class attribute to a short, snake_case identifier.
      2. Implement ``is_configured()`` and ``health_check()`` (auth group).
      3. Implement any capability group methods they support.
      4. Raise ``ConnectorNotImplementedError`` (via ``super()`` default) for
         anything they don't support — never raise plain ``NotImplementedError``.

    Contract tests in ``tests/test_integration_contract.py`` verify that every
    registered connector satisfies this interface.
    """

    #: Short, lowercase snake_case identifier, e.g. ``"email"``, ``"calendar"``.
    name: str = ""

    # ──────────────────────────────────────────────────────────────────────────
    # 1. auth — Authentication & Configuration
    # ──────────────────────────────────────────────────────────────────────────

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """Return True if all required credentials/env vars are present.

        Must be synchronous and must NOT make network calls.
        """

    @abc.abstractmethod
    async def health_check(self) -> ConnectorHealth:
        """Make a cheap live call to verify connectivity.

        If the connector is not configured, return immediately with
        ``status="not_configured"`` instead of raising.
        """

    # ──────────────────────────────────────────────────────────────────────────
    # 2. fetch — Read Data
    # ──────────────────────────────────────────────────────────────────────────

    async def fetch_messages(
        self,
        limit: int = 20,
        filter_unread: bool = True,
    ) -> list[NormalizedMessage]:
        """Return normalized messages (emails, chat, …).

        Default: raises ConnectorNotImplementedError.
        """
        raise ConnectorNotImplementedError(self.name, "fetch_messages")

    async def fetch_events(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[NormalizedEvent]:
        """Return calendar events in the given time window.

        Default: raises ConnectorNotImplementedError.
        """
        raise ConnectorNotImplementedError(self.name, "fetch_events")

    async def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[NormalizedMessage | NormalizedEvent]:
        """Free-text search over the connector's data.

        Default: raises ConnectorNotImplementedError.
        """
        raise ConnectorNotImplementedError(self.name, "search")

    # ──────────────────────────────────────────────────────────────────────────
    # 3. act — Write / Mutate
    # ──────────────────────────────────────────────────────────────────────────

    async def send_message(self, msg: OutboundMessage) -> ActionResult:
        """Send a message (email, SMS, chat, …).

        Default: raises ConnectorNotImplementedError.
        """
        raise ConnectorNotImplementedError(self.name, "send_message")

    async def create_event(self, event: EventDraft) -> ActionResult:
        """Create a calendar event.

        Default: raises ConnectorNotImplementedError.
        """
        raise ConnectorNotImplementedError(self.name, "create_event")

    async def update_event(self, event_id: str, patch: dict[str, Any]) -> ActionResult:
        """Patch an existing event.

        Default: raises ConnectorNotImplementedError.
        """
        raise ConnectorNotImplementedError(self.name, "update_event")

    async def delete_event(self, event_id: str) -> ActionResult:
        """Delete / cancel an event.

        Default: raises ConnectorNotImplementedError.
        """
        raise ConnectorNotImplementedError(self.name, "delete_event")

    async def mark_read(self, message_id: str) -> ActionResult:
        """Mark a message as read.

        Default: raises ConnectorNotImplementedError.
        """
        raise ConnectorNotImplementedError(self.name, "mark_read")

    # ──────────────────────────────────────────────────────────────────────────
    # 4. webhook_in — Receive Webhooks
    # ──────────────────────────────────────────────────────────────────────────

    def verify_webhook(self, headers: dict[str, str], body_bytes: bytes) -> bool:
        """Verify HMAC / signature of an incoming webhook payload.

        Default: raises ConnectorNotImplementedError.
        """
        raise ConnectorNotImplementedError(self.name, "verify_webhook")

    def parse_webhook(self, headers: dict[str, str], body_bytes: bytes) -> WebhookEvent:
        """Parse a provider payload into a normalized WebhookEvent.

        Default: raises ConnectorNotImplementedError.
        """
        raise ConnectorNotImplementedError(self.name, "parse_webhook")

    # ──────────────────────────────────────────────────────────────────────────
    # 5. webhook_out — Register / Deregister Webhooks
    # ──────────────────────────────────────────────────────────────────────────

    async def register_webhook(self, callback_url: str) -> str:
        """Register a webhook with the provider.

        Returns the provider-assigned webhook ID.
        Default: raises ConnectorNotImplementedError.
        """
        raise ConnectorNotImplementedError(self.name, "register_webhook")

    async def deregister_webhook(self, webhook_id: str) -> bool:
        """Remove a webhook registration.

        Default: raises ConnectorNotImplementedError.
        """
        raise ConnectorNotImplementedError(self.name, "deregister_webhook")

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _not_configured_health(self) -> ConnectorHealth:
        """Return a standard 'not_configured' health response."""
        return ConnectorHealth(
            connector=self.name,
            status="not_configured",
            detail=f"{self.name} connector is not configured (missing credentials).",
        )

    @staticmethod
    def _ms(start: float) -> float:
        """Return elapsed milliseconds since ``start`` (from time.monotonic())."""
        return round((time.monotonic() - start) * 1000, 1)

    def __repr__(self) -> str:
        configured = "configured" if self.is_configured() else "not_configured"
        return f"<{self.__class__.__name__} name={self.name!r} {configured}>"
