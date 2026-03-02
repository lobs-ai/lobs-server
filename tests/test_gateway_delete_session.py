"""Tests for WorkerGateway.delete_session — verifies proper WS challenge-response flow."""
import asyncio
import json
import pytest
from unittest.mock import patch

from app.orchestrator.worker_gateway import WorkerGateway


def _msg(data: dict):
    import aiohttp
    class M:
        pass
    m = M()
    m.type = aiohttp.WSMsgType.TEXT
    m.data = json.dumps(data)
    return m


def _make_ws(messages_fn):
    """Create a fake WS that yields messages from messages_fn(ws)."""
    class FakeWS:
        def __init__(self):
            self._sent = []
        async def send_json(self, data):
            self._sent.append(data)
        def __aiter__(self):
            return messages_fn(self)
    return FakeWS()


class _CM:
    def __init__(self, ws):
        self._ws = ws
    async def __aenter__(self): return self._ws
    async def __aexit__(self, *a): pass


class _Client:
    def __init__(self, ws):
        self._ws = ws
    def ws_connect(self, *a, **kw): return _CM(self._ws)
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass


@pytest.mark.asyncio
async def test_delete_session_success():
    """Happy path: challenge → connect OK → sessions.delete OK."""
    ids = {}

    class FakeWS:
        def __init__(self): self._sent = []
        async def send_json(self, data):
            self._sent.append(data)
            if data.get("method") == "connect":
                ids["connect"] = data["id"]
            elif data.get("method") == "sessions.delete":
                ids["delete"] = data["id"]

        def __aiter__(self): return self._gen()
        async def _gen(self):
            yield _msg({"type": "event", "event": "connect.challenge", "payload": {"nonce": "abc", "ts": 1}})
            for _ in range(100):
                if "connect" in ids: break
                await asyncio.sleep(0.01)
            yield _msg({"type": "res", "id": ids["connect"], "ok": True, "payload": {}})
            for _ in range(100):
                if "delete" in ids: break
                await asyncio.sleep(0.01)
            yield _msg({"type": "res", "id": ids["delete"], "ok": True, "payload": {}})

    ws = FakeWS()
    with patch("app.orchestrator.worker_gateway.aiohttp.ClientSession", return_value=_Client(ws)):
        result = await WorkerGateway(None).delete_session("test-key")
    assert result is True


@pytest.mark.asyncio
async def test_delete_session_not_found_is_success():
    """sessions.delete not_found should be treated as success."""
    ids = {}

    class FakeWS:
        async def send_json(self, data):
            if data.get("method") == "connect": ids["connect"] = data["id"]
            elif data.get("method") == "sessions.delete": ids["delete"] = data["id"]
        def __aiter__(self): return self._gen()
        async def _gen(self):
            yield _msg({"type": "event", "event": "connect.challenge", "payload": {"nonce": "n", "ts": 1}})
            for _ in range(100):
                if "connect" in ids: break
                await asyncio.sleep(0.01)
            yield _msg({"type": "res", "id": ids["connect"], "ok": True, "payload": {}})
            for _ in range(100):
                if "delete" in ids: break
                await asyncio.sleep(0.01)
            yield _msg({"type": "res", "id": ids["delete"], "ok": False, "error": {"type": "not_found", "message": "session not found"}})

    with patch("app.orchestrator.worker_gateway.aiohttp.ClientSession", return_value=_Client(FakeWS())):
        result = await WorkerGateway(None).delete_session("gone-key")
    assert result is True


@pytest.mark.asyncio
async def test_delete_session_auth_failure():
    """Auth failure returns False."""
    ids = {}

    class FakeWS:
        async def send_json(self, data):
            if data.get("method") == "connect": ids["connect"] = data["id"]
        def __aiter__(self): return self._gen()
        async def _gen(self):
            yield _msg({"type": "event", "event": "connect.challenge", "payload": {"nonce": "n", "ts": 1}})
            for _ in range(100):
                if "connect" in ids: break
                await asyncio.sleep(0.01)
            yield _msg({"type": "res", "id": ids["connect"], "ok": False, "error": {"code": "UNAUTHORIZED", "message": "bad token"}})

    with patch("app.orchestrator.worker_gateway.aiohttp.ClientSession", return_value=_Client(FakeWS())):
        result = await WorkerGateway(None).delete_session("some-key")
    assert result is False


@pytest.mark.asyncio
async def test_delete_session_network_error_returns_false():
    """Network exception returns False."""
    class BadClient:
        def ws_connect(self, *a, **kw): raise ConnectionError("network down")
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    with patch("app.orchestrator.worker_gateway.aiohttp.ClientSession", return_value=BadClient()):
        result = await WorkerGateway(None).delete_session("any-key")
    assert result is False
