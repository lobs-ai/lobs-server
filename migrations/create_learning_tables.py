"""Create learning_plans and learning_lessons tables."""
import sqlite3

DB_PATH = "data/lobs.db"

def migrate():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS learning_plans (
            id TEXT PRIMARY KEY,
            topic TEXT NOT NULL,
            goal TEXT,
            total_days INTEGER DEFAULT 30,
            current_day INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            schedule_cron TEXT DEFAULT '0 7 * * *',
            schedule_tz TEXT DEFAULT 'America/New_York',
            delivery_channel TEXT DEFAULT 'discord',
            plan_outline JSON,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS learning_lessons (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES learning_plans(id),
            day_number INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT,
            summary TEXT,
            delivered_at DATETIME,
            document_path TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    print("Created learning_plans + learning_lessons tables.")

if __name__ == "__main__":
    migrate()
