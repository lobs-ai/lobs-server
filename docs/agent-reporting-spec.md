# Agent Task Reporting Specification

**Version:** 1.0  
**Date:** 2026-02-23  
**Status:** Draft

---

## Overview

This document defines how AI agents communicate completed work to users in the Lobs system. It specifies the report structure, delivery channels, and communication protocols to ensure users understand what agents did, why, and what happens next.

**Goal:** Enable trust and feedback loops by making agent work transparent, comprehensible, and actionable.

---

## 1. Task Completion Report Schema

### 1.1 Core Report Structure

Every completed task generates a **Task Completion Report** with the following fields:

```typescript
interface TaskCompletionReport {
  // Identity
  task_id: string
  title: string
  project_id: string
  agent_type: string  // "programmer", "researcher", "writer", "coordinator"
  
  // Outcome
  status: "completed" | "failed" | "blocked"
  success: boolean
  completed_at: string  // ISO 8601 timestamp
  duration_minutes: number
  
  // Work Summary
  summary: string  // 1-3 sentence human-readable summary
  actions_taken: Action[]  // Detailed list of actions
  decisions_made: Decision[]  // Key decisions with rationale
  artifacts_created: Artifact[]  // Files, docs, code changes
  
  // Resources Used
  model: string  // e.g., "anthropic/claude-sonnet-4-5"
  token_usage: {
    input_tokens: number
    output_tokens: number
    total_cost_usd: number
  }
  
  // Context & Next Steps
  next_steps: string[]  // Suggested follow-up actions
  blockers: string[]  // Current blockers, if any
  questions_for_human: string[]  // Clarifications needed
  
  // Metadata
  escalation_tier: number  // 0=none, 1-4=escalation level
  retry_count: number
  related_tasks: string[]  // IDs of dependent tasks
  github_compare_url?: string  // For code changes
}

interface Action {
  type: "file_created" | "file_modified" | "command_run" | "api_call" | "research" | "decision"
  description: string
  timestamp: string
  details?: any  // Type-specific details
}

interface Decision {
  what: string  // What was decided
  why: string  // Rationale
  alternatives_considered?: string[]
}

interface Artifact {
  type: "code" | "documentation" | "research_report" | "data" | "config"
  path: string
  description: string
  size_bytes?: number
  lines_changed?: number  // For code
}
```

### 1.2 Report Levels

Reports have **three detail levels** depending on delivery channel:

1. **Notification (brief)** — 1-2 sentences for real-time alerts
2. **Summary (standard)** — Full report with key fields (default for dashboard)
3. **Detailed (verbose)** — Includes full transcript, all actions, debug info

---

## 2. Reporting Strategy: Real-Time vs. Batch

### 2.1 Real-Time Reporting (Immediate)

**Trigger:** Task status changes to `completed` or `failed`

**Recipients:**
- User who created the task
- Watchers of the parent project
- Any user with `notify_on_completion` flag for the task

**Channels:**
- WebSocket push (live dashboard update)
- In-app notification badge
- Mobile push notification (if configured)

**Content:** Notification-level report (brief summary)

**Example:**
```json
{
  "type": "task_completed",
  "task_id": "task-123",
  "title": "Implement user authentication",
  "agent": "programmer",
  "status": "completed",
  "summary": "Added JWT-based auth with refresh tokens. All tests passing.",
  "timestamp": "2026-02-23T15:30:00Z"
}
```

### 2.2 Batch Reporting (Scheduled)

**Trigger:** Daily digest at 8 AM user local time

**Recipients:** All users with `daily_digest: true` preference

**Channels:**
- Email
- Dashboard "Today's Completed Work" section

**Content:** Summary-level reports for all completed tasks in the last 24 hours, grouped by project

**Example Email Subject:**
```
Lobs Daily Digest: 5 tasks completed, 2 need your review
```

### 2.3 Event-Driven Reporting (On-Demand)

