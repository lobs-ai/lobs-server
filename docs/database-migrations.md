# Database Migration Strategy

**Status:** Proposed  
**Last Updated:** 2026-02-22

## Problem Statement

lobs-server currently uses **ad-hoc Python migration scripts** with raw SQL commands to evolve the database schema. This approach has several critical problems:

### Current Issues

1. **No version tracking** — Can't determine what schema version a database is at
2. **No automatic generation** — Every schema change requires manual SQL authoring
3. **No rollback capability** — Failed migrations can't be easily undone
4. **Error-prone** — Raw SQL is fragile, especially with SQLite's limited ALTER TABLE support
5. **No testing framework** — Migrations run in production with no validation
6. **Manual execution** — Developers must remember to run migration scripts
7. **No coordination** — Different environments may have different schemas
8. **Poor observability** — No audit trail of what migrations ran when

### Current Approach

Migrations live in `migrations/` as standalone Python scripts:

```python
# migrations/add_task_model_tier.py
def add_column_if_missing(cursor, table: str, column: str, ddl: str):
    cursor.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cursor.fetchall()]
    if column not in cols:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
```

**Problems:**
- Developer must manually run each script
- No record of which migrations have been applied
- If script fails halfway through, database is in unknown state
- Adding an index or constraint requires complex SQLite table recreation
- Hard to test without affecting production database

---

## Proposed Solution: Alembic with Async Support

We adopt **Alembic** as the official migration tool, configured for async SQLAlchemy and SQLite.

### Why Alembic?

**Alembic is the standard migration tool for SQLAlchemy projects.** It provides:

✅ **Version tracking** — Tracks schema version in `alembic_version` table  
✅ **Auto-generation** — Detects model changes and generates migrations  
✅ **Upgrade/downgrade** — Bidirectional migrations with rollback support  
✅ **Testing** — Can apply migrations to test databases  
✅ **Audit trail** — Migration history is tracked in version control  
✅ **Async support** — Works with aiosqlite and async SQLAlchemy  
✅ **Production-ready** — Battle-tested by thousands of projects  

### Architecture

```
┌─────────────────────────────────────────────────────┐
│  Developer modifies app/models.py                   │
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│  alembic revision --autogenerate -m "Add column"    │
│  → Compares models.py to current database schema    │
│  → Generates migration file in alembic/versions/    │
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│  Developer reviews migration file                   │
│  → Check upgrade() and downgrade() logic            │
│  → Add data migrations if needed                    │
│  → Commit migration file to git                     │
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│  Deployment runs: bin/migrate                       │
│  → Creates backup (data/backups/pre-migration.db)   │
│  → Runs alembic upgrade head                        │
│  → Verifies schema matches models                   │
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
          ┌───────┴──────┐
          │              │
    ✅ Success      ❌ Failure
          │              │
          │              ▼
          │     ┌─────────────────────────┐
          │     │ Auto-rollback:          │
          │     │ alembic downgrade -1    │
          │     │ Restore from backup     │
          │     └─────────────────────────┘
          │
          ▼
  Database ready
```

---

## Implementation Plan

### Phase 1: Alembic Setup (1-2 hours)

**Goal:** Initialize Alembic for async SQLAlchemy + SQLite

#### Step 1.1: Initialize Alembic

```bash
cd /Users/lobs/lobs-server
source .venv/bin/activate
alembic init --template async alembic
```

This creates:
```
alembic/
├── env.py           # Alembic environment configuration
├── script.py.mako   # Template for new migrations
└── versions/        # Migration files live here
alembic.ini          # Alembic configuration file
```

#### Step 1.2: Configure alembic.ini

Edit `alembic.ini`:

```ini
[alembic]
script_location = alembic
sqlalchemy.url = sqlite+aiosqlite:///data/lobs.db  # Use async driver

# Truncate slug in migration filenames to 40 chars
# (prevents filename length issues)
truncate_slug_length = 40

# Template used to generate migration files
file_template = %%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s
```

#### Step 1.3: Configure env.py for Async

Edit `alembic/env.py` to:
- Import `Base` from `app.models`
- Set `target_metadata = Base.metadata`
- Configure async connection for `run_migrations_online()`

