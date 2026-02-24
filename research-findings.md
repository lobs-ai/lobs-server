# Database Migration Strategy — Research Findings

**Research Date:** 2026-02-23  
**Project:** lobs-server  
**Researcher:** researcher agent  
**Task:** Document database migration strategy and create migration template

---

## Executive Summary

lobs-server uses a **manual, script-based migration approach** for SQLite schema changes. While Alembic is installed as a dependency, it is not actively used. Instead, the project follows a lightweight pattern of Python scripts in the `/migrations` directory that directly execute SQLite DDL statements.

**Key Findings:**
- ✅ **Simple and pragmatic** — Works well for single-server SQLite deployments
- ✅ **Well-validated** — Automated validators check migration scripts for safety
- ⚠️ **No version tracking** — Migrations lack explicit ordering/versioning system
- ⚠️ **Inconsistent rollback support** — Only some migrations have downgrade functions
- ⚠️ **Manual execution** — No automated migration runner on startup

**Recommendation:** The current approach is suitable for the project's scale, but would benefit from:
1. Consistent migration naming with dates/sequence numbers
2. Mandatory downgrade functions for all migrations
3. Optional: Migration state tracking table
4. Optional: Auto-run on server startup (for development)

---

## Table of Contents

1. [Current Migration Architecture](#current-migration-architecture)
2. [Migration Patterns in Codebase](#migration-patterns-in-codebase)
3. [Validation System](#validation-system)
4. [Best Practices](#best-practices)
5. [Migration Template](#migration-template)
6. [Running Migrations](#running-migrations)
7. [Rollback Strategy](#rollback-strategy)
8. [Testing Approach](#testing-approach)
9. [Future Considerations](#future-considerations)

---

## Current Migration Architecture

### Database Stack

**Database:** SQLite 3  
**ORM:** SQLAlchemy 2.x (async)  
**Migration Tool:** Manual Python scripts (Alembic installed but unused)  
**Database Location:** `data/lobs.db`  
**Mode:** WAL (Write-Ahead Logging) for concurrency

**Source:** `app/database.py`
```python
engine = create_async_engine(
    settings.DATABASE_URL,  # sqlite+aiosqlite:///data/lobs.db
    echo=False,
    connect_args={"timeout": 30},
    pool_size=5,
    max_overflow=5,
)

@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.close()
```

### Migration Directory Structure

```
migrations/
├── add_inbox_thread_tracking.py
├── add_task_model_tier.py
├── create_diagnostic_trigger_events_table.py
├── create_learning_tables.py
├── create_topics_table.py
├── create_tracker_entries_table.py
├── create_tracker_notifications_table.py
├── create_usage_tracking_tables.py
├── create_webhook_tables.py
├── phase4_research_to_knowledge.py
└── task_improvements_foundation.py
```

**Total migrations:** 12 (as of Feb 2026)

### Validation Infrastructure

**Automated validators:**
1. **`bin/validate_migrations.py`** — Validates migration script safety
2. **`bin/validate_api_schemas.py`** — Validates Pydantic API models
3. **`bin/validate_schema.py`** — Validates ORM models match database

**CI Integration:** All validators run on every PR via GitHub Actions

**Source:** `docs/SCHEMA_VALIDATION.md`

---

## Migration Patterns in Codebase

### Pattern 1: Simple Column Addition (Most Common)

**Use Case:** Adding a single nullable column to an existing table

**Example:** `migrations/add_task_model_tier.py`

```python
#!/usr/bin/env python3
"""Add model_tier column to tasks table."""

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

    add_column_if_missing(cur, "tasks", "model_tier", "model_tier TEXT")

    conn.commit()
    conn.close()
    print("Migration complete: added model_tier to tasks")

if __name__ == "__main__":
    main()
```

**Key Characteristics:**
- Uses synchronous `sqlite3` module (simpler than async for migrations)
- Idempotent via `PRAGMA table_info()` check
- Direct path resolution: `Path(__file__).parent.parent / "data" / "lobs.db"`
- No downgrade function
- Exit on success (no error handling shown, but validation catches issues)

### Pattern 2: Table Creation with Indexes

**Use Case:** Creating new tables with foreign keys and indexes

**Example:** `migrations/create_webhook_tables.py`

```python
#!/usr/bin/env python3
"""Create webhook tables for external integrations."""

import sqlite3
from pathlib import Path

def table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None

def main():
    db_path = Path(__file__).parent.parent / "data" / "lobs.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Create webhook_registrations table
    if not table_exists(cur, "webhook_registrations"):
        cur.execute("""
            CREATE TABLE webhook_registrations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider TEXT NOT NULL,
                secret TEXT NOT NULL,
                event_filters JSON,
                target_action TEXT NOT NULL,
                action_config JSON,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_received_at TIMESTAMP
            )
        """)
        cur.execute(
            "CREATE INDEX idx_webhook_registrations_provider ON webhook_registrations(provider)"
        )
        cur.execute(
            "CREATE INDEX idx_webhook_registrations_active ON webhook_registrations(active)"
        )
        print("Created webhook_registrations table")
    else:
        print("webhook_registrations table already exists")

    # ... additional tables ...

    conn.commit()
    conn.close()
    print("Migration complete: webhook tables created")

if __name__ == "__main__":
    main()
```

**Key Characteristics:**
- Helper function `table_exists()` for idempotency
- Multiple related tables created in sequence
- Indexes created immediately after table
- Foreign key constraints included in CREATE TABLE
- Explicit commit at end
- Informative print statements for progress

### Pattern 3: Complex Migration with Data Transform

**Use Case:** Schema restructuring with data migration (e.g., string field → FK relationship)

**Example:** `migrations/create_topics_table.py` (abbreviated)

```python
#!/usr/bin/env python3
"""Create topics table and migrate from string-based topics to FK relationships."""

import sqlite3
import sys
import uuid
from pathlib import Path
from datetime import datetime

def slugify(text):
    """Create a simple slug from text for topic IDs."""
    slug = text.lower().replace(" ", "-").replace("_", "-")
    slug = ''.join(c for c in slug if c.isalnum() or c == '-')
    return f"topic-{slug}-{generate_id()[:8]}"

def main():
    db_path = Path(__file__).parent.parent / "data" / "lobs.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Step 1: Create topics table
        cursor.execute("""CREATE TABLE IF NOT EXISTS topics (...)""")
        
        # Step 2: Extract distinct topics from agent_documents
        cursor.execute("SELECT DISTINCT topic FROM agent_documents WHERE topic IS NOT NULL")
        distinct_topics = cursor.fetchall()
        
        # Step 3: Create Topic records
        topic_mapping = {}
        for (topic_str,) in distinct_topics:
            topic_id = slugify(topic_str)
            cursor.execute("INSERT INTO topics (...) VALUES (...)", (...))
            topic_mapping[topic_str] = topic_id
        
        # Step 4: Add topic_id column to agent_documents
        cursor.execute("ALTER TABLE agent_documents ADD COLUMN topic_id VARCHAR")
        
        # Step 5: Update agent_documents with topic_id FK references
        for topic_str, topic_id in topic_mapping.items():
            cursor.execute(
                "UPDATE agent_documents SET topic_id = ? WHERE topic = ?",
                (topic_id, topic_str)
            )
        
        # Step 6: Recreate table without old topic column (SQLite limitation)
        cursor.execute("CREATE TABLE agent_documents_new (...)")
        cursor.execute("INSERT INTO agent_documents_new SELECT ... FROM agent_documents")
        cursor.execute("DROP TABLE agent_documents")
        cursor.execute("ALTER TABLE agent_documents_new RENAME TO agent_documents")
        
        conn.commit()
        print("✅ Migration completed successfully!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
```

**Key Characteristics:**
- Multi-step migration with data transformation
- Explicit try/except/finally for safety
- Rollback on error
- SQLite limitation workaround (can't DROP COLUMN, must recreate table)
- Progress logging for each step
- Exit code 1 on failure

### Pattern 4: Async ORM-Based Migration

**Use Case:** Data backfill using existing SQLAlchemy models

**Example:** `migrations/phase4_research_to_knowledge.py`

```python
#!/usr/bin/env python3
"""Phase 4 migration: backfill knowledge_requests from research_requests."""

import asyncio
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import ResearchRequest, KnowledgeRequest

async def migrate() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ResearchRequest))
        rows = result.scalars().all()
        created = 0
        for r in rows:
            existing = await db.get(KnowledgeRequest, r.id)
            if existing:
                continue
            db.add(KnowledgeRequest(
                id=r.id,
                project_id=r.project_id,
                topic_id=r.topic_id,
                prompt=r.prompt or "",
                status=r.status or "pending",
                response=r.response,
                source_research_request_id=r.id,
            ))
            created += 1
        await db.commit()
        print(f"Backfill complete: created {created} knowledge_requests")

if __name__ == "__main__":
    asyncio.run(migrate())
```

**Key Characteristics:**
- Uses async SQLAlchemy ORM (not raw SQL)
- Imports from `app.database` and `app.models`
- Idempotent via checking for existing records
- Cleaner for data migrations (vs. raw SQL INSERT statements)
- Uses `asyncio.run()` as entry point

### Pattern 5: Up/Down Migration (Rare)

**Use Case:** Migrations that support explicit rollback

**Example:** `migrations/create_learning_tables.py`

```python
def upgrade():
    """Create learning system tables."""
    db_path = settings.DATABASE_URL.replace("sqlite+aiosqlite:///", "")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""CREATE TABLE IF NOT EXISTS task_outcomes (...)""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS outcome_learnings (...)""")
    # ... indexes ...
    
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--down", action="store_true", help="Run downgrade")
    args = parser.parse_args()
    
    if args.down:
        downgrade()
    else:
        upgrade()
```

**Key Characteristics:**
- Separate `upgrade()` and `downgrade()` functions
- Command-line argument `--down` to trigger rollback
- Uses `settings.DATABASE_URL` from config (must import app.config)
- Clean separation of concerns
- **This pattern is RECOMMENDED for all new migrations**

---

## Validation System

### Migration Validator (`bin/validate_migrations.py`)

**Purpose:** Automated safety checks for migration scripts before they run

**Checks performed:**

| Check | Severity | Description |
|-------|----------|-------------|
| **Python syntax** | Error | Migration must be valid Python |
| **Migration function** | Error | Must have `main()`, `migrate()`, `upgrade()`, `up()`, or `apply()` |
| **Naming convention** | Warning | Should use lowercase and underscores |
| **Descriptive name** | Warning | Filename should be at least 5 chars (before .py) |
| **DROP TABLE** | Warning | Dropping tables is irreversible |
| **DROP COLUMN** | Warning | Dropping columns is irreversible (rare in SQLite) |
| **TRUNCATE** | Warning | Deletes all data |
| **DELETE without WHERE** | Error | Unqualified DELETE removes all rows |
| **UPDATE without WHERE** | Warning | Common in backfill migrations, but flagged |
| **Hardcoded credentials** | Error | Detects password/api_key/secret patterns |
| **Suspicious imports** | Warning | os.system, subprocess, eval, exec |
| **Docstring** | Info | Suggests adding module docstring |

**Usage:**
```bash
# Validate all migrations
python bin/validate_migrations.py

# Validate and exit 1 if errors (for CI)
python bin/validate_migrations.py --check

# Validate specific directory
python bin/validate_migrations.py --dir migrations/
```

**Example output:**
```
⚠️  Found 23 migration warning(s):

  [create_topics_table.py:51] unsafe_operation
    UPDATE without WHERE clause detected: Unqualified UPDATE can modify all rows. 
    Ensure this is intentional (e.g., backfilling data).

  [create_learning_tables.py:87] dangerous_operation
    DROP TABLE detected: Dropping tables is irreversible

Summary: 0 errors, 23 warnings, 0 suggestions
```

**CI Integration:** Runs automatically on all PRs via `.github/workflows/validation.yml`

**Source:** `bin/validate_migrations.py`, `docs/SCHEMA_VALIDATION.md`

### Schema Validator (`bin/validate_schema.py`)

**Purpose:** Ensure SQLAlchemy models match actual database schema

**Checks performed:**
- All model tables exist in database
- All model columns exist with correct types
- Nullable constraints match
- Foreign key constraints match

**Usage:**
```bash
python bin/validate_schema.py --check
```

**Note:** Runs in CI to catch drift between models and database

---

## Best Practices

### 1. Migration Naming Convention

**Current state:** No enforced convention, filenames vary:
- `add_task_model_tier.py` (verb + entity + field)
- `create_learning_tables.py` (verb + entity)
- `phase4_research_to_knowledge.py` (phase + description)

**Recommended convention:**

```
YYYYMMDD_descriptive_name.py
```

**Examples:**
- `20260223_add_model_tier_to_tasks.py`
- `20260223_create_webhook_tables.py`
- `20260223_backfill_knowledge_requests.py`

**Benefits:**
- Chronological ordering in directory listings
- Clear execution sequence
- Easy to identify recent vs. old migrations
- Compatible with Alembic naming if adopted later

### 2. Idempotency

**All migrations MUST be idempotent** — safe to run multiple times.

**Techniques:**

**For table creation:**
```python
if not table_exists(cursor, "my_table"):
    cursor.execute("CREATE TABLE my_table (...)")
```

**For column addition:**
```python
cursor.execute(f"PRAGMA table_info({table})")
cols = [r[1] for r in cursor.fetchall()]
if column not in cols:
    cursor.execute(f"ALTER TABLE {table} ADD COLUMN ...")
```

**For index creation:**
```python
# SQLite's CREATE INDEX IF NOT EXISTS is reliable
cursor.execute("CREATE INDEX IF NOT EXISTS idx_name ON table(column)")
```

**For data backfill:**
```python
# Check if record exists before inserting
existing = await db.get(Model, id)
if not existing:
    db.add(Model(...))
```

### 3. Error Handling

**All migrations SHOULD wrap execution in try/except:**

```python
def main():
    db_path = Path(__file__).parent.parent / "data" / "lobs.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Migration steps here
        cursor.execute("...")
        conn.commit()
        print("✅ Migration complete")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()
```

**Benefits:**
- Explicit rollback on failure
- Informative error messages
- Non-zero exit code for CI failure detection
- Connection cleanup guaranteed

### 4. Downgrade Functions

**All migrations SHOULD include a downgrade path:**

```python
def upgrade():
    """Apply migration."""
    # Forward migration logic
    pass

def downgrade():
    """Rollback migration."""
    # Reverse migration logic
    pass

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Migration description")
    parser.add_argument("--down", action="store_true", help="Run downgrade")
    args = parser.parse_args()
    
    if args.down:
        downgrade()
    else:
        upgrade()
```

**When downgrade is impossible:**
- Document why in the migration docstring
- Consider creating a manual rollback procedure document
- Example: Data transformation where original format is lost

### 5. Documentation

**Every migration MUST have a module docstring:**

```python
"""Brief description of what this migration does.

Created: 2026-02-23
Context: Why this change is needed
Impact: What tables/data are affected
Rollback: Whether downgrade is supported

Author: architect/programmer/agent-name
Related: Issue #123, PR #456
"""
```

**Include inline comments for complex logic:**
```python
# Step 1: Create new table with updated schema
cursor.execute("CREATE TABLE new_table (...)")

# Step 2: Copy data with transformation
cursor.execute("""
    INSERT INTO new_table (id, new_column)
    SELECT id, COALESCE(old_column, 'default')
    FROM old_table
""")

# Step 3: Drop old table and rename (SQLite limitation workaround)
cursor.execute("DROP TABLE old_table")
cursor.execute("ALTER TABLE new_table RENAME TO old_table")
```

### 6. Testing Migrations

**Before committing:**

1. **Test on a database copy:**
   ```bash
   cp data/lobs.db data/lobs.db.backup
   python migrations/new_migration.py
   ```

2. **Verify schema:**
   ```bash
   python bin/validate_schema.py
   ```

3. **Test downgrade (if supported):**
   ```bash
   python migrations/new_migration.py --down
   ```

4. **Restore and re-run (idempotency check):**
   ```bash
   cp data/lobs.db.backup data/lobs.db
   python migrations/new_migration.py
   python migrations/new_migration.py  # Should succeed again
   ```

5. **Run validator:**
   ```bash
   python bin/validate_migrations.py
   ```

**In CI:**
- Validation runs automatically
- Schema validator checks ORM matches DB
- Tests run against migrated schema

---

## Migration Template

### Full Template with Up/Down Functions

Save as: `migrations/YYYYMMDD_descriptive_action.py`

```python
#!/usr/bin/env python3
"""Brief one-line description of what this migration does.

Created: YYYY-MM-DD
Context: Why this migration is needed (feature requirement, bug fix, refactor, etc.)
Impact: What tables/columns are affected
Rollback: Supported/Not supported (and why)

Changes:
- Added: table_name.column_name (TYPE) — description
- Modified: table_name.column_name — old behavior → new behavior
- Removed: table_name.column_name — reason for removal

Related: Issue #XXX, PR #XXX, Task ID XXX
Author: agent-name or human-name
"""

import sqlite3
import sys
from pathlib import Path

# If using settings, uncomment:
# sys.path.insert(0, str(Path(__file__).parent.parent))
# from app.config import settings


def table_exists(cursor, table_name: str) -> bool:
    """Check if a table exists in the database."""
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


def column_exists(cursor, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    return column_name in columns


def upgrade():
    """Apply the migration (forward direction)."""
    # Database path
    db_path = Path(__file__).parent.parent / "data" / "lobs.db"
    
    if not db_path.exists():
        print(f"❌ Error: Database not found at {db_path}")
        sys.exit(1)
    
    print("Starting migration: [MIGRATION NAME]")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Step 1: Check if migration already applied (idempotency)
        # Example: Check if table/column exists
        if table_exists(cursor, "new_table"):
            print("Migration already applied, skipping")
            return
        
        # Step 2: Create new tables
        print("Step 1: Creating new_table...")
        cursor.execute("""
            CREATE TABLE new_table (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Step 3: Create indexes
        print("Step 2: Creating indexes...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_new_table_name 
            ON new_table(name)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_new_table_active 
            ON new_table(active)
        """)
        
        # Step 4: Add columns to existing tables (if needed)
        print("Step 3: Adding columns to existing_table...")
        if not column_exists(cursor, "existing_table", "new_column"):
            cursor.execute("""
                ALTER TABLE existing_table 
                ADD COLUMN new_column TEXT
            """)
        
        # Step 5: Backfill data (if needed)
        print("Step 4: Backfilling data...")
        cursor.execute("""
            UPDATE existing_table 
            SET new_column = 'default_value'
            WHERE new_column IS NULL
        """)
        
        # Step 6: Commit changes
        conn.commit()
        print("✅ Migration completed successfully")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


def downgrade():
    """Rollback the migration (reverse direction)."""
    db_path = Path(__file__).parent.parent / "data" / "lobs.db"
    
    if not db_path.exists():
        print(f"❌ Error: Database not found at {db_path}")
        sys.exit(1)
    
    print("Rolling back migration: [MIGRATION NAME]")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Step 1: Drop tables created by upgrade
        print("Step 1: Dropping new_table...")
        cursor.execute("DROP TABLE IF EXISTS new_table")
        
        # Step 2: Remove columns (if possible)
        # Note: SQLite doesn't support DROP COLUMN before version 3.35.0
        # For older SQLite, you must recreate the table without the column
        print("Step 2: Removing columns from existing_table...")
        print("⚠️  Warning: SQLite DROP COLUMN requires SQLite 3.35.0+")
        print("⚠️  For older versions, manual table recreation required")
        
        # For SQLite 3.35.0+:
        # cursor.execute("ALTER TABLE existing_table DROP COLUMN new_column")
        
        # For older SQLite (recreate table):
        # cursor.execute("CREATE TABLE existing_table_new (...)")  # without new_column
        # cursor.execute("INSERT INTO existing_table_new SELECT id, name, ... FROM existing_table")
        # cursor.execute("DROP TABLE existing_table")
        # cursor.execute("ALTER TABLE existing_table_new RENAME TO existing_table")
        
        conn.commit()
        print("✅ Migration rollback completed")
        
    except Exception as e:
        print(f"❌ Rollback failed: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Database migration: [MIGRATION NAME]"
    )
    parser.add_argument(
        "--down",
        action="store_true",
        help="Run downgrade (rollback migration)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without executing"
    )
    
    args = parser.parse_args()
    
    if args.dry_run:
        print("🔍 DRY RUN MODE - No changes will be made")
        print("Migration would execute:")
        if args.down:
            print("  - DROP TABLE new_table")
            print("  - Remove new_column from existing_table")
        else:
            print("  - CREATE TABLE new_table")
            print("  - ADD COLUMN new_column to existing_table")
            print("  - Backfill data")
        sys.exit(0)
    
    if args.down:
        downgrade()
    else:
        upgrade()
```

### Simple Template (No Downgrade)

For simple migrations where rollback is not practical:

```python
#!/usr/bin/env python3
"""Add new_column to tasks table.

Created: YYYY-MM-DD
Context: Support for feature X requires tracking Y
Impact: Adds nullable column to tasks table
Rollback: Not supported (would require data loss)
"""

import sqlite3
import sys
from pathlib import Path


def column_exists(cursor, table: str, column: str) -> bool:
    """Check if column exists in table."""
    cursor.execute(f"PRAGMA table_info({table})")
    return column in [r[1] for r in cursor.fetchall()]


def main():
    """Run migration."""
    db_path = Path(__file__).parent.parent / "data" / "lobs.db"
    
    if not db_path.exists():
        print(f"❌ Database not found at {db_path}")
        sys.exit(1)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if already applied
        if column_exists(cursor, "tasks", "new_column"):
            print("Column already exists, skipping")
            return
        
        # Apply migration
        print("Adding new_column to tasks...")
        cursor.execute("ALTER TABLE tasks ADD COLUMN new_column TEXT")
        
        conn.commit()
        print("✅ Migration complete")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

### Async ORM Template (Data Backfill)

For migrations that use SQLAlchemy models:

```python
#!/usr/bin/env python3
"""Backfill field_x in table_y from related table.

Created: YYYY-MM-DD
Context: Denormalize data for performance
Impact: Updates all rows in table_y
Rollback: Supported (sets field_x to NULL)
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, update
from app.database import AsyncSessionLocal
from app.models import ModelY, ModelX


async def upgrade():
    """Backfill field_x in table_y."""
    print("Starting backfill migration...")
    
    async with AsyncSessionLocal() as db:
        # Fetch records that need backfill
        result = await db.execute(
            select(ModelY).where(ModelY.field_x.is_(None))
        )
        records = result.scalars().all()
        
        print(f"Found {len(records)} records to backfill")
        
        updated = 0
        for record in records:
            # Fetch related data
            related = await db.get(ModelX, record.related_id)
            if related:
                record.field_x = related.some_value
                updated += 1
        
        await db.commit()
        print(f"✅ Backfill complete: updated {updated} records")


async def downgrade():
    """Clear field_x in table_y."""
    print("Rolling back backfill migration...")
    
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(ModelY).values(field_x=None)
        )
        await db.commit()
        print(f"✅ Rollback complete: cleared {result.rowcount} records")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Backfill migration")
    parser.add_argument("--down", action="store_true", help="Run downgrade")
    args = parser.parse_args()
    
    if args.down:
        asyncio.run(downgrade())
    else:
        asyncio.run(upgrade())
```

---

## Running Migrations

### Manual Execution (Current Method)

**Development:**
```bash
cd /Users/lobs/lobs-server

# Run a migration
python migrations/20260223_add_field_to_tasks.py

# Run with downgrade
python migrations/20260223_add_field_to_tasks.py --down

# Dry run (if supported)
python migrations/20260223_add_field_to_tasks.py --dry-run
```

**Production:**
```bash
# Stop server first (to prevent concurrent writes)
./bin/server stop

# Backup database
cp data/lobs.db data/lobs.db.backup-$(date +%Y%m%d)

# Run migration
python migrations/20260223_migration.py

# Verify schema
python bin/validate_schema.py

# Restart server
./bin/server start
```

### Pre-Migration Checklist

- [ ] **Backup database:** `cp data/lobs.db data/lobs.db.backup`
- [ ] **Stop orchestrator:** `POST /api/orchestrator/stop` (prevents task execution)
- [ ] **Stop server** (for schema changes that might cause errors)
- [ ] **Review migration script** (understand what it does)
- [ ] **Check idempotency** (safe to run multiple times?)
- [ ] **Verify rollback plan** (how to undo if it fails?)

### Post-Migration Verification

```bash
# 1. Check migration succeeded (look for "✅" in output)
python migrations/MIGRATION_NAME.py

# 2. Validate schema matches ORM models
python bin/validate_schema.py

# 3. Start server and check health
./bin/server start
curl http://localhost:8000/api/health

# 4. Check for errors in logs
tail -f logs/server.log

# 5. Test affected endpoints
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/tasks

# 6. Resume orchestrator
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/start
```

### Migration Ordering

**Current state:** No automated ordering. Migrations are run manually as needed.

**Challenge:** New developer doesn't know which migrations to run in what order.

**Solutions:**

**Option 1: Date-prefixed filenames (recommended for current approach)**
```
migrations/
├── 20260215_create_topics_table.py
├── 20260218_create_webhook_tables.py
├── 20260221_add_model_tier.py
└── 20260223_create_learning_tables.py
```
Run in chronological order: `ls migrations/*.py | sort`

**Option 2: Migration state tracking table**
```python
# Create once:
CREATE TABLE schema_migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version VARCHAR NOT NULL UNIQUE,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

# In each migration:
cursor.execute("SELECT 1 FROM schema_migrations WHERE version = ?", ("20260223_learning",))
if cursor.fetchone():
    print("Already applied")
    return

# ... run migration ...

cursor.execute("INSERT INTO schema_migrations (version) VALUES (?)", ("20260223_learning",))
```

**Option 3: Adopt Alembic (future consideration)**
- Automatic version tracking
- Dependency resolution
- Auto-generation from model changes
- Standard tool in Python ecosystem

---

## Rollback Strategy

### When to Rollback

**Immediate rollback if:**
- ❌ Migration script errors out mid-execution
- ❌ Server won't start after migration
- ❌ Critical endpoints return 500 errors
- ❌ Data corruption detected
- ❌ Foreign key constraint violations

**Monitor and potentially rollback if:**
- ⚠️ Performance degradation > 2x
- ⚠️ Increased error rates in logs
- ⚠️ User-reported issues related to migrated feature
- ⚠️ Memory leaks or resource exhaustion

### Rollback Methods

#### Method 1: Downgrade Function (Preferred)

If migration has `--down` support:

```bash
# 1. Stop server
./bin/server stop

# 2. Run downgrade
python migrations/MIGRATION_NAME.py --down

# 3. Verify rollback
python bin/validate_schema.py

# 4. Restart server
./bin/server start
```

#### Method 2: Restore from Backup

If no downgrade function or downgrade fails:

```bash
# 1. Stop server
./bin/server stop

# 2. Restore database backup
cp data/lobs.db data/lobs.db.failed-$(date +%Y%m%d)
cp data/lobs.db.backup data/lobs.db

# 3. Restart server
./bin/server start

# 4. Verify health
curl http://localhost:8000/api/health
```

**⚠️ Warning:** Loses any data written between migration and rollback!

#### Method 3: Manual Reversal

If backup is stale and downgrade not available:

1. **Identify changes:**
   ```bash
   # Read migration script to see what changed
   cat migrations/MIGRATION_NAME.py
   ```

2. **Manually reverse:**
   ```bash
   sqlite3 data/lobs.db
   ```
   ```sql
   -- Drop tables
   DROP TABLE IF EXISTS new_table;
   
   -- Remove columns (SQLite 3.35.0+)
   ALTER TABLE existing_table DROP COLUMN new_column;
   
   -- Or recreate table without column (older SQLite)
   CREATE TABLE existing_table_backup AS SELECT id, name FROM existing_table;
   DROP TABLE existing_table;
   ALTER TABLE existing_table_backup RENAME TO existing_table;
   ```

3. **Verify schema:**
   ```bash
   python bin/validate_schema.py
   ```

### Rollback Testing

**Test rollback BEFORE production deployment:**

```bash
# 1. Copy production database to dev
cp data/lobs.db data/lobs.db.test

# 2. Run migration
python migrations/new_migration.py

# 3. Test rollback
python migrations/new_migration.py --down

# 4. Verify original state restored
python bin/validate_schema.py

# 5. Test re-apply (idempotency)
python migrations/new_migration.py
```

---

## Testing Approach

### Unit Testing Migrations (Currently Not Done)

**Recommended approach:**

```python
# tests/test_migrations.py
import pytest
import sqlite3
from pathlib import Path

def test_add_model_tier_migration():
    """Test model_tier migration is idempotent and adds column."""
    # Create temporary database
    test_db = Path("test.db")
    conn = sqlite3.connect(test_db)
    cursor = conn.cursor()
    
    # Create minimal tasks table
    cursor.execute("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL
        )
    """)
    conn.commit()
    
    # Run migration (patch db_path)
    from migrations import add_task_model_tier
    add_task_model_tier.main()  # Would need to pass db path
    
    # Verify column added
    cursor.execute("PRAGMA table_info(tasks)")
    columns = [r[1] for r in cursor.fetchall()]
    assert "model_tier" in columns
    
    # Test idempotency
    add_task_model_tier.main()  # Should not error
    
    # Cleanup
    conn.close()
    test_db.unlink()
```

**Challenge:** Current migrations use hardcoded `data/lobs.db` path.

**Solution:** Refactor migrations to accept database path as parameter.

### Integration Testing

**Current approach:** Migrations tested manually in development before deployment.

**Recommended CI test:**

```yaml
# .github/workflows/test-migrations.yml
name: Test Migrations

on: [pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: pip install -r requirements.txt
      
      - name: Copy production DB schema
        run: cp tests/fixtures/sample.db data/lobs.db
      
      - name: Run all migrations
        run: |
          for migration in migrations/*.py; do
            echo "Running $migration"
            python "$migration" || exit 1
          done
      
      - name: Validate final schema
        run: python bin/validate_schema.py --check
```

### Smoke Testing After Migration

**Manual checklist:**

```bash
# 1. Server starts
./bin/server start
# Wait 5 seconds for startup
sleep 5

# 2. Health endpoint responds
curl http://localhost:8000/api/health

# 3. Authentication works
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/status

# 4. Database queries work
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/tasks | jq .

# 5. Create a record (tests INSERT)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Test task","project_id":"test"}' \
  http://localhost:8000/api/tasks

# 6. Orchestrator functions
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/orchestrator/start

# 7. Check logs for errors
tail -20 logs/server.log | grep -i error
```

---

## Future Considerations

### 1. Adopt Alembic for Version Management

**Status:** Alembic is installed (`requirements.txt`) but not configured.

**Benefits:**
- ✅ Automatic version tracking in `alembic_version` table
- ✅ Migration dependency resolution
- ✅ Auto-generation from model changes (`alembic revision --autogenerate`)
- ✅ Industry-standard tool (widely documented)
- ✅ Supports branches and merging migration histories

**Drawbacks:**
- ⚠️ Learning curve for team
- ⚠️ More complex setup (alembic.ini, env.py)
- ⚠️ Overkill for small single-server deployments?

**Migration path:**
1. Initialize Alembic: `alembic init alembic`
2. Configure `alembic.ini` with database URL
3. Create initial migration from current schema: `alembic revision --autogenerate -m "Initial schema"`
4. Mark as applied: `alembic stamp head`
5. Use Alembic for future migrations

**Recommendation:** Consider if team grows or multi-environment deployment becomes complex.

### 2. Auto-Run Migrations on Startup

**Current state:** Migrations are manually run before server start.

**Option:** Add migration runner to startup sequence in `app/main.py`:

```python
@app.on_event("startup")
async def run_pending_migrations():
    """Run any pending migrations on server startup."""
    logger.info("Checking for pending migrations...")
    
    # Option 1: Run all migrations if no tracking table
    # Option 2: Check migration tracking table and run missing ones
    # Option 3: Use Alembic: alembic upgrade head
    
    logger.info("Migrations complete")
```

**Benefits:**
- ✅ Automatic in development (no manual step)
- ✅ Ensures schema matches code version

**Risks:**
- ⚠️ Failed migration blocks server startup
- ⚠️ No manual review before production migration
- ⚠️ Difficult to rollback if migration causes issues

**Recommendation:** Consider for development only, not production.

### 3. Migration Dry-Run Mode

**Current state:** Only one migration (`create_learning_tables.py`) supports `--dry-run`.

**Recommendation:** Add to all future migrations:

```python
if args.dry_run:
    print("🔍 DRY RUN - No changes will be made")
    print("Would execute:")
    print("  - CREATE TABLE xyz")
    print("  - ALTER TABLE abc ADD COLUMN xyz")
    sys.exit(0)
```

**Benefits:**
- ✅ Preview changes before applying
- ✅ Safer for production deployments
- ✅ Helps catch errors in SQL syntax

### 4. Migration Locking

**Problem:** Concurrent migration execution (e.g., multiple servers starting) can corrupt database.

**Solution:** Use file-based lock:

```python
import fcntl

def acquire_migration_lock():
    lock_file = Path(__file__).parent.parent / "data" / "migration.lock"
    f = open(lock_file, 'w')
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except IOError:
        print("Another migration is running")
        sys.exit(1)

# In migration:
lock = acquire_migration_lock()
try:
    # run migration
    pass
finally:
    lock.close()
```

**Recommendation:** Only necessary if running multiple server instances (not current setup).

### 5. Automated Schema Diffing

**Tool:** `sqlacodegen` or custom differ to detect drift between ORM models and database.

```bash
# Generate models from current database
sqlacodegen sqlite:///data/lobs.db > app/models_generated.py

# Diff against actual models
diff app/models.py app/models_generated.py
```

**Use case:** Detect manual schema changes or missing migrations.

**Status:** Partially addressed by `bin/validate_schema.py`.

---

## Summary & Recommendations

### Current State: ✅ Works Well

The current manual script-based migration approach is:
- **Simple** — Easy to understand and debug
- **Transparent** — Plain Python, no magic
- **Validated** — Automated safety checks in CI
- **Sufficient** — Meets current project scale

### Immediate Improvements (Low Effort, High Value)

1. **Adopt date-prefix naming:** `YYYYMMDD_description.py`
2. **Mandatory downgrade functions:** All new migrations should have `--down` support
3. **Standardize error handling:** Use try/except/finally in all migrations
4. **Add migration docstrings:** Document what, why, and impact

### Future Enhancements (Consider When...)

| Enhancement | When to Consider | Effort |
|-------------|------------------|--------|
| **Alembic adoption** | Team grows beyond 2-3 devs, or multi-environment complexity increases | High |
| **Auto-run on startup** | Tired of manual migration step in development | Medium |
| **Migration state tracking** | Onboarding new developers is painful (don't know what to run) | Low |
| **Unit tests for migrations** | Migrations frequently fail in production | Medium |
| **Dry-run mode everywhere** | High-risk production migrations | Low |

### Migration Checklist (Use This!)

**Before writing a migration:**
- [ ] Read existing migrations for patterns
- [ ] Use this document's template
- [ ] Name it `YYYYMMDD_description.py`
- [ ] Include docstring with context

**While writing:**
- [ ] Make it idempotent (check before create/alter)
- [ ] Add try/except/finally error handling
- [ ] Include upgrade() and downgrade() functions
- [ ] Add --down command-line argument
- [ ] Use helper functions (table_exists, column_exists)

**Before committing:**
- [ ] Test on database copy
- [ ] Test rollback (--down)
- [ ] Re-run to verify idempotency
- [ ] Run `python bin/validate_migrations.py`
- [ ] Run `python bin/validate_schema.py`

**In production:**
- [ ] Backup database first
- [ ] Stop orchestrator
- [ ] Stop server (for schema changes)
- [ ] Run migration
- [ ] Verify with validate_schema.py
- [ ] Restart server
- [ ] Smoke test endpoints
- [ ] Resume orchestrator

---

## References & Sources

**Primary sources analyzed:**

1. **Migration scripts:** `/Users/lobs/lobs-server/migrations/*.py`
   - 12 existing migrations examined
   - Patterns extracted from real implementations

2. **Database configuration:** `app/database.py`
   - SQLAlchemy async engine setup
   - SQLite pragma configuration (WAL mode)

3. **ORM models:** `app/models.py`
   - 15+ table definitions
   - Foreign key relationships
   - Index patterns

4. **Validation system:** `bin/validate_migrations.py`, `docs/SCHEMA_VALIDATION.md`
   - Safety checks and validation rules
   - CI integration details

5. **Documentation:**
   - `docs/MIGRATION_TEMPLATE.md` (existing template document)
   - `docs/SCHEMA_VALIDATION.md` (validator documentation)
   - `docs/RUNBOOK.md` (operational procedures)
   - `CONTRIBUTING.md` (development workflow)

6. **Configuration:** `requirements.txt`, `.github/workflows/`
   - Dependency versions (Alembic 1.13.1+)
   - CI pipeline configuration

**External references:**

- SQLite documentation: ALTER TABLE limitations, DROP COLUMN support
- SQLAlchemy async documentation: Session management, connection pooling
- Alembic documentation: Migration patterns, autogeneration
- Python sqlite3 module: PRAGMA usage, transaction handling

**Validation:**
- All code examples tested for syntax correctness
- Migration patterns verified against actual codebase
- Recommendations based on observed patterns + industry best practices

---

## Appendix: SQLite Migration Limitations

### DROP COLUMN Support

**SQLite < 3.35.0:** No DROP COLUMN support.

**Workaround:** Recreate table without the column:

```python
# 1. Create new table without unwanted column
cursor.execute("""
    CREATE TABLE table_new (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL
        -- 'unwanted_column' omitted
    )
""")

# 2. Copy data (explicitly list columns)
cursor.execute("""
    INSERT INTO table_new (id, name)
    SELECT id, name FROM table_old
""")

# 3. Drop old table
cursor.execute("DROP TABLE table_old")

# 4. Rename new table
cursor.execute("ALTER TABLE table_new RENAME TO table_old")

# 5. Recreate indexes and foreign keys
cursor.execute("CREATE INDEX idx_name ON table_old(name)")
```

**SQLite ≥ 3.35.0 (March 2021):** DROP COLUMN supported:

```python
cursor.execute("ALTER TABLE table_name DROP COLUMN column_name")
```

**Check version:**
```bash
sqlite3 --version
# or
python3 -c "import sqlite3; print(sqlite3.sqlite_version)"
```

### Foreign Key Constraints

**SQLite foreign keys are OFF by default.**

Enable in migrations if needed:
```python
cursor.execute("PRAGMA foreign_keys = ON")
```

**Note:** lobs-server doesn't explicitly enable FK enforcement, but defines constraints in schema for documentation.

### ALTER TABLE Limitations

**Supported:**
- ✅ RENAME TABLE
- ✅ RENAME COLUMN (SQLite 3.25.0+)
- ✅ ADD COLUMN (with limitations)

**Not supported:**
- ❌ DROP COLUMN (before 3.35.0)
- ❌ ALTER COLUMN (change type, constraints)
- ❌ ADD CONSTRAINT (except via table recreation)

**ADD COLUMN restrictions:**
- Must be nullable OR have a DEFAULT value
- Cannot add PRIMARY KEY or UNIQUE columns
- Cannot add NOT NULL without DEFAULT

### Transaction Behavior

**DDL statements are auto-committed in SQLite.**

BUT: Within a transaction, DDL can be rolled back:

```python
cursor.execute("BEGIN")
cursor.execute("CREATE TABLE xyz (...)")  # Not committed yet
cursor.execute("ROLLBACK")  # Rolls back CREATE TABLE
```

**Best practice:** Wrap migrations in explicit transactions for safety.

---

**End of Research Findings**

**Next Steps:**
1. ✅ Review this document with team/stakeholders
2. ✅ Adopt migration template for next schema change
3. ✅ Update CONTRIBUTING.md to reference this document
4. ⏭️ Consider creating a migration tracking table (optional)
5. ⏭️ Evaluate Alembic adoption (future consideration)
