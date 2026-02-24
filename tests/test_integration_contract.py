"""Contract tests for Integration Contract v1.

These tests verify that every registered connector:
  1. Subclasses BaseConnector
  2. Sets a non-empty ``name``
  3. Implements is_configured() without network calls
  4. Implements health_check() (may be mocked)
  5. Returns the correct normalized entity types
  6. Raises ConnectorNotImplementedError for unsupported capabilities
  7. Never leaks raw provider objects (all returns are normalized entities)

To add a new connector to the contract suite, append it to ALL_CONNECTORS.

Run:
    python -m pytest tests/test_integration_contract.py -v
"""

from __future__ import annotations

import asyncio
from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone, timedelta
from typing import Type
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Contract entities
# ---------------------------------------------------------------------------

from integrations.entities import (
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
from integrations.base_connector import BaseConnector


# ---------------------------------------------------------------------------
# Helper: minimal concrete connector for testing base class behaviour
# ---------------------------------------------------------------------------


class _MinimalConnector(BaseConnector):
    """Minimal concrete implementation — only satisfies ABC requirements."""

    name = "test_minimal"

    def __init__(self, configured: bool = True) -> None:
        self._configured = configured

    def is_configured(self) -> bool:
        return self._configured


# ---------------------------------------------------------------------------
# Connector registry — add new connectors here
# ---------------------------------------------------------------------------

def _make_email_connector():
    """Build an EmailConnector with a mocked underlying EmailService."""
    from integrations.email_connector import EmailConnector
    db = MagicMock()
    connector = EmailConnector(db)
    # Inject a mocked service so no real credentials are needed
    mock_svc = MagicMock()
    mock_svc.is_configured.return_value = True
    mock_svc._detect_mode.return_value = "gmail_api"
    mock_svc.get_unread = AsyncMock(return_value=[{
        "id": "msg-1",
        "from": "Alice <alice@example.com>",
        "to": "bob@example.com",
        "subject": "Hello",
        "date": "Mon, 24 Feb 2026 12:00:00 +0000",
        "body": "Hi there",
        "snippet": "Hi there",
        "labels": ["INBOX"],
        "is_unread": True,
    }])
    mock_svc.search = AsyncMock(return_value=[{
        "id": "msg-2",
        "from": "Carol <carol@example.com>",
        "to": "bob@example.com",
        "subject": "Search result",
        "date": "Mon, 24 Feb 2026 09:00:00 +0000",
        "body": "Found it",
        "snippet": "Found it",
        "labels": [],
        "is_unread": False,
    }])
    mock_svc.send = AsyncMock(return_value={"id": "sent-1", "status": "sent"})
    mock_svc.mark_read = AsyncMock(return_value=True)
    connector._svc = mock_svc
    return connector


def _make_calendar_connector():
    """Build a CalendarConnector with a mocked underlying GoogleCalendarService."""
    from integrations.calendar_connector import CalendarConnector
    db = MagicMock()
    connector = CalendarConnector(db)
    mock_svc = MagicMock()
    mock_svc.is_configured.return_value = True
    _event_raw = {
        "gcal_id": "evt-1",
        "title": "Team Standup",
        "description": "Daily sync",
        "start": "2026-02-24T09:00:00+00:00",
        "end": "2026-02-24T09:30:00+00:00",
        "attendees": ["alice@example.com"],
        "location": "Zoom",
        "all_day": False,
        "status": "confirmed",
    }
    mock_svc.get_lobs_events = AsyncMock(return_value=[_event_raw])
    mock_svc.create_event = AsyncMock(return_value={**_event_raw, "gcal_id": "evt-new"})
    mock_svc.update_event = AsyncMock(return_value={**_event_raw, "gcal_id": "evt-1", "title": "Updated"})
    mock_svc.delete_event = AsyncMock(return_value=True)
    connector._svc = mock_svc
    return connector


# Each entry: (connector_instance, supported_capabilities)
# Capabilities: "fetch_messages", "fetch_events", "search", "send_message",
#               "create_event", "update_event", "delete_event", "mark_read",
#               "webhook_in", "webhook_out"

ALL_CONNECTORS = [
    (
        _make_email_connector,
        {"fetch_messages", "search", "send_message", "mark_read"},
    ),
    (
        _make_calendar_connector,
        {"fetch_events", "create_event", "update_event", "delete_event"},
    ),
]

# ---------------------------------------------------------------------------
# Entity tests
# ---------------------------------------------------------------------------


class TestNormalizedEntities:
    """All normalized entity dataclasses must be constructable with required args."""

    def test_normalized_message(self):
        msg = NormalizedMessage(
            id="1",
            connector="email",
            subject="Hello",
            body="Body text",
            sender="alice@example.com",
            recipients=["bob@example.com"],
            timestamp=datetime.now(tz=timezone.utc),
        )
        assert msg.id == "1"
        assert msg.connector == "email"
        assert msg.is_unread is True
        assert msg.labels == []
        assert msg.raw == {}

    def test_normalized_event(self):
        now = datetime.now(tz=timezone.utc)
        ev = NormalizedEvent(
            id="evt-1",
            connector="calendar",
            title="Meeting",
            description="Sync",
            start=now,
            end=now + timedelta(hours=1),
            attendees=["alice@example.com"],
        )
        assert ev.status == "confirmed"
        assert ev.is_all_day is False
        assert ev.location == ""

    def test_normalized_task(self):
        task = NormalizedTask(
            id="task-1",
            connector="github",
            title="Fix bug",
            description="Details",
            due=None,
        )
        assert task.status == "open"
        assert task.priority == "normal"

    def test_normalized_contact(self):
        c = NormalizedContact(id="c-1", connector="crm", name="Alice")
        assert c.email == ""
        assert c.phone == ""

    def test_action_result_success(self):
        r = ActionResult(success=True, connector="email", resource_id="123")
        assert r.success is True
        assert r.detail == ""

    def test_action_result_failure(self):
        r = ActionResult(success=False, connector="calendar", detail="Not found")
        assert r.success is False

    def test_connector_health_ok(self):
        h = ConnectorHealth(connector="email", status="ok", latency_ms=12.3)
        assert h.status == "ok"

    def test_connector_health_not_configured(self):
        h = ConnectorHealth(connector="email", status="not_configured")
        assert h.latency_ms == 0.0

    def test_outbound_message(self):
        m = OutboundMessage(to=["alice@example.com"], subject="Hi", body="Hello")
        assert m.html is False
        assert m.cc == []

    def test_event_draft(self):
        now = datetime.now(tz=timezone.utc)
        draft = EventDraft(title="Lunch", start=now, end=now + timedelta(hours=1))
        assert draft.description == ""
        assert draft.attendees == []

    def test_webhook_event(self):
        we = WebhookEvent(
            connector="email",
            event_type="message.received",
            resource_id="msg-99",
            payload={"subject": "Test"},
        )
        assert we.raw == {}

    def test_error_hierarchy(self):
        err = ConnectorError("email", "rate limited")
        assert isinstance(err, IntegrationError)
        assert "email" in str(err)
        assert "rate limited" in str(err)

        auth_err = ConnectorAuthError("calendar", "token expired")
        assert isinstance(auth_err, IntegrationError)

        ni_err = ConnectorNotImplementedError("github", "not supported")
        assert isinstance(ni_err, IntegrationError)


# ---------------------------------------------------------------------------
# BaseConnector tests
# ---------------------------------------------------------------------------


class TestBaseConnector:
    """Verify BaseConnector raises ConnectorNotImplementedError for all defaults."""

    @pytest.fixture
    def connector(self):
        return _MinimalConnector(configured=True)

    @pytest.fixture
    def unconfigured(self):
        return _MinimalConnector(configured=False)

    def test_name_set(self, connector):
        assert connector.name == "test_minimal"

    def test_is_configured_true(self, connector):
        assert connector.is_configured() is True

    def test_is_configured_false(self, unconfigured):
        assert unconfigured.is_configured() is False

    @pytest.mark.asyncio
    async def test_health_check_ok(self, connector):
        health = await connector.health_check()
        assert isinstance(health, ConnectorHealth)
        assert health.status == "ok"
        assert health.connector == "test_minimal"

    @pytest.mark.asyncio
    async def test_health_check_not_configured(self, unconfigured):
        health = await unconfigured.health_check()
        assert health.status == "not_configured"

    @pytest.mark.asyncio
    async def test_fetch_messages_raises(self, connector):
        with pytest.raises(ConnectorNotImplementedError):
            await connector.fetch_messages()

    @pytest.mark.asyncio
    async def test_fetch_events_raises(self, connector):
        now = datetime.now(tz=timezone.utc)
        with pytest.raises(ConnectorNotImplementedError):
            await connector.fetch_events(now, now + timedelta(days=1))

    @pytest.mark.asyncio
    async def test_search_raises(self, connector):
        with pytest.raises(ConnectorNotImplementedError):
            await connector.search("query")

    @pytest.mark.asyncio
    async def test_send_message_raises(self, connector):
        msg = OutboundMessage(to=["a@b.com"], subject="s", body="b")
        with pytest.raises(ConnectorNotImplementedError):
            await connector.send_message(msg)

    @pytest.mark.asyncio
    async def test_create_event_raises(self, connector):
        now = datetime.now(tz=timezone.utc)
        draft = EventDraft(title="x", start=now, end=now + timedelta(hours=1))
        with pytest.raises(ConnectorNotImplementedError):
            await connector.create_event(draft)

    @pytest.mark.asyncio
    async def test_update_event_raises(self, connector):
        with pytest.raises(ConnectorNotImplementedError):
            await connector.update_event("evt-1", {"title": "New"})

    @pytest.mark.asyncio
    async def test_delete_event_raises(self, connector):
        with pytest.raises(ConnectorNotImplementedError):
            await connector.delete_event("evt-1")

    @pytest.mark.asyncio
    async def test_mark_read_raises(self, connector):
        with pytest.raises(ConnectorNotImplementedError):
            await connector.mark_read("msg-1")

    def test_verify_webhook_default_false(self, connector):
        assert connector.verify_webhook({}, b"{}") is False

    def test_parse_webhook_raises(self, connector):
        with pytest.raises(ConnectorNotImplementedError):
            connector.parse_webhook({}, b"{}")

    @pytest.mark.asyncio
    async def test_register_webhook_raises(self, connector):
        with pytest.raises(ConnectorNotImplementedError):
            await connector.register_webhook("https://example.com/hook")

    @pytest.mark.asyncio
    async def test_deregister_webhook_raises(self, connector):
        with pytest.raises(ConnectorNotImplementedError):
            await connector.deregister_webhook("hook-id")

    def test_repr(self, connector):
        r = repr(connector)
        assert "test_minimal" in r
        assert "configured=True" in r


# ---------------------------------------------------------------------------
# Per-connector contract tests (parametrized over ALL_CONNECTORS)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("factory,supported", ALL_CONNECTORS)
class TestConnectorContract:
    """Run the contract suite against every registered connector."""

    # ---- auth ----

    def test_is_subclass_of_base_connector(self, factory, supported):
        connector = factory()
        assert isinstance(connector, BaseConnector), (
            f"{type(connector).__name__} must subclass BaseConnector"
        )

    def test_name_not_empty(self, factory, supported):
        connector = factory()
        assert connector.name, f"{type(connector).__name__}.name must be a non-empty string"
        assert isinstance(connector.name, str)

    def test_is_configured_returns_bool(self, factory, supported):
        connector = factory()
        result = connector.is_configured()
        assert isinstance(result, bool), (
            f"{connector.name}.is_configured() must return bool, got {type(result)}"
        )

    @pytest.mark.asyncio
    async def test_health_check_returns_connector_health(self, factory, supported):
        connector = factory()
        health = await connector.health_check()
        assert isinstance(health, ConnectorHealth), (
            f"{connector.name}.health_check() must return ConnectorHealth"
        )
        assert health.connector == connector.name, (
            "ConnectorHealth.connector must match connector.name"
        )
        assert health.status in ("ok", "degraded", "error", "not_configured"), (
            f"Invalid health status: {health.status!r}"
        )

    # ---- fetch_messages ----

    @pytest.mark.asyncio
    async def test_fetch_messages_supported(self, factory, supported):
        connector = factory()
        if "fetch_messages" not in supported:
            with pytest.raises(ConnectorNotImplementedError):
                await connector.fetch_messages()
            return
        result = await connector.fetch_messages()
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, NormalizedMessage), (
                f"{connector.name}.fetch_messages() must return list[NormalizedMessage]"
            )
            assert item.connector == connector.name
            assert isinstance(item.timestamp, datetime)

    @pytest.mark.asyncio
    async def test_fetch_messages_filter_unread(self, factory, supported):
        connector = factory()
        if "fetch_messages" not in supported:
            pytest.skip("fetch_messages not supported")
        result = await connector.fetch_messages(limit=5, filter_unread=True)
        assert isinstance(result, list)

    # ---- fetch_events ----

    @pytest.mark.asyncio
    async def test_fetch_events_supported(self, factory, supported):
        connector = factory()
        now = datetime.now(tz=timezone.utc)
        if "fetch_events" not in supported:
            with pytest.raises(ConnectorNotImplementedError):
                await connector.fetch_events(now, now + timedelta(days=7))
            return
        result = await connector.fetch_events(now, now + timedelta(days=7))
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, NormalizedEvent), (
                f"{connector.name}.fetch_events() must return list[NormalizedEvent]"
            )
            assert item.connector == connector.name
            assert isinstance(item.start, datetime)
            assert isinstance(item.end, datetime)
            assert item.status in ("confirmed", "tentative", "cancelled")

    # ---- search ----

    @pytest.mark.asyncio
    async def test_search_supported(self, factory, supported):
        connector = factory()
        if "search" not in supported:
            with pytest.raises(ConnectorNotImplementedError):
                await connector.search("test query")
            return
        result = await connector.search("test query")
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, (NormalizedMessage, NormalizedEvent)), (
                f"{connector.name}.search() must return list[NormalizedMessage | NormalizedEvent]"
            )

    # ---- send_message ----

    @pytest.mark.asyncio
    async def test_send_message_supported(self, factory, supported):
        connector = factory()
        msg = OutboundMessage(
            to=["alice@example.com"],
            subject="Test",
            body="Hello from contract tests",
        )
        if "send_message" not in supported:
            with pytest.raises(ConnectorNotImplementedError):
                await connector.send_message(msg)
            return
        result = await connector.send_message(msg)
        assert isinstance(result, ActionResult), (
            f"{connector.name}.send_message() must return ActionResult"
        )
        assert result.connector == connector.name
        assert isinstance(result.success, bool)

    # ---- create_event ----

    @pytest.mark.asyncio
    async def test_create_event_supported(self, factory, supported):
        connector = factory()
        now = datetime.now(tz=timezone.utc)
        draft = EventDraft(
            title="Contract Test Event",
            start=now + timedelta(hours=1),
            end=now + timedelta(hours=2),
            description="Automated contract test",
        )
        if "create_event" not in supported:
            with pytest.raises(ConnectorNotImplementedError):
                await connector.create_event(draft)
            return
        result = await connector.create_event(draft)
        assert isinstance(result, ActionResult)
        assert result.connector == connector.name
        assert isinstance(result.success, bool)

    # ---- update_event ----

    @pytest.mark.asyncio
    async def test_update_event_supported(self, factory, supported):
        connector = factory()
        if "update_event" not in supported:
            with pytest.raises(ConnectorNotImplementedError):
                await connector.update_event("evt-1", {"title": "New title"})
            return
        result = await connector.update_event("evt-1", {"title": "New title"})
        assert isinstance(result, ActionResult)
        assert result.connector == connector.name

    # ---- delete_event ----

    @pytest.mark.asyncio
    async def test_delete_event_supported(self, factory, supported):
        connector = factory()
        if "delete_event" not in supported:
            with pytest.raises(ConnectorNotImplementedError):
                await connector.delete_event("evt-1")
            return
        result = await connector.delete_event("evt-1")
        assert isinstance(result, ActionResult)
        assert result.connector == connector.name

    # ---- mark_read ----

    @pytest.mark.asyncio
    async def test_mark_read_supported(self, factory, supported):
        connector = factory()
        if "mark_read" not in supported:
            with pytest.raises(ConnectorNotImplementedError):
                await connector.mark_read("msg-1")
            return
        result = await connector.mark_read("msg-1")
        assert isinstance(result, ActionResult)
        assert result.connector == connector.name

    # ---- webhook_in ----

    def test_verify_webhook_returns_bool(self, factory, supported):
        connector = factory()
        result = connector.verify_webhook({}, b"{}")
        assert isinstance(result, bool), (
            f"{connector.name}.verify_webhook() must return bool"
        )

    # ---- unsupported capabilities must raise ConnectorNotImplementedError ----

    @pytest.mark.asyncio
    async def test_unsupported_register_webhook_raises(self, factory, supported):
        connector = factory()
        if "webhook_out" not in supported:
            with pytest.raises(ConnectorNotImplementedError):
                await connector.register_webhook("https://example.com/hook")

    @pytest.mark.asyncio
    async def test_unsupported_deregister_webhook_raises(self, factory, supported):
        connector = factory()
        if "webhook_out" not in supported:
            with pytest.raises(ConnectorNotImplementedError):
                await connector.deregister_webhook("hook-id")


