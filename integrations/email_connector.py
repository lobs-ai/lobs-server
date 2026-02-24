"""Email connector — Integration Contract v1 adapter.

Wraps ``app.services.email_service.EmailService`` and translates its raw
dicts into normalized contract entities.  Supports Gmail API and IMAP/SMTP
fallback (whichever the underlying service detects).

Supported capabilities:
  ✅ auth         — is_configured(), health_check()
  ✅ fetch        — fetch_messages(), search()
  ❌ fetch_events — not applicable (raises ConnectorNotImplementedError)
  ✅ act          — send_message(), mark_read()
  ⚠️  webhook_in  — verify_webhook stub (provider-specific HMAC not set up)
  ❌ webhook_out  — Gmail push setup not yet implemented
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Union

from sqlalchemy.ext.asyncio import AsyncSession

from integrations.base_connector import BaseConnector
from integrations.entities import (
    ActionResult,
    ConnectorError,
    ConnectorHealth,
    NormalizedMessage,
    OutboundMessage,
)

logger = logging.getLogger(__name__)


def _parse_date(date_str: str) -> datetime:
    """Best-effort parse of RFC 2822 / ISO date strings."""
    if not date_str:
        return datetime.now(tz=timezone.utc)
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(date_str)
    except Exception:
        return datetime.now(tz=timezone.utc)


def _normalize(raw: dict, connector_name: str) -> NormalizedMessage:
    """Convert an EmailService raw dict into a NormalizedMessage."""
    return NormalizedMessage(
        id=str(raw.get("id", "")),
        connector=connector_name,
        subject=raw.get("subject", ""),
        body=raw.get("body", raw.get("snippet", "")),
        sender=raw.get("from", ""),
        recipients=[r.strip() for r in raw.get("to", "").split(",") if r.strip()],
        timestamp=_parse_date(raw.get("date", "")),
        is_unread=raw.get("is_unread", True),
        labels=raw.get("labels", []),
        raw=raw,
    )


class EmailConnector(BaseConnector):
    """Contract v1 adapter for the email integration."""

    name = "email"

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._svc: object | None = None  # lazy-loaded EmailService

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _service(self):
        """Return (cached) EmailService instance."""
        if self._svc is None:
            from app.services.email_service import EmailService  # avoid circular import
            self._svc = EmailService(self._db)
        return self._svc

    # ------------------------------------------------------------------
    # 1. auth
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        return self._service().is_configured()

    async def health_check(self) -> ConnectorHealth:
        if not self.is_configured():
            return ConnectorHealth(
                connector=self.name,
                status="not_configured",
                detail="Email service is not configured — set GMAIL_CREDENTIALS_FILE or IMAP credentials",
            )
        start = time.monotonic()
        try:
            # Fetch at most 1 message as a connectivity probe.
            await self._service().get_unread(max_results=1)
            return self._timed_health(start, f"mode={self._service()._detect_mode()}")
        except Exception as exc:
            return ConnectorHealth(
                connector=self.name,
                status="error",
                detail=str(exc),
            )

    # ------------------------------------------------------------------
    # 2. fetch
    # ------------------------------------------------------------------

    async def fetch_messages(
        self,
        limit: int = 20,
        filter_unread: bool = False,
    ) -> list[NormalizedMessage]:
        try:
            if filter_unread:
                raws = await self._service().get_unread(max_results=limit)
            else:
                raws = await self._service().get_unread(max_results=limit)
            return [_normalize(r, self.name) for r in raws if r]
        except Exception as exc:
            logger.error("[email_connector] fetch_messages failed: %s", exc)
            raise ConnectorError(self.name, str(exc)) from exc

    async def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[NormalizedMessage]:
        try:
            raws = await self._service().search(query=query, max_results=limit)
            return [_normalize(r, self.name) for r in raws if r]
        except Exception as exc:
            logger.error("[email_connector] search failed: %s", exc)
            raise ConnectorError(self.name, str(exc)) from exc

    # ------------------------------------------------------------------
    # 3. act
    # ------------------------------------------------------------------

    async def send_message(self, msg: OutboundMessage) -> ActionResult:
        try:
            result = await self._service().send(
                to=", ".join(msg.to),
                subject=msg.subject,
                body=msg.body,
                html=msg.html,
                cc=", ".join(msg.cc),
                bcc=", ".join(msg.bcc),
            )
            if result:
                return ActionResult(
                    success=True,
                    connector=self.name,
                    resource_id=str(result.get("id", "")),
                    detail=result.get("status", "sent"),
                    raw=result,
                )
            return ActionResult(
                success=False,
                connector=self.name,
                detail="send returned None — check email service configuration",
            )
        except Exception as exc:
            logger.error("[email_connector] send_message failed: %s", exc)
            raise ConnectorError(self.name, str(exc)) from exc

    async def mark_read(self, message_id: str) -> ActionResult:
        try:
            ok = await self._service().mark_read(message_id)
            return ActionResult(
                success=ok,
                connector=self.name,
                resource_id=message_id,
                detail="marked read" if ok else "mark_read returned False",
            )
        except Exception as exc:
            logger.error("[email_connector] mark_read failed: %s", exc)
            raise ConnectorError(self.name, str(exc)) from exc

    # ------------------------------------------------------------------
    # 4. webhook_in (stub — Gmail push webhooks require Cloud Pub/Sub setup)
    # ------------------------------------------------------------------

    def verify_webhook(self, headers: dict, body_bytes: bytes) -> bool:
        # Gmail push notifications are authenticated via Google-signed JWT.
        # Full verification not yet implemented; return False to be safe.
        logger.debug("[email_connector] verify_webhook called — not fully implemented")
        return False
