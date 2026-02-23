"""WebSocket reconnection tests for chat endpoint."""

import pytest
import pytest_asyncio
import json
from httpx import AsyncClient, ASGITransport
from starlette.testclient import TestClient
from app.main import app
from app.database import get_db
from tests.conftest import get_test_db


@pytest.mark.asyncio
class TestWebSocketReconnection:
    """Test WebSocket reconnection scenarios using synchronous TestClient."""
    
    # ============================================================================
    # AUTH TOKEN VALIDATION
    # ============================================================================
    
    def test_connection_with_valid_token(self, sync_client_with_token):
        """Test that connection succeeds with valid auth token."""
        token = sync_client_with_token.test_token
        session_key = "auth-valid-test"
        
        with sync_client_with_token.websocket_connect(
            f"/api/chat/ws?session_key={session_key}&token={token}"
        ) as websocket:
            # Should receive connection confirmation
            data = websocket.receive_json()
            assert data["type"] == "connected"
            assert data["session_key"] == session_key
    
    def test_connection_with_invalid_token(self, sync_client_with_token):
        """Test that connection fails with invalid auth token."""
        session_key = "auth-invalid-test"
        invalid_token = "invalid-token-12345"
        
        # Connection should be rejected
        with pytest.raises(Exception):
            with sync_client_with_token.websocket_connect(
                f"/api/chat/ws?session_key={session_key}&token={invalid_token}"
            ) as websocket:
                # Should not get here
                pass
    
    def test_connection_with_missing_token(self, sync_client_with_token):
        """Test that connection fails when token is missing."""
        session_key = "auth-missing-test"
        
        # Connection should be rejected
        with pytest.raises(Exception):
            with sync_client_with_token.websocket_connect(
                f"/api/chat/ws?session_key={session_key}"
            ) as websocket:
                pass
    
    def test_connection_with_empty_token(self, sync_client_with_token):
        """Test that connection fails with empty token."""
        session_key = "auth-empty-test"
        
        # Connection should be rejected
        with pytest.raises(Exception):
            with sync_client_with_token.websocket_connect(
                f"/api/chat/ws?session_key={session_key}&token="
            ) as websocket:
                pass
    
    # ============================================================================
    # RECONNECTION SCENARIOS
    # ============================================================================
    
    def test_reconnection_after_disconnect(self, sync_client_with_token):
        """Test that client can reconnect after disconnect."""
        token = sync_client_with_token.test_token
        session_key = "reconnect-test"
        
        # First connection
        with sync_client_with_token.websocket_connect(
            f"/api/chat/ws?session_key={session_key}&token={token}"
        ) as websocket:
            # Receive connection confirmation
            data = websocket.receive_json()
            assert data["type"] == "connected"
            assert data["session_key"] == session_key
            
            # Send a message
            websocket.send_json({
                "type": "send_message",
                "content": "Message before disconnect"
            })
            
            # Receive the broadcast message
            data = websocket.receive_json()
            assert data["type"] == "message"
            assert data["data"]["content"] == "Message before disconnect"
        
        # Connection closed - now reconnect
        with sync_client_with_token.websocket_connect(
            f"/api/chat/ws?session_key={session_key}&token={token}"
        ) as websocket:
            # Should receive connection confirmation again
            data = websocket.receive_json()
            assert data["type"] == "connected"
            assert data["session_key"] == session_key
            
            # Send another message after reconnect
            websocket.send_json({
                "type": "send_message",
                "content": "Message after reconnect"
            })
            
            # Should receive the broadcast
            data = websocket.receive_json()
            assert data["type"] == "message"
            assert data["data"]["content"] == "Message after reconnect"
    
    def test_multiple_reconnections(self, sync_client_with_token):
        """Test that multiple reconnections work correctly."""
        token = sync_client_with_token.test_token
        session_key = "multi-reconnect-test"
        
        # Connect, send message, disconnect - repeat 3 times
        for i in range(3):
            with sync_client_with_token.websocket_connect(
                f"/api/chat/ws?session_key={session_key}&token={token}"
            ) as websocket:
                # Receive connection confirmation
                data = websocket.receive_json()
                assert data["type"] == "connected"
                
                # Send message
                websocket.send_json({
                    "type": "send_message",
                    "content": f"Message {i}"
                })
                
                # Receive broadcast
                data = websocket.receive_json()
                assert data["type"] == "message"
                assert data["data"]["content"] == f"Message {i}"
    
    def test_reconnection_to_different_session(self, sync_client_with_token):
        """Test reconnecting to a different session."""
        token = sync_client_with_token.test_token
        
        # Connect to session A
        with sync_client_with_token.websocket_connect(
            f"/api/chat/ws?session_key=session-a&token={token}"
        ) as websocket:
            data = websocket.receive_json()
            assert data["session_key"] == "session-a"
            
            # Send message to session A
            websocket.send_json({
                "type": "send_message",
                "content": "Message in session A"
            })
            data = websocket.receive_json()
            assert data["type"] == "message"
        
        # Reconnect to session B
        with sync_client_with_token.websocket_connect(
            f"/api/chat/ws?session_key=session-b&token={token}"
        ) as websocket:
            data = websocket.receive_json()
            assert data["session_key"] == "session-b"
            
            # Send message to session B
            websocket.send_json({
                "type": "send_message",
                "content": "Message in session B"
            })
            data = websocket.receive_json()
            assert data["type"] == "message"
    
    # ============================================================================
    # MESSAGE ORDERING AFTER RECONNECT
    # ============================================================================
    
    async def test_message_ordering_after_reconnect(self, sync_client_with_token, client):
        """Test that messages maintain correct order after reconnection."""
        token = sync_client_with_token.test_token
        session_key = "order-test"
        
        # First connection - send messages 0-2
        with sync_client_with_token.websocket_connect(
            f"/api/chat/ws?session_key={session_key}&token={token}"
        ) as websocket:
            # Clear connection confirmation
            websocket.receive_json()
            
            for i in range(3):
                websocket.send_json({
                    "type": "send_message",
                    "content": f"Message {i}"
                })
                # Receive broadcast
                data = websocket.receive_json()
                assert data["type"] == "message"
                assert data["data"]["content"] == f"Message {i}"
        
        # Disconnect, then reconnect
        with sync_client_with_token.websocket_connect(
            f"/api/chat/ws?session_key={session_key}&token={token}"
        ) as websocket:
            # Clear connection confirmation
            websocket.receive_json()
            
            # Send messages 3-5 after reconnect
            for i in range(3, 6):
                websocket.send_json({
                    "type": "send_message",
                    "content": f"Message {i}"
                })
                # Receive broadcast
                data = websocket.receive_json()
                assert data["type"] == "message"
                assert data["data"]["content"] == f"Message {i}"
        
        # Verify message history has correct order via REST API
        response = await client.get(f"/api/chat/sessions/{session_key}/messages")
        assert response.status_code == 200
        
        messages = response.json()
        assert len(messages) == 6
        
        # Verify chronological order
        for i, message in enumerate(messages):
            assert message["content"] == f"Message {i}"
            
            # Verify timestamps are monotonically increasing
            if i > 0:
                prev_time = messages[i-1]["created_at"]
                curr_time = message["created_at"]
                assert curr_time >= prev_time, "Messages not in chronological order"
    
    async def test_message_ordering_with_rapid_reconnects(self, sync_client_with_token, client):
        """Test message ordering with rapid connect/disconnect cycles."""
        token = sync_client_with_token.test_token
        session_key = "rapid-order-test"
        
        # Send messages with reconnections between each one
        for i in range(5):
            with sync_client_with_token.websocket_connect(
                f"/api/chat/ws?session_key={session_key}&token={token}"
            ) as websocket:
                # Clear connection confirmation
                websocket.receive_json()
                
                # Send one message
                websocket.send_json({
                    "type": "send_message",
                    "content": f"Rapid message {i}"
                })
                
                # Receive broadcast
                data = websocket.receive_json()
                assert data["type"] == "message"
                assert data["data"]["content"] == f"Rapid message {i}"
            # Disconnect immediately after each message
        
        # Verify all messages are in order
        response = await client.get(f"/api/chat/sessions/{session_key}/messages")
        messages = response.json()
        
        assert len(messages) == 5
        for i, message in enumerate(messages):
            assert message["content"] == f"Rapid message {i}"
    
    async def test_interleaved_messages_multiple_reconnects(self, sync_client_with_token, client):
        """Test that messages from multiple reconnection sessions interleave correctly."""
        token = sync_client_with_token.test_token
        session_key = "interleave-test"
        
        # First batch: messages 0-1
        with sync_client_with_token.websocket_connect(
            f"/api/chat/ws?session_key={session_key}&token={token}"
        ) as websocket:
            websocket.receive_json()  # Clear connection
            
            for i in range(2):
                websocket.send_json({
                    "type": "send_message",
                    "content": f"Batch A message {i}"
                })
                websocket.receive_json()  # Clear broadcast
        
        # Second batch: messages 2-3
        with sync_client_with_token.websocket_connect(
            f"/api/chat/ws?session_key={session_key}&token={token}"
        ) as websocket:
            websocket.receive_json()  # Clear connection
            
            for i in range(2):
                websocket.send_json({
                    "type": "send_message",
                    "content": f"Batch B message {i}"
                })
                websocket.receive_json()  # Clear broadcast
        
        # Third batch: messages 4-5
        with sync_client_with_token.websocket_connect(
            f"/api/chat/ws?session_key={session_key}&token={token}"
        ) as websocket:
            websocket.receive_json()  # Clear connection
            
            for i in range(2):
                websocket.send_json({
                    "type": "send_message",
                    "content": f"Batch C message {i}"
                })
                websocket.receive_json()  # Clear broadcast
        
        # Verify messages are in chronological order
        response = await client.get(f"/api/chat/sessions/{session_key}/messages")
        messages = response.json()
        
        assert len(messages) == 6
        
        # Check that timestamps are monotonically increasing
        for i in range(1, len(messages)):
            assert messages[i]["created_at"] >= messages[i-1]["created_at"]
    
    # ============================================================================
    # CONCURRENT CONNECTIONS
    # ============================================================================
    
    def test_multiple_clients_same_session_reconnect(self, sync_client_with_token):
        """Test that multiple clients on same session handle reconnection correctly."""
        token = sync_client_with_token.test_token
        session_key = "multi-client-test"
        
        # Create two separate client instances
        client1 = TestClient(app)
        client2 = TestClient(app)
        
        # Connect both clients to same session
        with client1.websocket_connect(
            f"/api/chat/ws?session_key={session_key}&token={token}"
        ) as ws1:
            with client2.websocket_connect(
                f"/api/chat/ws?session_key={session_key}&token={token}"
            ) as ws2:
                # Clear connection confirmations
                ws1.receive_json()
                ws2.receive_json()
                
                # Send message from client 1
                ws1.send_json({
                    "type": "send_message",
                    "content": "From client 1"
                })
                
                # Both should receive the broadcast
                data1 = ws1.receive_json()
                data2 = ws2.receive_json()
                
                assert data1["type"] == "message"
                assert data2["type"] == "message"
                assert data1["data"]["content"] == "From client 1"
                assert data2["data"]["content"] == "From client 1"
            
            # Client 2 disconnected, client 1 still connected
            # Send message from client 1
            ws1.send_json({
                "type": "send_message",
                "content": "After client 2 disconnect"
            })
            
            # Only client 1 should receive it
            data = ws1.receive_json()
            assert data["type"] == "message"
            assert data["data"]["content"] == "After client 2 disconnect"
        
        # Both disconnected - now reconnect client 2
        with client2.websocket_connect(
            f"/api/chat/ws?session_key={session_key}&token={token}"
        ) as ws2:
            ws2.receive_json()  # Clear connection
            
            # Verify client 2 can still send messages
            ws2.send_json({
                "type": "send_message",
                "content": "Client 2 reconnected"
            })
            
            data = ws2.receive_json()
            assert data["type"] == "message"
            assert data["data"]["content"] == "Client 2 reconnected"
    
    # ============================================================================
    # ERROR SCENARIOS
    # ============================================================================
    
    def test_reconnect_after_server_error(self, sync_client_with_token):
        """Test that client can reconnect after a server error."""
        token = sync_client_with_token.test_token
        session_key = "error-recovery-test"
        
        # First successful connection
        with sync_client_with_token.websocket_connect(
            f"/api/chat/ws?session_key={session_key}&token={token}"
        ) as websocket:
            websocket.receive_json()  # Clear connection
            
            # Send valid message
            websocket.send_json({
                "type": "send_message",
                "content": "Valid message"
            })
            data = websocket.receive_json()
            assert data["type"] == "message"
            
            # Send invalid message (should get error but not disconnect)
            websocket.send_json({
                "type": "unknown_type",
                "data": "invalid"
            })
            
            # Should receive error message
            data = websocket.receive_json()
            assert data["type"] == "error"
            assert "Unknown event type" in data["message"]
            
            # Connection should still be alive - send another valid message
            websocket.send_json({
                "type": "send_message",
                "content": "After error"
            })
            data = websocket.receive_json()
            assert data["type"] == "message"
            assert data["data"]["content"] == "After error"
        
        # Should be able to reconnect after disconnect
        with sync_client_with_token.websocket_connect(
            f"/api/chat/ws?session_key={session_key}&token={token}"
        ) as websocket:
            websocket.receive_json()  # Clear connection
            
            # Verify connection works
            websocket.send_json({
                "type": "send_message",
                "content": "Reconnected successfully"
            })
            data = websocket.receive_json()
            assert data["type"] == "message"
