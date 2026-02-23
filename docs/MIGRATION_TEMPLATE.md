# Migration Guide: [Feature/Refactor Name]

**Date:** YYYY-MM-DD  
**Version:** X.Y.Z → X.Y.Z  
**Severity:** 🔴 Breaking | 🟡 Non-Breaking | 🟢 Internal Only  
**Downtime Required:** Yes/No  
**Rollback Difficulty:** Easy | Moderate | Hard

---

## Overview

**What Changed:**  
Brief 1-2 sentence summary of the change.

**Why:**  
Reason for the change (performance, maintainability, feature support, etc.).

**Impact:**  
Who/what is affected (API consumers, database, agents, UI, etc.).

---

## Breaking Changes

### API Changes

**Endpoints Modified:**
- `METHOD /path` — Change description
  - Request: What changed in request schema
  - Response: What changed in response schema
  
**New Endpoints:**
- `METHOD /path` — Purpose

**Deprecated Endpoints:**
- `METHOD /path` — Use `METHOD /new-path` instead

**Removed Endpoints:**
- `METHOD /path` — Replaced by X

### Database Schema

**Tables Modified:**
- `table_name`
  - Added: `column_name TYPE` — Description
  - Changed: `column_name` — Old TYPE → New TYPE
  - Removed: `column_name`

**Migration Required:** Yes/No  
**Migration Script:** `migrations/YYYYMMDD_description.sql` or handled by Alembic

### Configuration

**Environment Variables:**
- Added: `VAR_NAME` — Description (default: value)
- Changed: `VAR_NAME` — Old meaning → New meaning
- Removed: `VAR_NAME` — Use `NEW_VAR_NAME` instead

**Config Files:**
- `file.json` — Changes required

### Agent Behavior

**Affected Agents:** agent-name, agent-name

**Changes:**
- Description of how agent behavior changes
- New capabilities or restrictions
- Prompt or registry changes

---

## Upgrade Steps

### Pre-Upgrade

**1. Backup**
```bash
# Database
cp data/lobs.db data/lobs.db.backup-YYYYMMDD

# Configuration
cp .env .env.backup-YYYYMMDD
```

**2. Check Prerequisites**
- [ ] Python version ≥ X.Y
- [ ] Disk space available (X GB for migration)
- [ ] No running tasks (check `/api/orchestrator/status`)
- [ ] Gateway version ≥ X.Y (if applicable)

**3. Notify Stakeholders**
- Stop orchestrator: `POST /api/orchestrator/stop`
- Announce maintenance window (if downtime required)

### Upgrade

**1. Pull Latest Code**
```bash
git fetch origin
git checkout vX.Y.Z  # or main/master
```

**2. Update Dependencies**
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

**3. Run Database Migration** (if required)
```bash
# Manual migration script
python migrations/YYYYMMDD_migration.py

# Or Alembic (if using)
alembic upgrade head
```

**4. Update Configuration** (if required)
```bash
# Add new environment variables to .env
echo "NEW_VAR=value" >> .env

# Update config files
# (provide specific instructions or script)
```

**5. Restart Server**
```bash
./bin/run
# or
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**6. Verify**
```bash
# Health check
curl http://localhost:8000/api/health

# Feature-specific verification
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/endpoint
```

**7. Resume Operations**
- Start orchestrator: `POST /api/orchestrator/start`
- Monitor logs for errors
- Check `/api/status` for system health

### Post-Upgrade

**1. Smoke Tests**
- [ ] Create a task
- [ ] Run worker on test task
- [ ] Check affected feature works correctly
- [ ] Review logs for warnings/errors

**2. Monitor**
- Watch logs for 15-30 minutes
- Check error rates in `/api/status`
- Verify agent execution works

**3. Clean Up** (optional, after verification)
```bash
# Remove old backups after N days
# Remove deprecated code/configs
```

---

## Rollback Procedure

**Difficulty:** Easy | Moderate | Hard  
**Data Loss Risk:** None | Minimal | Significant

### When to Rollback

Roll back if:
- Critical errors in logs
- Feature completely broken
- Database corruption detected
- Performance degradation > X%

### Rollback Steps

**1. Stop Server**
```bash
# Stop orchestrator first
curl -X POST http://localhost:8000/api/orchestrator/stop

# Kill server process
pkill -f uvicorn
```

**2. Restore Code**
```bash
git checkout vX.Y.Z-previous
```

**3. Restore Database** (if schema changed)
```bash
# Stop server first!
cp data/lobs.db data/lobs.db.failed-YYYYMMDD
cp data/lobs.db.backup-YYYYMMDD data/lobs.db

