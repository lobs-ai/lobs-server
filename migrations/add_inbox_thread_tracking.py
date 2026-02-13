#!/usr/bin/env python3
"""Add last_processed_message_id column to inbox_threads table."""

import sqlite3
import sys
from pathlib import Path

def main():
    db_path = Path(__file__).parent.parent / "data" / "lobs.db"
    
    if not db_path.exists():
        print(f"Error: Database not found at {db_path}")
        sys.exit(1)
    
    print(f"Adding last_processed_message_id column to inbox_threads...")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if column already exists
        cursor.execute("PRAGMA table_info(inbox_threads)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if "last_processed_message_id" in columns:
            print("Column already exists, skipping migration")
            return
        
        # Add column
        cursor.execute("""
            ALTER TABLE inbox_threads 
            ADD COLUMN last_processed_message_id VARCHAR
        """)
        
        conn.commit()
        print("✓ Column added successfully")
        
    except Exception as e:
        print(f"Error: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
