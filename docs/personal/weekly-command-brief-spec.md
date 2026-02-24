# Personal Weekly Command Brief — Spec

**Purpose:** Cut context-switch overhead by collapsing academics, internship prep, esports, and build tasks into one ranked weekly plan reviewed Sunday evening (or Monday morning).

**Format:** Markdown doc (or Notion page). Updated once per week, referenced daily. No tools required beyond a text editor.

---

## Why This Exists

Rafe's bottleneck is not capacity — it's cognitive overhead from switching between four unrelated domains (school, career, esports, software). Each switch costs ~15 minutes of re-orientation. The brief acts as a **preloaded mental state**: one read, one decision on what matters, no re-deciding mid-week.

---

## Structure

The brief has four sections. They are filled out in order. **Section 1 drives the week; the others are context.**

---

### Section 1 — Must-Win 3

> The three outcomes that, if achieved by end of week, make this week a success.

**Rules:**
- Exactly three. Not two, not five.
- Each must be **completable this week** (not "make progress on thesis" — "finish lit review draft by Friday").
- One must come from academics or internship prep (the domains with hard external deadlines).
- Phrased as outcomes, not tasks. "Recruiter screen scheduled with [company]" not "send emails."

**Format:**
```
1. [Outcome] — by [day]
2. [Outcome] — by [day]
3. [Outcome] — by [day]
```

---

### Section 2 — Deadlines

> Hard commitments this week with fixed due dates. Pulled from calendar and syllabi.

**Format:**
```
| Date       | What                          | Domain       | Status    |
|------------|-------------------------------|--------------|-----------|
| Mon 2/24   | CS 4820 pset due               | Academics    | in flight |
| Wed 2/26   | Esports roster lock            | Esports      | done      |
| Fri 2/28   | Take-home case study submitted | Internship   | not started |
```

**Status options:** `not started` / `in flight` / `done` / `at risk`

Flag anything `at risk` in red (or prefix with `⚠️`). An `at risk` item automatically becomes a Must-Win candidate.

---

### Section 3 — Delegations

> Things you've handed off to agents, teammates, or other people. Your job is to unblock, not do.

**Format:**
```
| Item                              | Delegated to       | Expected by | Action needed?     |
|-----------------------------------|--------------------|-------------|---------------------|
| Server deploy fix                 | programmer agent   | Tue          | None — monitor      |
| Recruiter outreach list           | writer agent       | Wed          | Review + approve    |
| Practice VOD review               | esports analyst    | Thu          | Async feedback      |
```

**The rule:** If you're doing work that belongs in this table, stop and redelegate.

---

### Section 4 — Deferred

> Things you're consciously choosing **not** to do this week. Named and parked.

**Why this section exists:** Unprocessed backlog creates ambient anxiety. Naming deferrals closes the loop mentally. "I know about X, I chose to skip it this week, it lives here."

**Format:**
```
- [ ] Rewrite esports analytics dashboard — deferred to next week (build sprint)
- [ ] Apply to summer research program — deferred 2 weeks (deadline is March 15)
- [ ] Read SICP chapters 4-5 — indefinitely deferred, not in any active path
```

---

## Scoring Rubric

Used when Section 1 is hard to fill — too many candidates competing for Must-Win slots.

### Dimensions

| Dimension    | Score | Description |
|--------------|-------|-------------|
| **Urgency**  | 1–3   | 1 = flexible, 2 = this week or next, 3 = this week or miss it |
| **Impact**   | 1–3   | 1 = nice-to-have, 2 = meaningfully advances a goal, 3 = gates something else |
| **Owned by me** | 0 or 1 | 0 = someone else can do it, 1 = only I can do it |

### Score = Urgency × Impact × Owned-by-me

Items with score ≥ 6 are Must-Win candidates. Pick the top 3 if there are more than 3.

**Example scoring:**

| Item                            | U | I | Own | Score |
|---------------------------------|---|---|-----|-------|
| Pset due Monday                 | 3 | 2 | 1   | **6** |
| Esports roster submission       | 3 | 3 | 1   | **9** |
| Research GitHub issues backlog  | 1 | 1 | 0   | 0     |
| Recruiter screen scheduled      | 2 | 3 | 1   | **6** |
| Read networking book            | 1 | 2 | 1   | 2     |

→ Must-Wins this week: Roster submission, Pset, Recruiter screen.

---

## Cadence