# If migration was applied, run down-migration
python migrations/YYYYMMDD_rollback.py
# or
alembic downgrade -1
```

**4. Restore Configuration**
```bash
cp .env.backup-YYYYMMDD .env
# Restore any other config files
```

**5. Restart Server**
```bash
./bin/run
```

**6. Verify Rollback**
```bash
curl http://localhost:8000/api/health
# Test critical functionality
```

**7. Investigate**
- Review logs from failed upgrade: `logs/app.log`
- Check error messages
- Open issue with details

---

## Testing Checklist

### Before Deployment

**Unit Tests:**
- [ ] All tests pass: `pytest`
- [ ] New tests added for changed behavior
- [ ] Coverage maintained or improved

**Integration Tests:**
- [ ] API endpoint tests updated
- [ ] Database migration tested on copy of prod data
- [ ] Agent execution tested

**Manual Testing:**
- [ ] Feature works in dev environment
- [ ] Error handling works
- [ ] Performance acceptable

### After Deployment

**Smoke Tests:**
- [ ] Server starts without errors
- [ ] Health endpoint responds
- [ ] Auth works
- [ ] Database queries work

**Feature Tests:**
- [ ] Affected endpoints return correct data
- [ ] UI displays correctly (if applicable)
- [ ] Agent behavior correct
- [ ] No regressions in related features

**Load Tests** (if significant change):
- [ ] Response times acceptable
- [ ] No memory leaks
- [ ] Database performance stable

---

## Troubleshooting

### Issue: [Common Problem]

**Symptoms:**
- Error message or behavior

**Cause:**
- Why this happens

**Fix:**
```bash
# Steps to resolve
```

### Issue: Migration Fails

**Symptoms:**
- Migration script errors out
- Database in inconsistent state

**Fix:**
```bash
# 1. Check migration logs
cat migrations/YYYYMMDD_migration.log

# 2. Verify prerequisites
# 3. Try manual steps if auto-migration failed
# 4. If stuck, rollback and investigate
```

### Issue: Server Won't Start

**Symptoms:**
- Server exits immediately
- Port binding errors
- Import errors

**Fix:**
```bash
# Check dependencies
pip list | grep [package]

# Check configuration
cat .env

# Check logs
tail -f logs/app.log
```

---

## Support

**Questions:** Open issue in GitHub  
**Bugs:** Report via `/api/inbox` or GitHub issues  
**Documentation:** See [CHANGELOG.md](../CHANGELOG.md) for all changes

---

# Example Migration: 5-Tier Model Hierarchy

**Date:** 2026-02-21  
**Version:** 0.x → 0.x  
**Severity:** 🟡 Non-Breaking (backward compatible)  
**Downtime Required:** No  
**Rollback Difficulty:** Easy

---

## Overview

**What Changed:**  
Expanded model routing from 3 tiers (cheap/standard/strong) to 5 tiers (micro/small/medium/standard/strong) with Ollama auto-discovery.

**Why:**  
Better cost control, support for local models, and more granular performance/cost trade-offs.

**Impact:**  
- Database: Added `model_tier` field to Task model
- API: New optional `model_tier` parameter on task creation
- Orchestrator: Uses 5-tier routing logic
- Agents: Can request specific tiers via task creation

---

## Breaking Changes

### API Changes

**Endpoints Modified:**
- `POST /api/tasks` — Now accepts optional `model_tier` field
  - Request: Added `model_tier?: "micro" | "small" | "medium" | "standard" | "strong"`
  - Response: Task object now includes `model_tier` field (may be null)
  
**Backward Compatibility:**  
✅ Yes — existing clients work unchanged. Old 3-tier values map to new tiers automatically.

### Database Schema

**Tables Modified:**
- `tasks`
  - Added: `model_tier VARCHAR(20)` — Explicit tier override (nullable)

**Migration Required:** Yes (automatic via Alembic or manual script)

**Migration Script:**
```sql
-- migrations/20260221_add_model_tier.sql
ALTER TABLE tasks ADD COLUMN model_tier VARCHAR(20);
-- No data migration needed; null values are valid
```

### Configuration

**Environment Variables:**
- No changes required

**Agent Registry:**
- Updated `config/agent-capabilities.json` to reference 5-tier system
- Old 3-tier references mapped automatically

### Agent Behavior

**Affected Agents:** All (orchestrator, project-manager, workers)

**Changes:**
- Orchestrator routes tasks using 5-tier logic instead of 3-tier
- Ollama models auto-discovered and injected into micro/small/medium tiers
- Local models no longer in separate "local" tier
- Project-manager can specify tier when creating tasks

**Backward Compatibility:**  
✅ Agents using old tier names ("cheap", "standard", "strong") continue to work via mapping.

---

## Upgrade Steps

### Pre-Upgrade

**1. Backup**
```bash
cp data/lobs.db data/lobs.db.backup-20260221
```

**2. Check Prerequisites**
- [ ] Python version ≥ 3.11
- [ ] No critical tasks in-flight (or wait for completion)
- [ ] Orchestrator can be safely stopped

**3. Notify Stakeholders**
```bash
# Optional: stop orchestrator to prevent task pickup during upgrade
curl -X POST http://localhost:8000/api/orchestrator/stop \
  -H "Authorization: Bearer $TOKEN"
```

### Upgrade

**1. Pull Latest Code**
```bash
cd ~/lobs-server
git fetch origin
git pull origin main
```

**2. Update Dependencies**
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

**3. Run Database Migration**
```bash
# If using Alembic
alembic upgrade head

