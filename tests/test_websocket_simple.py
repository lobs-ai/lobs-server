"""Simple WebSocket connection test."""

import pytest
from starlette.testclient import TestClient
from app.main import app
from app.database import get_db
from tests.conftest import get_test_db


def test_websocket_basic_connection(sync_test_token):
    """Test basic WebSocket connection with valid token."""
    # Override the database dependency
    app.dependency_overrides[get_db] = get_test_db
    
    client = TestClient(app)
    token = sync_test_token
    session_key = "test-session"
    
    try:
        with client.websocket_connect(
            f"/api/chat/ws?session_key={session_key}&token={token}"
        ) as websocket:
            # Should receive connection confirmation
            data = websocket.receive_json()
            print(f"Received: {data}")
            assert data["type"] == "connected"
            assert data["session_key"] == session_key
    finally:
        app.dependency_overrides.clear()


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