See implementation details in Appendix A.

#### Step 1.4: Create Initial Migration

**Generate baseline migration** that captures current schema:

```bash
# This creates a migration representing the current database state
alembic revision --autogenerate -m "initial schema"
```

**IMPORTANT:** Review the generated migration file! Alembic may detect differences between `models.py` and the actual database (due to manual migrations). Adjust as needed.

#### Step 1.5: Mark Database as Current

Since the database already has this schema, we **stamp** it without running the migration:

```bash
alembic stamp head
```

This inserts the current revision ID into the `alembic_version` table, marking the database as "already migrated."

**Acceptance Criteria:**
- ✅ `alembic current` shows the baseline migration
- ✅ `alembic upgrade head` runs with no changes
- ✅ `alembic history` shows migration history

---

### Phase 2: Migration Workflow (ongoing)

**Goal:** Establish standard workflow for schema changes

#### 2.1: Making Schema Changes

**Developer workflow:**

1. **Modify models** in `app/models.py`:
   ```python
   class Task(Base):
       __tablename__ = "tasks"
       # ... existing columns ...
       priority = Column(String)  # NEW COLUMN
   ```

2. **Generate migration**:
   ```bash
   alembic revision --autogenerate -m "add task priority"
   ```

3. **Review generated migration** in `alembic/versions/`:
   ```python
   def upgrade() -> None:
       op.add_column('tasks', sa.Column('priority', sa.String(), nullable=True))
   
   def downgrade() -> None:
       op.drop_column('tasks', 'priority')
   ```

4. **Add data migrations** if needed:
   ```python
   def upgrade() -> None:
       op.add_column('tasks', sa.Column('priority', sa.String(), nullable=True))
       # Set default value for existing rows
       op.execute("UPDATE tasks SET priority = 'medium' WHERE priority IS NULL")
   ```

5. **Test migration** (see Testing Strategy below)

6. **Commit migration file** to git:
   ```bash
   git add alembic/versions/2026_02_22_1430-abc123_add_task_priority.py
   git commit -m "migration: add task priority"
   ```

#### 2.2: Applying Migrations

**Production deployment:**

```bash
# Run automated migration script
./bin/migrate
```

The `bin/migrate` script:
1. Creates timestamped backup
2. Runs `alembic upgrade head`
3. Verifies schema
4. Logs migration result

#### 2.3: Rolling Back Migrations

**If migration fails or has bugs:**

```bash
# Rollback one migration
alembic downgrade -1

# Rollback to specific version
alembic downgrade <revision_id>

# Or restore from backup
cp data/backups/pre-migration-2026-02-22T14-30.db data/lobs.db
```

**Acceptance Criteria:**
- ✅ Schema changes auto-generate migrations
- ✅ Migrations are reviewed before deployment
- ✅ Migrations are tested in isolated database
- ✅ Backups are created before each migration
- ✅ Failed migrations can be rolled back

---

### Phase 3: Migrate Existing Scripts (optional)

**Goal:** Consolidate ad-hoc migrations into Alembic history

**Options:**

**Option A: Leave as-is** (recommended)
- Keep existing `migrations/*.py` scripts as historical artifacts
- All **future** migrations use Alembic
- No risk of breaking existing deployments
- **Tradeoff:** Migration history is split (ad-hoc + Alembic)

**Option B: Recreate in Alembic**
- Create Alembic migrations matching each ad-hoc script
- Stamp database to mark them as applied
- Unified migration history
- **Tradeoff:** Time-consuming, risk of mismatch errors

**Recommendation:** **Option A.** The ad-hoc scripts are done and working. Focus effort on future migrations.

---

## Testing Strategy

**Never apply untested migrations to production database.**

### 1. Test Migrations on Copy of Production Database

```bash
# Create test database from production backup
cp data/lobs.db data/lobs-test.db

# Point Alembic at test database (temporarily edit alembic.ini)
# OR use environment variable:
export DATABASE_URL="sqlite+aiosqlite:///data/lobs-test.db"

# Run migration
alembic upgrade head

# Verify schema
sqlite3 data/lobs-test.db ".schema tasks"

# Test downgrade
alembic downgrade -1
alembic upgrade head

# Clean up
rm data/lobs-test.db
```

