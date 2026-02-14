"""Create tracker_notifications table."""
import asyncio
from sqlalchemy import text
from app.database import engine

async def migrate():
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tracker_notifications (
                id TEXT PRIMARY KEY,
                deadline_key TEXT NOT NULL,
                notification_type TEXT NOT NULL,
                message_summary TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                cooldown_hours INTEGER DEFAULT 12
            )
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_tracker_notifications_deadline_key 
            ON tracker_notifications(deadline_key, sent_at)
        """))
    print("Migration complete: tracker_notifications table created")

if __name__ == "__main__":
    asyncio.run(migrate())
