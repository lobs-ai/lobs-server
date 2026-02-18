#!/usr/bin/env python3
"""Phase 4 migration: backfill knowledge_requests from research_requests."""

import asyncio
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import ResearchRequest, KnowledgeRequest


async def migrate() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ResearchRequest))
        rows = result.scalars().all()
        created = 0
        for r in rows:
            existing = await db.get(KnowledgeRequest, r.id)
            if existing:
                continue
            db.add(KnowledgeRequest(
                id=r.id,
                project_id=r.project_id,
                topic_id=r.topic_id,
                prompt=r.prompt or "",
                status=r.status or "pending",
                response=r.response,
                source_research_request_id=r.id,
            ))
            created += 1
        await db.commit()
        print(f"Backfill complete: created {created} knowledge_requests")


if __name__ == "__main__":
    asyncio.run(migrate())
