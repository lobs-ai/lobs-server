# Research Findings: Analytics Pipeline & iOS Architecture Best Practices

**Date:** 2026-02-13  
**Task ID:** b57ff04f-5484-4f00-beb4-a2bb3f5b1b55  
**Context:** Architecture improvements for analytics and iOS codebase organization

---

## Executive Summary

This research analyzes two critical architectural improvements:
1. **Analytics as a structured pipeline** (not ad-hoc logging)
2. **iOS codebase modularity** (addressing merge conflicts and maintainability)

**Key Recommendations:**
- ✅ Implement event-driven analytics with versioned schema
- ✅ Use batch queuing with local persistence for reliability
- ✅ Break large files into feature modules (<300 lines per file)
- ✅ Centralize feature flags for testability and rollout control

---

## Part A: Analytics Pipeline Architecture

### Current Problem

**Symptom:** Analytics becomes "unstructured logging"
- Random log lines scattered across codebase
- No consistent format or versioning
- Hard to query, aggregate, or evolve
- Privacy concerns not systematically addressed

**Impact:**
- Can't answer business questions ("how many users RSVP'd?")
- Breaking changes when adding fields
- GDPR/privacy violations if not careful

---

### Solution: Event-Driven Analytics Pipeline

```
┌──────────────────────────────────────────────────────┐
│ iOS App                                              │
│                                                      │
│  User Action → AnalyticsEvent                       │
│      ↓                                               │
│  EventQueue (local SQLite/disk)                     │
│      ↓ (batch flush every 20 events or 30s)         │
│  POST /v1/analytics/events (batch)                  │
└──────────────┬───────────────────────────────────────┘
               │
               ↓
┌──────────────────────────────────────────────────────┐
│ Backend Server                                       │
│                                                      │
│  Validate → Filter (privacy) → Store → Aggregate    │
│      ↓                                               │
│  Analytics Database (events table)                  │
│      ↓                                               │
│  Daily aggregation → Dashboard metrics              │
└──────────────────────────────────────────────────────┘
```

---

### 1. Event Schema Design

#### Core Schema

```swift
struct AnalyticsEvent: Codable {
    // Identity
    let eventName: String           // e.g., "event_rsvp"
    let eventVersion: Int           // Schema version (start at 1)
    let eventId: UUID               // Unique event ID (deduplication)
    
    // Context
    let timestamp: Date             // Client timestamp (ISO 8601)
    let userId: String?             // User ID (nil if logged out)
    let anonymousId: String         // Stable device ID (UUID)
    let sessionId: String           // Current session ID
    
    // Device context
    let deviceId: String            // Device identifier
    let appVersion: String          // e.g., "2.1.0"
    let buildCommit: String?        // Git SHA (for debugging)
    let osVersion: String           // e.g., "iOS 17.2"
    let deviceModel: String         // e.g., "iPhone 14 Pro"
    
    // Event-specific payload
    let properties: [String: AnyCodable]  // Flexible payload
    
    // Privacy
    let allowsAnalytics: Bool       // User consent flag
}
```

**Key design choices:**

✅ **Versioning at event level** (`eventVersion`)
- Allows schema evolution without breaking changes
- Backend can handle multiple versions of same event

✅ **Anonymous ID separate from User ID**
- Tracks users before/after login
- Enables cross-device analysis

✅ **Flexible properties dict**
- Event-specific data without rigid schema
- Use `AnyCodable` wrapper for type safety

✅ **Privacy flag embedded**
- Client-side signal for server filtering
- Can't be bypassed by malicious requests (server validates)

---

#### Event Catalog

**Create:** `Docs/AnalyticsEventCatalog.md`

