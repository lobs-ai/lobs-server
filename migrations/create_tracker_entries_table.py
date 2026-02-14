#!/usr/bin/env python3
"""Create tracker_entries table for personal work tracker."""

import sqlite3
import sys
from pathlib import Path

def main():
    db_path = Path(__file__).parent.parent / "data" / "lobs.db"
    
    if not db_path.exists():
        print(f"Error: Database not found at {db_path}")
        sys.exit(1)
    
    print(f"Creating tracker_entries table...")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if table already exists
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='tracker_entries'
        """)
        
        if cursor.fetchone():
            print("Table already exists, skipping migration")
            return
        
        # Create table
        cursor.execute("""
            CREATE TABLE tracker_entries (
                id VARCHAR NOT NULL PRIMARY KEY,
                type VARCHAR NOT NULL,
                raw_text TEXT NOT NULL,
                duration INTEGER,
                category VARCHAR,
                due_date DATETIME,
                estimated_minutes INTEGER,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes
        cursor.execute("""
            CREATE INDEX idx_tracker_entries_type ON tracker_entries(type)
        """)
        
        cursor.execute("""
            CREATE INDEX idx_tracker_entries_due_date ON tracker_entries(due_date)
        """)
        
        cursor.execute("""
            CREATE INDEX idx_tracker_entries_created_at ON tracker_entries(created_at)
        """)
        
        conn.commit()
        print("✓ Table created successfully")
        
    except Exception as e:
        print(f"Error: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