# Or manual SQL (if not using Alembic)
sqlite3 data/lobs.db < migrations/20260221_add_model_tier.sql
```

**4. Restart Server**
```bash
# Kill old process
pkill -f uvicorn

# Start new version
./bin/run
```

**5. Verify**
```bash
# Health check
curl http://localhost:8000/api/health

# Check task creation with new field
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test task",
    "description": "Testing model_tier field",
    "project_id": "abc123",
    "model_tier": "small"
  }'
```

**6. Resume Operations**
```bash
# Restart orchestrator
curl -X POST http://localhost:8000/api/orchestrator/start \
  -H "Authorization: Bearer $TOKEN"
```

### Post-Upgrade

**1. Smoke Tests**
- [ ] Create task without `model_tier` (should work, tier auto-selected)
- [ ] Create task with `model_tier: "small"` (should work, tier enforced)
- [ ] Run worker task (should route to correct model)
- [ ] Check logs for tier selection messages

**2. Monitor**
- Watch orchestrator logs for "Selected model tier: X" messages
- Verify Ollama models appear in tier selection (if Ollama running)
- Check `/api/orchestrator/status` for healthy workers

**3. Clean Up**
```bash
# After 24-48 hours of successful operation
rm data/lobs.db.backup-20260221
```

---

## Rollback Procedure

**Difficulty:** Easy  
**Data Loss Risk:** None (new field is optional)

### When to Rollback

Roll back if:
- Model routing completely broken
- Workers fail to spawn
- Database errors on task creation

### Rollback Steps

**1. Stop Server**
```bash
pkill -f uvicorn
```

**2. Restore Code**
```bash
cd ~/lobs-server
git checkout <previous-commit-hash>
```

**3. Rollback Database** (optional)
```bash
# Only needed if migration causes issues
# The new column is nullable, so old code ignores it
# But if needed:
sqlite3 data/lobs.db "ALTER TABLE tasks DROP COLUMN model_tier;"
# Note: SQLite doesn't support DROP COLUMN in older versions
# Alternative: restore from backup
cp data/lobs.db.backup-20260221 data/lobs.db
```

**4. Restart Server**
```bash
./bin/run
```

**5. Verify Rollback**
```bash
curl http://localhost:8000/api/health
# Test task creation works
```

---

## Testing Checklist

### Before Deployment

**Unit Tests:**
- [x] All tests pass
- [x] New tests for 5-tier routing
- [x] Model tier field validation

**Integration Tests:**
- [x] Task creation with and without `model_tier`
- [x] Orchestrator routes to correct models
- [x] Ollama discovery works

**Manual Testing:**
- [x] Created tasks in dev with each tier
- [x] Verified worker spawned with correct model
- [x] Checked Ollama models auto-discovered

### After Deployment

**Smoke Tests:**
- [ ] Server started successfully
- [ ] Task creation works (with and without tier)
- [ ] Orchestrator running
- [ ] Workers spawning correctly

**Feature Tests:**
- [ ] Task with `model_tier: "micro"` uses Ollama model
- [ ] Task with `model_tier: "strong"` uses Opus/Codex
- [ ] Task without tier auto-selects appropriate tier
- [ ] Existing tasks (null tier) still work

---

## Troubleshooting

### Issue: Worker Spawns with Wrong Model

**Symptoms:**
- Task specifies `model_tier: "small"` but worker uses Opus

**Cause:**
- Model tier not passed to worker spawn call
- Registry not updated with 5-tier config

**Fix:**
```bash
# 1. Check worker.py for tier handling
grep -n "model_tier" app/orchestrator/worker.py

# 2. Verify registry has 5-tier entries
cat config/agent-capabilities.json | jq '.tiers'

# 3. Check orchestrator logs for tier selection
tail -f logs/orchestrator.log | grep "Selected model tier"
```

### Issue: Ollama Models Not Appearing

**Symptoms:**
- Ollama running but models not used for micro/small tiers

**Cause:**
- Ollama not accessible at expected URL
- Models not discovered during startup

**Fix:**
```bash
# 1. Verify Ollama is running
curl http://localhost:11434/api/tags

# 2. Check discovery in server logs
tail -f logs/app.log | grep -i ollama

# 3. Manually verify model list endpoint
curl http://localhost:8000/api/orchestrator/models
```

### Issue: Migration Fails with Column Already Exists

**Symptoms:**
- Migration script errors: "column model_tier already exists"

**Cause:**
- Migration already run, or manual column added

**Fix:**
```bash
# Check if column exists
sqlite3 data/lobs.db "PRAGMA table_info(tasks);" | grep model_tier

# If exists, skip migration or mark as complete in Alembic
alembic stamp head
```

---

## Support

**Questions:** Open issue in GitHub  
**Bugs:** Report via GitHub issues  
**Documentation:** See [CHANGELOG.md](../CHANGELOG.md) section "5-Tier Model Hierarchy"