**Trigger:** User requests task details or agent asks for feedback

**Channels:**
- Chat interface (conversational response)
- API endpoint: `GET /api/tasks/{id}/report`

**Content:** Detailed-level report with full context

---

## 3. Delivery Channels

### 3.1 Dashboard (Primary)

**Location:** Task detail view, Activity timeline

**Report Level:** Summary

**Features:**
- Expandable sections for actions, decisions, artifacts
- Click to view files/diffs
- Inline feedback buttons ("👍 Looks good", "❌ Needs changes")
- "Ask follow-up" button (opens chat)

**UI Example:**
```
┌─────────────────────────────────────────────────────┐
│ ✅ Task Completed                                   │
│ Implement user authentication                       │
│ by programmer • 45 minutes ago                      │
├─────────────────────────────────────────────────────┤
│ Summary                                             │
│ Added JWT-based auth with refresh tokens.          │
│ All tests passing.                                  │
│                                                     │
│ 📁 Files Changed (3)                                │
│   ✨ src/auth/jwt.ts                 (+120 lines)  │
│   ✏️  src/middleware/auth.ts         (+45 lines)   │
│   ✅ tests/auth.test.ts              (+89 lines)   │
│                                                     │
│ 🎯 Key Decisions                                    │
│   • Used httpOnly cookies for refresh tokens       │
│   • Access tokens expire in 15 minutes             │
│                                                     │
│ 💡 Next Steps                                       │
│   1. Review token expiry policy                    │
│   2. Test with production OAuth providers          │
│                                                     │
│ [View Diff] [👍 Approve] [💬 Ask Question]         │
└─────────────────────────────────────────────────────┘
```

### 3.2 Chat (Conversational)

**Trigger:** User asks "What did you do?" or "@agent, show me task X"

**Report Level:** Summary, with ability to drill down

**Format:** Natural language with embedded actions

**Example:**
```
User: What did you do on the auth task?

Agent: I completed the user authentication task 45 minutes ago. Here's what I did:

1. Added JWT-based authentication with refresh tokens (src/auth/jwt.ts)
2. Created auth middleware to protect routes (src/middleware/auth.ts)
3. Wrote comprehensive tests - all 12 passing (tests/auth.test.ts)

Key decision: I used httpOnly cookies for refresh tokens to prevent XSS attacks. 
Access tokens expire in 15 minutes, refresh tokens in 7 days.

Next steps for you:
- Review the token expiry policy (is 15 min too short for your users?)
- Test with your OAuth providers (GitHub, Google)

Would you like me to adjust anything?
```

### 3.3 Email (Batch Summary)

**Trigger:** Daily digest

**Report Level:** Brief summary with link to full report

**Format:** HTML email with task cards

**Example:**
```html
<h2>5 Tasks Completed Today</h2>

<div class="task-card">
  <h3>✅ Implement user authentication</h3>
  <p>by programmer • 45 minutes ago</p>
  <p>Added JWT-based auth with refresh tokens. All tests passing.</p>
  <p><strong>Files changed:</strong> 3 files, +254 lines</p>
  <a href="https://lobs.app/tasks/task-123">View Details →</a>
</div>

<div class="task-card">
  <h3>📝 Write API documentation</h3>
  <p>by writer • 2 hours ago</p>
  <p>Documented all 15 endpoints with examples and error codes.</p>
  <a href="https://lobs.app/tasks/task-456">View Details →</a>
</div>

<div class="needs-review">
  <h3>⚠️ 2 Tasks Need Your Review</h3>
  <a href="https://lobs.app/inbox">Go to Inbox →</a>
</div>
```

### 3.4 Mobile Push Notifications (Optional)

**Trigger:** High-priority task completion or blocker

**Report Level:** Notification (1 sentence)

**Example:**
```
Lobs: ✅ programmer completed "Fix critical bug" 
Tap to review changes
```

### 3.5 API (Programmatic)

