#!/usr/bin/env python3
"""Backfill project tracking modes.

Rules:
- tracking='github' when github_repo is set
- tracking='inbox' when project id is default inbox
- otherwise tracking='local' when currently null/empty
"""

import asyncio
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Project
from app.config import settings


async def main() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Project))
        projects = result.scalars().all()

        updated = 0
        for project in projects:
            current = (project.tracking or "").strip().lower()
            desired = current

            if project.github_repo:
                desired = "github"
            elif project.id == settings.DEFAULT_INBOX_PROJECT_ID:
                desired = "inbox"
            elif not current:
                desired = "local"

            if desired != current:
                project.tracking = desired
                updated += 1

        if updated:
            await db.commit()
            print(f"updated_projects={updated}")
        else:
            print("updated_projects=0")


if __name__ == "__main__":
    asyncio.run(main())