# ---------------------------------------------------------------------------
# Email connector unit tests
# ---------------------------------------------------------------------------


class TestEmailConnector:
    """Focused tests for the EmailConnector beyond the generic contract."""

    @pytest.fixture
    def connector(self):
        return _make_email_connector()

    def test_name(self, connector):
        assert connector.name == "email"

    def test_is_configured(self, connector):
        assert connector.is_configured() is True

    @pytest.mark.asyncio
    async def test_health_check_ok(self, connector):
        h = await connector.health_check()
        assert h.status == "ok"
        assert h.connector == "email"

    @pytest.mark.asyncio
    async def test_health_check_not_configured(self):
        from integrations.email_connector import EmailConnector
        db = MagicMock()
        connector = EmailConnector(db)
        mock_svc = MagicMock()
        mock_svc.is_configured.return_value = False
        connector._svc = mock_svc
        h = await connector.health_check()
        assert h.status == "not_configured"

    @pytest.mark.asyncio
    async def test_fetch_messages_returns_normalized(self, connector):
        msgs = await connector.fetch_messages(limit=10)
        assert len(msgs) == 1
        msg = msgs[0]
        assert isinstance(msg, NormalizedMessage)
        assert msg.id == "msg-1"
        assert msg.connector == "email"
        assert msg.subject == "Hello"
        assert msg.sender == "Alice <alice@example.com>"
        assert isinstance(msg.timestamp, datetime)

    @pytest.mark.asyncio
    async def test_search_returns_normalized(self, connector):
        results = await connector.search("found")
        assert len(results) == 1
        assert isinstance(results[0], NormalizedMessage)
        assert results[0].id == "msg-2"
        assert results[0].is_unread is False

    @pytest.mark.asyncio
    async def test_send_message_success(self, connector):
        msg = OutboundMessage(to=["bob@example.com"], subject="Hi", body="Hello")
        result = await connector.send_message(msg)
        assert result.success is True
        assert result.resource_id == "sent-1"
        assert result.connector == "email"

    @pytest.mark.asyncio
    async def test_send_message_failure_returns_action_result(self):
        from integrations.email_connector import EmailConnector
        db = MagicMock()
        connector = EmailConnector(db)
        mock_svc = MagicMock()
        mock_svc.is_configured.return_value = True
        mock_svc.send = AsyncMock(return_value=None)
        connector._svc = mock_svc
        msg = OutboundMessage(to=["bob@example.com"], subject="Hi", body="Hello")
        result = await connector.send_message(msg)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_mark_read_success(self, connector):
        result = await connector.mark_read("msg-1")
        assert result.success is True
        assert result.resource_id == "msg-1"

    @pytest.mark.asyncio
    async def test_mark_read_failure(self):
        from integrations.email_connector import EmailConnector
        db = MagicMock()
        connector = EmailConnector(db)
        mock_svc = MagicMock()
        mock_svc.mark_read = AsyncMock(return_value=False)
        mock_svc.is_configured.return_value = True
        connector._svc = mock_svc
        result = await connector.mark_read("msg-1")
        assert result.success is False

    def test_verify_webhook_returns_false(self, connector):
        assert connector.verify_webhook({}, b"{}") is False

    def test_fetch_events_raises(self, connector):
        now = datetime.now(tz=timezone.utc)
        with pytest.raises(ConnectorNotImplementedError):
            asyncio.get_event_loop().run_until_complete(
                connector.fetch_events(now, now + timedelta(days=1))
            )


