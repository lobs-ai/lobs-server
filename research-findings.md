# Deployment Architecture Research Findings

**Research Task:** Document deployment architecture with setup scripts  
**Researcher:** researcher agent  
**Date:** 2026-02-23  
**Status:** Initial investigation complete

---

## Executive Summary

lobs-server currently has **development-focused setup** but lacks **production deployment documentation and automation**. The codebase has good modularity and configuration management, but deployment knowledge is tacit rather than documented.

**Key Finding:** The system is ready to deploy, but deployment processes are undocumented and manual. Setup scripts exist for development (macOS LaunchAgent), but production deployment (Linux VM with systemd + Tailscale) is not codified.

**Recommendation:** Create three deployment modes with full automation:
1. **Development** (macOS, local)
2. **Production** (Linux VM, systemd, Tailscale)  
3. **Quick Start** (any platform, temporary)

---

## Current State Analysis

### What Exists

#### 1. **Development Setup** (macOS-focused)

**Source:** `bin/server`, `bin/run`, `bin/setup-agents`, `QUICKSTART.md`, `SETUP.md`

- **LaunchAgent management** (`bin/server`) — start/stop/restart for macOS
- **Manual run script** (`bin/run`) — direct uvicorn execution with LAN/local mode
- **Agent workspace setup** (`bin/setup-agents`) — configures OpenClaw agent workspaces
- **Comprehensive documentation** — Quick start and detailed setup guides

**Current deployment method:**
```bash
# Development (macOS)
source .venv/bin/activate
./bin/run        # Runs uvicorn directly
# OR
./bin/server start  # Uses LaunchAgent for persistent service
```

#### 2. **Configuration Management**

**Source:** `.env.example`, `app/config.py`

Good configuration isolation via environment variables:
- Database path
- Orchestrator settings (enabled, poll interval, max workers)
- OpenClaw Gateway URL and token
- Backup settings (enabled, interval, retention)
- Logging (level, format, directory)

**All settings have sensible defaults** — server runs without `.env` file.

#### 3. **Dependencies**

**Source:** `requirements.txt`

Clean, minimal dependency tree:
- FastAPI + uvicorn (web server)
- SQLAlchemy + aiosqlite (async database)
- Pydantic (validation)
- pytest (testing)
- aiohttp (HTTP client for agents)
- scipy (vector search for memories)
- croniter (calendar recurrence)

**No complex dependencies** — installs cleanly on Python 3.11+

#### 4. **Service Architecture**

**Source:** `ARCHITECTURE.md`, `README.md`

Single-process FastAPI application with:
- REST API (16+ routers)
- WebSocket server (chat)
- Background orchestrator (task polling and agent spawning)
- SQLite database (WAL mode for concurrency)

**Ports:**
- 8000 (HTTP + WebSocket) — configurable via `--port`

