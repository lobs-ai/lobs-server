"""Email integration service (Gmail via API or IMAP/SMTP fallback).

Supports:
  - Reading emails (inbox, unread, search)
  - Sending emails
  - Gmail API (preferred) or IMAP/SMTP (fallback)

Setup (Gmail API — recommended):
  1. Enable Gmail API in Google Cloud Console
  2. Create OAuth2 credentials
  3. Set environment variables:
     - GMAIL_CREDENTIALS_FILE: path to credentials.json
     - GMAIL_TOKEN_FILE: path to token.json (auto-created)

Setup (IMAP/SMTP — fallback):
  1. Set environment variables:
     - EMAIL_IMAP_HOST, EMAIL_IMAP_PORT
     - EMAIL_SMTP_HOST, EMAIL_SMTP_PORT
     - EMAIL_USERNAME, EMAIL_PASSWORD
"""

import base64
import email as email_lib
import logging
import os
import uuid
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Gmail API config
GMAIL_CREDENTIALS_FILE = os.environ.get("GMAIL_CREDENTIALS_FILE", "credentials/gmail.json")
GMAIL_TOKEN_FILE = os.environ.get("GMAIL_TOKEN_FILE", "credentials/gmail_token.json")
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

# IMAP/SMTP fallback config
IMAP_HOST = os.environ.get("EMAIL_IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.environ.get("EMAIL_IMAP_PORT", "993"))
SMTP_HOST = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
EMAIL_USERNAME = os.environ.get("EMAIL_USERNAME", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")


def _get_gmail_service():
    """Build and return an authorized Gmail API service."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        logger.debug("[EMAIL] google-api-python-client not installed")
        return None

    creds = None
    if os.path.exists(GMAIL_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE, GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # No valid token and no way to refresh — skip (interactive OAuth not supported in daemon)
            logger.warning("[EMAIL] Gmail token missing or expired and no refresh token — run manual auth first")
            return None
        os.makedirs(os.path.dirname(GMAIL_TOKEN_FILE) or ".", exist_ok=True)
        with open(GMAIL_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


class EmailService:
    """Read and send emails via Gmail API or IMAP/SMTP."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._gmail = None
        self._mode: str | None = None

    def _detect_mode(self) -> str:
        """Detect which email backend to use."""
        if self._mode:
            return self._mode
        if os.path.exists(GMAIL_CREDENTIALS_FILE) or os.path.exists(GMAIL_TOKEN_FILE):
            self._mode = "gmail_api"
        elif EMAIL_USERNAME and EMAIL_PASSWORD:
            self._mode = "imap_smtp"
        else:
            self._mode = "none"
        return self._mode

    def is_configured(self) -> bool:
        return self._detect_mode() != "none"

    async def get_unread(self, max_results: int = 20) -> list[dict[str, Any]]:
        """Get unread emails."""
        mode = self._detect_mode()
        if mode == "gmail_api":
            return await self._gmail_get_unread(max_results)
        elif mode == "imap_smtp":
            return await self._imap_get_unread(max_results)
        return []

    async def search(self, query: str, max_results: int = 20) -> list[dict[str, Any]]:
        """Search emails."""
        mode = self._detect_mode()
        if mode == "gmail_api":
            return await self._gmail_search(query, max_results)
        return []

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
        cc: str = "",
        bcc: str = "",
    ) -> dict[str, Any] | None:
        """Send an email."""
        mode = self._detect_mode()
        if mode == "gmail_api":
            return await self._gmail_send(to, subject, body, html, cc, bcc)
        elif mode == "imap_smtp":
            return await self._smtp_send(to, subject, body, html, cc, bcc)
        logger.warning("[EMAIL] No email backend configured")
        return None

    async def mark_read(self, message_id: str) -> bool:
        """Mark an email as read."""
        mode = self._detect_mode()
        if mode == "gmail_api":
            return await self._gmail_mark_read(message_id)
        return False

    # ── Gmail API implementations ────────────────────────────────────

    def _get_gmail(self):
        if self._gmail is None:
            self._gmail = _get_gmail_service()
        return self._gmail

    async def _get_gmail_async(self):
        """Get the Gmail service without blocking the event loop."""
        if self._gmail is None:
            import asyncio
            self._gmail = await asyncio.to_thread(_get_gmail_service)
        return self._gmail

    async def _run(self, fn):
        """Run a synchronous Google API call in a thread executor."""
        import asyncio
        return await asyncio.to_thread(fn)

    async def _gmail_get_unread(self, max_results: int) -> list[dict[str, Any]]:
        service = await self._get_gmail_async()
        if not service:
            return []
        try:
            results = await self._run(service.users().messages().list(
                userId="me", q="is:unread", maxResults=max_results
            ).execute)
            messages = results.get("messages", [])
            return [await self._gmail_get_message(m["id"]) for m in messages]
        except Exception as e:
            logger.error("[EMAIL] Gmail list failed: %s", e)
            return []

    async def _gmail_search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        service = await self._get_gmail_async()
        if not service:
            return []
        try:
            results = await self._run(service.users().messages().list(
                userId="me", q=query, maxResults=max_results
            ).execute)
            messages = results.get("messages", [])
            return [await self._gmail_get_message(m["id"]) for m in messages]
        except Exception as e:
            logger.error("[EMAIL] Gmail search failed: %s", e)
            return []

    async def _gmail_get_message(self, msg_id: str) -> dict[str, Any]:
        service = await self._get_gmail_async()
        if not service:
            return {}
        try:
            msg = await self._run(service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute)
            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            body = self._extract_gmail_body(msg.get("payload", {}))
            return {
                "id": msg_id,
                "from": headers.get("from", ""),
                "to": headers.get("to", ""),
                "subject": headers.get("subject", ""),
                "date": headers.get("date", ""),
                "snippet": msg.get("snippet", ""),
                "body": body[:5000],
                "labels": msg.get("labelIds", []),
                "is_unread": "UNREAD" in msg.get("labelIds", []),
            }
        except Exception as e:
            logger.error("[EMAIL] Gmail get message failed: %s", e)
            return {"id": msg_id, "error": str(e)}

    async def _gmail_send(self, to, subject, body, html, cc, bcc) -> dict[str, Any] | None:
        service = await self._get_gmail_async()
        if not service:
            return None
        try:
            if html:
                msg = MIMEMultipart("alternative")
                msg.attach(MIMEText(body, "html"))
            else:
                msg = MIMEText(body)
            msg["to"] = to
            msg["subject"] = subject
            if cc:
                msg["cc"] = cc
            if bcc:
                msg["bcc"] = bcc

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            result = await self._run(service.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute)
            logger.info("[EMAIL] Sent email to %s: %s", to, subject)
            return {"id": result.get("id"), "status": "sent"}
        except Exception as e:
            logger.error("[EMAIL] Gmail send failed: %s", e, exc_info=True)
            return None

    async def _gmail_mark_read(self, msg_id: str) -> bool:
        service = await self._get_gmail_async()
        if not service:
            return False
        try:
            await self._run(service.users().messages().modify(
                userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
            ).execute)
            return True
        except Exception as e:
            logger.error("[EMAIL] Gmail mark read failed: %s", e)
            return False

    @staticmethod
    def _extract_gmail_body(payload: dict) -> str:
        """Extract text body from Gmail message payload."""
        if payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            if part.get("parts"):
                result = EmailService._extract_gmail_body(part)
                if result:
                    return result
        return ""

    # ── IMAP/SMTP fallback implementations ───────────────────────────

    async def _imap_get_unread(self, max_results: int) -> list[dict[str, Any]]:
        import imaplib
        try:
            mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            mail.login(EMAIL_USERNAME, EMAIL_PASSWORD)
            mail.select("inbox")
            _, data = mail.search(None, "UNSEEN")
            ids = data[0].split()[-max_results:] if data[0] else []
            emails = []
            for eid in ids:
                _, msg_data = mail.fetch(eid, "(RFC822)")
                if msg_data[0]:
                    msg = email_lib.message_from_bytes(msg_data[0][1])
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                                break
                    else:
                        body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
                    emails.append({
                        "id": eid.decode(),
                        "from": msg.get("From", ""),
                        "to": msg.get("To", ""),
                        "subject": msg.get("Subject", ""),
                        "date": msg.get("Date", ""),
                        "body": body[:5000],
                        "is_unread": True,
                    })
            mail.logout()
            return emails
        except Exception as e:
            logger.error("[EMAIL] IMAP fetch failed: %s", e)
            return []

    async def _smtp_send(self, to, subject, body, html, cc, bcc) -> dict[str, Any] | None:
        import smtplib
        try:
            msg = MIMEText(body, "html" if html else "plain")
            msg["From"] = EMAIL_USERNAME
            msg["To"] = to
            msg["Subject"] = subject
            if cc:
                msg["Cc"] = cc

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(EMAIL_USERNAME, EMAIL_PASSWORD)
                server.send_message(msg)

            logger.info("[EMAIL] Sent email via SMTP to %s: %s", to, subject)
            return {"status": "sent"}
        except Exception as e:
            logger.error("[EMAIL] SMTP send failed: %s", e, exc_info=True)
            return None
