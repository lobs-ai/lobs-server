# Context Panel Design — Intelligence Dashboard

**Status:** Approved for implementation  
**Initiative ID:** bb89fbf4  
**Created:** 2026-02-23  
**Author:** architect

---

## Problem Statement

When viewing the Intelligence dashboard, users currently see:
- **Initiatives tab:** Proposals from agents requiring review
- **Reflections tab:** Reflection cycles showing agent insights
- **Sweeps tab:** Historical decision batches

**The gap:** There's no way to see what an agent or project is actively working on, what they've learned, or what context is relevant to their current work.

**User need:** When reviewing an initiative or reflection, I want to see:
- "What tasks is this agent currently working on?"
- "What research or memory context do they have access to?"
- "What recent outcomes or learnings are relevant?"
- "What's the project status this relates to?"

**Goal:** Add a context panel that surfaces relevant operational data (tasks, research, memory) for the selected agent or project, helping users make more informed decisions and understand agent behavior.

---

## Proposed Solution

Add a **collapsible context panel** to the Intelligence dashboard that displays contextual information based on the current selection:

### Selection Context Types

1. **Agent-focused** — When an initiative or reflection is selected, show context for the proposing agent
2. **Project-focused** — When viewing project-related items, show project context
3. **Global overview** — When nothing is selected, show system-wide highlights

### Panel Location

- **Right sidebar** in Intelligence view (similar to detail panel pattern already used)
- **Collapsible** — Can be hidden to maximize space for main content
- **Persistent state** — User preference stored locally for show/hide
- **Responsive** — Adjusts width based on available space (min 280px, ideal 360px, max 400px)

### Data Sections

The context panel displays 4 key sections:

#### 1. Active Tasks
**Purpose:** Show what work is in flight  
**Data:**
- Tasks assigned to this agent/project with `work_state IN ('not_started', 'in_progress', 'blocked')`
- Display: title, status, priority, last updated
- Max 5 items, sorted by priority then updated date
- Action: Click to open task detail

#### 2. Recent Completions
**Purpose:** Show recent successes/failures for pattern detection  
**Data:**
- Tasks completed/failed in last 7 days for this agent/project
- Display: title, outcome (success/blocked/failed), completion date
- Show review state if available (approved/changes_requested)
- Max 5 items, sorted by completion date descending

#### 3. Memory Context
**Purpose:** Surface relevant agent memories and learnings  
**Data:**
- Recent memory entries from agent workspace (if available)
- Relevant learnings from `outcome_learnings` table (for this agent type)
- Display: memory title/snippet, date, confidence (for learnings)
- Max 3-5 items, sorted by relevance/recency

#### 4. Research & Topics
**Purpose:** Show active research areas  
**Data:**
- Topics this agent has contributed to or referenced
- Recent documents created by this agent
- Display: topic name, last activity, document count
- Max 3 items, sorted by activity date

### Visual Design

```
┌─────────────────────────────────────────┐
│  Intelligence                     [≡]   │  ← Header with collapse button
├─────────────────────────────────────────┤
│                                         │
│  [Initiatives] [Reflections] [Sweeps]  │  ← Tab bar
│                                         │
├─────────┬───────────────┬──────────────┤
│         │               │              │
│  List   │  Detail View  │   Context    │  ← Three-column layout
│         │               │    Panel     │
│         │               │              │
│ • Item1 │  Initiative   │ ┌──────────┐ │
│ • Item2 │  Details      │ │ Active   │ │
│ • Item3 │               │ │ Tasks    │ │
│ • Item4 │               │ │          │ │
│         │               │ │ • Task 1 │ │
│         │               │ │ • Task 2 │ │
│         │               │ └──────────┘ │
│         │               │              │
│         │               │ ┌──────────┐ │
│         │               │ │ Recent   │ │
│         │               │ │Complete  │ │
│         │               │ │          │ │
│         │               │ │ ✓ Task A │ │
│         │               │ │ ✗ Task B │ │
│         │               │ └──────────┘ │
│         │               │              │
│         │               │ ┌──────────┐ │
│         │               │ │ Memory   │ │
│         │               │ │ Context  │ │
│         │               │ └──────────┘ │
│         │               │              │
└─────────┴───────────────┴──────────────┘
```

### Interaction Design

**Show/Hide:**
- Toggle button in Intelligence header (top-right)
- Keyboard shortcut: `Cmd+K` (or `Cmd+Shift+C` for "Context")
- Default: Visible on first load, remembers user preference

**Responsive Behavior:**
- On narrow windows (< 1200px width), auto-collapse context panel
- User can manually expand if needed
- On very narrow (< 900px), hide context panel entirely

**Loading States:**
- Show skeleton loaders while fetching data
- Gracefully handle API errors (show "Unable to load" message)
- Cache data briefly (30s) to avoid excessive API calls