```markdown
# Analytics Event Catalog

## Authentication Events

### `sign_up`
**Version:** 1  
**Fired when:** User completes registration  
**Properties:**
- `method`: "email" | "google" | "apple"
- `referral_code`: String? (if invited)

### `sign_in`
**Version:** 1  
**Fired when:** User logs in  
**Properties:**
- `method`: "email" | "google" | "apple"
- `remember_me`: Bool

## Event Events

### `event_create`
**Version:** 2 (added `visibility` in v2)  
**Fired when:** User creates an event  
**Properties:**
- `event_type`: "public" | "private" | "community"
- `has_location`: Bool
- `has_image`: Bool
- `visibility`: "public" | "friends" | "invite_only" (v2+)

### `event_view`
**Version:** 1  
**Fired when:** User views event details  
**Properties:**
- `event_id`: String
- `source`: "feed" | "calendar" | "search" | "direct_link"

### `event_rsvp`
**Version:** 1  
**Fired when:** User RSVPs to event  
**Properties:**
- `event_id`: String
- `response`: "yes" | "maybe" | "no"
- `changed_from`: "yes" | "maybe" | "no" | null

## Messaging Events

### `dm_send`
**Version:** 1  
**Fired when:** User sends DM  
**Properties:**
- `has_image`: Bool
- `has_location`: Bool
- `message_length_bucket`: "short" | "medium" | "long"

### `community_join`
**Version:** 1  
**Fired when:** User joins community  
**Properties:**
- `community_id`: String
- `join_method`: "invite" | "search" | "suggestion"

## App Lifecycle Events

### `app_open`
**Version:** 1  
**Fired when:** App becomes active  
**Properties:**
- `is_cold_start`: Bool
- `previous_version`: String? (if upgraded)

### `push_notification_open`
**Version:** 1  
**Fired when:** User taps push notification  
**Properties:**
- `notification_type`: "event_invite" | "dm" | "event_reminder" | etc.
- `was_backgrounded`: Bool
```

**Rationale:**
- Single source of truth for all events
- Makes schema changes explicit and reviewable
- Helps with dashboard design (know what fields exist)

---

### 2. Transport & Reliability

#### Client-Side Queue (iOS)

```swift
// File: Analytics/AnalyticsQueue.swift

final class AnalyticsQueue {
    private let db: SQLiteDatabase
    private let api: AnalyticsAPI
    private let batchSize = 20
    private let flushInterval: TimeInterval = 30
    
    private var flushTimer: Timer?
    
    init() {
        self.db = SQLiteDatabase(path: "analytics_queue.db")
        self.api = AnalyticsAPI()
        setupQueue()
        startFlushTimer()
    }
    
    // MARK: - Public API
    
    func track(event: AnalyticsEvent) {
        // Non-blocking: queue for later flush
        DispatchQueue.global(qos: .utility).async {
            do {
                try self.db.insert(event)
                
                // Flush if batch size reached
                if try self.db.count() >= self.batchSize {
                    self.flush()
                }
            } catch {
                // Fail silently - analytics shouldn't crash the app
                print("Analytics queue error: \(error)")
            }
        }
    }
    
    // MARK: - Flush Logic
    
    private func flush() {
        DispatchQueue.global(qos: .utility).async {
            do {
                let events = try self.db.fetchBatch(limit: self.batchSize)
                guard !events.isEmpty else { return }
                
                // Send to server
                try await self.api.sendBatch(events)
                
                // Delete from local queue on success
                try self.db.delete(eventIds: events.map { $0.eventId })
                
            } catch {
                // Keep events in queue for retry
                print("Analytics flush failed: \(error)")
                
                // Exponential backoff
                self.scheduleRetry()
            }
        }
    }
    
    private func startFlushTimer() {
        flushTimer = Timer.scheduledTimer(
            withTimeInterval: flushInterval,
            repeats: true
        ) { [weak self] _ in
            self?.flush()
        }
    }
    
    private func scheduleRetry() {
        // Simple exponential backoff
        DispatchQueue.global(qos: .utility).asyncAfter(
            deadline: .now() + 60  // Retry after 1 minute
        ) {
            self.flush()
        }
    }
}
```

**Key features:**
- ✅ **Local persistence** (SQLite) - events not lost if app crashes
- ✅ **Batch upload** - reduces network overhead
- ✅ **Non-blocking** - doesn't slow down UI
- ✅ **Automatic retry** - resilient to network failures
- ✅ **Background queue** - uses utility QoS (low priority)

---

#### Backend Endpoint

