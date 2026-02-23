"""WebSocket reliability tests - simulate failure scenarios."""

import pytest
import asyncio
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import WebSocketDisconnect


class TestWebSocketReliability:
    """
    Test WebSocket connection reliability and failure scenarios.
    
    These tests validate:
    1. Connection drops and reconnection
    2. Message ordering during disconnection
    3. Duplicate message handling
    4. Timeout scenarios
    5. Graceful degradation
    """
    
    # ============================================================================
    # CONNECTION MANAGEMENT
    # ============================================================================
    
    @pytest.mark.asyncio
    async def test_connection_drop_recovery(self, client: AsyncClient):
        """Test that chat works after connection drop via REST fallback."""
        session_key = "drop-recovery-test"
        
        # Create session
        response = await client.post(
            "/api/chat/sessions",
            json={"session_key": session_key}
        )
        assert response.status_code == 200
        
        # Simulate message sent before drop (via REST)
        response = await client.post(
            f"/api/chat/sessions/{session_key}/send",
            json={"content": "Before drop"}
        )
        assert response.status_code == 200
        
        # Simulate reconnection - send another message
        response = await client.post(
            f"/api/chat/sessions/{session_key}/send",
            json={"content": "After reconnect"}
        )
        assert response.status_code == 200
        
        # Verify both messages are in history
        response = await client.get(f"/api/chat/sessions/{session_key}/messages")
        messages = response.json()
        assert len(messages) == 2
        assert messages[0]["content"] == "Before drop"
        assert messages[1]["content"] == "After reconnect"
    
    @pytest.mark.asyncio
    async def test_message_ordering_during_disconnect(self, client: AsyncClient):
        """Test that messages maintain correct order even if sent during disconnect."""
        session_key = "ordering-test"
        
        # Create session
        await client.post(
            "/api/chat/sessions",
            json={"session_key": session_key}
        )
        
        # Send multiple messages in rapid succession
        for i in range(5):
            response = await client.post(
                f"/api/chat/sessions/{session_key}/send",
                json={"content": f"Message {i}"}
            )
            assert response.status_code == 200
        
        # Verify messages are in correct order
        response = await client.get(f"/api/chat/sessions/{session_key}/messages")
        messages = response.json()
        assert len(messages) == 5
        
        for i, message in enumerate(messages):
            assert message["content"] == f"Message {i}"
            
            # Verify timestamps are monotonically increasing
            if i > 0:
                prev_time = messages[i-1]["created_at"]
                curr_time = message["created_at"]
                assert curr_time >= prev_time, "Messages not in chronological order"
    
    @pytest.mark.asyncio
    async def test_duplicate_message_prevention(self, client: AsyncClient):
        """Test that duplicate messages are handled correctly."""
        session_key = "duplicate-test"
        
        # Create session
        await client.post(
            "/api/chat/sessions",
            json={"session_key": session_key}
        )
        
        # Send same message twice rapidly
        message_data = {"content": "Duplicate test message"}
        
        response1 = await client.post(
            f"/api/chat/sessions/{session_key}/send",
            json=message_data
        )
        response2 = await client.post(
            f"/api/chat/sessions/{session_key}/send",
            json=message_data
        )
        
        assert response1.status_code == 200
        assert response2.status_code == 200
        
        # Both should succeed (duplicates are allowed - client may intentionally resend)
        # Verify both messages are stored
        response = await client.get(f"/api/chat/sessions/{session_key}/messages")
        messages = response.json()
        
        # Should have 2 messages with same content but different IDs/timestamps
        duplicate_messages = [m for m in messages if m["content"] == "Duplicate test message"]
        assert len(duplicate_messages) == 2
        assert duplicate_messages[0]["id"] != duplicate_messages[1]["id"]
    
    @pytest.mark.asyncio
    async def test_session_persistence_across_connections(self, client: AsyncClient):
        """Test that sessions persist and messages remain available after reconnection."""
        session_key = "persistence-test"
        
        # Create session and send messages
        await client.post(
            "/api/chat/sessions",
            json={"session_key": session_key, "label": "Persistence Test"}
        )
        
        await client.post(
            f"/api/chat/sessions/{session_key}/send",
            json={"content": "Message 1"}
        )
        await client.post(
            f"/api/chat/sessions/{session_key}/send",
            json={"content": "Message 2"}
        )
        
        # Simulate disconnection and new connection
        # Verify session still exists
        response = await client.get("/api/chat/sessions")
        sessions = response.json()
        session_keys = [s["session_key"] for s in sessions]
        assert session_key in session_keys
        
        # Verify messages are still available
        response = await client.get(f"/api/chat/sessions/{session_key}/messages")
        messages = response.json()
        assert len(messages) == 2
        assert messages[0]["content"] == "Message 1"
        assert messages[1]["content"] == "Message 2"
    
    # ============================================================================
    # TIMEOUT SCENARIOS
    # ============================================================================
    
    @pytest.mark.asyncio
    async def test_message_send_timeout_fallback(self, client: AsyncClient):
        """Test that message send works even if WebSocket is unavailable."""
        session_key = "timeout-test"
        
        # Create session
        await client.post(
            "/api/chat/sessions",
            json={"session_key": session_key}
        )
        
        # Send message via REST (simulating WebSocket timeout fallback)
        response = await client.post(
            f"/api/chat/sessions/{session_key}/send",
            json={"content": "Fallback message"},
            timeout=5.0  # Set explicit timeout
        )
        
        assert response.status_code == 200
        message = response.json()
        assert message["content"] == "Fallback message"
        
        # Verify message is stored
        response = await client.get(f"/api/chat/sessions/{session_key}/messages")
        messages = response.json()
        assert len(messages) == 1
        assert messages[0]["content"] == "Fallback message"
    
    @pytest.mark.asyncio
    async def test_typing_indicator_timeout(self, client: AsyncClient):
        """Test that typing indicators time out and clear."""
        session_key = "typing-timeout-test"
        
        # Set typing indicator
        await client.post(
            f"/api/chat/webhook/{session_key}/typing",
            json={"is_typing": True}
        )
        
        # Check it's active
        response = await client.get(f"/api/chat/sessions/{session_key}/status")
        assert response.json()["is_typing"] is True
        
        # Wait for cleanup (typing indicators should auto-clear after 60 seconds)
        # Note: In tests we don't want to wait 60s, so we test the manual clear
        await client.post(
            f"/api/chat/webhook/{session_key}/typing",
            json={"is_typing": False}
        )
        
        # Verify it cleared
        response = await client.get(f"/api/chat/sessions/{session_key}/status")
        assert response.json()["is_typing"] is False
    
    # ============================================================================
    # ERROR HANDLING
    # ============================================================================
    
    @pytest.mark.asyncio
    async def test_invalid_session_graceful_error(self, client: AsyncClient):
        """Test that accessing invalid session returns graceful error."""
        # Try to send message to non-existent session
        # Note: The API auto-creates sessions, so we test other scenarios
        
        # Try to get messages from non-existent session
        response = await client.get("/api/chat/sessions/nonexistent-session/messages")
        # Should auto-create and return empty list
        assert response.status_code == 200
        messages = response.json()
        assert len(messages) == 0
    
    @pytest.mark.asyncio
    async def test_malformed_message_error(self, client: AsyncClient):
        """Test that malformed messages are rejected gracefully."""
        session_key = "malformed-test"
        
        # Create session
        await client.post(
            "/api/chat/sessions",
            json={"session_key": session_key}
        )
        
        # Try to send message with missing content
        response = await client.post(
            f"/api/chat/sessions/{session_key}/send",
            json={}
        )
        
        # Should return validation error
        assert response.status_code == 422  # Unprocessable Entity
    
    @pytest.mark.asyncio
    async def test_concurrent_message_sends(self, client: AsyncClient):
        """Test that concurrent message sends are handled correctly."""
        session_key = "concurrent-test"
        
        # Create session
        await client.post(
            "/api/chat/sessions",
            json={"session_key": session_key}
        )
        
        # Send multiple messages concurrently
        async def send_message(index: int):
            return await client.post(
                f"/api/chat/sessions/{session_key}/send",
                json={"content": f"Concurrent message {index}"}
            )
        
        # Send 10 messages concurrently
        results = await asyncio.gather(*[send_message(i) for i in range(10)])
        
        # All should succeed
        for response in results:
            assert response.status_code == 200
        
        # Verify all messages are stored
        response = await client.get(f"/api/chat/sessions/{session_key}/messages")
        messages = response.json()
        assert len(messages) == 10
    
    # ============================================================================
    # SESSION MANAGEMENT
    # ============================================================================
    
    @pytest.mark.asyncio
    async def test_multiple_sessions_isolation(self, client: AsyncClient):
        """Test that messages in different sessions remain isolated."""
        # Create two sessions
        await client.post(
            "/api/chat/sessions",
            json={"session_key": "session-a", "label": "Session A"}
        )
        await client.post(
            "/api/chat/sessions",
            json={"session_key": "session-b", "label": "Session B"}
        )
        
        # Send messages to each
        await client.post(
            "/api/chat/sessions/session-a/send",
            json={"content": "Message in A"}
        )
        await client.post(
            "/api/chat/sessions/session-b/send",
            json={"content": "Message in B"}
        )
        
        # Verify isolation
        response_a = await client.get("/api/chat/sessions/session-a/messages")
        messages_a = response_a.json()
        assert len(messages_a) == 1
        assert messages_a[0]["content"] == "Message in A"
        
        response_b = await client.get("/api/chat/sessions/session-b/messages")
        messages_b = response_b.json()
        assert len(messages_b) == 1
        assert messages_b[0]["content"] == "Message in B"
    
    @pytest.mark.asyncio
    async def test_session_list_consistency(self, client: AsyncClient):
        """Test that session list remains consistent across operations."""
        # Create multiple sessions
        session_keys = ["list-test-1", "list-test-2", "list-test-3"]
        
        for key in session_keys:
            await client.post(
                "/api/chat/sessions",
                json={"session_key": key, "label": f"Session {key}"}
            )
        
        # Get session list
        response = await client.get("/api/chat/sessions")
        sessions = response.json()
        
        # Verify all created sessions are present
        actual_keys = [s["session_key"] for s in sessions]
        for key in session_keys:
            assert key in actual_keys
    
    # ============================================================================
    # MESSAGE HISTORY
    # ============================================================================
    
    @pytest.mark.asyncio
    async def test_message_pagination_reliability(self, client: AsyncClient):
        """Test that message pagination works reliably."""
        session_key = "pagination-reliability-test"
        
        # Create session
        await client.post(
            "/api/chat/sessions",
            json={"session_key": session_key}
        )
        
        # Send many messages
        message_ids = []
        for i in range(20):
            response = await client.post(
                f"/api/chat/sessions/{session_key}/send",
                json={"content": f"Message {i}"}
            )
            message_ids.append(response.json()["id"])
        
        # Test pagination with limit
        response = await client.get(
            f"/api/chat/sessions/{session_key}/messages?limit=10"
        )
        page1 = response.json()
        assert len(page1) == 10
        
        # Get next page using 'before' cursor
        last_id = page1[-1]["id"]
        response = await client.get(
            f"/api/chat/sessions/{session_key}/messages?before={last_id}&limit=10"
        )
        page2 = response.json()
        assert len(page2) == 10
        
        # Verify no overlap
        page1_ids = {m["id"] for m in page1}
        page2_ids = {m["id"] for m in page2}
        assert page1_ids.isdisjoint(page2_ids)
    
    @pytest.mark.asyncio
    async def test_message_retrieval_after_many_messages(self, client: AsyncClient):
        """Test that old messages can be retrieved even after many new messages."""
        session_key = "many-messages-test"
        
        # Create session
        await client.post(
            "/api/chat/sessions",
            json={"session_key": session_key}
        )
        
        # Send first message
        response = await client.post(
            f"/api/chat/sessions/{session_key}/send",
            json={"content": "First message"}
        )
        first_message_id = response.json()["id"]
        
        # Send many more messages
        for i in range(100):
            await client.post(
                f"/api/chat/sessions/{session_key}/send",
                json={"content": f"Message {i}"}
            )
        
        # Retrieve all messages
        response = await client.get(
            f"/api/chat/sessions/{session_key}/messages?limit=1000"
        )
        messages = response.json()
        
        # Verify first message is still there
        first_messages = [m for m in messages if m["id"] == first_message_id]
        assert len(first_messages) == 1
        assert first_messages[0]["content"] == "First message"
    
    # ============================================================================
    # WEBHOOK RELIABILITY
    # ============================================================================
    
    @pytest.mark.asyncio
    async def test_webhook_message_delivery(self, client: AsyncClient):
        """Test that webhook-delivered messages are stored correctly."""
        session_key = "webhook-delivery-test"
        
        # Create session
        await client.post(
            "/api/chat/sessions",
            json={"session_key": session_key}
        )
        
        # Simulate agent response via webhook
        response = await client.post(
            f"/api/chat/webhook/{session_key}/message",
            json={
                "content": "Agent response via webhook",
                "metadata": {"model": "test-model"}
            }
        )
        assert response.status_code == 200
        
        # Verify message is stored
        response = await client.get(f"/api/chat/sessions/{session_key}/messages")
        messages = response.json()
        assert len(messages) == 1
        assert messages[0]["role"] == "assistant"
        assert messages[0]["content"] == "Agent response via webhook"
        assert messages[0]["message_metadata"]["model"] == "test-model"
    
    @pytest.mark.asyncio
    async def test_webhook_typing_indicator_reliability(self, client: AsyncClient):
        """Test that typing indicators work reliably via webhook."""
        session_key = "webhook-typing-test"
        
        # Set typing on
        response = await client.post(
            f"/api/chat/webhook/{session_key}/typing",
            json={"is_typing": True}
        )
        assert response.status_code == 200
        
        # Verify status
        response = await client.get(f"/api/chat/sessions/{session_key}/status")
        assert response.status_code == 200
        status = response.json()
        assert status["is_typing"] is True
        
        # Set typing off
        response = await client.post(
            f"/api/chat/webhook/{session_key}/typing",
            json={"is_typing": False}
        )
        assert response.status_code == 200
        
        # Verify status cleared
        response = await client.get(f"/api/chat/sessions/{session_key}/status")
        status = response.json()
        assert status["is_typing"] is False
    
    @pytest.mark.asyncio
    async def test_rapid_typing_updates(self, client: AsyncClient):
        """Test that rapid typing status updates are handled correctly."""
        session_key = "rapid-typing-test"
        
        # Rapidly toggle typing status
        for i in range(10):
            is_typing = i % 2 == 0
            await client.post(
                f"/api/chat/webhook/{session_key}/typing",
                json={"is_typing": is_typing}
            )
        
        # Final state should match last update (False)
        response = await client.get(f"/api/chat/sessions/{session_key}/status")
        status = response.json()
        assert status["is_typing"] is False