| When             | What |
|------------------|------|
| **Sunday 7pm**   | Fill out the brief for the coming week (30 min max) |
| **Morning glance** | Re-read Must-Win 3 only — no re-planning |
| **Midweek (Wed)** | Check Deadlines table: anything now `at risk`? |
| **Friday EOD**   | Mark outcomes done/not-done. One sentence on why anything slipped. |
| **Next Sunday**  | Pull unfinished items into new brief's Deferred section |

---

## Sample Brief — Week of Feb 24

```markdown
# Weekly Command Brief — Feb 24, 2026

## Must-Win 3
1. Submit CS 4820 pset — by Mon midnight
2. Schedule final-round interview at Jane Street — by Wed
3. Lock esports spring roster + send to league admin — by Wed

## Deadlines
| Date   | What                          | Domain      | Status     |
|--------|-------------------------------|-------------|------------|
| Mon    | CS 4820 pset                  | Academics   | in flight  |
| Wed    | Esports roster lock           | Esports     | not started|
| Fri    | Take-home case due (Citadel)  | Internship  | not started|

## Delegations
| Item                        | Who              | By   | Action         |
|-----------------------------|------------------|------|----------------|
| Lobs server test coverage   | programmer agent | Tue  | None           |
| Recruiter email drafts      | writer agent     | Mon  | Review drafts  |
| Scouting report on [team]   | esports analyst  | Thu  | Feedback async |

## Deferred
- [ ] SICP Ch 4-5 — deferred, no active deadline
- [ ] LinkedIn profile refresh — next week
- [ ] Clean up old build tasks in Lobs — Friday if time
```

---

## Sample Brief — Internship Crunch Week

```markdown
# Weekly Command Brief — Mar 10, 2026

## Must-Win 3
1. Complete and submit Citadel final project — by Thu 5pm (hard deadline)
2. Prep and complete Jane Street superday (Fri) — research, case practice done by Thu
3. Finish midterm paper outline (due Apr 3) — outline + sources locked by Sun

## Deadlines
| Date   | What                      | Domain     | Status  |
|--------|---------------------------|------------|---------|
| Thu    | Citadel final project     | Internship | ⚠️ at risk |
| Fri    | Jane Street superday      | Internship | in flight |
| Sun    | Midterm outline (self)    | Academics  | not started |

## Delegations
| Item                         | Who              | By   | Action         |
|------------------------------|------------------|------|----------------|
| Esports weekly recap post    | writer agent     | Wed  | Approve post   |
| Lobs weekly brief generation | orchestrator     | Mon  | Read + confirm |

## Deferred
- [ ] Esports roster planning — deferred to Mar 17
- [ ] Lobs build sprint tasks — all deferred this week, internship priority
- [ ] Research paper revisions — deferred to Apr
```

---

## Design Decisions & Tradeoffs

**Why three Must-Wins, not five?**
Five is too many — it re-creates the backlog problem. Three forces genuine prioritization. If you can't pick three, you haven't thought hard enough.

**Why not a digital tool (Notion, Linear, etc.)?**
This is a planning artifact, not a tracking system. It should be quick to write and quick to read. A Notion page is fine. A spreadsheet is worse (more friction). Keep it as markdown in Lobs or a single Notion doc.

**Why is "Deferred" a named section?**
Without it, deferred items float in the backlog indefinitely. Naming them acknowledges they exist, removes them from working memory, and gives them a place to re-surface next week.

**Why score on 3 dimensions instead of a simple priority label?**
Because "priority" collapses two different things (how urgent vs. how impactful) into one, which makes prioritization debates impossible. Separate dimensions let you argue clearly: "This is urgent-3 but impact-1, so it shouldn't be a Must-Win."

**Why weekly, not daily?**
Daily planning has high overhead and breaks down when a single busy day is missed. A weekly unit is resilient — missing one morning doesn't destabilize the plan.

---

## Future Evolution (when this proves useful)

These are **not built now** — they're future options if the format proves valuable:

1. **Auto-generation from Lobs data** — Orchestrator could pre-populate Deadlines from calendar and Delegations from active agent tasks. Would save 10–15 min of weekly setup.
2. **Retrospective tracking** — After 4 weeks, analyze which Must-Wins were completed. Gives data on estimation accuracy and domain balance.
3. **Brief as a chat artifact** — Post the brief to a Lobs chat channel on Monday morning as a lightweight accountability mechanism.