# ---------------------------------------------------------------------------
# Calendar connector unit tests
# ---------------------------------------------------------------------------


class TestCalendarConnector:
    """Focused tests for the CalendarConnector beyond the generic contract."""

    @pytest.fixture
    def connector(self):
        return _make_calendar_connector()

    def test_name(self, connector):
        assert connector.name == "calendar"

    def test_is_configured(self, connector):
        assert connector.is_configured() is True

    @pytest.mark.asyncio
    async def test_health_check_ok(self, connector):
        h = await connector.health_check()
        assert h.status == "ok"
        assert h.connector == "calendar"

    @pytest.mark.asyncio
    async def test_health_check_not_configured(self):
        from integrations.calendar_connector import CalendarConnector
        db = MagicMock()
        connector = CalendarConnector(db)
        mock_svc = MagicMock()
        mock_svc.is_configured.return_value = False
        connector._svc = mock_svc
        h = await connector.health_check()
        assert h.status == "not_configured"

    @pytest.mark.asyncio
    async def test_fetch_events_returns_normalized(self, connector):
        now = datetime.now(tz=timezone.utc)
        events = await connector.fetch_events(now, now + timedelta(days=7))
        assert len(events) == 1
        ev = events[0]
        assert isinstance(ev, NormalizedEvent)
        assert ev.id == "evt-1"
        assert ev.connector == "calendar"
        assert ev.title == "Team Standup"
        assert isinstance(ev.start, datetime)
        assert isinstance(ev.end, datetime)
        assert ev.status == "confirmed"

    @pytest.mark.asyncio
    async def test_create_event_success(self, connector):
        now = datetime.now(tz=timezone.utc)
        draft = EventDraft(
            title="New Meeting",
            start=now + timedelta(hours=1),
            end=now + timedelta(hours=2),
        )
        result = await connector.create_event(draft)
        assert isinstance(result, ActionResult)
        assert result.success is True
        assert result.resource_id == "evt-new"

    @pytest.mark.asyncio
    async def test_create_event_failure_returns_action_result(self):
        from integrations.calendar_connector import CalendarConnector
        db = MagicMock()
        connector = CalendarConnector(db)
        mock_svc = MagicMock()
        mock_svc.is_configured.return_value = True
        mock_svc.create_event = AsyncMock(return_value=None)
        connector._svc = mock_svc
        now = datetime.now(tz=timezone.utc)
        draft = EventDraft(title="x", start=now, end=now + timedelta(hours=1))
        result = await connector.create_event(draft)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_update_event_success(self, connector):
        result = await connector.update_event("evt-1", {"title": "Updated"})
        assert result.success is True
        assert result.connector == "calendar"

    @pytest.mark.asyncio
    async def test_delete_event_success(self, connector):
        result = await connector.delete_event("evt-1")
        assert result.success is True
        assert result.resource_id == "evt-1"

    @pytest.mark.asyncio
    async def test_delete_event_failure(self):
        from integrations.calendar_connector import CalendarConnector
        db = MagicMock()
        connector = CalendarConnector(db)
        mock_svc = MagicMock()
        mock_svc.is_configured.return_value = True
        mock_svc.delete_event = AsyncMock(return_value=False)
        connector._svc = mock_svc
        result = await connector.delete_event("evt-missing")
        assert result.success is False

    def test_fetch_messages_raises(self, connector):
        with pytest.raises(ConnectorNotImplementedError):
            asyncio.get_event_loop().run_until_complete(connector.fetch_messages())

    def test_send_message_raises(self, connector):
        msg = OutboundMessage(to=["a@b.com"], subject="s", body="b")
        with pytest.raises(ConnectorNotImplementedError):
            asyncio.get_event_loop().run_until_complete(connector.send_message(msg))
