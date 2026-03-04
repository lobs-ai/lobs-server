#!/usr/bin/env python3
"""Add spawn_count column to tasks table for runaway spawn guard."""

import sqlite3
from pathlib import Path


def main() -> None:
    db_path = Path(__file__).parent.parent / "data" / "lobs.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(tasks)")
    columns = {row[1] for row in cur.fetchall()}

    if "spawn_count" not in columns:
        cur.execute("ALTER TABLE tasks ADD COLUMN spawn_count INTEGER DEFAULT 0")
        print("Added tasks.spawn_count")
    else:
        print("tasks.spawn_count already exists")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
