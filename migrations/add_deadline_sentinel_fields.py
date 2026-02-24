#!/usr/bin/env python3
"""Add Deadline Sentinel fields to tracker_entries."""

import sqlite3

DB_PATH = "data/lobs.db"


def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    columns = [row[1] for row in cursor.execute("PRAGMA table_info(tracker_entries)").fetchall()]

    if "commitment_type" not in columns:
        cursor.execute("ALTER TABLE tracker_entries ADD COLUMN commitment_type TEXT")
        print("Added commitment_type")

    if "priority_score" not in columns:
        cursor.execute("ALTER TABLE tracker_entries ADD COLUMN priority_score INTEGER")
        print("Added priority_score")

    if "next_action" not in columns:
        cursor.execute("ALTER TABLE tracker_entries ADD COLUMN next_action TEXT")
        print("Added next_action")

    if "escalation_task_id" not in columns:
        cursor.execute("ALTER TABLE tracker_entries ADD COLUMN escalation_task_id TEXT")
        print("Added escalation_task_id")

    if "last_escalated_at" not in columns:
        cursor.execute("ALTER TABLE tracker_entries ADD COLUMN last_escalated_at DATETIME")
        print("Added last_escalated_at")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
