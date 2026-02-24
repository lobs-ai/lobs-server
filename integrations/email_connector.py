"""Email connector — Integration Contract v1 adapter.

Wraps the existing ``app.services.email_service.EmailService`` (Gmail API or
IMAP/SMTP fallback) and exposes it through the canonical BaseConnector
interface.

Supported capabilities
  auth       — is_configured, health_check
  fetch      — fetch_messages (unread), search
  act        — send_message, mark_read
  webhook_in — not supported (raises ConnectorNotImplementedError)
  webhook_out— not supported (raises ConnectorNotImplementedError)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from integrations.base_connector import BaseConnector
from integrations.entities import (
    ActionResult,
    ConnectorHealth,
    NormalizedMessage,
    OutboundMessage,
)

logger = logging.getLogger(__name__)


def _parse_date(date_str: str) -> datetime:
    """Parse RFC 2822 / ISO-8601 date strings, falling back to now."""
    if not date_str:
        return datetime.now(timezone.utc)
    try:
        from dateutil.parser import parse as _parse
        return _parse(date_str).replace(tzinfo=timezone.utc) if _parse(date_str).tzinfo is None else _parse(date_str)
    except Exception:
        return datetime.now(timezone.utc)


def _raw_to_normalized(raw: dict, connector: str = "email") -> NormalizedMessage:
    """Convert a raw dict from EmailService into a NormalizedMessage."""
    recipients: list[str] = []
    to_field = raw.get("to", "")
    if to_field:
        recipients = [addr.strip() for addr in to_field.split(",") if addr.strip()]

    return NormalizedMessage(
        id=raw.get("id", ""),
        connector=connector,
        subject=raw.get("subject", ""),
        body=raw.get("body", raw.get("snippet", "")),
        sender=raw.get("from", ""),
        recipients=recipients,
        timestamp=_parse_date(raw.get("date", "")),
        is_unread=raw.get("is_unread", True),
        labels=raw.get("labels", []),
        raw=raw,
    )


class EmailConnector(BaseConnector):
    """Canonical connector for email (Gmail API / IMAP-SMTP fallback).

    The connector is *sessionless* — it creates a fresh EmailService per call
    because the underlying service accepts a DB session (optional for email
    operations).  Pass ``db=None`` when constructing outside of a request
    context; the service will still work for email operations that don't touch
    the DB.
    """

    name = "email"

    def __init__(self, db=None) -> None:
        # Lazy import to avoid circular dependencies and heavy startup cost.
        from app.services.email_service import EmailService

        self._svc = EmailService(db)  # type: ignore[arg-type]

    # ── auth ──────────────────────────────────────────────────────────

    def is_configured(self) -> bool:  # noqa: D102
        return self._svc.is_configured()

    async def health_check(self) -> ConnectorHealth:  # noqa: D102
        if not self.is_configured():
            return self._not_configured_health()

        t0 = time.monotonic()
        try:
            msgs = await self._svc.get_unread(max_results=1)
            latency = self._ms(t0)
            return ConnectorHealth(
                connector=self.name,
                status="ok",
                latency_ms=latency,
                detail=f"Fetched {len(msgs)} message(s) successfully.",
            )
        except Exception as exc:
            return ConnectorHealth(
                connector=self.name,
                status="error",
                latency_ms=self._ms(t0),
                detail=str(exc),
            )

    # ── fetch ─────────────────────────────────────────────────────────

    async def fetch_messages(
        self,
        limit: int = 20,
        filter_unread: bool = True,
    ) -> list[NormalizedMessage]:
        """Return unread emails normalized to NormalizedMessage."""
        raw_list = await self._svc.get_unread(max_results=limit)
        return [_raw_to_normalized(r) for r in raw_list]

    async def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[NormalizedMessage]:
        """Search emails and return normalized results."""
        raw_list = await self._svc.search(query=query, max_results=limit)
        return [_raw_to_normalized(r) for r in raw_list]

    # ── act ───────────────────────────────────────────────────────────

    async def send_message(self, msg: OutboundMessage) -> ActionResult:
        """Send an email.  ``msg.to`` may contain multiple addresses."""
        to = ", ".join(msg.to)
        cc = ", ".join(msg.cc) if msg.cc else ""
        bcc = ", ".join(msg.bcc) if msg.bcc else ""

        result = await self._svc.send(
            to=to,
            subject=msg.subject,
            body=msg.body,
            html=msg.html,
            cc=cc,
            bcc=bcc,
        )
        if result:
            return ActionResult(
                success=True,
                connector=self.name,
                resource_id=result.get("id", ""),
                raw=result,
            )
        return ActionResult(
            success=False,
            connector=self.name,
            detail="Email service returned no result — backend may not be configured.",
        )

    async def mark_read(self, message_id: str) -> ActionResult:
        """Mark an email as read by its provider message ID."""
        ok = await self._svc.mark_read(message_id)
        return ActionResult(
            success=ok,
            connector=self.name,
            resource_id=message_id,
            detail="" if ok else "mark_read returned False — may not be supported by backend.",
        )