```python
# Backend: app/routers/analytics.py

from fastapi import APIRouter, Depends, HTTPException
from typing import List
from app.auth import require_auth
from app.models import AnalyticsEvent, User
from app.database import get_db

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])

@router.post("/events")
async def ingest_events(
    events: List[AnalyticsEventPayload],
    user: User = Depends(require_auth),
    db = Depends(get_db)
):
    """
    Batch ingest analytics events.
    
    - Validates event schema
    - Filters based on user privacy settings
    - Stores in events table
    - Returns 202 Accepted (async processing)
    """
    
    # Check user's analytics consent
    if not user.allows_analytics:
        # Drop all events if user opted out
        return {"received": 0, "dropped": len(events), "reason": "user_opt_out"}
    
    processed = []
    dropped = 0
    
    for event_data in events:
        try:
            # Validate event
            event = AnalyticsEvent(
                event_name=event_data.eventName,
                event_version=event_data.eventVersion,
                event_id=event_data.eventId,
                user_id=user.id,
                timestamp=event_data.timestamp,
                properties=event_data.properties,
                device_context={
                    "app_version": event_data.appVersion,
                    "os_version": event_data.osVersion,
                    "device_model": event_data.deviceModel,
                }
            )
            
            # Anonymize if needed (based on event type or user settings)
            if should_anonymize(event_data.eventName):
                event.user_id = None  # Keep only anonymous_id
            
            processed.append(event)
            
        except Exception as e:
            dropped += 1
            print(f"Invalid event: {e}")
    
    # Bulk insert
    if processed:
        db.bulk_insert(processed)
    
    return {
        "received": len(processed),
        "dropped": dropped,
        "status": "accepted"
    }
```

**Key features:**
- ✅ **Batch endpoint** - accepts array of events
- ✅ **Privacy-aware** - respects user consent
- ✅ **Anonymization** - removes PII when appropriate
- ✅ **Async processing** - returns 202 immediately, processes later
- ✅ **Validation** - rejects malformed events gracefully

---

### 3. Privacy & User Controls

#### User Settings (iOS)

```swift
// File: Views/Settings/PrivacySettingsView.swift

struct PrivacySettingsView: View {
    @AppStorage("allows_analytics") private var allowsAnalytics = true
    @AppStorage("show_activity_status") private var showActivity = true
    
    var body: some View {
        Form {
            Section {
                Toggle("Share Activity Status", isOn: $showActivity)
                    .onChange(of: showActivity) { newValue in
                        // Sync to server
                        Task {
                            try await updatePrivacySettings(
                                showActivity: newValue
                            )
                        }
                    }
                
                Text("Let friends see when you're active")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            
            Section {
                Toggle("Share Usage Data", isOn: $allowsAnalytics)
                    .onChange(of: allowsAnalytics) { newValue in
                        // Sync to server
                        Task {
                            try await updatePrivacySettings(
                                allowsAnalytics: newValue
                            )
                        }
                        
                        // Clear local queue if opted out
                        if !newValue {
                            AnalyticsQueue.shared.clearQueue()
                        }
                    }
                
                Text("Help improve the app by sharing anonymized usage data")
                    .font(.caption)
                    .foregroundColor(.secondary)
            } header: {
                Text("Analytics")
            }
        }
        .navigationTitle("Privacy")
    }
}
```

**Key features:**
- ✅ **Explicit consent** - user controls analytics
- ✅ **Clear language** - explains what data is collected
- ✅ **Immediate effect** - clears queue if opted out
- ✅ **Persisted** - synced to server and stored locally

---

### 4. Minimal Useful Dashboard

#### High-Signal Events (Start Small)

**Core Metrics (6-10 events):**

1. **Acquisition:**
   - `app_open` (daily active users)
   - `sign_up` (new user registrations)
   - `sign_in` (returning users)

2. **Engagement:**
   - `event_create` (content creation)
   - `event_view` (content consumption)
   - `event_rsvp` (commitment)

3. **Social:**
   - `dm_send` (communication)
   - `community_join` (network growth)

4. **Retention:**
   - `push_notification_open` (re-engagement)
   - `app_open` (daily actives)

**Rationale:** These 9 events tell the core story of app health.

---

#### Simple Aggregation Queries

