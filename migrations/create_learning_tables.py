"""Create task outcomes and learning tables for agent learning system.

Created: 2026-02-23
Phase: 1.1 - Database & Tracking
"""

import sqlite3
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings


def upgrade():
    """Create learning system tables."""
    db_path = settings.DATABASE_URL.replace("sqlite+aiosqlite:///", "")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Create task_outcomes table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_outcomes (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            worker_run_id TEXT,
            agent_type TEXT NOT NULL,
            success BOOLEAN NOT NULL,
            task_category TEXT,
            task_complexity TEXT,
            context_hash TEXT,
            human_feedback TEXT,
            review_state TEXT,
            applied_learnings TEXT,
            learning_disabled BOOLEAN NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
    """)
    
    # Create indexes for task_outcomes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_outcomes_task_id ON task_outcomes(task_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_outcomes_agent_type ON task_outcomes(agent_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_outcomes_success ON task_outcomes(success)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_outcomes_context_hash ON task_outcomes(context_hash)")
    
    # Create outcome_learnings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS outcome_learnings (
            id TEXT PRIMARY KEY,
            agent_type TEXT NOT NULL,
            pattern_name TEXT NOT NULL,
            lesson_text TEXT NOT NULL,
            lesson_rationale TEXT,
            confidence REAL NOT NULL DEFAULT 0.5,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            last_success_at TIMESTAMP,
            last_failure_at TIMESTAMP,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            source_outcome_ids TEXT,
            task_context_pattern TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
    """)
    
    # Create indexes for outcome_learnings
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_outcome_learnings_agent_type ON outcome_learnings(agent_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_outcome_learnings_pattern_name ON outcome_learnings(pattern_name)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_outcome_learnings_is_active ON outcome_learnings(is_active)")
    
    conn.commit()
    conn.close()
    
    print("✅ Created task_outcomes and outcome_learnings tables")


def downgrade():
    """Drop learning system tables."""
    db_path = settings.DATABASE_URL.replace("sqlite+aiosqlite:///", "")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("DROP TABLE IF EXISTS outcome_learnings")
    cursor.execute("DROP TABLE IF EXISTS task_outcomes")
    
    conn.commit()
    conn.close()
    
    print("✅ Dropped learning system tables")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Learning system database migration")
    parser.add_argument("--down", action="store_true", help="Run downgrade (drop tables)")
    args = parser.parse_args()
    
    if args.down:
        downgrade()
    else:
        upgrade()
