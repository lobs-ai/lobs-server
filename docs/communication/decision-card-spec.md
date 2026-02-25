# Decision Card Specification — 60-Second Human Approvals

**Status:** Draft  
**Created:** 2026-02-25  
**Author:** architect  
**Origin:** Initiative 5e8ad1b3 — "60-Second Decision Card" UX

---

## 1. Problem Statement

Rafe's bottleneck is **attention, not intelligence**. When agents need human input, the current inbox format is too unstructured: items vary in length, bury the decision, and mix context with action. Rafe spends cycles reconstructing the situation before he can even evaluate options.

The result: approvals are delayed, agents stay blocked, and the highest-value work stalls.

**Goal:** Any human decision should be completable in under 60 seconds. The card structure does the cognitive work upfront so the human just chooses.

---

## 2. Decision Card Format

Every blocked-task notification that requires a human decision **must** use this exact format. Cards live in inbox items (`content` field) and optionally as Mission Control UI components.

### 2.1 Card Schema (Markdown Template)

```markdown
## 🃏 Decision Required — [DECISION TITLE]

**Task:** [task title / task ID]  
**Deadline:** [ISO timestamp or relative, e.g. "2h from now" | "none"]  
**Urgency:** [🔴 Critical | 🟡 Standard | 🟢 Advisory]  
**If no response by deadline:** [consequence string]

---

### What Happened
[1–3 sentences. Factual. No opinion. Agent name + what it hit + what it tried.]

### Your Options

| # | Option | Consequence |
|---|--------|-------------|
| A | [action] | [tradeoff — what you gain and what you give up] |
| B | [action] | [tradeoff] |
| C | [action] | [tradeoff] |

### Recommendation
**→ [Option X]** — [one sentence explaining why this is the right call given current context]

### To Approve
[Exact action: e.g. "Reply 'A'" / "Click Approve on task [id]" / "Run: lobs approve task-123 --option A"]
```

### 2.2 Field Definitions

