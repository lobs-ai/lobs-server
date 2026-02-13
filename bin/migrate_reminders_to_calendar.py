#!/usr/bin/env python3
"""Migration: Replace reminders table with scheduled_events table."""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.config import settings


async def migrate():
    """Migrate from reminders to scheduled_events."""
    engine = create_async_engine(settings.DATABASE_URL, echo=True)
    
    async with engine.begin() as conn:
        # Check if scheduled_events already exists
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_events'")
        )
        if result.fetchone():
            print("✅ Table 'scheduled_events' already exists")
            
            # Check if we need to drop reminders
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='reminders'")
            )
            if result.fetchone():
                print("Dropping old 'reminders' table...")
                await conn.execute(text("DROP TABLE reminders"))
                print("✅ Dropped reminders table")
            
            return
        
        print("Creating 'scheduled_events' table...")
        
        # Create new scheduled_events table
        await conn.execute(text("""
            CREATE TABLE scheduled_events (
                id VARCHAR PRIMARY KEY,
                title VARCHAR NOT NULL,
                description TEXT,
                event_type VARCHAR NOT NULL,
                scheduled_at DATETIME NOT NULL,
                end_at DATETIME,
                all_day BOOLEAN DEFAULT 0,
                recurrence_rule VARCHAR,
                recurrence_end DATETIME,
                target_type VARCHAR NOT NULL,
                target_agent VARCHAR,
                task_project_id VARCHAR,
                task_notes TEXT,
                task_priority VARCHAR,
                status VARCHAR NOT NULL DEFAULT 'pending',
                last_fired_at DATETIME,
                next_fire_at DATETIME,
                fire_count INTEGER DEFAULT 0,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                FOREIGN KEY (task_project_id) REFERENCES projects(id)
            )
        """))
        
        # Create indices for common queries
        await conn.execute(text(
            "CREATE INDEX ix_scheduled_events_scheduled_at ON scheduled_events (scheduled_at)"
        ))
        await conn.execute(text(
            "CREATE INDEX ix_scheduled_events_status ON scheduled_events (status)"
        ))
        await conn.execute(text(
            "CREATE INDEX ix_scheduled_events_event_type ON scheduled_events (event_type)"
        ))
        await conn.execute(text(
            "CREATE INDEX ix_scheduled_events_next_fire_at ON scheduled_events (next_fire_at)"
        ))
        
        print("✅ Created scheduled_events table with indices")
        
        # Check if reminders table exists
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='reminders'")
        )
        
        if result.fetchone():
            print("Migrating data from reminders to scheduled_events...")
            
            # Migrate existing reminders
            await conn.execute(text("""
                INSERT INTO scheduled_events (
                    id, title, description, event_type, scheduled_at,
                    target_type, status, created_at, updated_at
                )
                SELECT 
                    id,
                    title,
                    NULL as description,
                    'reminder' as event_type,
                    due_at as scheduled_at,
                    'self' as target_type,
                    'pending' as status,
                    CURRENT_TIMESTAMP as created_at,
                    CURRENT_TIMESTAMP as updated_at
                FROM reminders
            """))
            
            # Get count of migrated rows
            result = await conn.execute(
                text("SELECT COUNT(*) FROM scheduled_events WHERE event_type = 'reminder'")
            )
            count = result.scalar()
            print(f"✅ Migrated {count} reminder(s)")
            
            # Drop old table
            print("Dropping old 'reminders' table...")
            await conn.execute(text("DROP TABLE reminders"))
            print("✅ Dropped reminders table")
        else:
            print("ℹ️  No reminders table found (skipping migration)")
        
        print("✅ Migration completed successfully")
    
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
