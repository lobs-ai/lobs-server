#!/usr/bin/env python3
"""Create topics table and migrate from string-based topics to FK relationships."""

import sqlite3
import sys
import uuid
from pathlib import Path
from datetime import datetime

def generate_id():
    """Generate a unique ID."""
    return str(uuid.uuid4())

def slugify(text):
    """Create a simple slug from text for topic IDs."""
    if not text:
        return generate_id()
    # Simple slugification: lowercase, replace spaces with hyphens
    slug = text.lower().replace(" ", "-").replace("_", "-")
    # Remove any non-alphanumeric characters except hyphens
    slug = ''.join(c for c in slug if c.isalnum() or c == '-')
    # Add prefix to ensure uniqueness
    return f"topic-{slug}-{generate_id()[:8]}"

def main():
    db_path = Path(__file__).parent.parent / "data" / "lobs.db"
    
    if not db_path.exists():
        print(f"Error: Database not found at {db_path}")
        sys.exit(1)
    
    print("Creating topics table and migrating data...")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if topics table already exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='topics'")
        if cursor.fetchone():
            print("Topics table already exists")
            # Check if migration is complete
            cursor.execute("PRAGMA table_info(agent_documents)")
            columns = [row[1] for row in cursor.fetchall()]
            if "topic_id" in columns and "topic" not in columns:
                print("Migration already complete, skipping")
                return
        
        # Step 1: Create topics table
        print("Step 1: Creating topics table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS topics (
                id VARCHAR PRIMARY KEY,
                title VARCHAR NOT NULL UNIQUE,
                description TEXT,
                icon VARCHAR,
                linked_project_id VARCHAR,
                auto_created BOOLEAN NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (linked_project_id) REFERENCES projects(id)
            )
        """)
        print("✓ Topics table created")
        
        # Step 2: Extract distinct topics from agent_documents
        print("Step 2: Extracting distinct topics from agent_documents...")
        cursor.execute("SELECT DISTINCT topic FROM agent_documents WHERE topic IS NOT NULL AND topic != ''")
        distinct_topics = cursor.fetchall()
        print(f"Found {len(distinct_topics)} distinct topics")
        
        # Step 3: Create Topic records
        print("Step 3: Creating Topic records...")
        topic_mapping = {}  # old topic string -> new topic_id
        now = datetime.utcnow().isoformat()
        
        for (topic_str,) in distinct_topics:
            topic_id = slugify(topic_str)
            cursor.execute("""
                INSERT INTO topics (id, title, description, auto_created, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (topic_id, topic_str, None, 1, now, now))
            topic_mapping[topic_str] = topic_id
            print(f"  Created topic: {topic_str} -> {topic_id}")
        
        print(f"✓ Created {len(topic_mapping)} topics")
        
        # Step 4: Add topic_id column to agent_documents
        print("Step 4: Adding topic_id column to agent_documents...")
        cursor.execute("PRAGMA table_info(agent_documents)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if "topic_id" not in columns:
            cursor.execute("""
                ALTER TABLE agent_documents 
                ADD COLUMN topic_id VARCHAR
            """)
            # Add foreign key constraint in a new table (SQLite limitation workaround)
            # Note: In production, consider using Alembic for proper FK handling
            print("✓ Added topic_id column")
        
        # Step 5: Update agent_documents to use topic_id FK
        print("Step 5: Updating agent_documents with topic_id references...")
        for topic_str, topic_id in topic_mapping.items():
            cursor.execute("""
                UPDATE agent_documents 
                SET topic_id = ? 
                WHERE topic = ?
            """, (topic_id, topic_str))
        
        # Count updated rows
        cursor.execute("SELECT COUNT(*) FROM agent_documents WHERE topic_id IS NOT NULL")
        updated_count = cursor.fetchone()[0]
        print(f"✓ Updated {updated_count} agent_documents with topic_id")
        
        # Step 6: Create new table without old topic column
        print("Step 6: Recreating agent_documents table without topic column...")
        
        # Get the current table structure
        cursor.execute("PRAGMA table_info(agent_documents)")
        columns_info = cursor.fetchall()
        
        # Create temporary table with new schema
        cursor.execute("""
            CREATE TABLE agent_documents_new (
                id VARCHAR PRIMARY KEY,
                title VARCHAR NOT NULL,
                filename VARCHAR,
                relative_path VARCHAR,
                content TEXT,
                content_is_truncated BOOLEAN DEFAULT 0,
                source VARCHAR,
                status VARCHAR,
                topic_id VARCHAR,
                project_id VARCHAR,
                task_id VARCHAR,
                date TIMESTAMP,
                is_read BOOLEAN NOT NULL DEFAULT 0,
                summary TEXT,
                FOREIGN KEY (topic_id) REFERENCES topics(id),
                FOREIGN KEY (project_id) REFERENCES projects(id),
                FOREIGN KEY (task_id) REFERENCES tasks(id)
            )
        """)
        
        # Copy data
        cursor.execute("""
            INSERT INTO agent_documents_new 
            SELECT id, title, filename, relative_path, content, content_is_truncated,
                   source, status, topic_id, project_id, task_id, date, is_read, summary
            FROM agent_documents
        """)
        
        # Drop old table and rename new one
        cursor.execute("DROP TABLE agent_documents")
        cursor.execute("ALTER TABLE agent_documents_new RENAME TO agent_documents")
        print("✓ Recreated agent_documents table")
        
        # Step 7: Add topic_id to research_requests
        print("Step 7: Adding topic_id column to research_requests...")
        cursor.execute("PRAGMA table_info(research_requests)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if "topic_id" not in columns:
            cursor.execute("""
                ALTER TABLE research_requests 
                ADD COLUMN topic_id VARCHAR
            """)
            print("✓ Added topic_id column to research_requests")
        
        conn.commit()
        print("\n✅ Migration completed successfully!")
        print(f"   - Created {len(topic_mapping)} topics")
        print(f"   - Updated {updated_count} agent documents")
        print(f"   - Migrated from string topics to FK relationships")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
