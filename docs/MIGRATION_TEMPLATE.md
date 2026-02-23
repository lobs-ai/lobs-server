# Migration Guide Template

**Version:** [Source Version] → [Target Version]  
**Date:** [YYYY-MM-DD]  
**Estimated Downtime:** [None / X minutes]  
**Risk Level:** [Low / Medium / High]

---

## Overview

[Brief description of what changed and why. 2-3 sentences max.]

**Key Impact Areas:**
- [Area 1 — brief description]
- [Area 2 — brief description]
- [Area 3 — brief description]

---

## Breaking Changes

### 1. [Change Name]

**What Changed:**  
[Clear description of the change]

**Why It Breaks:**  
[Explain what will fail and why]

**Migration Path:**  
[Step-by-step instructions to adapt]

```bash
# Example: Old way
old_command --flag value

# New way
new_command --new-flag value
```

**API Changes:**
```diff
- OLD_ENDPOINT or OLD_FIELD
+ NEW_ENDPOINT or NEW_FIELD
```

---

### 2. [Another Breaking Change]

[Repeat structure above for each breaking change]

---

## Upgrade Steps

### Pre-Upgrade Checklist

- [ ] **Backup database** — `cp data/lobs.db data/lobs.db.backup-$(date +%Y%m%d)`
- [ ] **Review breaking changes** — Read section above
- [ ] **Check dependencies** — Ensure pip packages are up to date
- [ ] **Notify stakeholders** — Inform users of planned maintenance
- [ ] **Tag current version** — `git tag v[current]` (if applicable)

### Upgrade Procedure

**Step 1: Stop the server**
```bash
# Find and stop the running server
pkill -f "uvicorn app.main:app"
# Or if using systemd:
# sudo systemctl stop lobs-server
```

**Step 2: Pull latest code**
```bash
git fetch origin
git checkout [target-branch-or-tag]
```

**Step 3: Update dependencies**
```bash
source .venv/bin/activate
pip install -r requirements.txt --upgrade
```

**Step 4: Run database migrations** (if any)
```bash
# Check for migration scripts
ls migrations/

# Apply migrations
python migrations/[migration-script].py
```

**Step 5: Update configuration**
```bash
# Review and update environment variables
cat .env.example  # Check for new required variables
nano .env         # Add/update as needed
```

**Step 6: Verify configuration**
```bash
# Test configuration loading
python -c "from app.config import settings; print('Config OK')"
```

**Step 7: Start server**
```bash
./bin/run
# Or if using systemd:
# sudo systemctl start lobs-server
```

**Step 8: Verify health**
```bash
curl http://localhost:8000/api/health
# Expected: {"status": "ok", ...}
```

**Step 9: Smoke test critical paths**
```bash
# Test authentication
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:8000/api/projects

# Test orchestrator
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:8000/api/orchestrator/status

# Test chat
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:8000/api/chat/sessions
```

---

## Rollback Procedure

**If the upgrade fails or causes issues, follow these steps to roll back:**

### Step 1: Stop the new version
```bash
pkill -f "uvicorn app.main:app"
```

### Step 2: Restore previous code
```bash
git checkout [previous-version-tag]
# Or restore from backup:
# git reset --hard HEAD@{1}
```

### Step 3: Restore database (if schema changed)
```bash
# Only if database schema was modified
mv data/lobs.db data/lobs.db.failed-upgrade
cp data/lobs.db.backup-[date] data/lobs.db
```

### Step 4: Restore dependencies
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### Step 5: Restart server
```bash
./bin/run
```

### Step 6: Verify rollback success
```bash
curl http://localhost:8000/api/health
```

### Step 7: Document the failure
```bash
# Create issue or log entry
echo "Rollback reason: [describe issue]" >> logs/rollback-$(date +%Y%m%d).log
```

---

## Testing Checklist

### Pre-Deployment Testing (Staging/Dev)

**Core Functionality:**
- [ ] Server starts without errors
- [ ] Health endpoint responds
- [ ] Authentication works (valid/invalid tokens)
- [ ] Database queries execute successfully