**External dependencies:**
- OpenClaw Gateway (default: http://127.0.0.1:18789) — for agent execution
- SQLite database file (default: `./data/lobs.db`)

### What's Missing

1. **❌ Production deployment documentation**
   - No Linux/systemd setup guide
   - No VM provisioning instructions
   - No Tailscale configuration docs

2. **❌ systemd service files**
   - No `.service` file for lobs-server
   - No dependencies/ordering defined
   - No restart policies

3. **❌ Setup automation scripts**
   - No install.sh / setup.sh for fresh VMs
   - No automated dependency installation
   - No automated service registration

4. **❌ Deployment runbook**
   - No redeployment procedures
   - No rollback procedures
   - No health check verification steps

5. **❌ Infrastructure-as-code**
   - No Terraform/Ansible/cloud-init configs
   - VM specs not documented
   - Network configuration not documented

6. **❌ Secrets management**
   - No documented approach for API tokens
   - No .env templating for production
   - No key rotation procedures

---

## Deployment Requirements Analysis

### Production Environment Specification

Based on the codebase and task description, the production deployment likely needs:

#### **Infrastructure**

- **VM Specifications** (to be determined):
  - OS: Linux (Ubuntu 22.04+ or Debian 12+ recommended)
  - CPU: 2+ cores (orchestrator + multiple agent workers)
  - RAM: 4GB+ (SQLite, Python, agent sessions)
  - Storage: 20GB+ (database, logs, backups)
  - Network: Tailscale VPN mesh network

- **Networking**:
  - Tailscale IP (100.x.x.x range) — primary access
  - Port 8000 accessible via Tailscale
  - Optional: localhost-only binding for security
  - No public internet exposure required

- **Security**:
  - Firewall: Block all ports except Tailscale
  - User: Non-root service account (`lobs`)
  - File permissions: 0600 for .env, 0644 for code
  - Database: Owner-only read/write (0600)

#### **System Services**

1. **lobs-server.service** (systemd)
   - Type: simple
   - User: lobs
   - WorkingDirectory: /home/lobs/lobs-server
   - ExecStart: /home/lobs/lobs-server/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
   - Restart: always
   - RestartSec: 10
   - Environment: Load from /home/lobs/lobs-server/.env
   - Wants: network-online.target
   - After: network-online.target

2. **Dependencies** (system packages):
   - python3.11+ (or python3.12/3.14)
   - python3-venv
   - python3-pip
   - git
   - sqlite3
   - build-essential (for scipy)
   - curl (for health checks)

3. **Optional Services**:
   - Log rotation (logrotate config for logs/)
   - Database backup timer (systemd timer unit)

#### **Directory Structure**

Proposed production layout:
```
/home/lobs/
├── lobs-server/           # Application code (git clone)
│   ├── .venv/             # Python virtual environment
│   ├── .env               # Production configuration (secrets)
│   ├── data/              # Database and state
│   │   ├── lobs.db
│   │   └── backups/       # Automated DB backups
│   ├── logs/              # Application logs
│   └── ...
├── .openclaw/             # OpenClaw Gateway workspace
│   └── workspace-*/       # Agent workspaces
└── deployment/            # Deployment scripts and configs
    ├── lobs-server.service
    ├── setup.sh
    └── backup.sh
```

#### **Environment Variables** (production `.env`)

Critical settings for production:
```bash
# Database
DATABASE_PATH=/home/lobs/lobs-server/data/lobs.db

# Orchestrator
ORCHESTRATOR_ENABLED=true
ORCHESTRATOR_POLL_INTERVAL=10
ORCHESTRATOR_MAX_WORKERS=3

# OpenClaw Gateway (adjust for production Tailscale IPs)
OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789  # or Tailscale IP
OPENCLAW_GATEWAY_TOKEN=<secret-token>

# Backups
BACKUP_ENABLED=true
BACKUP_INTERVAL_HOURS=6
BACKUP_RETENTION_COUNT=30
BACKUP_DIR=/home/lobs/lobs-server/data/backups

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json  # Structured logging for production
LOG_DIR=/home/lobs/lobs-server/logs

# Projects directory
LOBS_PROJECTS_DIR=/home/lobs
AGENT_FILES_DIR=/home/lobs/lobs-orchestrator/agents
```

---

## Deployment Scenarios

### Scenario 1: Fresh VM Deployment

**Goal:** Deploy lobs-server to a new Linux VM from scratch

**Steps required:**
1. Provision VM (cloud provider or local)
2. Install Tailscale and join network
3. Create `lobs` user account
4. Install system dependencies (Python, git, build tools)
5. Clone lobs-server repository
6. Create Python virtual environment
7. Install Python dependencies
8. Generate API tokens
9. Configure .env file
10. Setup agent workspaces
11. Create systemd service
12. Enable and start service
13. Verify health check
14. Configure log rotation
15. Setup backup automation

**Current gap:** All steps are manual, no automation

### Scenario 2: Update/Redeploy Existing Server

**Goal:** Update code and restart service on existing deployment

**Steps required:**
1. Pull latest code (`git pull`)
2. Activate venv and update dependencies (`pip install -r requirements.txt`)
3. Run database migrations (if any)
4. Restart service (`systemctl restart lobs-server`)
5. Verify health check
6. Monitor logs for errors

**Current gap:** No documented procedure, no update script

### Scenario 3: Disaster Recovery

**Goal:** Restore service on new VM from database backup

**Steps required:**
1. Deploy fresh VM (Scenario 1, steps 1-11)
2. Stop service
3. Restore database from backup
4. Regenerate API tokens (or restore from secure storage)
5. Start service
6. Verify functionality
7. Reconnect clients (Mission Control, Mobile)

**Current gap:** No backup/restore documentation

---

## Proposed Solution Architecture

### Deliverables

#### **1. Documentation**

**DEPLOYMENT.md** — Production deployment guide
- VM specifications and requirements
- Tailscale setup instructions
- Step-by-step deployment procedures
- Configuration examples
- Troubleshooting guide
- Security checklist

**RUNBOOK.md** — Operational procedures (already exists, needs deployment section)
- Service management (start/stop/restart)
- Health monitoring
- Log inspection
- Backup and restore
- Update procedures
- Rollback procedures

#### **2. Systemd Service Files**

**deployment/lobs-server.service** — systemd unit file
```systemd
[Unit]
Description=Lobs Server - Central backend for Lobs Mission Control
Documentation=https://github.com/RafeSymonds/lobs-server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=lobs
Group=lobs
WorkingDirectory=/home/lobs/lobs-server
EnvironmentFile=/home/lobs/lobs-server/.env
ExecStart=/home/lobs/lobs-server/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level warning

Restart=always
RestartSec=10
StartLimitBurst=5
StartLimitIntervalSec=300

StandardOutput=journal
StandardError=journal
SyslogIdentifier=lobs-server

NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

**Optional:** `lobs-backup.service` + `lobs-backup.timer` for scheduled backups

#### **3. Setup Scripts**

**deployment/setup.sh** — Fresh VM setup automation
- Check prerequisites
- Install system dependencies
- Create user and directories
- Clone repository
- Setup Python environment
- Install dependencies
- Create default .env from template
- Generate initial API token
- Setup agent workspaces
- Install systemd service
- Enable and start service
- Verify deployment

**deployment/update.sh** — Update existing deployment
- Pull latest code
- Update dependencies
- Run migrations (when implemented)
- Restart service
- Verify health

**deployment/backup.sh** — Manual backup trigger
- Stop service (optional)
- Create timestamped database backup
- Prune old backups
- Restart service

**deployment/restore.sh** — Restore from backup
- Stop service
- Restore specified backup
- Start service
- Verify database integrity

#### **4. Configuration Templates**

**deployment/.env.production** — Production environment template
- Pre-filled with production-appropriate values
- Placeholder for secrets (tokens, passwords)
- Comments explaining each variable

**deployment/logrotate.conf** — Log rotation config
- Daily rotation
- 30-day retention
- Compression after rotation

#### **5. Health Check Script**

**deployment/health-check.sh** — Verify deployment
- Check service status
- Test API health endpoint
- Verify database connectivity
- Check orchestrator status
- Test WebSocket connectivity
- Validate Tailscale network access

---

## Implementation Recommendations

### Priority 1: Core Deployment (P0)

**Essential for first production deployment:**
1. ✅ **DEPLOYMENT.md** — Comprehensive deployment guide
2. ✅ **lobs-server.service** — systemd unit file
3. ✅ **setup.sh** — Automated fresh deployment
4. ✅ **.env.production template** — Production config template

**Estimated effort:** Writer (4-6 hours)

### Priority 2: Operations (P1)

**Needed for ongoing operations:**
1. ✅ **update.sh** — Safe update procedure
2. ✅ **backup.sh** + **restore.sh** — Manual backup/restore
3. ✅ **health-check.sh** — Deployment verification
4. ✅ Expand **RUNBOOK.md** — Add deployment operations

**Estimated effort:** Programmer (3-4 hours)

### Priority 3: Advanced (P2)

**Nice-to-have for mature operations:**
1. ⚠️ **lobs-backup.timer** — Automated backup systemd timer
2. ⚠️ **logrotate.conf** — Automated log cleanup
3. ⚠️ **Monitoring integration** — Prometheus/Grafana exports
4. ⚠️ **Infrastructure-as-code** — Terraform for VM provisioning

**Estimated effort:** Programmer + Architect (8-12 hours)

---

## Risk Analysis

### Current Risks (Without Documentation)

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **Undocumented deployment** | High | High | Create DEPLOYMENT.md |
| **Manual deployment errors** | High | Medium | Automate via setup.sh |
| **Service doesn't restart on reboot** | High | Medium | Create systemd service |
| **No backup automation** | High | Medium | Implement backup timer |
| **Inconsistent environments** | Medium | High | Standardize via .env template |
| **Lost deployment knowledge** | High | Low | Document everything |

### Deployment Complexity Assessment

**Overall complexity: LOW to MEDIUM**

**Why it's straightforward:**
- ✅ Single Python application (no microservices)
- ✅ Embedded SQLite database (no separate DB server)
- ✅ No container orchestration needed
- ✅ No complex networking (Tailscale handles it)
- ✅ Minimal external dependencies

**Gotchas to watch for:**
- ⚠️ OpenClaw Gateway must be accessible (same VM or Tailscale)
- ⚠️ API tokens must be regenerated or securely transferred
- ⚠️ Agent workspace setup requires OpenClaw CLI access
- ⚠️ Database backups must be tested for restore
- ⚠️ Python version consistency (3.11+ required)

---

## Open Questions

1. **VM Specifications**
   - What cloud provider? (AWS, DigitalOcean, Linode, on-prem?)
   - Exact VM size? (2vCPU/4GB vs 4vCPU/8GB?)
   - Storage requirements? (SSD size, backup storage location?)

2. **Tailscale Configuration**
   - How is Tailscale provisioned? (manual install or cloud-init?)
   - What's the Tailscale network name?
   - Access control lists (ACLs)?

3. **Multi-Service Coordination**
   - Does OpenClaw Gateway run on the same VM?
   - If separate: how do they discover each other? (Tailscale IPs hardcoded?)
   - Should there be a "deploy-all" script for the full Lobs ecosystem?

4. **Secrets Management**
   - How are API tokens distributed to clients?
   - How is OPENCLAW_GATEWAY_TOKEN secured?
   - Should we use systemd CredentialStore or just file permissions?

5. **Database Growth**
   - Expected growth rate? (affects backup retention)
   - Maximum database size planning?
   - Archival strategy?

6. **Monitoring & Alerting**
   - What monitoring system is in use?
   - Health check frequency?
   - Alert destinations (email, SMS, Slack?)

---

## Next Steps

### Immediate Actions (This Task)

1. **Create DEPLOYMENT.md** — Comprehensive deployment documentation
2. **Create systemd service file** — `deployment/lobs-server.service`
3. **Create setup.sh** — Automated deployment script
4. **Create .env.production template** — Production config reference

### Follow-Up Work (Separate Tasks)

1. **Test deployment on fresh VM** — Validate all procedures
2. **Document VM provisioning** — Add to DEPLOYMENT.md
3. **Implement backup automation** — systemd timer + service
4. **Create update/rollback procedures** — Operational scripts
5. **Add monitoring** — Health check integration

### Handoff Recommendations

**To Writer:**
- Write DEPLOYMENT.md (comprehensive deployment guide)
- Update RUNBOOK.md (add deployment operations section)
- Create deployment/ directory README

**To Programmer:**
- Implement setup.sh, update.sh, backup.sh, restore.sh
- Create systemd service files
- Create .env.production template
- Write health-check.sh

**To Architect (if needed):**
- Design multi-service deployment orchestration
- Define VM infrastructure specifications
- Design secrets management approach
- Define monitoring and alerting architecture

---

## Appendix: Current Development Setup

### macOS LaunchAgent (bin/server)

Current macOS setup uses LaunchAgent at:
```
~/Library/LaunchAgents/com.lobs.server.plist
```

**Managed by:** `bin/server` script (start/stop/restart/status/logs)

**This is NOT suitable for Linux production deployment** — systemd is the standard.

### Manual Run (bin/run)

Direct uvicorn execution with two modes:
- `./bin/run lan` → Binds 0.0.0.0 (accessible over network)
- `./bin/run local` → Binds 127.0.0.1 (localhost only)

**This works for production** but lacks:
- Automatic restart on failure
- System boot integration
- Resource limits
- Logging integration

---

## References

**Analyzed Files:**
- `README.md` — Project overview
- `QUICKSTART.md` — 5-minute setup guide
- `SETUP.md` — Detailed developer setup
- `ARCHITECTURE.md` — System architecture
- `docs/RUNBOOK.md` — Operational procedures (partial)
- `.env.example` — Configuration template
- `bin/run` — Manual startup script
- `bin/server` — macOS LaunchAgent manager
- `bin/setup-agents` — Agent workspace setup
- `requirements.txt` — Python dependencies
- `app/config.py` — Configuration management
- `app/main.py` — FastAPI application entry

**External References:**
- systemd service documentation: https://www.freedesktop.org/software/systemd/man/systemd.service.html
- Tailscale setup guides: https://tailscale.com/kb/
- FastAPI deployment: https://fastapi.tiangolo.com/deployment/
- Uvicorn deployment: https://www.uvicorn.org/deployment/

---

## Conclusion

lobs-server is **deployment-ready from a code perspective**, but **lacks deployment automation and documentation**. The system is well-designed with clear configuration management and minimal dependencies.

**The path to production is clear:**
1. Document the deployment process (DEPLOYMENT.md)
2. Create systemd service file
3. Automate setup (setup.sh)
4. Test on fresh VM
5. Document operational procedures

**Estimated total effort:** 1-2 days for Writer + Programmer collaboration.

**Blocker:** None — all information needed is available, just needs to be documented and automated.

**Confidence:** High — straightforward deployment scenario with no complex infrastructure requirements.
