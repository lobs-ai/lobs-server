#!/usr/bin/env python3
"""Task improvements schema foundation migration (phases 0.5-3)."""

import sqlite3
from pathlib import Path


def add_column_if_missing(cursor, table: str, column: str, ddl: str):
    cursor.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cursor.fetchall()]
    if column not in cols:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def main():
    db_path = Path(__file__).parent.parent / "data" / "lobs.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    add_column_if_missing(cur, "tasks", "external_source", "external_source TEXT")
    add_column_if_missing(cur, "tasks", "external_id", "external_id TEXT")
    add_column_if_missing(cur, "tasks", "external_updated_at", "external_updated_at TIMESTAMP")
    add_column_if_missing(cur, "tasks", "sync_state", "sync_state TEXT")
    add_column_if_missing(cur, "tasks", "conflict_payload", "conflict_payload JSON")
    add_column_if_missing(cur, "tasks", "workspace_id", "workspace_id TEXT")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            description TEXT,
            is_default BOOLEAN NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS workspace_files (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            path TEXT NOT NULL,
            title TEXT,
            content TEXT,
            content_hash TEXT,
            file_metadata JSON,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(workspace_id, path),
            FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS file_links (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            source_file_id TEXT NOT NULL,
            target_file_id TEXT NOT NULL,
            relation TEXT NOT NULL DEFAULT 'references',
            weight FLOAT DEFAULT 1.0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_profiles (
            id TEXT PRIMARY KEY,
            agent_type TEXT NOT NULL UNIQUE,
            display_name TEXT,
            prompt_template TEXT,
            config JSON,
            policy_tier TEXT NOT NULL DEFAULT 'standard',
            active BOOLEAN NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS routine_registry (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            trigger TEXT,
            schedule TEXT,
            policy_tier TEXT NOT NULL DEFAULT 'standard',
            enabled BOOLEAN NOT NULL DEFAULT 1,
            config JSON,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_requests (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            project_id TEXT,
            topic_id TEXT,
            prompt TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            response TEXT,
            source_research_request_id TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
    print("Task improvements migration complete")


if __name__ == "__main__":
    main()