### 2. Automated Testing in CI/CD

**Add to pytest suite:**

```python
# tests/test_migrations.py
import pytest
from alembic import command
from alembic.config import Config

@pytest.fixture
def alembic_config():
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", "sqlite+aiosqlite:///:memory:")
    return config

def test_migrations_run_cleanly(alembic_config):
    """Test that migrations apply without errors."""
    command.upgrade(alembic_config, "head")

def test_downgrade_works(alembic_config):
    """Test that downgrades don't fail."""
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "-1")
    command.upgrade(alembic_config, "head")
```

### 3. Manual Verification Checklist

Before deploying a migration:

- [ ] Migration file reviewed for correctness
- [ ] Tested on copy of production database
- [ ] Verified schema matches models after upgrade
- [ ] Tested downgrade (rollback) works
- [ ] Data migrations tested with realistic data
- [ ] Backup exists and is restorable
- [ ] Deployment plan documented (if complex)

---

## Backup-Before-Migrate SOP

**Standard Operating Procedure for Production Migrations**

### Automated Backup Script

Create `bin/migrate`:

```bash
#!/bin/bash
# bin/migrate - Run database migrations with automatic backup

set -e  # Exit on error

DB_PATH="data/lobs.db"
BACKUP_DIR="data/backups"
TIMESTAMP=$(date +%Y-%m-%dT%H-%M-%S)
BACKUP_PATH="$BACKUP_DIR/pre-migration-$TIMESTAMP.db"

echo "=== lobs-server migration script ==="
echo "Timestamp: $TIMESTAMP"

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

# Create backup
echo "Creating backup: $BACKUP_PATH"
cp "$DB_PATH" "$BACKUP_PATH"
cp "$DB_PATH-shm" "$BACKUP_PATH-shm" 2>/dev/null || true
cp "$DB_PATH-wal" "$BACKUP_PATH-wal" 2>/dev/null || true

# Verify backup
if [ ! -f "$BACKUP_PATH" ]; then
    echo "ERROR: Backup failed"
    exit 1
fi

BACKUP_SIZE=$(du -h "$BACKUP_PATH" | cut -f1)
echo "Backup created: $BACKUP_SIZE"

# Run migrations
echo "Running migrations..."
alembic upgrade head

if [ $? -eq 0 ]; then
    echo "✅ Migration successful"
    echo "Backup: $BACKUP_PATH"
else
    echo "❌ Migration failed"
    echo "To restore backup:"
    echo "  cp $BACKUP_PATH $DB_PATH"
    exit 1
fi

# Clean up old backups (keep last 30)
echo "Cleaning old backups (keeping last 30)..."
ls -t "$BACKUP_DIR"/pre-migration-*.db | tail -n +31 | xargs rm -f 2>/dev/null || true

echo "=== Migration complete ==="
```

**Make executable:**
```bash
chmod +x bin/migrate
```

### Manual Backup Procedure

**Before any risky migration:**

```bash
# Create timestamped backup
TIMESTAMP=$(date +%Y-%m-%dT%H-%M-%S)
cp data/lobs.db "data/backups/manual-backup-$TIMESTAMP.db"

# Verify backup is readable
sqlite3 "data/backups/manual-backup-$TIMESTAMP.db" "SELECT COUNT(*) FROM tasks;"
```

### Backup Retention Policy

- **Automated backups:** Keep last 30 (oldest auto-deleted)
- **Manual backups:** Keep indefinitely
- **Major releases:** Create named backup before deployment

---

## Rollback Procedures

### Scenario 1: Migration Fails During `upgrade`

**Alembic tracks partial migrations.** If upgrade fails, the database may be in an inconsistent state.

**Immediate response:**

```bash
# Restore from backup
LATEST_BACKUP=$(ls -t data/backups/pre-migration-*.db | head -1)
cp "$LATEST_BACKUP" data/lobs.db

# Restart server
./bin/run
```

**Root cause analysis:**
1. Check migration file for errors
2. Check application logs for SQL errors
3. Verify database schema constraints

