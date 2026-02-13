#!/usr/bin/env python3
"""Migration: Add agent column to memories table."""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.config import settings


async def migrate():
    """Add agent column to memories table."""
    engine = create_async_engine(settings.DATABASE_URL, echo=True)
    
    async with engine.begin() as conn:
        # Check if column exists
        result = await conn.execute(
            text("PRAGMA table_info(memories)")
        )
        columns = [row[1] for row in result]
        
        if "agent" in columns:
            print("✅ Column 'agent' already exists in memories table")
            return
        
        print("Adding 'agent' column to memories table...")
        
        # Add column with default value
        await conn.execute(
            text("ALTER TABLE memories ADD COLUMN agent VARCHAR NOT NULL DEFAULT 'main'")
        )
        
        # Create index
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_memories_agent ON memories (agent)")
        )
        
        # Drop the unique constraint on path (SQLite doesn't support DROP CONSTRAINT)
        # We'll handle uniqueness at the application level for (path, agent) combination
        
        print("✅ Migration completed successfully")
    
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
