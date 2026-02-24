# Research Findings: PAW Competitive Positioning Snapshot Q1 2026

**Task:** Competitive Positioning Snapshot: PAW vs AI Assistants vs Agent Platforms  
**Full battlecard:** `docs/research/paw-positioning-snapshot-q1-2026.md`

---

## Summary

Compared PAW (Lobs/OpenClaw stack) against 5 alternatives across autonomy depth, human control, reporting quality, and setup friction. External sources verified via web_fetch (OpenAI Agents SDK docs, LangGraph docs, CrewAI docs, ChatGPT Enterprise).

## Key Finding

PAW's defensible position is **"Agent Operations Platform"** — the only option in this competitive set that ships orchestration + tiered human approval workflows + operational telemetry as a pre-assembled system. Frameworks (LangGraph, OpenAI Agents SDK, CrewAI) require 3-6 weeks of engineering to reach equivalent governance. AI assistants (ChatGPT, Claude Code) don't reach it at all.

## 3 Defensible Claims

1. **"Autonomous execution with human checkpoints — built in."** (vs. frameworks that make you build HITL logic yourself)
2. **"PAW is not just an agent runtime — it's an operations control plane."** (vs. assistants that have no ops telemetry)
3. **"Reduce the glue code between agent logic, oversight, and reporting."** (vs. assembling SDK + approval system + LangSmith + cost tracker separately)

## Biggest Risk

No published benchmark data. Claims are architecturally credible but need empirical proof points (time-to-first-automation, intervention rate, MTTR on failed runs).

## See Full Battlecard

`docs/research/paw-positioning-snapshot-q1-2026.md`