**Endpoint:** `GET /api/tasks/{task_id}/report`

**Response:** Full report schema (JSON)

**Use Cases:**
- Third-party integrations
- Custom dashboards
- Automated workflows

---

## 4. Sample Reports by Agent Type

### 4.1 Task Execution Agent (Programmer)

**Scenario:** Implement a new feature

```json
{
  "task_id": "task-789",
  "title": "Add dark mode toggle",
  "agent_type": "programmer",
  "status": "completed",
  "success": true,
  "completed_at": "2026-02-23T15:30:00Z",
  "duration_minutes": 32,
  
  "summary": "Implemented dark mode with persistent user preference. All components updated, tests passing.",
  
  "actions_taken": [
    {
      "type": "file_created",
      "description": "Created theme provider component",
      "timestamp": "2026-02-23T15:05:00Z",
      "details": { "path": "src/components/ThemeProvider.tsx" }
    },
    {
      "type": "file_modified",
      "description": "Updated 12 components with theme support",
      "timestamp": "2026-02-23T15:20:00Z",
      "details": { "files": ["Button.tsx", "Header.tsx", "..."] }
    },
    {
      "type": "command_run",
      "description": "Ran test suite - all tests passing",
      "timestamp": "2026-02-23T15:28:00Z",
      "details": { "command": "npm test", "exit_code": 0 }
    }
  ],
  
  "decisions_made": [
    {
      "what": "Store theme preference in localStorage",
      "why": "Persists across sessions without requiring backend changes",
      "alternatives_considered": ["Database storage", "Cookie-based"]
    },
    {
      "what": "Use CSS variables for theme colors",
      "why": "Enables runtime theme switching without CSS recompilation",
      "alternatives_considered": ["SCSS variables", "Tailwind config"]
    }
  ],
  
  "artifacts_created": [
    {
      "type": "code",
      "path": "src/components/ThemeProvider.tsx",
      "description": "Theme context provider with localStorage sync",
      "lines_changed": 87
    },
    {
      "type": "code",
      "path": "src/styles/themes.css",
      "description": "CSS variables for light and dark themes",
      "lines_changed": 156
    }
  ],
  
  "token_usage": {
    "input_tokens": 15420,
    "output_tokens": 8930,
    "total_cost_usd": 0.0847
  },
  
  "next_steps": [
    "Review color contrast ratios for accessibility",
    "Test on mobile devices",
    "Consider adding system preference detection"
  ],
  
  "questions_for_human": [
    "Should dark mode be the default for new users?",
    "Do you want auto-switching based on time of day?"
  ],
  
  "github_compare_url": "https://github.com/user/repo/compare/abc123...def456",
  "model": "anthropic/claude-sonnet-4-5"
}
```

### 4.2 Research Synthesis Agent (Researcher)

**Scenario:** Investigate a technical approach