```sql
-- Daily Active Users
SELECT 
    DATE(timestamp) as date,
    COUNT(DISTINCT user_id) as dau,
    COUNT(DISTINCT anonymous_id) as total_sessions
FROM analytics_events
WHERE event_name = 'app_open'
  AND timestamp >= DATE('now', '-30 days')
GROUP BY DATE(timestamp)
ORDER BY date DESC;

-- Sign-up Funnel
SELECT 
    COUNT(*) FILTER (WHERE event_name = 'app_open') as opens,
    COUNT(*) FILTER (WHERE event_name = 'sign_up') as signups,
    ROUND(100.0 * COUNT(*) FILTER (WHERE event_name = 'sign_up') / 
          COUNT(*) FILTER (WHERE event_name = 'app_open'), 2) as conversion_rate
FROM analytics_events
WHERE timestamp >= DATE('now', '-7 days');

-- Event Engagement
SELECT 
    DATE(timestamp) as date,
    COUNT(*) FILTER (WHERE event_name = 'event_create') as created,
    COUNT(*) FILTER (WHERE event_name = 'event_view') as views,
    COUNT(*) FILTER (WHERE event_name = 'event_rsvp') as rsvps,
    ROUND(100.0 * COUNT(*) FILTER (WHERE event_name = 'event_rsvp') / 
          COUNT(*) FILTER (WHERE event_name = 'event_view'), 2) as rsvp_rate
FROM analytics_events
WHERE timestamp >= DATE('now', '-30 days')
GROUP BY DATE(timestamp)
ORDER BY date DESC;

-- Top Communities by Activity
SELECT 
    properties->>'community_id' as community_id,
    COUNT(*) as joins,
    COUNT(DISTINCT user_id) as unique_users
FROM analytics_events
WHERE event_name = 'community_join'
  AND timestamp >= DATE('now', '-30 days')
GROUP BY community_id
ORDER BY joins DESC
LIMIT 10;
```

---

#### Dashboard UI (Backend)

```python
# Backend: app/routers/dashboard.py

@router.get("/analytics/overview")
async def analytics_overview(
    days: int = 30,
    user: User = Depends(require_admin),  # Admin-only
    db = Depends(get_db)
):
    """
    High-level analytics overview.
    """
    
    since = datetime.now() - timedelta(days=days)
    
    # DAU
    dau_query = """
        SELECT DATE(timestamp) as date, COUNT(DISTINCT user_id) as count
        FROM analytics_events
        WHERE event_name = 'app_open' AND timestamp >= ?
        GROUP BY DATE(timestamp)
        ORDER BY date
    """
    dau = db.execute(dau_query, (since,)).fetchall()
    
    # Engagement funnel
    funnel_query = """
        SELECT 
            event_name,
            COUNT(*) as count,
            COUNT(DISTINCT user_id) as unique_users
        FROM analytics_events
        WHERE event_name IN ('event_view', 'event_rsvp', 'dm_send')
          AND timestamp >= ?
        GROUP BY event_name
    """
    funnel = db.execute(funnel_query, (since,)).fetchall()
    
    return {
        "dau_trend": dau,
        "engagement_funnel": funnel,
        "period_days": days
    }
```

**Start simple, iterate based on questions.**

---

## Part B: iOS Architecture Improvements

### Current Problem

**Large files create bottlenecks:**
- `SettingsTabView.swift` (992 lines)
- `ConnectionsAPI.swift` (636 lines)
- `EventDetailsView.swift` (474 lines)

**Impact:**
- 🔴 **Merge conflicts** - multiple devs editing same file
- 🔴 **Slow compilation** - SwiftUI recompiles entire file on change
- 🔴 **Hard to navigate** - scrolling 500+ lines to find one function
- 🔴 **Hard to test** - can't test subcomponents in isolation

---

### Solution 1: Modular File Organization

#### Target: <300 lines per file

**Principle:** Extract cohesive subcomponents.

---

#### Example: Breaking Down SettingsTabView

**Before (992 lines):**
```
SettingsTabView.swift
├── Profile section
├── Privacy toggles
├── Notification preferences
├── Account management
├── Support/about
└── 20+ @State variables
```

**After (modular):**
```
Settings/
├── SettingsTabView.swift (120 lines) - Navigation shell
├── Profile/
│   ├── ProfileSettingsView.swift (180 lines)
│   └── ProfileEditSheet.swift (150 lines)
├── Privacy/
│   ├── PrivacySettingsView.swift (200 lines)
│   └── PrivacyExplanationView.swift (80 lines)
├── Notifications/
│   ├── NotificationSettingsView.swift (220 lines)
│   └── NotificationChannelRow.swift (60 lines)
├── Account/
│   ├── AccountSettingsView.swift (150 lines)
│   ├── DeleteAccountSheet.swift (120 lines)
│   └── LogoutButton.swift (40 lines)
└── Support/
    ├── SupportView.swift (100 lines)
    └── AboutView.swift (80 lines)
```

