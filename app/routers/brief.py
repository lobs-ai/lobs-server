"""Daily Ops Brief endpoint.

GET /api/brief/today
  - Returns today's brief as markdown + structured sections
  - Optional ?send_to_chat=true to post it to the chat thread
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.brief_service import BriefFormatter, BriefService
from app.services.chat_manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/brief", tags=["brief"])


def _section_to_dict(section) -> dict[str, Any]:
    return {
        "name": section.name,
        "icon": section.icon,
        "available": section.available,
        "error": section.error,
        "items": [
            {
                "source": item.source,
                "title": item.title,
                "detail": item.detail,
                "priority": item.priority,
                "url": item.url,
                "time": item.time.isoformat() if item.time else None,
            }
            for item in section.items
        ],
    }


@router.get("/today")
async def get_brief_today(
    send_to_chat: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Generate today's Daily Ops Brief.

    Returns markdown card + structured section data.
    Pass `send_to_chat=true` to post the brief as an assistant message.
    """
    brief = await BriefService(db).generate()
    markdown = BriefFormatter.to_markdown(brief)

    if send_to_chat:
        session_key = os.getenv("BRIEF_CHAT_SESSION_KEY", "main")
        try:
            from app.routers.chat import store_message

            msg = await store_message(
                session_key=session_key,
                role="assistant",
                content=markdown,
                db=db,
                metadata={
                    "source": "daily_brief",
                    "generated_at": brief.generated_at.isoformat(),
                },
            )
            await db.commit()

            await manager.broadcast_to_session(
                session_key,
                {
                    "type": "message",
                    "data": {
                        "id": msg.id,
                        "role": msg.role,
                        "content": msg.content,
                        "created_at": msg.created_at.isoformat(),
                        "message_metadata": msg.message_metadata,
                    },
                },
            )
            logger.info("[BRIEF] Brief posted to chat session '%s'", session_key)
        except Exception as exc:
            logger.error("[BRIEF] Failed to send brief to chat: %s", exc, exc_info=True)

    return {
        "markdown": markdown,
        "sections": [_section_to_dict(s) for s in brief.sections],
        "generated_at": brief.generated_at.isoformat(),
        "suggested_plan": brief.suggested_plan,
    }
