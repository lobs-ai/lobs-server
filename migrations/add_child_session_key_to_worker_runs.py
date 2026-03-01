"""Add child_session_key and agent_type to worker_runs for restart recovery."""
import sqlite3, os

DB_PATH = os.environ.get("DATABASE_URL", "lobs.db").replace("sqlite:///", "")

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(worker_runs)")
    cols = {row[1] for row in cur.fetchall()}
    if "child_session_key" not in cols:
        cur.execute("ALTER TABLE worker_runs ADD COLUMN child_session_key TEXT")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_worker_runs_child_session_key ON worker_runs (child_session_key)")
        print("Added child_session_key")
    if "agent_type" not in cols:
        cur.execute("ALTER TABLE worker_runs ADD COLUMN agent_type TEXT")
        print("Added agent_type")
    conn.commit(); conn.close()
    print("Migration complete")

if __name__ == "__main__":
    migrate()