```json
{
  "task_id": "task-321",
  "title": "Research real-time collaboration solutions",
  "agent_type": "researcher",
  "status": "completed",
  "success": true,
  "completed_at": "2026-02-23T16:45:00Z",
  "duration_minutes": 127,
  
  "summary": "Analyzed 4 real-time collaboration frameworks. Recommend Yjs for our use case based on performance, TypeScript support, and provider ecosystem.",
  
  "actions_taken": [
    {
      "type": "research",
      "description": "Evaluated Yjs, Automerge, ShareDB, and CRDT.js",
      "timestamp": "2026-02-23T15:10:00Z",
      "details": {
        "sources": [
          "https://github.com/yjs/yjs",
          "https://automerge.org/docs/",
          "https://share.github.io/sharedb/"
        ]
      }
    },
    {
      "type": "research",
      "description": "Benchmarked performance with 10k operations",
      "timestamp": "2026-02-23T16:00:00Z",
      "details": { "methodology": "Local benchmark suite, Node.js 20" }
    },
    {
      "type": "file_created",
      "description": "Created comparison matrix and recommendation doc",
      "timestamp": "2026-02-23T16:40:00Z",
      "details": { "path": "docs/research/realtime-collab-evaluation.md" }
    }
  ],
  
  "decisions_made": [
    {
      "what": "Recommend Yjs over alternatives",
      "why": "Best TypeScript support (100% typed), 3x faster than Automerge in benchmarks, rich provider ecosystem (WebRTC, WebSocket, IndexedDB)",
      "alternatives_considered": [
        "Automerge - Strong academic backing but slower",
        "ShareDB - Mature but lacks CRDT guarantees",
        "CRDT.js - Too low-level, would require custom implementation"
      ]
    }
  ],
  
  "artifacts_created": [
    {
      "type": "research_report",
      "path": "docs/research/realtime-collab-evaluation.md",
      "description": "Comprehensive evaluation with benchmarks, pros/cons, implementation plan",
      "size_bytes": 28400
    },
    {
      "type": "data",
      "path": "docs/research/benchmark-results.json",
      "description": "Raw benchmark data (operations/sec, memory usage)",
      "size_bytes": 5200
    }
  ],
  
  "token_usage": {
    "input_tokens": 42300,
    "output_tokens": 18900,
    "total_cost_usd": 0.2145
  },
  
  "next_steps": [
    "Review evaluation report",
    "Approve Yjs selection",
    "Create implementation task for programmer"
  ],
  
  "questions_for_human": [
    "Are you comfortable with the bundle size increase (~70KB gzipped)?",
    "Should we support offline-first mode from day 1?"
  ],
  
  "model": "anthropic/claude-sonnet-4-5"
}
```

### 4.3 Coordination Agent (Project Manager)

**Scenario:** Orchestrate multi-agent workflow

```json
{
  "task_id": "task-555",
  "title": "Ship v2.0 release",
  "agent_type": "coordinator",
  "status": "completed",
  "success": true,
  "completed_at": "2026-02-23T18:00:00Z",
  "duration_minutes": 240,
  
  "summary": "Coordinated v2.0 release across 3 agents. All 8 release tasks completed, changelog published, deployment successful.",
  
  "actions_taken": [
    {
      "type": "decision",
      "description": "Created release plan with 8 tasks",
      "timestamp": "2026-02-23T14:05:00Z",
      "details": {
        "tasks_created": [
          "task-556: Update dependencies",
          "task-557: Run security audit",
          "task-558: Update changelog",
          "task-559: Build release artifacts",
          "task-560: Test staging deployment",
          "task-561: Create GitHub release",
          "task-562: Deploy to production",
          "task-563: Send release announcement"
        ]
      }
    },
    {
      "type": "api_call",
      "description": "Assigned tasks to programmer, writer, specialist",
      "timestamp": "2026-02-23T14:15:00Z",
      "details": { "assignments": 8 }
    },
    {
      "type": "decision",
      "description": "Blocked deployment pending security audit results",
      "timestamp": "2026-02-23T15:30:00Z",
      "details": { "blocker_task": "task-557" }
    },
    {
      "type": "decision",
      "description": "Approved deployment after audit passed",
      "timestamp": "2026-02-23T16:45:00Z"
    }
  ],
  
  "decisions_made": [
    {
      "what": "Deploy during business hours (not off-hours)",
      "why": "Team available for monitoring. Low-risk release with rollback plan.",
      "alternatives_considered": ["Weekend deployment", "Gradual rollout"]
    },
    {
      "what": "Skip performance regression tests",
      "why": "No performance-critical changes in this release. Would delay by 2 hours.",
      "alternatives_considered": ["Run full test suite"]
    }
  ],
  
  "artifacts_created": [
    {
      "type": "documentation",
      "path": "CHANGELOG.md",
      "description": "v2.0 changelog with 23 changes documented",
      "lines_changed": 67
    },
    {
      "type": "data",
      "path": "releases/v2.0.0/",
      "description": "Release artifacts (3 platforms)",
      "size_bytes": 45000000
    }
  ],
  
  "token_usage": {
    "input_tokens": 8420,
    "output_tokens": 3200,
    "total_cost_usd": 0.0398
  },
  
  "next_steps": [
    "Monitor error rates for 24 hours",
    "Collect user feedback on new features",
    "Plan v2.1 based on feedback"
  ],
  
  "related_tasks": [
    "task-556", "task-557", "task-558", "task-559",
    "task-560", "task-561", "task-562", "task-563"
  ],
  
  "model": "anthropic/claude-sonnet-3-5"
}
```