**API Endpoints:**
- [ ] `GET /api/projects` — List projects
- [ ] `POST /api/projects` — Create project
- [ ] `GET /api/tasks` — List tasks
- [ ] `POST /api/tasks` — Create task
- [ ] `GET /api/memories/search` — Search memories
- [ ] `GET /api/chat/sessions` — List chat sessions
- [ ] `GET /api/calendar/events` — List events

**Orchestrator:**
- [ ] Scanner finds eligible tasks
- [ ] Router delegates to project-manager
- [ ] Worker spawns successfully
- [ ] Task state updates correctly
- [ ] Failure handling works (escalation, retry)

**Integration:**
- [ ] OpenClaw Gateway communication
- [ ] WebSocket connections (chat)
- [ ] Database writes/reads
- [ ] Token validation

### Post-Deployment Testing (Production)

**Immediate (within 5 minutes):**
- [ ] Server is responding to requests
- [ ] No error spikes in logs
- [ ] Active sessions still work
- [ ] Background jobs are running

**Short-term (within 1 hour):**
- [ ] Task orchestration cycle completes
- [ ] Agent workers spawn successfully
- [ ] Chat messages delivered
- [ ] Memory search returns results

**Long-term (within 24 hours):**
- [ ] No performance degradation
- [ ] No recurring errors
- [ ] All scheduled tasks execute
- [ ] User-reported issues (if any)

---

## Post-Migration Tasks

**Immediate:**
- [ ] Monitor logs for errors (`tail -f logs/server.log`)
- [ ] Check orchestrator activity (`curl /api/orchestrator/status`)
- [ ] Verify agent workers (`curl /api/worker/activity`)

**Within 24 hours:**
- [ ] Review system health dashboard
- [ ] Clean up old backup files (after confirming stability)
- [ ] Update documentation (if needed)
- [ ] Tag new version (`git tag v[new-version]`)

**Within 1 week:**
- [ ] Remove deprecated code (if any grace period is over)
- [ ] Archive migration docs (move to `docs/migrations/archive/`)
- [ ] Update CHANGELOG.md with final notes

---

## Known Issues & Workarounds

**Issue:** [Description of known issue]  
**Impact:** [Who/what is affected]  
**Workaround:** [Temporary fix]  
**Tracking:** [Link to issue or ticket]

---

## Support & Resources

**Documentation:**
- [ARCHITECTURE.md](ARCHITECTURE.md) — System overview
- [AGENTS.md](AGENTS.md) — API reference
- [CHANGELOG.md](CHANGELOG.md) — Version history

**Getting Help:**
- Check logs: `tail -f logs/server.log`
- System status: `curl /api/health`
- Open issue: [link to issue tracker]

**Emergency Contacts:**
- [Role/Person]: [Contact method]

---

# Example Migration: 5-Tier Model Routing

**Version:** 3-Tier → 5-Tier Model Hierarchy  
**Date:** 2026-02-21  
**Estimated Downtime:** None  
**Risk Level:** Medium

---

## Overview

Upgraded model routing system from 3 tiers (cheap/standard/strong) to 5 tiers (micro/small/medium/standard/strong) for better cost control and local model support. Ollama models are now auto-discovered and injected based on parameter count.

**Key Impact Areas:**
- **Model configuration** — Tier names changed, new tiers added
- **Agent prompts** — References to model tiers need updating
- **Task routing** — Auto-selection logic updated
- **Local models** — Dedicated "local" tier removed, now auto-injected

---

## Breaking Changes

### 1. Model Tier Names Changed

**What Changed:**  
- Old tiers: `cheap`, `standard`, `strong`
- New tiers: `micro`, `small`, `medium`, `standard`, `strong`

**Why It Breaks:**  
Any code or configuration that hardcodes tier names will fail. API requests with `model_tier="cheap"` will not map correctly.

**Migration Path:**  
Update all references to old tier names:

```diff
# Agent configuration (e.g., config/agents/researcher.yaml)
- model_tier: cheap
+ model_tier: small

# Task creation (e.g., API calls)
- {"model_tier": "cheap"}
+ {"model_tier": "small"}

# Environment variables
- DEFAULT_MODEL_TIER=cheap
+ DEFAULT_MODEL_TIER=small
```

