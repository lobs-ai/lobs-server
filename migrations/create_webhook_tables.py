#!/usr/bin/env python3
"""Create webhook tables for external integrations."""

import sqlite3
from pathlib import Path


def table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


def main():
    db_path = Path(__file__).parent.parent / "data" / "lobs.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Create webhook_registrations table
    if not table_exists(cur, "webhook_registrations"):
        cur.execute("""
            CREATE TABLE webhook_registrations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider TEXT NOT NULL,
                secret TEXT NOT NULL,
                event_filters JSON,
                target_action TEXT NOT NULL,
                action_config JSON,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_received_at TIMESTAMP
            )
        """)
        cur.execute(
            "CREATE INDEX idx_webhook_registrations_provider ON webhook_registrations(provider)"
        )
        cur.execute(
            "CREATE INDEX idx_webhook_registrations_active ON webhook_registrations(active)"
        )
        print("Created webhook_registrations table")
    else:
        print("webhook_registrations table already exists")

    # Create webhook_events table
    if not table_exists(cur, "webhook_events"):
        cur.execute("""
            CREATE TABLE webhook_events (
                id TEXT PRIMARY KEY,
                registration_id TEXT,
                provider TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload JSON NOT NULL,
                headers JSON,
                signature_valid INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                processing_result JSON,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                processed_at TIMESTAMP,
                FOREIGN KEY (registration_id) REFERENCES webhook_registrations(id)
            )
        """)
        cur.execute(
            "CREATE INDEX idx_webhook_events_registration ON webhook_events(registration_id)"
        )
        cur.execute(
            "CREATE INDEX idx_webhook_events_provider ON webhook_events(provider)"
        )
        cur.execute(
            "CREATE INDEX idx_webhook_events_type ON webhook_events(event_type)"
        )
        cur.execute(
            "CREATE INDEX idx_webhook_events_status ON webhook_events(status)"
        )
        cur.execute(
            "CREATE INDEX idx_webhook_events_created ON webhook_events(created_at)"
        )
        print("Created webhook_events table")
    else:
        print("webhook_events table already exists")

    # Create webhook_deliveries table
    if not table_exists(cur, "webhook_deliveries"):
        cur.execute("""
            CREATE TABLE webhook_deliveries (
                id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL,
                error_message TEXT,
                next_retry_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (event_id) REFERENCES webhook_events(id)
            )
        """)
        cur.execute(
            "CREATE INDEX idx_webhook_deliveries_event ON webhook_deliveries(event_id)"
        )
        cur.execute(
            "CREATE INDEX idx_webhook_deliveries_status ON webhook_deliveries(status)"
        )
        cur.execute(
            "CREATE INDEX idx_webhook_deliveries_retry ON webhook_deliveries(next_retry_at)"
        )
        print("Created webhook_deliveries table")
    else:
        print("webhook_deliveries table already exists")

    conn.commit()
    conn.close()
    print("Migration complete: webhook tables created")


if __name__ == "__main__":
    main()