**Fix and retry:**
1. Fix migration file
2. Test on copy of database
3. Restore backup
4. Re-run `alembic upgrade head`

### Scenario 2: Migration Succeeds but Breaks Application

**Symptom:** Migration completes but application throws errors (500s, KeyErrors, etc.)

**Immediate response:**

```bash
# Rollback one migration
alembic downgrade -1

# OR restore from backup if rollback fails
LATEST_BACKUP=$(ls -t data/backups/pre-migration-*.db | head -1)
cp "$LATEST_BACKUP" data/lobs.db
```

**Root cause analysis:**
1. Check if code expects old schema
2. Verify migration didn't drop data
3. Check for application logic bugs

**Fix:**
1. Fix application code or migration
2. Test thoroughly
3. Re-deploy

### Scenario 3: Need to Rollback Multiple Migrations

**To rollback to specific version:**

```bash
# View migration history
alembic history

# Rollback to specific revision
alembic downgrade <revision_id>

# OR rollback N steps
alembic downgrade -2  # Go back 2 migrations
```

### Scenario 4: Database Corruption

**Symptom:** `sqlite3.DatabaseError`, file corruption, WAL recovery failures

**Recovery:**

```bash
# Restore from most recent backup
LATEST_BACKUP=$(ls -t data/backups/*.db | head -1)
cp "$LATEST_BACKUP" data/lobs.db

# Verify database integrity
sqlite3 data/lobs.db "PRAGMA integrity_check;"

# If integrity check fails, try earlier backup
SECOND_BACKUP=$(ls -t data/backups/*.db | head -2 | tail -1)
cp "$SECOND_BACKUP" data/lobs.db
```

### Rollback Testing

**Always test downgrades before deploying:**

```bash
# On test database
alembic upgrade head
alembic downgrade -1
alembic upgrade head

# Verify data integrity after round-trip
```

---

## SQLite-Specific Migration Considerations

### Limited ALTER TABLE Support

**SQLite does not support:**
- DROP COLUMN (until SQLite 3.35+)
- ALTER COLUMN type
- ADD CONSTRAINT (except NOT NULL in some versions)

**Workaround:** Table recreation pattern

Alembic handles this automatically via `batch_alter_table`:

```python
def upgrade() -> None:
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.alter_column('status', type_=sa.String(50))
        batch_op.drop_column('old_field')
```

**How it works:**
1. Create new table with desired schema
2. Copy data from old table
3. Drop old table
4. Rename new table

**Risks:**
- Foreign key constraints must be recreated
- Triggers and indexes must be recreated
- Data loss if copy logic is wrong

**Mitigation:**
- Always test table recreation migrations on production copy
- Verify row counts before/after
- Test foreign key constraints after migration

### WAL Mode Considerations

**WAL files (`lobs.db-wal`, `lobs.db-shm`) contain uncommitted transactions.**

**During migration:**
1. Stop application (or ensure no writes)
2. SQLite will checkpoint WAL to main database
3. Run migration
4. Restart application

**For backups:**
- Copy all three files: `.db`, `.db-shm`, `.db-wal`
- OR run `PRAGMA wal_checkpoint(TRUNCATE)` before backup

### Foreign Key Enforcement

**SQLite foreign keys are OFF by default** but lobs-server should enable them.

**Verify in migrations:**

```python
def upgrade() -> None:
    # Ensure foreign keys are enabled
    op.execute("PRAGMA foreign_keys = ON")
    
    # Then run migration
    op.add_column('tasks', sa.Column('user_id', sa.String(), nullable=True))
    op.create_foreign_key('fk_task_user', 'tasks', 'users', ['user_id'], ['id'])
```

**Note:** Alembic's batch operations automatically handle foreign keys correctly.

---

## Common Migration Patterns

### Pattern 1: Add Nullable Column

```python
def upgrade() -> None:
    op.add_column('tasks', sa.Column('priority', sa.String(), nullable=True))

def downgrade() -> None:
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.drop_column('priority')
```

### Pattern 2: Add Non-Nullable Column with Default