---

## 5. User Feedback Protocol

### 5.1 Feedback Types

Users can provide feedback on completed tasks through:

1. **Binary approval** — "👍 Looks good" or "❌ Needs changes"
2. **Rating** — 1-5 stars for quality
3. **Text feedback** — Specific comments or corrections
4. **Actions** — "Merge PR", "Request changes", "Archive"

### 5.2 Feedback Capture

**Channels:**
- Dashboard inline buttons
- Chat responses (natural language)
- Email reply-to for digest emails
- API: `POST /api/tasks/{id}/feedback`

**Schema:**
```typescript
interface TaskFeedback {
  task_id: string
  user_id: string
  feedback_type: "approval" | "rejection" | "comment" | "rating"
  rating?: number  // 1-5
  comment?: string
  action_taken?: "merged" | "requested_changes" | "archived"
  timestamp: string
}
```

### 5.3 Feedback Loop

**Agent learning system integration:**
- Approvals → Positive reinforcement (increase pattern weight)
- Rejections → Negative signal (decrease pattern weight)
- Comments → Extract patterns for prompt enhancement
- Ratings → Aggregate score for agent performance tracking

See [agent-learning-READY.md](./agent-learning-READY.md) for detailed learning system specification.

---

## 6. Notification Preferences

Users control reporting via preferences:

```typescript
interface NotificationPreferences {
  // Real-time
  websocket_enabled: boolean
  mobile_push_enabled: boolean
  
  // Batch
  daily_digest_enabled: boolean
  daily_digest_time: string  // "08:00" in user's timezone
  
  // Filters
  notify_only_for_my_tasks: boolean
  notify_on_failures: boolean
  notify_on_blockers: boolean
  
  // Channels
  email_enabled: boolean
  email_address: string
}
```

**API Endpoint:** `PUT /api/users/me/preferences`

---

## 7. Privacy & Security

### 7.1 Report Access Control

- **Task creator** — Full access to all report details
- **Project members** — Access to summary-level reports
- **Organization admins** — Access to all reports in organization
- **Public** — No access (all reports are private by default)

### 7.2 Sensitive Data Handling

**Redaction rules:**
- API keys, tokens, passwords → `[REDACTED]`
- PII in transcripts → Masked before storage
- Internal tool outputs → Sanitized for user reports

**Audit trail:**
- All report access logged
- Feedback events tracked
- Report modifications recorded

---

## 8. Implementation Notes

### 8.1 Database Changes

**New table:** `task_reports`
```sql
CREATE TABLE task_reports (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  report_json TEXT NOT NULL,  -- Full report as JSON
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  report_version INTEGER DEFAULT 1
);

CREATE INDEX idx_task_reports_task_id ON task_reports(task_id);
```

**New table:** `task_feedback`
```sql
CREATE TABLE task_feedback (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  feedback_type TEXT NOT NULL,
  rating INTEGER,
  comment TEXT,
  action_taken TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_task_feedback_task_id ON task_feedback(task_id);
CREATE INDEX idx_task_feedback_user_id ON task_feedback(user_id);
```

### 8.2 API Endpoints

**New endpoints:**
```
GET  /api/tasks/{id}/report          # Get task completion report
POST /api/tasks/{id}/feedback        # Submit feedback
GET  /api/tasks/{id}/feedback        # Get all feedback for task
GET  /api/digest/daily               # Get daily digest data
PUT  /api/users/me/preferences       # Update notification preferences
```