**Empty States:**
- "No active tasks" with helpful message
- "No recent completions" 
- "No memory context available"

---

## Architecture

### Backend API Requirements

#### New Endpoints

**1. `/api/intelligence/context/agent/{agent_type}`**
```json
GET /api/intelligence/context/agent/programmer

Response:
{
  "agent_type": "programmer",
  "active_tasks": [
    {
      "id": "abc123",
      "title": "Implement auth middleware",
      "status": "in_progress",
      "work_state": "in_progress",
      "priority": "high",
      "project_id": "proj-1",
      "updated_at": "2026-02-23T14:30:00Z"
    }
  ],
  "recent_completions": [
    {
      "id": "xyz789",
      "title": "Fix login bug",
      "outcome": "completed",
      "review_state": "approved",
      "completed_at": "2026-02-22T10:15:00Z"
    }
  ],
  "memory_context": [
    {
      "type": "learning",
      "title": "Always include error handling",
      "snippet": "Tasks without proper error handling...",
      "confidence": 0.85,
      "date": "2026-02-20T08:00:00Z"
    }
  ],
  "topics": [
    {
      "id": "topic-1",
      "name": "Authentication System",
      "last_activity": "2026-02-22T14:00:00Z",
      "document_count": 3
    }
  ]
}
```

**2. `/api/intelligence/context/project/{project_id}`**
```json
GET /api/intelligence/context/project/proj-1

Response:
{
  "project_id": "proj-1",
  "project_name": "Auth System",
  "active_tasks": [...],  // Similar structure
  "recent_completions": [...],
  "assigned_agents": ["programmer", "researcher"],
  "memory_context": [...],
  "topics": [...]
}
```

#### Data Queries

**Active tasks:**
```sql
SELECT * FROM tasks
WHERE (assigned_agent = :agent_type OR project_id = :project_id)
  AND work_state IN ('not_started', 'in_progress', 'blocked')
ORDER BY priority DESC, updated_at DESC
LIMIT 5
```

**Recent completions:**
```sql
SELECT * FROM tasks
WHERE (assigned_agent = :agent_type OR project_id = :project_id)
  AND work_state IN ('completed', 'failed')
  AND updated_at > NOW() - INTERVAL '7 days'
ORDER BY updated_at DESC
LIMIT 5
```

**Memory context:**
```sql
-- Learnings for agent
SELECT * FROM outcome_learnings
WHERE agent_type = :agent_type
  AND active = true
ORDER BY confidence DESC, created_at DESC
LIMIT 5

-- Could also fetch from agent workspace memory/ directory
-- via file system read (future enhancement)
```

**Topics:**
```sql
-- Find topics with documents created by agent
SELECT t.* FROM topics t
JOIN documents d ON d.topic_id = t.id
WHERE d.created_by = :agent_type
  OR t.last_activity > NOW() - INTERVAL '7 days'
ORDER BY t.last_activity DESC
LIMIT 3
```

### Frontend Components

**SwiftUI Structure:**

```swift
// New file: ContextPanel.swift
struct ContextPanel: View {
  let contextType: ContextType
  let contextId: String  // agent_type or project_id
  @ObservedObject var vm: AppViewModel
  
  enum ContextType {
    case agent(String)
    case project(String)
    case global
  }
  
  var body: some View {
    ScrollView {
      VStack(spacing: 16) {
        ActiveTasksSection(...)
        RecentCompletionsSection(...)
        MemoryContextSection(...)
        TopicsSection(...)
      }
    }
  }
}

// Sections
struct ActiveTasksSection: View { ... }
struct RecentCompletionsSection: View { ... }
struct MemoryContextSection: View { ... }
struct TopicsSection: View { ... }
```

**Integration into IntelligenceView:**

```swift
struct IntelligenceView: View {
  @State private var showContextPanel: Bool = true
  @State private var contextType: ContextType = .global
  
  var body: some View {
    HSplitView {
      // Existing list
      initiativesList
      
      // Existing detail
      initiativeDetail
      
      // NEW: Context panel
      if showContextPanel {
        ContextPanel(
          contextType: contextType,
          contextId: selectedContextId,
          vm: vm
        )
        .frame(minWidth: 280, idealWidth: 360, maxWidth: 400)
      }
    }
  }
}
```

---

## Design Tradeoffs

### Considered Alternatives

#### 1. **Modal overlay instead of sidebar**
- ❌ **Rejected:** Interrupts workflow, requires closing to see main content
- ✅ **Chosen:** Sidebar is less intrusive, allows side-by-side comparison

#### 2. **Tabs within context panel**
- ❌ **Rejected:** Adds extra navigation layer, hides information
- ✅ **Chosen:** Scrollable vertical sections show all info at once