```python
def upgrade() -> None:
    # Add nullable first
    op.add_column('tasks', sa.Column('priority', sa.String(), nullable=True))
    # Set default for existing rows
    op.execute("UPDATE tasks SET priority = 'medium' WHERE priority IS NULL")
    # Make non-nullable
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.alter_column('priority', nullable=False)

def downgrade() -> None:
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.drop_column('priority')
```

### Pattern 3: Rename Column

```python
def upgrade() -> None:
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.alter_column('old_name', new_column_name='new_name')

def downgrade() -> None:
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.alter_column('new_name', new_column_name='old_name')
```

### Pattern 4: Add Index

```python
def upgrade() -> None:
    op.create_index('idx_tasks_status', 'tasks', ['status'])

def downgrade() -> None:
    op.drop_index('idx_tasks_status')
```

### Pattern 5: Create Table

```python
def upgrade() -> None:
    op.create_table(
        'notifications',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'])
    )

def downgrade() -> None:
    op.drop_table('notifications')
```

### Pattern 6: Data Migration

```python
from alembic import op
from sqlalchemy import text

def upgrade() -> None:
    # Schema change
    op.add_column('tasks', sa.Column('status_v2', sa.String()))
    
    # Data migration
    conn = op.get_bind()
    conn.execute(text("""
        UPDATE tasks 
        SET status_v2 = CASE 
            WHEN status = 'todo' THEN 'active'
            WHEN status = 'done' THEN 'completed'
            ELSE status
        END
    """))
    
    # Drop old column, rename new
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.drop_column('status')
        batch_op.alter_column('status_v2', new_column_name='status')
```

---

## Observability & Monitoring

### Migration Logs

**Log all migrations to system activity:**

Extend `bin/migrate` to record migrations:

```bash
# After successful migration
alembic current | while read line; do
    echo "Migration applied: $line" | tee -a data/migration.log
done
```

**Or integrate with lobs-server activity tracking:**

```python
# In migration script or post-migration hook
from app.services.activity_logger import log_activity

log_activity(
    type="system.migration",
    message=f"Applied migration: {revision}",
    metadata={"revision": revision, "timestamp": datetime.now()}
)
```

### Health Checks

**Add migration status to `/api/health` endpoint:**

```python
# app/routers/status.py
from alembic.script import ScriptDirectory
from alembic.runtime.migration import MigrationContext

def get_migration_status():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        current = context.get_current_revision()
        head = script.get_current_head()
        
    return {
        "current": current,
        "head": head,
        "needs_upgrade": current != head
    }

# Include in health check response
@router.get("/health")
async def health():
    migration_status = get_migration_status()
    return {
        "status": "ok",
        "database": "connected",
        "migrations": migration_status
    }
```

### Alerts

**Detect pending migrations on startup:**

```python
# app/main.py
@app.on_event("startup")
async def check_migrations():
    status = get_migration_status()
    if status["needs_upgrade"]:
        logger.warning(f"Database needs migration: {status['current']} -> {status['head']}")
        logger.warning("Run: ./bin/migrate")
```

---

## Tradeoffs

### What We Gain

✅ **Safety** — Automated backups, rollback capability, version tracking  
✅ **Productivity** — Auto-generation saves time vs manual SQL  
✅ **Reliability** — Battle-tested tool, fewer human errors  
✅ **Observability** — Clear migration history, audit trail  
✅ **Testability** — Can test migrations in isolation  
✅ **Coordination** — Same schema version across environments  

### What We Lose

❌ **Simplicity** — More moving parts than raw SQL scripts  
❌ **Learning curve** — Team must learn Alembic concepts  
❌ **Migration file bloat** — `alembic/versions/` fills up over time  
❌ **Auto-generation quirks** — May generate unnecessary migrations  

### What Stays the Same

🟰 **SQLite** — Still using SQLite, no database change  
🟰 **SQLAlchemy** — Still using same ORM, no code changes  
🟰 **Async** — Alembic supports async, no perf impact  

### Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| **Alembic generates bad migration** | Medium | High | Always review auto-generated migrations |
| **Migration fails mid-upgrade** | Low | High | Automated backups before every migration |
| **Downgrade loses data** | Medium | Critical | Test downgrades, use data migration patterns |
| **SQLite table recreation breaks FKs** | Medium | Medium | Use batch_alter_table, test thoroughly |
| **Developer forgets to run migration** | High | Medium | Add health check alert for pending migrations |
| **Backup fills disk** | Low | Low | Auto-delete old backups (keep last 30) |

