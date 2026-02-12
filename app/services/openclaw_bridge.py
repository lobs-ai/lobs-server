"""OpenClaw integration bridge for chat."""

from typing import Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import ChatMessage
from app.services.chat_manager import manager


class OpenClawBridge:
    """
    Bridge between lobs-server chat and OpenClaw.
    
    Design:
    - Messages from dashboard are stored in DB
    - OpenClaw polls or receives webhooks for new messages
    - OpenClaw posts responses via webhook endpoint
    - Server broadcasts responses to connected clients
    
    This interface is swappable - can be replaced with direct SDK integration later.
    """
    
    async def send_message_to_openclaw(
        self,
        session_key: str,
        content: str,
        db: AsyncSession
    ):
        """
        Send a user message to OpenClaw for processing.
        
        For now, this just stores the message in DB.
        OpenClaw will poll /api/chat/sessions/{session_key}/messages
        or we'll set up a webhook.
        
        In the future, this could:
        - Call OpenClaw API directly
        - Use OpenClaw SDK
        - Send via message queue
        """
        # For now, message is already in DB from the caller
        # In the future, trigger OpenClaw processing here
        pass
    
    async def receive_agent_response(
        self,
        session_key: str,
        content: str,
        db: AsyncSession,
        metadata: Optional[dict] = None
    ):
        """
        Receive a response from OpenClaw and broadcast to clients.
        
        Called by the webhook endpoint when OpenClaw delivers a response.
        """
        import uuid
        
        # Store message in database
        message = ChatMessage(
            id=str(uuid.uuid4()),
            session_key=session_key,
            role="assistant",
            content=content,
            created_at=datetime.now(),
            message_metadata=metadata
        )
        db.add(message)
        await db.commit()
        
        # Broadcast to connected clients
        await manager.broadcast_to_session(session_key, {
            "type": "message",
            "data": {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at.isoformat(),
                "message_metadata": message.message_metadata
            }
        })
        
        return message
    
    async def set_typing_indicator(
        self,
        session_key: str,
        is_typing: bool
    ):
        """
        Set typing indicator state and broadcast to clients.
        
        Called by webhook when OpenClaw starts/stops processing.
        """
        manager.set_typing(session_key, is_typing)
        
        event_type = "typing_start" if is_typing else "typing_stop"
        await manager.broadcast_to_session(session_key, {
            "type": event_type
        })


# Global bridge instance
bridge = OpenClawBridge()