| Field | Required | Description |
|-------|----------|-------------|
| `DECISION TITLE` | Yes | Noun phrase. Max 8 words. "Override budget cap for task 123." |
| `Task` | Yes | Task title + ID for direct lookup |
| `Deadline` | Yes | When the decision must be made. ISO or relative. `none` if advisory. |
| `Urgency` | Yes | One of three tiers (see §4) |
| `If no response by deadline` | Yes | What the system will do automatically. Must be a complete sentence. |
| `What Happened` | Yes | Factual context. No jargon. Max 3 sentences. |
| `Options` | Yes | 2–4 options. Never 1 (that's not a decision). Never 5+ (cognitive overload). |
| `Option / Consequence` | Yes | Each option paired with its cost and benefit, both stated explicitly. |
| `Recommendation` | Yes | Architect or agent must pick one. Neutral "it depends" is not allowed. |
| `To Approve` | Yes | The exact mechanical action the human takes. No ambiguity. |

### 2.3 Writing Rules

**Keep it short.** The entire card must render in one screen (< 500 words). If context is needed, link to a doc.

**Name the option consequence, not just the option.** Bad: "Option B: Retry." Good: "Option B: Retry — will consume ~$0.40 more budget; 60% historical success rate."

**The recommendation must be opinionated.** "Option B" not "Either A or B depending on your preference." Agents must commit.

**No consequence = no option.** Every option must state what you gain AND what you give up. Options without tradeoffs aren't options, they're instructions.

**Deadline is not optional.** Even advisory cards must say "none" explicitly so it's clear the human checked.

**Default action must be safe.** The "if no response" default must leave the system in a stable state. Never default to irreversible actions (deletion, spend, deploy to prod).

---

## 3. Urgency Tiers

### 🔴 Critical — Respond within 2 hours

Triggered when:
- System is actively blocked (agent cannot proceed, task stuck in progress)
- Budget hard cap reached (model calls failing)
- Data loss or corruption risk
- Production deployment gating
- Escalation tier ≥ 3 with no human response

Default (no response): System pauses the task. No further agent work. Error surfaced in daily brief.

### 🟡 Standard — Respond within 24 hours

Triggered when:
- Task blocked on a decision but not failing
- An inbox-approved item has been queued > 48h without pickup
- Feature scope needs clarification before significant work begins
- Remediation path ambiguous after ≥ 2 retries

Default (no response): System escalates to 🔴 Critical on the next evaluation cycle (next morning's brief).

### 🟢 Advisory — Respond within 72 hours

Triggered when:
- Optimization decision (two valid approaches, no urgency)
- Feature proposal needing approval to proceed
- Research findings that need a decision about direction

Default (no response): System logs the card as "expired-advisory", moves on with existing approach, flags in weekly digest.

---

## 4. Default Urgency Mapping

Use this table when creating decision cards programmatically:

| Signal | Urgency |
|--------|---------|
| `task.escalation_tier >= 3` | 🔴 Critical |
| `task.work_state = 'in_progress'` AND `updated_at > 24h ago` | 🔴 Critical |
| Budget lane exhausted | 🔴 Critical |
| `task.retry_count >= 3` with no human response | 🟡 Standard |
| Stuck remediation (inbox-approved, queued > 48h) | 🟡 Standard |
| Scope/approach decision before implementation starts | 🟡 Standard |
| Architecture choice with no blocking dependency | 🟢 Advisory |
| Feature proposal, no current blocker | 🟢 Advisory |

---

## 5. Integration Points

### 5.1 Inbox Items

Decision cards are delivered as inbox items. The `content` field contains the full markdown card. The `summary` field is the title line: "Decision Required — [TITLE]".

Agents creating inbox items for blocked tasks **must** use the decision card template rather than freeform prose.

### 5.2 Mission Control UI (future)

Mission Control will parse the card markdown and render it as a structured component:
- Title bar with urgency color + deadline countdown timer
- Options as radio buttons or tap targets
- Recommendation highlighted
- One-tap approval action

This is a future UI enhancement; the markdown is the source of truth now.

### 5.3 Daily Ops Brief

The `BriefService` will include pending decision cards in the morning brief, filtered to 🔴 Critical first, then 🟡 Standard. Advisory cards are omitted from the brief unless overdue.

### 5.4 Notification Escalation (future)

If a 🔴 Critical card passes its deadline without response, the orchestrator sends a push notification (via OpenClaw notify). Not implemented yet.

---

## 6. Implementation Plan

These tasks are ordered by dependency. See handoffs JSON for full specs.

### Task 1: Document and examples (this doc + copy doc) ✅
- Spec written
- Sample copy with 5 real examples

### Task 2: Decision card generator utility (programmer, small)
- `app/utils/decision_card.py` — function `make_decision_card(title, deadline, urgency, what_happened, options, recommendation, approve_action) -> str`
- Returns formatted markdown string
- Validates: 2–4 options, recommendation is one of the option keys, deadline present
- Unit tests for the generator

### Task 3: Inbox item format enforcement (programmer, medium)
- Agents creating blocked-task inbox items must use the card format
- Create pydantic model `DecisionCardPayload` with all required fields
- `POST /api/inbox` accepts optional `decision_card: DecisionCardPayload` — if provided, `content` is auto-generated from template
- Backward compatible (freeform content still accepted for non-decision items)

### Task 4: BriefService integration (programmer, small)
- `BriefService` adds a "Pending Decisions" section to the daily brief
- Query: inbox items where `content` contains `"🃏 Decision Required"` AND `is_read = False` AND `modified_at < 24h ago`
- Sort: 🔴 first, then 🟡, then 🟢
- Format: compact — title, deadline, urgency, recommendation line only (not full card)

---

## 7. Testing Strategy

**Unit tests:**
- `make_decision_card()` validates all required fields
- Rejects 0, 1, or 5+ options
- Rejects recommendation not in options list
- Returns well-formed markdown

**Integration tests:**
- Create inbox item with `decision_card` payload → verify `content` matches expected template
- BriefService with pending decision cards → verify "Pending Decisions" section present and sorted

**Manual / acceptance:**
- Render a sample card in Mission Control and verify it fits one screen
- Have Rafe time-trial a decision on a sample card — target ≤ 60 seconds from read to response

---

## 8. Tradeoffs

### Why markdown, not a structured JSON payload?
Markdown renders immediately in Mission Control, chat, and email. JSON requires a UI parser before it's useful. We can add structured parsing later — the markdown is already human-readable without it. Tradeoff: harder to query programmatically (regex-based for now).

### Why require a recommendation?
Without a recommendation, cards are just menus. The agent has more context than the human right now; forcing a recommendation means that context is applied. The human can override it. Neutrality ("it depends") is just punting the cognitive work back to the human — which defeats the purpose.

### Why 2-4 options max?
Research (Hick's Law, clinical decision studies) confirms that beyond 4 options, decision time increases non-linearly. Two options are clean. Three are right for most situations. Four is the max before the card needs to be split into two sequential decisions.

### Why a default action on deadline expiry?
If there's no default, expired cards require manual cleanup. With a default, the system stays in motion. The default must always be the "do nothing / preserve state" option — never an irreversible action. This is a safety constraint, not a convenience.

---

*See also: [decision-card-copy.md](decision-card-copy.md) for sample card text across 5 real scenarios.*