---

## Appendix A: Alembic env.py Configuration

**File:** `alembic/env.py`

```python
"""Alembic environment configuration for async SQLAlchemy + SQLite."""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import your models
from app.database import Base
from app.models import *  # Import all models so Alembic can see them

# Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set target metadata for 'autogenerate' support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generate SQL, don't execute)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with provided connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in async mode."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect to database and execute)."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

---

## Appendix B: bin/migrate Script

**File:** `bin/migrate`

```bash
#!/bin/bash
# Database migration script with automated backup and verification

set -e  # Exit immediately on error
set -u  # Treat unset variables as errors
set -o pipefail  # Fail if any command in pipe fails

# Configuration
DB_PATH="${DB_PATH:-data/lobs.db}"
BACKUP_DIR="${BACKUP_DIR:-data/backups}"
TIMESTAMP=$(date +%Y-%m-%dT%H-%M-%S)
BACKUP_PATH="$BACKUP_DIR/pre-migration-$TIMESTAMP.db"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "╔══════════════════════════════════════════════════════════╗"
echo "║       lobs-server Database Migration Script             ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "Timestamp: $TIMESTAMP"
echo "Database:  $DB_PATH"
echo ""

# Check if database exists
if [ ! -f "$DB_PATH" ]; then
    echo -e "${RED}ERROR: Database not found at $DB_PATH${NC}"
    exit 1
fi

# Check if alembic is available
if ! command -v alembic &> /dev/null; then
    echo -e "${RED}ERROR: alembic not found. Activate virtual environment.${NC}"
    echo "Run: source .venv/bin/activate"
    exit 1
fi

# Check for pending migrations
echo "Checking for pending migrations..."
CURRENT=$(alembic current 2>/dev/null | grep -v "INFO" | head -1 || echo "none")
HEAD=$(alembic heads 2>/dev/null | grep -v "INFO" | head -1 || echo "none")

if [ "$CURRENT" = "$HEAD" ]; then
    echo -e "${GREEN}✓ Database is up to date (revision: $CURRENT)${NC}"
    echo "No migrations needed."
    exit 0
fi

echo -e "${YELLOW}→ Migration needed${NC}"
echo "  Current: $CURRENT"
echo "  Target:  $HEAD"
echo ""

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Create backup
echo "Creating backup..."
cp "$DB_PATH" "$BACKUP_PATH"

# Copy WAL files if they exist (SQLite WAL mode)
[ -f "$DB_PATH-shm" ] && cp "$DB_PATH-shm" "$BACKUP_PATH-shm" 2>/dev/null || true
[ -f "$DB_PATH-wal" ] && cp "$DB_PATH-wal" "$BACKUP_PATH-wal" 2>/dev/null || true

# Verify backup
if [ ! -f "$BACKUP_PATH" ]; then
    echo -e "${RED}ERROR: Backup creation failed${NC}"
    exit 1
fi

BACKUP_SIZE=$(du -h "$BACKUP_PATH" | cut -f1)
echo -e "${GREEN}✓ Backup created: $BACKUP_SIZE${NC}"
echo "  Location: $BACKUP_PATH"
echo ""

# Run migrations
echo "Running migrations..."
echo "─────────────────────────────────────────────────────────"

if alembic upgrade head; then
    echo "─────────────────────────────────────────────────────────"
    echo -e "${GREEN}✓ Migration successful${NC}"
    
    # Verify new revision
    NEW_CURRENT=$(alembic current 2>/dev/null | grep -v "INFO" | head -1)
    echo "  New revision: $NEW_CURRENT"
    echo ""
    
    # Clean up old backups (keep last 30)
    echo "Cleaning old backups (keeping last 30)..."
    OLD_COUNT=$(ls -t "$BACKUP_DIR"/pre-migration-*.db 2>/dev/null | wc -l)
    if [ "$OLD_COUNT" -gt 30 ]; then
        ls -t "$BACKUP_DIR"/pre-migration-*.db | tail -n +31 | xargs rm -f 2>/dev/null || true
        REMOVED=$((OLD_COUNT - 30))
        echo -e "${GREEN}✓ Removed $REMOVED old backup(s)${NC}"
    else
        echo "  No cleanup needed ($OLD_COUNT backups total)"
    fi
    
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║             Migration Complete - Success                 ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
    