#### 3. **Global context (not selection-based)**
- ❌ **Rejected:** Too much noise, not actionable
- ✅ **Chosen:** Context follows selection, stays relevant

#### 4. **Fetch all data upfront vs. on-demand**
- ⚖️ **Trade-off:**
  - Upfront: Faster when switching selections, but wasted if not viewed
  - On-demand: Slower initial load, but only fetches what's needed
- ✅ **Chosen:** On-demand with 30s cache — balances freshness and performance

### Key Decisions

**Why context panel instead of inline?**
- Keeps Intelligence dashboard focused on decision-making (initiatives/reflections)
- Context panel is supplementary, not primary workflow
- Can be hidden when not needed

**Why agent-focused first?**
- Initiatives and reflections are already agent-centric
- Natural to ask "what else is this agent doing?"
- Project context can come in v2 if needed

**Why limit to 5 items per section?**
- Prevents overwhelming information
- Forces prioritization (most relevant items)
- Can add "View all" link later if needed

---

## Implementation Plan

### Phase 1: Backend API (1-2 days)

**Task 1.1:** Create context data models
- Add Pydantic schemas for `AgentContext`, `ProjectContext`
- Define response models for each section (tasks, completions, etc.)
- **Acceptance:** Schemas validate correctly, match design spec

**Task 1.2:** Implement `/api/intelligence/context/agent/{agent_type}` endpoint
- Create `app/routers/intelligence_context.py`
- Add queries for active tasks, recent completions
- Return mock data initially for memory/topics
- **Acceptance:** Endpoint returns 200 with correct structure

**Task 1.3:** Add memory context query
- Query `outcome_learnings` table for agent-specific learnings
- Format as context items (title, snippet, confidence, date)
- **Acceptance:** Returns top 5 learnings for agent, ordered by confidence

**Task 1.4:** Add topics query
- Join `topics` and `documents` to find agent activity
- Return topics with recent activity
- **Acceptance:** Returns topics agent has contributed to

**Task 1.5:** Add project context endpoint
- Implement `/api/intelligence/context/project/{project_id}`
- Similar queries but filtered by project_id
- **Acceptance:** Returns project-specific context

**Task 1.6:** Add caching layer
- Use simple in-memory cache (30s TTL)
- Cache context responses per agent/project
- **Acceptance:** Subsequent requests within 30s return cached data

### Phase 2: Frontend Models & API Client (0.5-1 day)

**Task 2.1:** Define Swift models
- Create `ContextModels.swift` with `AgentContext`, `ProjectContext`
- Add `TaskSummary`, `CompletionSummary`, `MemoryItem`, `TopicSummary`
- **Acceptance:** Models decode from API responses

**Task 2.2:** Add API client methods
- Extend `APIService` with `fetchAgentContext(agentType:)`
- Add `fetchProjectContext(projectId:)`
- **Acceptance:** Methods fetch and decode context data

### Phase 3: UI Components (1-2 days)

**Task 3.1:** Create `ContextPanel.swift` shell
- Basic structure with ScrollView and sections
- Accept `contextType` and `contextId` parameters
- Display loading state initially
- **Acceptance:** Panel renders in Intelligence view

**Task 3.2:** Implement `ActiveTasksSection`
- Display up to 5 active tasks
- Show title, status badge, priority indicator
- Click to open task (if task detail view exists)
- **Acceptance:** Shows active tasks, handles empty state

**Task 3.3:** Implement `RecentCompletionsSection`
- Display up to 5 recent completions
- Show outcome (✓/✗), title, date
- Color-code by outcome
- **Acceptance:** Shows completions, handles empty state

**Task 3.4:** Implement `MemoryContextSection`
- Display memory items and learnings
- Show confidence bar for learnings
- Truncate long snippets
- **Acceptance:** Shows memory context, handles empty state

**Task 3.5:** Implement `TopicsSection`
- Display topics with document count
- Show last activity date
- Click to open topic (future enhancement)
- **Acceptance:** Shows topics, handles empty state

**Task 3.6:** Add loading and error states
- Skeleton loaders for each section
- Error messages for API failures
- Retry button on errors
- **Acceptance:** Graceful loading/error handling

### Phase 4: Integration (1 day)

**Task 4.1:** Integrate into `IntelligenceView`
- Add `@State var showContextPanel: Bool`
- Add context panel to HSplitView
- Determine context type from selection
- **Acceptance:** Context panel appears alongside detail view

**Task 4.2:** Add toggle button
- Button in Intelligence header to show/hide panel
- Store preference in UserDefaults
- **Acceptance:** User can toggle panel visibility

**Task 4.3:** Implement selection-based context
- When initiative selected → show agent context for `proposedByAgent`
- When reflection selected → show context for reflection agents
- No selection → show global or hide panel
- **Acceptance:** Context updates based on selection

