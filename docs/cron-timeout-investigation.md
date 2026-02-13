# OpenClaw Cron RPC Timeout Investigation

**Date:** 2026-02-13  
**Investigator:** Programmer Agent  
**Status:** ✅ Root cause identified  

---

## Problem Statement

Cron tool consistently times out during morning brief execution, even after `SIGUSR1` restart. User reports RPC timeout errors.

## Investigation Process

1. **Checked OpenClaw status:**
   - Gateway: healthy, reachable (12ms)
   - Sessions: 4 active
   - Cron scheduler: enabled
   - No obvious service-level issues

2. **Examined logs:**
   ```bash
   grep -i "embedded run timeout\|cron" /tmp/openclaw/openclaw-2026-02-13.log
   ```

3. **Found root cause:**
   ```json
   {
     "subsystem": "agent/embedded",
     "message": "embedded run timeout: runId=71de8060-3785-4708-9fad-1367a410e39a sessionId=544c6529-2933-4705-aabb-5b551ab8ed53 timeoutMs=600000"
   }
   ```

---

## Root Cause

**Not an RPC issue** — the agent job exceeds OpenClaw's embedded run timeout limit.

### Details

- **Timeout limit:** 600,000ms (10 minutes)
- **Cron job type:** `agentTurn` (isolated session)
- **Failure mode:** Job processing exceeds time limit → timeout → appears as "RPC timeout" to caller

### Why It Times Out

Morning brief cron job:
1. Processes inbox items sequentially
2. Each item can spawn LLM calls, file operations, API requests
3. Large inbox → cumulative runtime > 10 minutes

---

## Why SIGUSR1 Doesn't Fix It

`SIGUSR1` triggers:
- Config reload
- Process refresh

**But does NOT:**
- Change timeout limits (hardcoded in code)
- Speed up job execution
- Batch/optimize inbox processing

---

## Solutions

### Option A: Quick Fix (OpenClaw Configuration)

**Change required in OpenClaw codebase:**

```typescript
// Current (hardcoded):
const DEFAULT_TIMEOUT = 600000; // 10 minutes

// Proposed:
const DEFAULT_TIMEOUT = config.cron?.agentTurnTimeoutMs ?? 1800000; // 30 min default
```

**Pros:**
- Minimal change
- Preserves current architecture

**Cons:**
- Doesn't scale (larger inbox → still hits limit)
- Wastes resources on long-running agent sessions

---

### Option B: Orchestrator-Layer Scheduling (Recommended)

**Move cron logic to lobs-orchestrator:**

```
┌─────────────────┐
│ lobs-orchestrator │
│   - Poll inbox   │
│   - Batch work   │
│   - Call API     │
└─────────────────┘
         ↓
┌─────────────────┐
│  lobs-server    │
│  /api/inbox     │
│  /api/tasks     │
└─────────────────┘
```

**Benefits:**
1. **No timeout limits** — orchestrator controls own runtime
2. **Smarter batching** — process high-priority first, defer low-priority
3. **Better retry logic** — failed items don't block entire batch
4. **Resource efficiency** — no long-running OpenClaw agent sessions

**Implementation:**
- Remove OpenClaw cron jobs for morning brief
- Add orchestrator scheduled task (runs every N hours)
- Orchestrator queries `GET /api/inbox?unprocessed=true`
- Processes items via `POST /api/tasks` (or direct actions)
- Tracks state in lobs-server DB

---

### Option C: Batch Processing Pattern

**Keep cron in OpenClaw, but chunk work:**

```typescript
// Cron job payload:
{
  "kind": "agentTurn",
  "message": "Process next 10 inbox items",
  "maxItems": 10
}
```

- Multiple cron jobs (each <10 min)
- Items tagged with `last_processed_at`
- Resume on next run

**Pros:**
- Works within current OpenClaw limits
- Incremental progress

**Cons:**
- Complex state tracking
- Multiple cron jobs = harder to monitor

---

## Recommendation

**Short-term:** Request OpenClaw timeout increase (Option A)  
**Long-term:** Migrate to orchestrator-layer scheduling (Option B)

**Rationale:**
- User already plans to "eventually want orchestrator to handle this"
- Orchestrator layer provides better control, scalability, observability
- Avoids OpenClaw agent session overhead for batch work

---

## Next Steps

1. **Decision:** User chooses Option A, B, or C
2. **If Option A:** File issue with OpenClaw team or patch locally
3. **If Option B:** Create architect task for orchestrator scheduling design
4. **If Option C:** Implement batched cron pattern

---

## Appendix: Related Log Entries

**Timeout event (2026-02-13T21:45:36.447Z):**
```
embedded run timeout: runId=71de8060-3785-4708-9fad-1367a410e39a
```

**Status check shows no RPC issues:**
- Gateway reachable: 12ms
- Discord: OK
- Sessions: 4 active

**Cron scheduler:** Running normally (heartbeat config shows enabled)