else
    # Migration failed
    echo "─────────────────────────────────────────────────────────"
    echo -e "${RED}✗ Migration failed${NC}"
    echo ""
    echo -e "${YELLOW}To restore from backup:${NC}"
    echo "  cp $BACKUP_PATH $DB_PATH"
    echo ""
    echo "For manual rollback:"
    echo "  alembic downgrade -1"
    echo ""
    exit 1
fi
```

Make executable:
```bash
chmod +x bin/migrate
```

---

## Appendix C: Migration Testing Checklist

**Before deploying any migration to production:**

### Pre-Flight Checks

- [ ] **Model changes are correct** — Review `app/models.py` diff
- [ ] **Migration auto-generated** — `alembic revision --autogenerate -m "..."`
- [ ] **Migration file reviewed** — Check `upgrade()` and `downgrade()` logic
- [ ] **Data migrations added** — If changing column types or adding constraints
- [ ] **SQLite limitations handled** — Using `batch_alter_table` where needed

### Testing

- [ ] **Test on copy of production database**
  ```bash
  cp data/lobs.db data/lobs-test.db
  # Edit alembic.ini to point to test DB
  alembic upgrade head
  # Verify schema and data
  sqlite3 data/lobs-test.db ".schema"
  ```

- [ ] **Test downgrade (rollback)**
  ```bash
  alembic downgrade -1
  alembic upgrade head
  ```

- [ ] **Verify row counts match**
  ```bash
  # Before migration
  sqlite3 data/lobs.db "SELECT COUNT(*) FROM tasks;"
  # After migration (on test DB)
  sqlite3 data/lobs-test.db "SELECT COUNT(*) FROM tasks;"
  ```

- [ ] **Check foreign key integrity**
  ```bash
  sqlite3 data/lobs-test.db "PRAGMA foreign_key_check;"
  ```

- [ ] **Run application tests**
  ```bash
  pytest tests/
  ```

### Deployment

- [ ] **Backup exists** — `./bin/migrate` creates automatic backup
- [ ] **Migration script reviewed** — Read the generated Python file
- [ ] **Downtime acceptable** — Or coordinate with users
- [ ] **Rollback plan documented** — Know how to undo if needed

### Post-Deployment

- [ ] **Verify migration applied** — `alembic current` shows expected revision
- [ ] **Check application health** — `/api/health` returns 200
- [ ] **Smoke test key features** — Create task, fetch projects, etc.
- [ ] **Monitor logs** — Watch for SQL errors or 500s

---

## Next Steps

### Immediate (Programmer Tasks)

1. **Initialize Alembic** — Run `alembic init --template async alembic`
2. **Configure env.py** — Set up async + import models (see Appendix A)
3. **Create baseline migration** — Capture current schema
4. **Create bin/migrate script** — Automated backup + upgrade (see Appendix B)
5. **Test on copy of database** — Verify setup works

### Future Enhancements

- **Pre-commit hook** — Warn if models.py changed but no migration generated
- **CI/CD integration** — Run migration tests in GitHub Actions
- **Migration dry-run mode** — Generate SQL without executing
- **Schema validation** — Compare runtime schema to models.py on startup
- **Migration notifications** — Alert team when migrations are pending

---

## References

- **Alembic Documentation:** https://alembic.sqlalchemy.org/
- **Async Migrations:** https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic
- **SQLite ALTER TABLE:** https://www.sqlite.org/lang_altertable.html
- **Decision Record:** [docs/decisions/0002-sqlite-for-primary-database.md](decisions/0002-sqlite-for-primary-database.md)
- **lobs-server Architecture:** [ARCHITECTURE.md](../ARCHITECTURE.md)

---

**Document Status:** Ready for implementation  
**Owner:** System Architect  
**Reviewers:** Programmer, DevOps

*Last updated: 2026-02-22*
