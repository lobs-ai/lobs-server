#!/usr/bin/env python3
"""Add summary, approved_by, and feedback columns to agent_reflections."""

import os
import asyncio


def run_postgres():
    import asyncpg

    async def migrate():
        database_url = os.environ.get("DATABASE_URL", "")
        conn_str = database_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(conn_str)
        try:
            await conn.execute("""
                ALTER TABLE agent_reflections
                    ADD COLUMN IF NOT EXISTS summary TEXT,
                    ADD COLUMN IF NOT EXISTS approved_by VARCHAR,
                    ADD COLUMN IF NOT EXISTS feedback TEXT
            """)
            print("Migration complete: added summary, approved_by, feedback to agent_reflections")
        finally:
            await conn.close()

    asyncio.run(migrate())


def run_sqlite(db_path):
    import sqlite3

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(agent_reflections)")
    existing = {row[1] for row in cur.fetchall()}

    added = []
    for col, col_type in [("summary", "TEXT"), ("approved_by", "VARCHAR"), ("feedback", "TEXT")]:
        if col not in existing:
            cur.execute(f"ALTER TABLE agent_reflections ADD COLUMN {col} {col_type}")
            added.append(col)

    conn.commit()
    conn.close()
    if added:
        print(f"Migration complete: added {', '.join(added)}")
    else:
        print("Columns already exist — nothing to do")


if __name__ == "__main__":
    from pathlib import Path
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith("postgresql"):
        run_postgres()
    else:
        db_path = os.environ.get("DATABASE_PATH", str(Path(__file__).parent.parent / "data" / "lobs.db"))
        run_sqlite(db_path)
