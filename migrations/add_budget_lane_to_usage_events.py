#!/usr/bin/env python3
"""Add budget_lane column to model_usage_events table.

budget_lane stores the explicit task-criticality lane (critical|standard|background)
set at worker spawn time.  This replaces the expensive keyword-heuristic queries
that were used to estimate per-lane spend.  Legacy events without the column
set fall back to heuristics.
"""

import sqlite3
from pathlib import Path


def add_column_if_missing(cursor, table: str, column: str, ddl: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cursor.fetchall()]
    if column not in cols:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        return True
    return False


def main() -> None:
    db_path = Path(__file__).parent.parent / "data" / "lobs.db"
    if not db_path.exists():
        # Try fallback paths used in dev
        for fallback in (
            Path(__file__).parent.parent / "lobs.db",
            Path(__file__).parent.parent / "data.db",
        ):
            if fallback.exists():
                db_path = fallback
                break

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    added = add_column_if_missing(
        cur,
        "model_usage_events",
        "budget_lane",
        "budget_lane TEXT",
    )

    # Index for efficient per-lane spend queries
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_model_usage_events_budget_lane
        ON model_usage_events (budget_lane)
        """
    )

    conn.commit()
    conn.close()

    if added:
        print("Migration complete: added budget_lane to model_usage_events (+ index)")
    else:
        print("Migration skipped: budget_lane already exists")


if __name__ == "__main__":
    main()
