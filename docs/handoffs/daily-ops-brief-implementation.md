# Daily Ops Brief — Implementation Handoffs

**Design doc:** [docs/daily-ops-brief-design.md](../daily-ops-brief-design.md)  
**Task ID:** bc10aeab-75e0-4ac8-9df3-e78bffcc1b77  
**Created:** 2026-02-24

---

## Handoff 1: BriefService + Adapters

```json
{
  "to": "programmer",
  "initiative": "daily-ops-brief",
  "title": "Implement BriefService with source adapters",
  "context": "Create app/services/brief_service.py with BriefItem/BriefSection/DailyBrief dataclasses, four source adapters (CalendarAdapter, EmailAdapter, GitHubAdapter, TasksAdapter), BriefFormatter with to_markdown() and suggest_plan(), and BriefService.generate(). See docs/daily-ops-brief-design.md sections 2-4 for full specs. Key: all adapters must be wrapped in try/except with per-adapter 10s timeout. Use asyncio.gather for parallel fetching. GitHubAdapter uses subprocess to call `gh` CLI. TasksAdapter queries DB directly. CalendarAdapter tries GoogleCalendarService then falls back to ScheduledEvent table. EmailAdapter uses EmailService.read_emails().",
  "acceptance": "1) BriefService.generate(db) returns DailyBrief even when all adapters fail. 2) Each failed adapter sets section.error, not raise. 3) BriefFormatter.to_markdown() produces valid markdown matching the example in design doc. 4) suggest_plan() returns a 1-3 sentence string prioritizing blockers > high-priority tasks > meetings.",
  "files": ["docs/daily-ops-brief-design.md"]
}
```

## Handoff 2: API Endpoint + Chat Delivery

```json
{
  "to": "programmer",
  "initiative": "daily-ops-brief",
  "title": "Add /api/brief/today endpoint with chat delivery",
  "context": "Create app/routers/brief.py with GET /api/brief/today?send_to_chat=false. Returns {markdown, sections, generated_at}. When send_to_chat=true, store as ChatMessage(role='assistant', metadata={'source': 'daily_brief'}) in session from env BRIEF_CHAT_SESSION_KEY (default 'main') and broadcast via WebSocket. Register router in app/main.py. Auth required. See docs/daily-ops-brief-design.md Task 2.",
  "acceptance": "1) GET /api/brief/today returns 200 with markdown key. 2) send_to_chat=true creates ChatMessage visible in chat. 3) Bearer token required. 4) Router registered in main.py.",
  "files": ["docs/daily-ops-brief-design.md"]
}
```

## Handoff 3: 8am Engine Trigger — Direct Timer Pattern

> **NOTE:** Use the direct engine timer pattern (same as memory maintenance), NOT RoutineRunner. See design doc §3 for the tradeoff decision.

```json
{
  "to": "programmer",
  "initiative": "daily-ops-brief",
  "title": "Wire 8am daily brief trigger into orchestrator engine (direct timer pattern)",
  "context": "Add the daily ops brief auto-trigger to app/orchestrator/engine.py and app/orchestrator/runtime_settings.py. Follow the EXACT same pattern as memory maintenance (_last_memory_maintenance_date_et) and daily compression (_daily_compression_hour_et). See the full specification in docs/handoffs/daily-ops-brief-handoffs.json (Task 3) and docs/daily-ops-brief-design.md section 5 Task 3. Key points: add SETTINGS_KEY_DAILY_BRIEF_HOUR_ET='orchestrator.daily_brief.hour_et' and SETTINGS_KEY_DAILY_BRIEF_LAST_DATE_ET to runtime_settings.py; add _brief_hour_et=8 and _last_brief_date_et to engine __init__; add check in control loop after memory maintenance block; add _run_daily_brief() private method that is fully exception-safe.",
  "acceptance": "1) Brief fires once per day at 8am ET. 2) _last_brief_date_et persisted to OrchestratorSetting — survives restart without re-firing. 3) Engine crash-safe — exceptions caught and logged with [BRIEF] prefix. 4) _brief_hour_et appears in orchestrator status endpoint output. 5) No RoutineRunner dependency added.",
  "files": ["docs/daily-ops-brief-design.md", "docs/handoffs/daily-ops-brief-handoffs.json", "app/orchestrator/engine.py", "app/orchestrator/runtime_settings.py"]
}
```

## Handoff 4: Tests

```json
{
  "to": "programmer",
  "initiative": "daily-ops-brief",
  "title": "Write tests for Daily Ops Brief",
  "context": "Create tests/test_brief_service.py. See docs/daily-ops-brief-design.md section 7 for test strategy. Mock all external adapters (no network calls). Test graceful degradation when adapters fail.",
  "acceptance": "1) Unit test BriefFormatter.to_markdown() with all sections populated. 2) Unit test to_markdown() with all sections errored. 3) Unit test TasksAdapter.fetch() with mocked DB. 4) Integration test GET /api/brief/today returns 200 with mocked adapters. All tests pass.",
  "files": ["docs/daily-ops-brief-design.md"]
}
```

## Dependency Order

1. **Handoff 1** (BriefService) — no deps
2. **Handoff 2** (API endpoint) — depends on Handoff 1
3. **Handoff 3** (8am trigger) — depends on Handoff 1
4. **Handoff 4** (Tests) — depends on Handoffs 1-3, or can be done in parallel with mocks
