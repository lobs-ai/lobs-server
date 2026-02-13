"""Chat endpoints - WebSocket and REST."""

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from pydantic import BaseModel

from app.database import get_db
from app.models import ChatSession, ChatMessage
from app.services.chat_manager import manager
from app.services.openclaw_bridge import bridge


router = APIRouter(prefix="/chat", tags=["chat"])


# --- Pydantic Models ---

class ChatMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
    message_metadata: Optional[dict] = None
    
    class Config:
        from_attributes = True


class ChatSessionResponse(BaseModel):
    id: str
    session_key: str
    label: Optional[str] = None
    created_at: datetime
    is_active: bool
    last_message_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class CreateSessionRequest(BaseModel):
    session_key: Optional[str] = None
    label: Optional[str] = None


class SendMessageRequest(BaseModel):
    content: str


class TypingStatusResponse(BaseModel):
    session_key: str
    is_typing: bool


class WebhookMessageRequest(BaseModel):
    content: str
    metadata: Optional[dict] = None


class WebhookTypingRequest(BaseModel):
    is_typing: bool


# --- Helper Functions ---

async def ensure_session_exists(session_key: str, db: AsyncSession) -> ChatSession:
    """Ensure a chat session exists, create if not."""
    result = await db.execute(
        select(ChatSession).where(ChatSession.session_key == session_key)
    )
    session = result.scalar_one_or_none()
    
    if not session:
        session = ChatSession(
            id=str(uuid.uuid4()),
            session_key=session_key,
            label=session_key,
            created_at=datetime.now(),
            is_active=True
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
    
    return session


async def store_message(
    session_key: str,
    role: str,
    content: str,
    db: AsyncSession,
    metadata: Optional[dict] = None
) -> ChatMessage:
    """Store a chat message and update session timestamp."""
    # Ensure session exists
    await ensure_session_exists(session_key, db)
    
    # Create message
    message = ChatMessage(
        id=str(uuid.uuid4()),
        session_key=session_key,
        role=role,
        content=content,
        created_at=datetime.now(),
        message_metadata=metadata
    )
    db.add(message)
    
    # Update session last_message_at
    result = await db.execute(
        select(ChatSession).where(ChatSession.session_key == session_key)
    )
    session = result.scalar_one()
    session.last_message_at = datetime.now()
    
    await db.commit()
    await db.refresh(message)
    
    return message


# --- WebSocket Endpoint ---

@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    session_key: str = Query("main"),
    token: str = Query(..., description="Bearer token for authentication")
):
    """
    WebSocket endpoint for real-time chat.
    
    Query params:
    - session_key: Chat session identifier (default: "main")
    - token: Bearer token for authentication (required)
    
    Events from client:
    - {"type": "send_message", "content": "...", "session_key": "..."}
    - {"type": "create_session", "label": "..."}
    - {"type": "list_sessions"}
    - {"type": "switch_session", "session_key": "..."}
    
    Events to client:
    - {"type": "connected", "session_key": "..."}
    - {"type": "message", "data": {...}}
    - {"type": "typing_start"}
    - {"type": "typing_stop"}
    - {"type": "session_list", "data": [...]}
    - {"type": "error", "message": "..."}
    """
    # Validate token before accepting connection
    async for db in get_db():
        try:
            from sqlalchemy import select
            from app.models import APIToken
            result = await db.execute(
                select(APIToken).where(APIToken.token == token, APIToken.active == True)
            )
            api_token = result.scalar_one_or_none()
            
            if not api_token:
                await websocket.close(code=1008, reason="Invalid or inactive token")
                return
            
            # Update last_used_at
            api_token.last_used_at = datetime.now()
            await db.commit()
            break
        except Exception as e:
            await websocket.close(code=1011, reason=f"Auth error: {str(e)}")
            return
    
    await manager.connect(websocket, session_key)
    
    try:
        # Send connection confirmation
        await manager.send_to_connection(websocket, {
            "type": "connected",
            "session_key": session_key
        })
        
        # Main message loop
        while True:
            data = await websocket.receive_json()
            
            event_type = data.get("type")
            
            if event_type == "send_message":
                await handle_send_message(websocket, session_key, data)
            
            elif event_type == "create_session":
                await handle_create_session(websocket, data)
            
            elif event_type == "list_sessions":
                await handle_list_sessions(websocket)
            
            elif event_type == "switch_session":
                # Switch to a different session
                new_session_key = data.get("session_key", "main")
                manager.disconnect(websocket, session_key)
                session_key = new_session_key
                await manager.connect(websocket, session_key)
                await manager.send_to_connection(websocket, {
                    "type": "connected",
                    "session_key": session_key
                })
            
            else:
                await manager.send_to_connection(websocket, {
                    "type": "error",
                    "message": f"Unknown event type: {event_type}"
                })
    
    except WebSocketDisconnect:
        manager.disconnect(websocket, session_key)
    except Exception as e:
        manager.disconnect(websocket, session_key)
        print(f"WebSocket error: {e}")


async def handle_send_message(websocket: WebSocket, current_session: str, data: dict):
    """Handle send_message event."""
    content = data.get("content")
    target_session = data.get("session_key", current_session)
    
    if not content:
        await manager.send_to_connection(websocket, {
            "type": "error",
            "message": "Missing content"
        })
        return
    
    async for db in get_db():
        try:
            # Store user message
            message = await store_message(
                session_key=target_session,
                role="user",
                content=content,
                db=db
            )
            
            # Broadcast to all clients on this session
            await manager.broadcast_to_session(target_session, {
                "type": "message",
                "data": {
                    "id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "created_at": message.created_at.isoformat(),
                    "message_metadata": message.message_metadata
                }
            })
            
            # Notify OpenClaw
            await bridge.send_message_to_openclaw(target_session, content, db)
            
        except Exception as e:
            await manager.send_to_connection(websocket, {
                "type": "error",
                "message": f"Failed to send message: {str(e)}"
            })


