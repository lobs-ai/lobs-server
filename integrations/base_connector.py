"""Abstract base class for all Integration Contract v1 connectors.

Every external integration adapter must subclass BaseConnector and implement
the five capability groups defined in integrations/contract_v1.md:

    auth       — is_configured(), health_check()
    fetch      — fetch_messages(), fetch_events(), search()
    act        — send_message(), create_event(), update_event(),
                 delete_event(), mark_read()
    webhook_in — verify_webhook(), parse_webhook()
    webhook_out— register_webhook(), deregister_webhook()

Capabilities the connector does not support should raise
ConnectorNotImplementedError (the default in this base class).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Union

from integrations.entities import (
    ActionResult,
    ConnectorError,
    ConnectorHealth,
    ConnectorNotImplementedError,
    EventDraft,
    NormalizedEvent,
    NormalizedMessage,
    OutboundMessage,
    WebhookEvent,
)


class BaseConnector(ABC):
    """Contract v1 connector base class.

    Subclasses must set ``name`` and implement ``is_configured()`` and
    ``health_check()``.  All other methods may be overridden selectively.
    """

    #: Unique lowercase connector name, e.g. "email", "calendar", "github"
    name: str = ""

    # ------------------------------------------------------------------
    # 1. auth — authentication & configuration
    # ------------------------------------------------------------------

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True iff all required credentials are present.

        Must NOT make network calls.  Used by the integration status
        endpoint to determine whether to even attempt a health check.
        """

    async def health_check(self) -> ConnectorHealth:
        """Make a cheap live call to verify connectivity.

        The default implementation returns a not_configured status if
        is_configured() is False, or ok with 0 ms otherwise.  Subclasses
        should override to perform a real probe.
        """
        if not self.is_configured():
            return ConnectorHealth(
                connector=self.name,
                status="not_configured",
                detail=f"{self.name} connector is not configured",
            )
        return ConnectorHealth(connector=self.name, status="ok")

    # ------------------------------------------------------------------
    # 2. fetch — read data
    # ------------------------------------------------------------------

    async def fetch_messages(
        self,
        limit: int = 20,
        filter_unread: bool = False,
    ) -> list[NormalizedMessage]:
        """Return normalized messages from the provider.

        Args:
            limit: Maximum number of messages to return.
            filter_unread: If True, return only unread messages.
        """
        raise ConnectorNotImplementedError(
            self.name, "fetch_messages is not supported by this connector"
        )

    async def fetch_events(
        self,
        start: datetime,
        end: datetime,
    ) -> list[NormalizedEvent]:
        """Return calendar events in the given half-open time window [start, end)."""
        raise ConnectorNotImplementedError(
            self.name, "fetch_events is not supported by this connector"
        )

    async def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[Union[NormalizedMessage, NormalizedEvent]]:
        """Free-text search over the connector's data."""
        raise ConnectorNotImplementedError(
            self.name, "search is not supported by this connector"
        )

    # ------------------------------------------------------------------
    # 3. act — write / mutate
    # ------------------------------------------------------------------

    async def send_message(self, msg: OutboundMessage) -> ActionResult:
        """Send a message (email, SMS, chat post, …)."""
        raise ConnectorNotImplementedError(
            self.name, "send_message is not supported by this connector"
        )

    async def create_event(self, event: EventDraft) -> ActionResult:
        """Create a calendar event."""
        raise ConnectorNotImplementedError(
            self.name, "create_event is not supported by this connector"
        )

    async def update_event(self, event_id: str, patch: dict) -> ActionResult:
        """Patch an existing calendar event."""
        raise ConnectorNotImplementedError(
            self.name, "update_event is not supported by this connector"
        )

    async def delete_event(self, event_id: str) -> ActionResult:
        """Delete or cancel a calendar event."""
        raise ConnectorNotImplementedError(
            self.name, "delete_event is not supported by this connector"
        )

    async def mark_read(self, message_id: str) -> ActionResult:
        """Mark a message as read."""
        raise ConnectorNotImplementedError(
            self.name, "mark_read is not supported by this connector"
        )

    # ------------------------------------------------------------------
    # 4. webhook_in — receive webhooks
    # ------------------------------------------------------------------

    def verify_webhook(self, headers: dict, body_bytes: bytes) -> bool:
        """Verify the HMAC / signature of an incoming webhook.

        Default implementation always returns False (safe: reject unknown).
        Connectors that support inbound webhooks MUST override this.
        """
        return False

    def parse_webhook(self, headers: dict, body_bytes: bytes) -> WebhookEvent:
        """Parse a provider payload into a normalized WebhookEvent."""
        raise ConnectorNotImplementedError(
            self.name, "parse_webhook is not supported by this connector"
        )

    # ------------------------------------------------------------------
    # 5. webhook_out — register / deregister webhooks
    # ------------------------------------------------------------------

    async def register_webhook(self, callback_url: str) -> str:
        """Register a webhook with the provider.

        Returns the provider-assigned webhook ID.
        """
        raise ConnectorNotImplementedError(
            self.name, "register_webhook is not supported by this connector"
        )

    async def deregister_webhook(self, webhook_id: str) -> bool:
        """Remove the webhook registration."""
        raise ConnectorNotImplementedError(
            self.name, "deregister_webhook is not supported by this connector"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _timed_health(self, start: float, detail: str = "") -> ConnectorHealth:
        """Convenience: build a successful ConnectorHealth with elapsed ms."""
        return ConnectorHealth(
            connector=self.name,
            status="ok",
            latency_ms=round((time.monotonic() - start) * 1000, 1),
            detail=detail,
        )

    def __repr__(self) -> str:
        configured = self.is_configured()
        return f"<{self.__class__.__name__} name={self.name!r} configured={configured}>"
