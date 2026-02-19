"""Create diagnostic_trigger_events table for reactive diagnostic auditing."""

import sqlite3
from pathlib import Path


def migrate(db_path: str = "data/lobs.db") -> None:
    path = Path(db_path)
    if not path.exists():
        print(f"Database not found at {db_path}")
        return

    conn = sqlite3.connect(path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS diagnostic_trigger_events (
                id TEXT PRIMARY KEY,
                trigger_type TEXT NOT NULL,
                trigger_key TEXT NOT NULL,
                status TEXT NOT NULL,
                suppression_reason TEXT,
                agent_type TEXT,
                task_id TEXT,
                project_id TEXT,
                trigger_payload JSON,
                diagnostic_reflection_id TEXT,
                diagnostic_result JSON,
                remediation_task_ids JSON,
                outcome JSON,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id),
                FOREIGN KEY(project_id) REFERENCES projects(id),
                FOREIGN KEY(diagnostic_reflection_id) REFERENCES agent_reflections(id)
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_diag_events_trigger_type ON diagnostic_trigger_events(trigger_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_diag_events_trigger_key ON diagnostic_trigger_events(trigger_key)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_diag_events_status ON diagnostic_trigger_events(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_diag_events_created_at ON diagnostic_trigger_events(created_at)")
        conn.commit()
        print("✅ Created diagnostic_trigger_events table")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