async def handle_create_session(websocket: WebSocket, data: dict):
    """Handle create_session event."""
    label = data.get("label", "New Session")
    session_key = data.get("session_key", str(uuid.uuid4()))
    
    async for db in get_db():
        try:
            session = await ensure_session_exists(session_key, db)
            if label:
                session.label = label
                await db.commit()
            
            await manager.send_to_connection(websocket, {
                "type": "session_created",
                "data": {
                    "id": session.id,
                    "session_key": session.session_key,
                    "label": session.label,
                    "created_at": session.created_at.isoformat(),
                    "is_active": session.is_active
                }
            })
        except Exception as e:
            await manager.send_to_connection(websocket, {
                "type": "error",
                "message": f"Failed to create session: {str(e)}"
            })


async def handle_list_sessions(websocket: WebSocket):
    """Handle list_sessions event."""
    async for db in get_db():
        try:
            result = await db.execute(
                select(ChatSession)
                .where(ChatSession.is_active == True)
                .order_by(desc(ChatSession.last_message_at))
            )
            sessions = result.scalars().all()
            
            await manager.send_to_connection(websocket, {
                "type": "session_list",
                "data": [
                    {
                        "id": s.id,
                        "session_key": s.session_key,
                        "label": s.label,
                        "created_at": s.created_at.isoformat(),
                        "is_active": s.is_active,
                        "last_message_at": s.last_message_at.isoformat() if s.last_message_at else None
                    }
                    for s in sessions
                ]
            })
        except Exception as e:
            await manager.send_to_connection(websocket, {
                "type": "error",
                "message": f"Failed to list sessions: {str(e)}"
            })


# --- REST Endpoints ---

@router.get("/sessions", response_model=List[ChatSessionResponse])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000)
):
    """List all active chat sessions."""
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.is_active == True)
        .order_by(desc(ChatSession.last_message_at))
        .limit(limit)
    )
    sessions = result.scalars().all()
    return sessions


@router.post("/sessions", response_model=ChatSessionResponse)
async def create_session(
    request: CreateSessionRequest,
    db: AsyncSession = Depends(get_db)
):
    """Create a new chat session."""
    session_key = request.session_key or str(uuid.uuid4())
    label = request.label or session_key
    
    # Check if session already exists
    result = await db.execute(
        select(ChatSession).where(ChatSession.session_key == session_key)
    )
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(status_code=400, detail="Session already exists")
    
    session = ChatSession(
        id=str(uuid.uuid4()),
        session_key=session_key,
        label=label,
        created_at=datetime.now(),
        is_active=True
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    
    return session


@router.get("/sessions/{session_key}/messages", response_model=List[ChatMessageResponse])
async def get_messages(
    session_key: str,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000),
    before: Optional[str] = Query(None, description="Message ID to paginate before")
):
    """Get message history for a session."""
    # Ensure session exists
    await ensure_session_exists(session_key, db)
    
    query = select(ChatMessage).where(ChatMessage.session_key == session_key)
    
    if before:
        # Get timestamp of 'before' message
        before_result = await db.execute(
            select(ChatMessage).where(ChatMessage.id == before)
        )
        before_msg = before_result.scalar_one_or_none()
        if before_msg:
            query = query.where(ChatMessage.created_at < before_msg.created_at)
    
    query = query.order_by(desc(ChatMessage.created_at)).limit(limit)
    
    result = await db.execute(query)
    messages = result.scalars().all()
    
    # Return in chronological order (oldest first)
    return list(reversed(messages))


@router.post("/sessions/{session_key}/send", response_model=ChatMessageResponse)
async def send_message(
    session_key: str,
    request: SendMessageRequest,
    db: AsyncSession = Depends(get_db)
):
    """Send a message (REST fallback for non-WebSocket clients)."""
    # Store user message
    message = await store_message(
        session_key=session_key,
        role="user",
        content=request.content,
        db=db
    )
    
    # Broadcast to WebSocket clients
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
    
    # Notify OpenClaw
    await bridge.send_message_to_openclaw(session_key, request.content, db)
    
    return message


@router.get("/sessions/{session_key}/status", response_model=TypingStatusResponse)
async def get_typing_status(session_key: str):
    """Check if agent is typing."""
    return TypingStatusResponse(
        session_key=session_key,
        is_typing=manager.is_typing(session_key)
    )


# --- Webhook Endpoint (OpenClaw Integration) ---

@router.post("/webhook/{session_key}/message")
async def webhook_receive_message(
    session_key: str,
    request: WebhookMessageRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Webhook endpoint for OpenClaw to deliver agent responses.
    
    OpenClaw calls this when it has a response ready.
    """
    await bridge.receive_agent_response(
        session_key=session_key,
        content=request.content,
        db=db,
        metadata=request.metadata
    )
    
    return {"status": "ok"}


@router.post("/webhook/{session_key}/typing")
async def webhook_set_typing(
    session_key: str,
    request: WebhookTypingRequest
):
    """
    Webhook endpoint for OpenClaw to set typing indicator.
    
    OpenClaw calls this when it starts/stops processing a message.
    """
    await bridge.set_typing_indicator(session_key, request.is_typing)
    
    return {"status": "ok"}
