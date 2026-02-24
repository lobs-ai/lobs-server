"""Create workflow engine tables: workflow_definitions, workflow_runs, workflow_events, workflow_subscriptions."""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "lobs.db"


def migrate():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # Check if already migrated
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='workflow_definitions'")
    if cur.fetchone():
        print("workflow_definitions table already exists — skipping migration")
        conn.close()
        return

    cur.executescript("""
        CREATE TABLE workflow_definitions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            nodes JSON NOT NULL,
            edges JSON NOT NULL,
            trigger JSON,
            metadata JSON,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE workflow_runs (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL REFERENCES workflow_definitions(id),
            workflow_version INTEGER NOT NULL,
            task_id TEXT REFERENCES tasks(id),
            trigger_type TEXT NOT NULL,
            trigger_payload JSON,
            status TEXT NOT NULL DEFAULT 'pending',
            current_node TEXT,
            node_states JSON NOT NULL DEFAULT '{}',
            context JSON NOT NULL DEFAULT '{}',
            session_key TEXT,
            error TEXT,
            started_at DATETIME,
            finished_at DATETIME,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX ix_workflow_runs_status ON workflow_runs(status);
        CREATE INDEX ix_workflow_runs_workflow_id ON workflow_runs(workflow_id);
        CREATE INDEX ix_workflow_runs_task_id ON workflow_runs(task_id);

        CREATE TABLE workflow_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            payload JSON NOT NULL,
            source TEXT,
            processed BOOLEAN NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX ix_workflow_events_type ON workflow_events(event_type);
        CREATE INDEX ix_workflow_events_processed ON workflow_events(processed);

        CREATE TABLE workflow_subscriptions (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL REFERENCES workflow_definitions(id),
            event_pattern TEXT NOT NULL,
            filter_conditions JSON,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX ix_workflow_subscriptions_workflow ON workflow_subscriptions(workflow_id);
    """)

    conn.commit()
    conn.close()
    print("Migration complete: workflow engine tables created")


if __name__ == "__main__":
    migrate()
