"""Add project_id and duration_seconds to worker_runs for analytics."""
import sqlite3, os

DATABASE_PATH = os.environ.get("DATABASE_PATH", "./data/lobs.db")

def migrate():
    conn = sqlite3.connect(DATABASE_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(worker_runs)")
    cols = {row[1] for row in cur.fetchall()}
    if "project_id" not in cols:
        cur.execute("ALTER TABLE worker_runs ADD COLUMN project_id TEXT")
        print("Added project_id")
    if "duration_seconds" not in cols:
        cur.execute("ALTER TABLE worker_runs ADD COLUMN duration_seconds REAL")
        print("Added duration_seconds")
    conn.commit()
    conn.close()
    print("Migration complete")

if __name__ == "__main__":
    migrate()
