"""Add external_id and external_source to scheduled_events for calendar sync."""

import sqlite3
import sys

DB_PATH = "data/lobs.db"


def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check if columns already exist
    columns = [row[1] for row in cursor.execute("PRAGMA table_info(scheduled_events)").fetchall()]

    if "external_id" not in columns:
        cursor.execute("ALTER TABLE scheduled_events ADD COLUMN external_id TEXT")
        print("Added external_id to scheduled_events")

    if "external_source" not in columns:
        cursor.execute("ALTER TABLE scheduled_events ADD COLUMN external_source TEXT")
        print("Added external_source to scheduled_events")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
