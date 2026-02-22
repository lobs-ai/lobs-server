#!/usr/bin/env python3
"""Add model_tier column to tasks table."""

import sqlite3
from pathlib import Path


def add_column_if_missing(cursor, table: str, column: str, ddl: str):
    cursor.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cursor.fetchall()]
    if column not in cols:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def main():
    db_path = Path(__file__).parent.parent / "data" / "lobs.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    add_column_if_missing(cur, "tasks", "model_tier", "model_tier TEXT")

    conn.commit()
    conn.close()
    print("Migration complete: added model_tier to tasks")


if __name__ == "__main__":
    main()