**API Changes:**
```diff
# Task model field
- "model_tier": "cheap"     → "model_tier": "small" or "micro"
- "model_tier": "standard"  → unchanged
- "model_tier": "strong"    → unchanged
```

---

### 2. Local Tier Removed

**What Changed:**  
Dedicated `local` tier removed. Local models (Ollama) now auto-inject into `micro`, `small`, or `medium` based on parameter count.

**Why It Breaks:**  
Tasks explicitly requesting `model_tier="local"` will fail or fall back incorrectly.

**Migration Path:**  
1. Remove all `model_tier="local"` assignments
2. Let system auto-select tier based on task complexity
3. For explicit local model use, specify `model_tier="micro"` or `"small"`

```diff
# Old way (explicit local)
- task.model_tier = "local"

# New way (auto-selection or explicit micro/small)
+ task.model_tier = None  # Auto-select
# OR
+ task.model_tier = "micro"  # Force cheapest tier (includes Ollama)
```

---

### 3. Model Hierarchy Reordered

**What Changed:**  
- Primary model: Codex 5.3 (was Sonnet 4.5)
- Fallback: Sonnet 4.5 (was Codex)
- Cheap tier: Gemini Flash / Haiku (was Gemini only)

**Why It Breaks:**  
Tasks may use different models than before, affecting output quality or cost.

**Migration Path:**  
No code changes required, but monitor task results for quality changes. If specific model is required:

```python
# Force specific model (not recommended, breaks fallback chain)
task.model_override = "anthropic/claude-sonnet-4-5"
```

---

### 4. Ollama Auto-Discovery

**What Changed:**  
System now auto-detects Ollama models and assigns them to tiers:
- <5B params → `micro`
- 5-15B params → `small`
- >15B params → `medium`

**Why It Breaks:**  
If Ollama models were previously hardcoded or manually configured, they may now appear in different tiers.

**Migration Path:**  
1. Run Ollama model scan: `curl /api/orchestrator/models/scan`
2. Verify tier assignments: `curl /api/orchestrator/models`
3. Remove manual Ollama configurations (now auto-managed)

---

## Upgrade Steps

### Pre-Upgrade Checklist

- [x] **Backup database** — `cp data/lobs.db data/lobs.db.backup-20260221`
- [x] **Review breaking changes** — See above
- [x] **Check agent configs** — Search for `model_tier: cheap` or `local`
- [x] **Notify agents** — Inform about model tier changes
- [x] **Tag current version** — `git tag v0.3.0-pre-5tier`

### Upgrade Procedure

**Step 1: Stop the server**
```bash
pkill -f "uvicorn app.main:app"
```

**Step 2: Pull latest code**
```bash
git fetch origin
git checkout feature/5-tier-model-routing
```

**Step 3: Update dependencies**
```bash
source .venv/bin/activate
pip install -r requirements.txt --upgrade
```

**Step 4: Update agent configurations**
```bash
# Find all agent configs with old tier names
grep -r "model_tier: cheap" config/agents/
grep -r "model_tier: local" config/agents/

# Update each file
sed -i '' 's/model_tier: cheap/model_tier: small/g' config/agents/*.yaml
sed -i '' 's/model_tier: local/model_tier: small/g' config/agents/*.yaml
```

**Step 5: Update environment variables**
```bash
# Add new tier configs to .env
cat >> .env << EOF
MICRO_TIER_MODEL=gemini/gemini-2.0-flash-lite
SMALL_TIER_MODEL=anthropic/claude-haiku-4
MEDIUM_TIER_MODEL=anthropic/claude-sonnet-4-5
STANDARD_TIER_MODEL=openai/gpt-5.3-codex
STRONG_TIER_MODEL=anthropic/claude-opus-4
EOF
```

**Step 6: Start server**
```bash
./bin/run
```

**Step 7: Trigger Ollama auto-discovery**
```bash
curl -X POST \
  -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:8000/api/orchestrator/models/scan
```