**Key changes:**
- ✅ Main file is now navigation shell (120 lines)
- ✅ Each section is standalone view
- ✅ Shared components extracted (`NotificationChannelRow`)
- ✅ Complex flows get dedicated sheets (`DeleteAccountSheet`)

---

#### Example: Breaking Down ConnectionsAPI

**Before (636 lines):**
```swift
class ConnectionsAPI {
    func fetchFriends() { ... }
    func sendFriendRequest() { ... }
    func acceptRequest() { ... }
    
    func fetchCommunities() { ... }
    func createCommunity() { ... }
    func joinCommunity() { ... }
    
    func sendMessage() { ... }
    func fetchMessages() { ... }
    func markRead() { ... }
    
    // 30+ methods...
}
```

**After (domain-separated):**
```
API/
├── NetworkClient.swift (80 lines) - Base HTTP client
├── FriendsAPI.swift (180 lines)
│   ├── fetchFriends()
│   ├── sendRequest()
│   ├── acceptRequest()
│   └── removeFriend()
├── CommunitiesAPI.swift (200 lines)
│   ├── fetchCommunities()
│   ├── createCommunity()
│   ├── joinCommunity()
│   └── leaveCommunity()
├── MessagingAPI.swift (220 lines)
│   ├── sendMessage()
│   ├── fetchConversations()
│   ├── fetchMessages()
│   └── markRead()
└── EventsAPI.swift (250 lines)
    ├── fetchEvents()
    ├── createEvent()
    ├── updateEvent()
    └── rsvpToEvent()
```

**Benefits:**
- ✅ Each API file has single responsibility
- ✅ Easier to find methods (sorted by domain)
- ✅ Parallel development (devs work on different APIs)
- ✅ Easier to mock for testing

---

### Solution 2: Centralized Feature Flags

#### Current Problem

**Feature toggles scattered:**
```swift
// In SettingsView
@AppStorage("show_activity_status") var showActivity = true

// In NotificationsView
@AppStorage("allow_push_notifications") var allowPush = true

// In EventsView
@AppStorage("enable_quick_rsvp") var enableQuickRsvp = false

// Hard to know what flags exist
// Hard to test different combinations
// Hard to do gradual rollouts
```

---

#### Solution: FeatureFlags Struct

```swift
// File: Config/FeatureFlags.swift

struct FeatureFlags {
    // MARK: - Privacy
    @AppStorage("show_activity_status") var showActivityStatus = true
    @AppStorage("allow_analytics") var allowAnalytics = true
    
    // MARK: - Notifications
    @AppStorage("allow_push_notifications") var allowPushNotifications = true
    @AppStorage("notification_sound_enabled") var notificationSound = true
    
    // MARK: - Features
    @AppStorage("enable_quick_rsvp") var enableQuickRsvp = false
    @AppStorage("enable_location_sharing") var enableLocationSharing = true
    @AppStorage("enable_dark_mode") var enableDarkMode = false
    
    // MARK: - Experiments (A/B tests)
    @AppStorage("experiment_new_feed_algorithm") var newFeedAlgorithm = false
    @AppStorage("experiment_inline_rsvp") var inlineRsvp = false
    
    // MARK: - Developer
    #if DEBUG
    @AppStorage("dev_show_debug_menu") var showDebugMenu = true
    @AppStorage("dev_mock_api_responses") var mockAPIResponses = false
    #endif
    
    // MARK: - Singleton
    static let shared = FeatureFlags()
    private init() {}
    
    // MARK: - Reset
    func resetToDefaults() {
        showActivityStatus = true
        allowAnalytics = true
        allowPushNotifications = true
        enableQuickRsvp = false
        // ...
    }
}
```

**Usage:**

```swift
// In any view
if FeatureFlags.shared.enableQuickRsvp {
    QuickRsvpButton(event: event)
} else {
    StandardRsvpButton(event: event)
}

// In tests
func testQuickRsvpFlow() {
    FeatureFlags.shared.enableQuickRsvp = true
    // ... test code
}

// In settings
Toggle("Quick RSVP", isOn: FeatureFlags.shared.$enableQuickRsvp)
```

**Benefits:**
- ✅ **Single source of truth** - all flags in one place
- ✅ **Discoverable** - easy to see what flags exist
- ✅ **Testable** - can set flags in tests
- ✅ **Documented** - grouped by category with comments
- ✅ **Type-safe** - compiler catches typos

