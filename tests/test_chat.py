"""Tests for chat endpoints."""

import pytest
from httpx import AsyncClient
from datetime import datetime


class TestChatREST:
    """Test REST chat endpoints."""
    
    @pytest.mark.asyncio
    async def test_create_session(self, client: AsyncClient):
        """Test creating a chat session."""
        response = await client.post(
            "/api/chat/sessions",
            json={"session_key": "test-session", "label": "Test Session"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["session_key"] == "test-session"
        assert data["label"] == "Test Session"
        assert data["is_active"] is True
    
    @pytest.mark.asyncio
    async def test_create_duplicate_session(self, client: AsyncClient):
        """Test creating a duplicate session fails."""
        # Create first session
        await client.post(
            "/api/chat/sessions",
            json={"session_key": "dup-test", "label": "First"}
        )
        
        # Try to create duplicate
        response = await client.post(
            "/api/chat/sessions",
            json={"session_key": "dup-test", "label": "Second"}
        )
        assert response.status_code == 400
    
    @pytest.mark.asyncio
    async def test_list_sessions(self, client: AsyncClient):
        """Test listing chat sessions."""
        # Create some sessions
        await client.post(
            "/api/chat/sessions",
            json={"session_key": "session1", "label": "Session 1"}
        )
        await client.post(
            "/api/chat/sessions",
            json={"session_key": "session2", "label": "Session 2"}
        )
        
        response = await client.get("/api/chat/sessions")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2
        session_keys = [s["session_key"] for s in data]
        assert "session1" in session_keys
        assert "session2" in session_keys
    
    @pytest.mark.asyncio
    async def test_send_message(self, client: AsyncClient):
        """Test sending a message via REST."""
        # Create session
        await client.post(
            "/api/chat/sessions",
            json={"session_key": "msg-test", "label": "Message Test"}
        )
        
        # Send message
        response = await client.post(
            "/api/chat/sessions/msg-test/send",
            json={"content": "Hello, world!"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["role"] == "user"
        assert data["content"] == "Hello, world!"
        assert "created_at" in data
    
    @pytest.mark.asyncio
    async def test_get_messages(self, client: AsyncClient):
        """Test retrieving message history."""
        session_key = "history-test"
        
        # Create session
        await client.post(
            "/api/chat/sessions",
            json={"session_key": session_key, "label": "History Test"}
        )
        
        # Send some messages
        await client.post(
            f"/api/chat/sessions/{session_key}/send",
            json={"content": "Message 1"}
        )
        await client.post(
            f"/api/chat/sessions/{session_key}/send",
            json={"content": "Message 2"}
        )
        await client.post(
            f"/api/chat/sessions/{session_key}/send",
            json={"content": "Message 3"}
        )
        
        # Get messages
        response = await client.get(f"/api/chat/sessions/{session_key}/messages")
        assert response.status_code == 200
        messages = response.json()
        assert len(messages) == 3
        
        # Should be in chronological order
        assert messages[0]["content"] == "Message 1"
        assert messages[1]["content"] == "Message 2"
        assert messages[2]["content"] == "Message 3"
    
    @pytest.mark.asyncio
    async def test_get_messages_pagination(self, client: AsyncClient):
        """Test message pagination."""
        session_key = "pagination-test"
        
        # Create session
        await client.post(
            "/api/chat/sessions",
            json={"session_key": session_key}
        )
        
        # Send messages
        message_ids = []
        for i in range(5):
            response = await client.post(
                f"/api/chat/sessions/{session_key}/send",
                json={"content": f"Message {i}"}
            )
            message_ids.append(response.json()["id"])
        
        # Get all messages
        response = await client.get(f"/api/chat/sessions/{session_key}/messages")
        all_messages = response.json()
        assert len(all_messages) == 5
        
        # Get messages before the 4th message (should get first 3)
        response = await client.get(
            f"/api/chat/sessions/{session_key}/messages?before={message_ids[3]}"
        )
        paginated = response.json()
        assert len(paginated) == 3
        assert paginated[0]["content"] == "Message 0"
    
    @pytest.mark.asyncio
    async def test_typing_status(self, client: AsyncClient):
        """Test typing status endpoint."""
        session_key = "typing-test"
        
        # Check typing status (should be false initially)
        response = await client.get(f"/api/chat/sessions/{session_key}/status")
        assert response.status_code == 200
        assert response.json()["is_typing"] is False
        
        # Set typing via webhook
        await client.post(
            f"/api/chat/webhook/{session_key}/typing",
            json={"is_typing": True}
        )
        
        # Check status again
        response = await client.get(f"/api/chat/sessions/{session_key}/status")
        assert response.json()["is_typing"] is True
        
        # Clear typing
        await client.post(
            f"/api/chat/webhook/{session_key}/typing",
            json={"is_typing": False}
        )
        
        response = await client.get(f"/api/chat/sessions/{session_key}/status")
        assert response.json()["is_typing"] is False
    
    @pytest.mark.asyncio
    async def test_webhook_receive_message(self, client: AsyncClient):
        """Test receiving agent response via webhook."""
        session_key = "webhook-test"
        
        # Create session
        await client.post(
            "/api/chat/sessions",
            json={"session_key": session_key}
        )
        
        # Send agent response via webhook
        response = await client.post(
            f"/api/chat/webhook/{session_key}/message",
            json={
                "content": "This is an agent response",
                "metadata": {"model": "claude-opus-4"}
            }
        )
        assert response.status_code == 200
        
        # Check message was stored
        response = await client.get(f"/api/chat/sessions/{session_key}/messages")
        messages = response.json()
        assert len(messages) == 1
        assert messages[0]["role"] == "assistant"
        assert messages[0]["content"] == "This is an agent response"
        assert messages[0]["message_metadata"]["model"] == "claude-opus-4"
    
    @pytest.mark.asyncio
    async def test_auto_create_session_on_message(self, client: AsyncClient):
        """Test that sending a message to a non-existent session creates it."""
        session_key = "auto-create-test"
        
        # Send message without creating session first
        response = await client.post(
            f"/api/chat/sessions/{session_key}/send",
            json={"content": "Hello"}
        )
        assert response.status_code == 200
        
        # Session should now exist
        response = await client.get("/api/chat/sessions")
        sessions = response.json()
        session_keys = [s["session_key"] for s in sessions]
        assert session_key in session_keys


class TestChatWebSocket:
    """Test WebSocket chat functionality."""
    
    @pytest.mark.asyncio
    async def test_websocket_connect(self, client: AsyncClient):
        """Test WebSocket connection."""
        async with client.websocket_connect("/api/chat/ws?session_key=ws-test") as websocket:
            # Should receive connection confirmation
            data = await websocket.receive_json()
            assert data["type"] == "connected"
            assert data["session_key"] == "ws-test"
    
    @pytest.mark.asyncio
    async def test_websocket_send_message(self, client: AsyncClient):
        """Test sending a message via WebSocket."""
        async with client.websocket_connect("/api/chat/ws?session_key=ws-msg-test") as websocket:
            # Wait for connection
            await websocket.receive_json()
            
            # Send message
            await websocket.send_json({
                "type": "send_message",
                "content": "Hello via WebSocket!"
            })
            
            # Should receive message broadcast
            data = await websocket.receive_json()
            assert data["type"] == "message"
            assert data["data"]["role"] == "user"
            assert data["data"]["content"] == "Hello via WebSocket!"
    
    @pytest.mark.asyncio
    async def test_websocket_create_session(self, client: AsyncClient):
        """Test creating a session via WebSocket."""
        async with client.websocket_connect("/api/chat/ws?session_key=main") as websocket:
            # Wait for connection
            await websocket.receive_json()
            
            # Create new session
            await websocket.send_json({
                "type": "create_session",
                "label": "WS Created Session",
                "session_key": "ws-created"
            })
            
            # Should receive session_created event
            data = await websocket.receive_json()
            assert data["type"] == "session_created"
            assert data["data"]["session_key"] == "ws-created"
            assert data["data"]["label"] == "WS Created Session"
    
    @pytest.mark.asyncio
    async def test_websocket_list_sessions(self, client: AsyncClient):
        """Test listing sessions via WebSocket."""
        # Create some sessions first
        async with AsyncClient(app=client.app, base_url="http://test") as http_client:
            await http_client.post(
                "/api/chat/sessions",
                json={"session_key": "list-test-1", "label": "List 1"}
            )
            await http_client.post(
                "/api/chat/sessions",
                json={"session_key": "list-test-2", "label": "List 2"}
            )
        
        async with client.websocket_connect("/api/chat/ws?session_key=main") as websocket:
            # Wait for connection
            await websocket.receive_json()
            
            # Request session list
            await websocket.send_json({
                "type": "list_sessions"
            })
            
            # Should receive session_list event
            data = await websocket.receive_json()
            assert data["type"] == "session_list"
            assert len(data["data"]) >= 2
    
    @pytest.mark.asyncio
    async def test_websocket_switch_session(self, client: AsyncClient):
        """Test switching sessions via WebSocket."""
        async with client.websocket_connect("/api/chat/ws?session_key=session-a") as websocket:
            # Wait for connection
            data = await websocket.receive_json()
            assert data["session_key"] == "session-a"
            
            # Switch to session-b
            await websocket.send_json({
                "type": "switch_session",
                "session_key": "session-b"
            })
            
            # Should receive new connection confirmation
            data = await websocket.receive_json()
            assert data["type"] == "connected"
            assert data["session_key"] == "session-b"
    
    @pytest.mark.asyncio
    async def test_websocket_broadcast(self, client: AsyncClient):
        """Test that messages broadcast to all connected clients."""
        session_key = "broadcast-test"
        
        # Connect two clients to the same session
        async with client.websocket_connect(f"/api/chat/ws?session_key={session_key}") as ws1:
            async with client.websocket_connect(f"/api/chat/ws?session_key={session_key}") as ws2:
                # Wait for connections
                await ws1.receive_json()
                await ws2.receive_json()
                
                # Send message from client 1
                await ws1.send_json({
                    "type": "send_message",
                    "content": "Broadcast test"
                })
                
                # Both clients should receive the message
                data1 = await ws1.receive_json()
                data2 = await ws2.receive_json()
                
                assert data1["type"] == "message"
                assert data2["type"] == "message"
                assert data1["data"]["content"] == "Broadcast test"
                assert data2["data"]["content"] == "Broadcast test"