**Step 8: Verify model tiers**
```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:8000/api/orchestrator/models | jq
```

Expected output:
```json
{
  "tiers": {
    "micro": ["gemini-2.0-flash-lite", "ollama/qwen2.5-coder:3b"],
    "small": ["claude-haiku-4", "ollama/mistral:7b"],
    "medium": ["claude-sonnet-4-5", "ollama/llama3:70b"],
    "standard": ["gpt-5.3-codex"],
    "strong": ["claude-opus-4"]
  }
}
```

**Step 9: Test task execution**
```bash
# Create test task with new tier
curl -X POST \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Test 5-tier routing","model_tier":"small"}' \
  http://localhost:8000/api/tasks

# Check orchestrator picks it up
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:8000/api/orchestrator/status
```

---

## Rollback Procedure

**Step 1: Stop server**
```bash
pkill -f "uvicorn app.main:app"
```

**Step 2: Restore previous code**
```bash
git checkout v0.3.0-pre-5tier
```

**Step 3: Restore agent configs**
```bash
# Revert tier name changes
sed -i '' 's/model_tier: small/model_tier: cheap/g' config/agents/*.yaml
```

**Step 4: Restart server**
```bash
./bin/run
```

**Step 5: Verify rollback**
```bash
curl http://localhost:8000/api/health
```

---

## Testing Checklist

### Pre-Deployment Testing

**Model Tier Selection:**
- [x] `micro` tier selects Gemini Flash Lite or small Ollama model
- [x] `small` tier selects Haiku or medium Ollama model
- [x] `medium` tier selects Sonnet or large Ollama model
- [x] `standard` tier selects Codex 5.3
- [x] `strong` tier selects Opus 4

**Ollama Auto-Discovery:**
- [x] Ollama models detected via API scan
- [x] Models assigned to correct tier based on param count
- [x] Models appear in tier listing endpoint

**Task Execution:**
- [x] Task with `model_tier=null` auto-selects appropriate tier
- [x] Task with `model_tier="small"` uses small tier model
- [x] Fallback chain works when primary model unavailable

**Agent Configs:**
- [x] All agent YAML files updated with new tier names
- [x] No references to `cheap` or `local` tiers remain
- [x] Agents spawn successfully with new configurations

### Post-Deployment Testing

**Immediate:**
- [x] All 5 tiers populated with models
- [x] No errors in orchestrator logs
- [x] First task executed successfully

**Short-term (1 hour):**
- [x] Multiple tasks across different tiers completed
- [x] Cost tracking reflects new tier structure
- [x] No model selection failures

**Long-term (24 hours):**
- [x] Average cost per task decreased (due to better tier selection)
- [x] Task success rate unchanged or improved
- [x] No agent complaints about model quality

---

## Post-Migration Tasks

**Immediate:**
- [x] Monitor orchestrator logs: `tail -f logs/orchestrator.log`
- [x] Track model usage: `curl /api/orchestrator/models/usage`
- [x] Verify cost metrics: `curl /api/status/costs`

**Within 24 hours:**
- [x] Review system health dashboard
- [x] Compare cost/task vs previous week
- [x] Update CHANGELOG.md with migration notes

**Within 1 week:**
- [x] Remove deprecated 3-tier code paths
- [x] Archive old agent configs
- [x] Document new tier selection guidelines

---

## Known Issues & Workarounds

**Issue:** Ollama models may not auto-discover if Ollama service is down  
**Impact:** `micro` and `small` tiers will fall back to cloud models  
**Workaround:** Manually trigger scan after Ollama restarts: `curl -X POST /api/orchestrator/models/scan`  
**Tracking:** Not a blocker, scan runs hourly

---

## Support & Resources

**Documentation:**
- [Model Routing Architecture](docs/model-routing.md)
- [Ollama Integration Guide](docs/ollama-setup.md)
- [CHANGELOG.md](CHANGELOG.md)

**Getting Help:**
- Check tier status: `curl /api/orchestrator/models`
- Check agent logs: `tail -f logs/agent-*.log`
- Slack: #lobs-support

**Emergency Contacts:**
- On-call engineer: [contact info]