---

#### Server-Side Feature Flag Override

**For gradual rollouts:**

```swift
// Remote config
struct RemoteFeatureFlags: Codable {
    let enableQuickRsvp: Bool?
    let newFeedAlgorithm: Bool?
    // nil = use local default
}

extension FeatureFlags {
    func syncWithServer() async throws {
        let remote = try await api.fetchFeatureFlags()
        
        // Override local flags with server values
        if let quickRsvp = remote.enableQuickRsvp {
            self.enableQuickRsvp = quickRsvp
        }
        if let feedAlgo = remote.newFeedAlgorithm {
            self.newFeedAlgorithm = feedAlgo
        }
    }
}

// Call on app launch
await FeatureFlags.shared.syncWithServer()
```

**Enables:**
- Gradual rollout (enable for 10% of users, then 50%, then 100%)
- A/B testing (random assignment server-side)
- Kill switch (disable broken feature remotely)

---

### Solution 3: View Component Library

**Extract reusable components:**

```
Views/Components/
├── Buttons/
│   ├── PrimaryButton.swift
│   ├── SecondaryButton.swift
│   └── IconButton.swift
├── Cards/
│   ├── EventCard.swift
│   ├── UserCard.swift
│   └── CommunityCard.swift
├── Input/
│   ├── SearchBar.swift
│   ├── TagInput.swift
│   └── DateTimePicker.swift
├── Lists/
│   ├── EmptyStateView.swift
│   ├── LoadingStateView.swift
│   └── ErrorStateView.swift
└── Modals/
    ├── ConfirmationSheet.swift
    ├── PhotoPicker.swift
    └── LocationPicker.swift
```

**Benefits:**
- Consistent UI (same button style everywhere)
- Faster development (reuse instead of recreate)
- Easier to theme (change button style in one place)
- Easier to test (test component once, not every usage)

---

## Implementation Roadmap

### Phase 1: Analytics Foundation (Week 1)
**Goal:** Get basic analytics working end-to-end

- [ ] Define `AnalyticsEvent` schema
- [ ] Create event catalog (10 core events)
- [ ] Implement `AnalyticsQueue` (iOS)
- [ ] Create `/v1/analytics/events` endpoint (backend)
- [ ] Add privacy toggle to settings
- [ ] Test with 2-3 events (`app_open`, `sign_in`, `event_create`)

**Success Criteria:**
- Events flow from iOS → Backend → Database
- No UI blocking
- Privacy toggle works
- Can query events from DB

---

### Phase 2: File Refactoring (Week 2)
**Goal:** Break down large files

**Priority order:**
1. **ConnectionsAPI.swift** (636 lines) → Highest merge conflict risk
   - Split into FriendsAPI, CommunitiesAPI, MessagingAPI, EventsAPI
2. **SettingsTabView.swift** (992 lines) → Second priority
   - Extract Profile, Privacy, Notifications, Account sections
3. **EventDetailsView.swift** (474 lines) → Third priority
   - Extract subviews (header, actions, attendees list, details)

**Success Criteria:**
- No file >300 lines
- All tests still pass
- No functional changes (refactor only)

---

### Phase 3: Feature Flags (Week 3)
**Goal:** Centralize and sync feature flags

- [ ] Create `FeatureFlags` struct
- [ ] Migrate all `@AppStorage` to FeatureFlags
- [ ] Add server endpoint for remote config
- [ ] Implement server sync on app launch
- [ ] Add debug menu to toggle flags (dev builds only)

**Success Criteria:**
- All flags accessible via `FeatureFlags.shared`
- Can override flags from server
- Debug menu works

---

### Phase 4: Dashboard (Week 4)
**Goal:** Make analytics actionable

- [ ] Create backend `/analytics/overview` endpoint
- [ ] Build simple admin dashboard (web or iOS)
- [ ] Add 3 core charts:
  - Daily Active Users
  - Sign-up funnel
  - Event engagement
- [ ] Set up daily aggregation job

**Success Criteria:**
- Can answer: "How many DAU this week?"
- Can answer: "What's our sign-up conversion rate?"
- Can answer: "Which events are most popular?"

---

## Best Practices Summary

### Analytics

**✅ DO:**
- Version your event schema
- Batch events for efficiency
- Persist locally before sending
- Respect user privacy settings
- Start with 6-10 high-signal events
- Use flexible properties dict for event-specific data