### 8.3 WebSocket Events

**New event types:**
```typescript
// Sent when task completes
{
  "type": "task_completed",
  "payload": {
    "task_id": "task-123",
    "report": { /* Brief report */ }
  }
}

// Sent when feedback is received
{
  "type": "task_feedback_received",
  "payload": {
    "task_id": "task-123",
    "feedback_type": "approval"
  }
}
```

### 8.4 Email Templates

**Required templates:**
- `daily-digest.html` — Daily summary email
- `task-completed.html` — Individual task completion (if enabled)
- `task-needs-review.html` — Agent requests feedback

**Template variables:**
```
{user_name}, {tasks_completed_count}, {tasks_needing_review_count},
{task_cards_html}, {dashboard_url}
```

---

## 9. Success Metrics

### 9.1 Adoption Metrics

- **Report view rate** — % of completed tasks where user views the full report
- **Feedback rate** — % of completed tasks that receive user feedback
- **Daily digest open rate** — % of digest emails opened
- **WebSocket connection rate** — % of active sessions with live updates

**Targets (first 4 weeks):**
- Report view rate > 60%
- Feedback rate > 30%
- Daily digest open rate > 40%

### 9.2 Quality Metrics

- **Time to feedback** — Average time between task completion and user feedback
- **Approval rate** — % of tasks approved on first attempt
- **Re-work rate** — % of tasks requiring changes after completion

**Targets:**
- Time to feedback < 2 hours (for real-time) or < 24 hours (for digest)
- Approval rate > 70%

### 9.3 System Health Metrics

- **Report generation time** — Time to build report after task completion
- **Notification delivery latency** — Time from completion to user notification
- **API response time** — `/api/tasks/{id}/report` endpoint performance

**Targets:**
- Report generation < 5 seconds
- Notification delivery < 10 seconds
- API response time < 500ms (p95)

---

## 10. Rollout Plan

### Phase 1: Foundation (Week 1)
- Implement report schema and database tables
- Build report generation in `worker.py` completion handler
- Create API endpoints for report retrieval
- Add basic dashboard UI for viewing reports

### Phase 2: Real-Time Notifications (Week 2)
- Implement WebSocket event broadcasting
- Add dashboard notification badges
- Create in-app notification center
- Test with 10% of users (feature flag)

### Phase 3: Batch Reporting (Week 3)
- Build daily digest aggregation
- Create email templates
- Implement email delivery
- Add user preference controls

### Phase 4: Feedback Loop (Week 4)
- Add feedback capture UI
- Implement feedback API endpoints
- Integrate with agent learning system
- Full rollout to all users

### Phase 5: Optimization (Week 5+)
- Mobile push notifications (optional)
- Report quality improvements based on feedback
- Performance optimization
- Advanced filtering and search

---

## 11. Open Questions

1. **Report retention:** How long do we keep historical reports? (Proposal: 90 days for full reports, indefinite for summaries)
2. **Notification fatigue:** Should we batch rapid task completions into a single notification? (Proposal: Yes, 5-minute window)
3. **Multi-language support:** Should reports be localized? (Proposal: Phase 2 feature)
4. **Export functionality:** Should users be able to export reports? (Proposal: Yes, CSV/JSON export)
5. **Report versioning:** If we update the schema, how do we handle old reports? (Proposal: Version field + schema migration)

---

## 12. References

- [Agent Learning System](./agent-learning-READY.md) — Feedback loop integration
- [Orchestrator Flow](./orchestrator-flow.md) — Task execution lifecycle
- [Database Migrations](./database-migrations.md) — Schema change process
- [ARCHITECTURE.md](../ARCHITECTURE.md) — System architecture overview

---

**Document Status:** Ready for review and approval  
**Next Step:** Review with product team, then create implementation tasks
