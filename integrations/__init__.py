"""Integration platform — canonical connector contract.

All external integrations extend BaseConnector from base_connector.py
and exchange data via normalized entities from entities.py.

See integrations/contract_v1.md for the full specification.
"""

from integrations.base_connector import BaseConnector
from integrations.entities import (
    ActionResult,
    ConnectorHealth,
    EventDraft,
    NormalizedContact,
    NormalizedEvent,
    NormalizedMessage,
    NormalizedTask,
    OutboundMessage,
    WebhookEvent,
    # Errors
    IntegrationError,
    ConnectorError,
    ConnectorAuthError,
    ConnectorNotImplementedError,
)

__all__ = [
    "BaseConnector",
    "ActionResult",
    "ConnectorHealth",
    "EventDraft",
    "NormalizedContact",
    "NormalizedEvent",
    "NormalizedMessage",
    "NormalizedTask",
    "OutboundMessage",
    "WebhookEvent",
    "IntegrationError",
    "ConnectorError",
    "ConnectorAuthError",
    "ConnectorNotImplementedError",
]