**Task 4.4:** Add keyboard shortcut
- Cmd+K to toggle context panel
- **Acceptance:** Keyboard shortcut works

**Task 4.5:** Responsive behavior
- Auto-collapse on narrow windows
- Hide entirely on very narrow
- **Acceptance:** Panel adapts to window size

### Phase 5: Polish & Testing (0.5-1 day)

**Task 5.1:** Visual refinement
- Match existing Intelligence view style
- Consistent spacing, colors, typography
- Section headers and dividers
- **Acceptance:** Looks cohesive with existing UI

**Task 5.2:** Performance optimization
- Implement 30s client-side cache
- Debounce selection changes (300ms)
- **Acceptance:** No excessive API calls

**Task 5.3:** Write tests
- Backend: Test context endpoints
- Frontend: Test context panel component rendering
- **Acceptance:** Tests pass

**Task 5.4:** Documentation
- Update AGENTS.md with new endpoints
- Add usage notes to Intelligence view comments
- **Acceptance:** Docs complete

---

## Testing Strategy

### Backend Testing

**Unit tests:**
- Test context queries return correct data
- Test filtering by agent/project
- Test date ranges and limits
- Test empty results

**Integration tests:**
- Test endpoint with real database
- Test caching behavior
- Test error handling (invalid agent, missing project)

### Frontend Testing

**Component tests:**
- Render each section with mock data
- Render empty states
- Render loading states
- Render error states

**Integration tests:**
- Test selection-based context switching
- Test panel toggle
- Test responsive behavior

### Manual Testing Checklist

- [ ] Select initiative → see agent context
- [ ] Select reflection → see agent context
- [ ] Deselect → context clears or shows global
- [ ] Active tasks section shows correct tasks
- [ ] Recent completions show success/failure
- [ ] Memory context shows learnings
- [ ] Topics section shows related topics
- [ ] Toggle panel hides/shows
- [ ] Keyboard shortcut works
- [ ] Responsive on narrow windows
- [ ] Loading states appear during fetch
- [ ] Error states show on API failure
- [ ] Click task → opens task detail (if available)

---

## Success Metrics

### User-Facing

- **Discoverability:** Users find and open context panel within first session
- **Utility:** Users report context panel helps decision-making (qualitative feedback)
- **Usage:** Context panel visible >50% of time users are in Intelligence view

### Technical

- **Performance:** Context API responds in <200ms (p95)
- **Reliability:** <1% error rate on context endpoints
- **Cache effectiveness:** >70% of requests served from cache

---

## Future Enhancements

**Not in initial scope** (consider for v2+):

1. **Inline task creation** — Create task directly from context panel
2. **Memory search** — Search agent memory from context panel
3. **Timeline view** — Show chronological agent activity
4. **Collaboration view** — Show which agents are working together
5. **Alerts/notifications** — Highlight blocked tasks or failures
6. **Trend analysis** — Success rate over time for agent
7. **Cross-agent comparison** — Compare context across multiple agents
8. **Export context** — Download context as markdown for reports

---

## Risks & Mitigations

### Risk 1: Performance Impact
**Impact:** Context queries could slow down Intelligence view  
**Likelihood:** Medium  
**Mitigation:**
- Implement caching (30s TTL)
- Limit queries to 5 items per section
- Index database columns used in queries
- Lazy-load context (only fetch when panel open)

### Risk 2: Stale Data
**Impact:** Context shows outdated information  
**Likelihood:** Medium  
**Mitigation:**
- 30s cache is reasonable for non-critical data
- Add manual refresh button
- Auto-refresh on significant events (task state change)

### Risk 3: UI Clutter
**Impact:** Three-column layout too cramped  
**Likelihood:** Low  
**Mitigation:**
- Make panel collapsible
- Responsive behavior on narrow screens
- Keep panel content concise (max 5 items per section)

### Risk 4: Scope Creep
**Impact:** Feature expands beyond initial design  
**Likelihood:** High  
**Mitigation:**
- Strict adherence to Phase 1-5 tasks
- Defer enhancements to v2
- Time-box implementation to 5-7 days

---

## Summary

**What we're building:** A collapsible context panel in the Intelligence dashboard that surfaces relevant tasks, completions, memory, and topics for the selected agent or project.

**Why:** Helps users understand what agents are working on and what context influences their proposals/reflections.

**How:** 
- Backend: New `/api/intelligence/context/*` endpoints
- Frontend: New `ContextPanel` component integrated into `IntelligenceView`
- Data: Tasks, completions, learnings, topics filtered by agent/project

**When:** 5-7 days across 5 phases (backend, models, UI, integration, polish)

**Success:** Users can see agent context alongside initiatives/reflections, improving decision-making speed and quality.

---

*Design complete. Ready for implementation.*
