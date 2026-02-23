# 2. SQLite for Primary Database

**Date:** 2026-02-22  
**Status:** Accepted  
**Deciders:** System architect, product owner

## Context

lobs-server needed a relational database for:
- Task and project management (CRUD, state transitions, queries)
- Memory system (notes, topics, documents, search)
- Calendar events and recurring schedules
- System activity and cost tracking
- Multi-table joins and transactions

The system is a single-user personal productivity tool running on a local development machine, not a multi-tenant SaaS application.

Key requirements:
- Reliable ACID transactions
- Good support for JSON fields (flexible schemas)
- Fast local queries (sub-10ms)
- Simple backup/restore
- Minimal operational overhead

## Decision

We use **SQLite** with Write-Ahead Logging (WAL) mode as the primary database.

Configuration:
- **WAL mode** — Enables concurrent reads during writes
- **aiosqlite** — Async SQLite adapter for FastAPI
- **SQLAlchemy ORM** — Type-safe models, migrations via Alembic
- **Single database file** — `lobs.db` in project root

## Consequences

### Positive

- **Zero configuration** — No server to install, configure, or manage
- **Zero operational cost** — No daemon, no ports, no authentication
- **Instant backups** — Copy a single file
- **Fast local queries** — No network latency, sub-millisecond reads
- **Simple development** — Same database for dev, test, and production
- **Portable** — Entire database is one file, easy to move or version
- **Excellent Python support** — aiosqlite, SQLAlchemy work seamlessly
- **JSON support** — SQLite 3.38+ has full JSON functions
- **Reliable** — SQLite is one of the most tested software libraries in the world

### Negative

- **No concurrent writes** — WAL mode helps, but write throughput limited to single connection
- **Single server only** — Can't distribute across multiple machines
- **Limited full-text search** — FTS5 exists but less powerful than Postgres
- **No advanced features** — No LISTEN/NOTIFY, no materialized views, no partial indexes
- **Size limits** — 281 TB max, but performance degrades well before that
- **Migration friction** — Moving to Postgres later requires schema translation

### Neutral

- Database is file-based, not network-accessible (matches single-user model)
- Backups are manual file copies (acceptable for personal tool)

## Alternatives Considered

### Option 1: PostgreSQL

- **Pros:**
  - Superior concurrent write performance
  - Advanced features (JSONB, full-text search, LISTEN/NOTIFY)
  - Better tooling (pgAdmin, extensions, monitoring)
  - Industry standard for production systems
  - Easy to scale to multi-user if needed

- **Cons:**
  - Requires server process (installation, configuration, maintenance)
  - Network overhead even for localhost
  - More complex backup/restore
  - Overkill for single-user workload
  - ~50-100MB memory overhead
  - Connection pool management required

- **Why rejected:** Operational overhead not justified for personal productivity tool. Current workload is ~10-100 requests/day, far below where Postgres advantages matter.

### Option 2: MySQL/MariaDB

- **Pros:**
  - Good concurrent write performance
  - Wide hosting availability
  - Familiar to many developers

- **Cons:**
  - Same operational overhead as Postgres
  - Weaker JSON support than Postgres
  - Less modern architecture
  - No compelling advantage over SQLite or Postgres

- **Why rejected:** If we need a server, Postgres is the better choice. If we don't, SQLite is simpler.

### Option 3: NoSQL (MongoDB, DynamoDB, etc.)

- **Pros:**
  - Flexible schema
  - Good for document storage
  - Horizontal scaling

- **Cons:**
  - Complex queries require application-level joins
  - No ACID transactions across documents (older versions)
  - Unfamiliar query language
  - Requires separate server
  - Poor fit for relational data (tasks → projects → initiatives)

- **Why rejected:** lobs-server data is highly relational. Tasks reference projects, projects reference initiatives, calendar events link to tasks. SQL is the right model.

### Option 4: In-Memory (Redis, Memcached)

- **Pros:**
  - Extremely fast
  - Simple key-value model

- **Cons:**
  - No persistence by default (or limited)
  - No complex queries
  - Limited data structures
  - Not a primary database

- **Why rejected:** Not suitable for primary storage. Could be used as cache layer later if needed.

## Migration Path

If the system outgrows SQLite (multiple concurrent users, write-heavy workload, need for LISTEN/NOTIFY), migration to Postgres is straightforward:

1. SQLAlchemy models work with both SQLite and Postgres
2. Alembic migrations can target Postgres
3. Data can be exported and reimported
4. Connection URL is the only code change needed

We'll monitor:
- Database file size (watch for >1GB)
- Write contention (look for `SQLITE_BUSY` errors)
- Query performance (slow query log)

If any of these become issues, we revisit this decision.

## References

- `app/database.py` — Database session management, WAL mode configuration
- `app/models.py` — SQLAlchemy models
- SQLite Documentation: https://www.sqlite.org/wal.html
- aiosqlite: https://github.com/omnilib/aiosqlite

## Notes

**Current performance metrics (as of Feb 2026):**
- Database size: ~5MB
- Average query time: <5ms
- Write throughput: Sufficient for current workload
- No SQLITE_BUSY errors observed

This decision is **reversible** but low-priority. SQLite is working well and will continue to do so for the foreseeable future.

---

*Based on Michael Nygard's ADR format*
