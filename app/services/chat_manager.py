"""WebSocket connection manager for chat."""

import asyncio
from typing import Dict, Set, Optional
from datetime import datetime, timedelta
from fastapi import WebSocket


class ConnectionManager:
    """Manages WebSocket connections and broadcasts for chat sessions."""
    
    def __init__(self):
        # session_key -> set of WebSocket connections
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # session_key -> typing state (expiry timestamp)
        self.typing_state: Dict[str, datetime] = {}
        # Background task for clearing expired typing indicators
        self.cleanup_task: Optional[asyncio.Task] = None
        self.typing_timeout_seconds = 30
    
    async def connect(self, websocket: WebSocket, session_key: str):
        """Accept and register a WebSocket connection."""
        await websocket.accept()
        if session_key not in self.active_connections:
            self.active_connections[session_key] = set()
        self.active_connections[session_key].add(websocket)
    
    def disconnect(self, websocket: WebSocket, session_key: str):
        """Unregister a WebSocket connection."""
        if session_key in self.active_connections:
            self.active_connections[session_key].discard(websocket)
            if not self.active_connections[session_key]:
                del self.active_connections[session_key]
    
    async def broadcast_to_session(self, session_key: str, message: dict):
        """Broadcast a message to all connections on a session."""
        if session_key not in self.active_connections:
            return
        
        disconnected = set()
        for connection in self.active_connections[session_key]:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.add(connection)
        
        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection, session_key)
    
    async def send_to_connection(self, websocket: WebSocket, message: dict):
        """Send a message to a specific connection."""
        try:
            await websocket.send_json(message)
        except Exception:
            pass
    
    def set_typing(self, session_key: str, is_typing: bool):
        """Set typing indicator state for a session."""
        if is_typing:
            self.typing_state[session_key] = datetime.now() + timedelta(seconds=self.typing_timeout_seconds)
        else:
            self.typing_state.pop(session_key, None)
    
    def is_typing(self, session_key: str) -> bool:
        """Check if typing indicator is active for a session."""
        if session_key not in self.typing_state:
            return False
        
        # Check if expired
        if datetime.now() > self.typing_state[session_key]:
            del self.typing_state[session_key]
            return False
        
        return True
    
    async def cleanup_typing_indicators(self):
        """Background task to clean up expired typing indicators."""
        while True:
            try:
                await asyncio.sleep(5)  # Check every 5 seconds
                
                expired = []
                now = datetime.now()
                for session_key, expiry in self.typing_state.items():
                    if now > expiry:
                        expired.append(session_key)
                
                # Broadcast typing_stop for expired sessions
                for session_key in expired:
                    del self.typing_state[session_key]
                    await self.broadcast_to_session(session_key, {"type": "typing_stop"})
            except Exception:
                pass


# Global connection manager instance
manager = ConnectionManager()