**❌ DON'T:**
- Send events synchronously (blocks UI)
- Hard-code event names (use constants)
- Collect PII without consent
- Build a complex dashboard on day 1
- Change event schemas without versioning

---

### Code Organization

**✅ DO:**
- Keep files <300 lines
- Group by feature/domain
- Extract reusable components
- Use clear folder structure
- Document module boundaries

**❌ DON'T:**
- Create "God objects" (ConnectionsAPI with 30 methods)
- Mix concerns (API + UI in same file)
- Nest too deeply (max 3 levels)
- Create premature abstractions

---

### Feature Flags

**✅ DO:**
- Centralize in one struct
- Group by category (privacy, features, experiments)
- Sync with server for gradual rollouts
- Add debug menu for testing
- Document each flag

**❌ DON'T:**
- Scatter flags across codebase
- Use string literals for flag names
- Leave old flags in code forever
- Make flags hard to discover

---

## Risk Mitigation

### Risk: Analytics Overhead Impacts Performance

**Mitigation:**
- Queue events asynchronously
- Batch uploads (reduces network calls)
- Use background queue (utility QoS)
- Measure impact with Instruments
- Add kill switch (disable analytics remotely)

---

### Risk: Breaking Changes During Refactor

**Mitigation:**
- Refactor one file at a time
- Run full test suite after each change
- Use git branches (one refactor per branch)
- Code review each refactor separately
- Keep functional changes separate from refactors

---

### Risk: Feature Flag Sprawl

**Mitigation:**
- Require documentation for new flags
- Periodic cleanup (remove unused flags)
- Limit to <20 active flags at a time
- Use naming convention (`feature_`, `experiment_`, `privacy_`)

---

## Success Metrics

### Analytics System
- ✅ 95%+ event delivery rate
- ✅ <50ms overhead per event tracking
- ✅ Zero UI blocking
- ✅ Can answer 10 core business questions

### Code Organization
- ✅ All files <300 lines
- ✅ <5 merge conflicts per week (down from current)
- ✅ 30% faster compile times
- ✅ 50% easier to onboard new developers

### Feature Flags
- ✅ All flags centralized
- ✅ Server sync working
- ✅ Can do gradual rollout
- ✅ A/B tests possible

---

## References & Resources

### Analytics
- **Segment Event Spec:** https://segment.com/docs/connections/spec/
- **Amplitude Event Taxonomy:** https://help.amplitude.com/hc/en-us/articles/360047138392
- **Mixpanel Best Practices:** https://mixpanel.com/blog/event-tracking-best-practices/
- **GDPR Compliance:** https://gdpr.eu/data-processing/

### iOS Architecture
- **Swift API Design Guidelines:** https://swift.org/documentation/api-design-guidelines/
- **SwiftUI Best Practices:** https://www.swiftbysundell.com/basics/swiftui/
- **App Architecture (objc.io book):** Covers MVVM, modular design

### Feature Flags
- **LaunchDarkly Patterns:** https://launchdarkly.com/blog/feature-flag-best-practices/
- **Flagsmith Docs:** https://docs.flagsmith.com/
- **Feature Toggles (Martin Fowler):** https://martinfowler.com/articles/feature-toggles.html

---

## Conclusion

These architectural improvements address technical debt **before** it becomes a bottleneck:

**Analytics Pipeline:**
- ✅ Structured, versioned event schema
- ✅ Reliable delivery with local queuing
- ✅ Privacy-first design
- ✅ Actionable dashboard

**Code Organization:**
- ✅ Modular files (<300 lines each)
- ✅ Domain-separated APIs
- ✅ Reusable component library
- ✅ Reduced merge conflicts

**Feature Flags:**
- ✅ Centralized management
- ✅ Server-side overrides
- ✅ Gradual rollout capability
- ✅ A/B testing ready

**Implementation:** 4 weeks (1 week per phase)

**Expected Impact:**
- 📈 Better product decisions (data-driven)
- 🚀 Faster development velocity
- 🐛 Fewer merge conflicts
- 🎯 Controlled feature rollouts

**Recommendation:** Start with Phase 1 (Analytics Foundation) this sprint.

---

**Confidence Level:** 🟢 **High** - Best practices are well-established, implementation is straightforward, risks are manageable.
