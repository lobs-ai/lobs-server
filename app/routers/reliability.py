"""Reliability digest API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.reliability_digest import (
    ReliabilityDigestGenerator,
    format_digest_markdown,
    send_digest_to_inbox,
)

router = APIRouter(prefix="/reliability", tags=["reliability"])


@router.post("/digest/generate")
async def generate_reliability_digest(
    hours: int = 24,
    send_to_inbox: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a reliability digest for the last N hours.
    
    Query params:
    - hours: Time window to analyze (default: 24)
    - send_to_inbox: Whether to send the digest to inbox (default: True)
    
    Returns:
    - digest: Structured reliability data
    - markdown: Formatted markdown version
    - inbox_item_id: ID of created inbox item (if send_to_inbox=True)
    """
    if hours < 1 or hours > 168:  # Max 1 week
        raise HTTPException(status_code=400, detail="hours must be between 1 and 168")
    
    generator = ReliabilityDigestGenerator(db)
    digest = await generator.generate_digest(hours=hours)
    markdown = format_digest_markdown(digest)
    
    inbox_item_id = None
    if send_to_inbox:
        inbox_item_id = await send_digest_to_inbox(db, digest)
        await db.commit()
    
    return {
        "digest": digest,
        "markdown": markdown,
        "inbox_item_id": inbox_item_id,
    }


@router.get("/digest/preview")
async def preview_reliability_digest(
    hours: int = 24,
    db: AsyncSession = Depends(get_db),
):
    """
    Preview a reliability digest without sending it to inbox.
    
    Returns just the structured digest data and markdown.
    """
    if hours < 1 or hours > 168:
        raise HTTPException(status_code=400, detail="hours must be between 1 and 168")
    
    generator = ReliabilityDigestGenerator(db)
    digest = await generator.generate_digest(hours=hours)
    markdown = format_digest_markdown(digest)
    
    return {
        "digest": digest,
        "markdown": markdown,
    }
