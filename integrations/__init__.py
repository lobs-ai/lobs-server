"""Integration Contract v1 — connector package.

Usage:
    from integrations.email_connector import EmailConnector
    from integrations.calendar_connector import CalendarConnector
    from integrations.entities import NormalizedMessage, ActionResult, ...

See integrations/contract_v1.md for the full specification.
"""

from integrations.entities import (  # noqa: F401 — re-export for convenience
    ActionResult,
    ConnectorAuthError,
    ConnectorError,
    ConnectorHealth,
    ConnectorNotImplementedError,
    EventDraft,
    IntegrationError,
    NormalizedContact,
    NormalizedEvent,
    NormalizedMessage,
    NormalizedTask,
    OutboundMessage,
    WebhookEvent,
)
from integrations.base_connector import BaseConnector  # noqa: F401
