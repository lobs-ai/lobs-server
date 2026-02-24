# Research Findings: Research-to-Build Handoff Contract for Opportunity Work

## Executive Recommendation
Adopt a **single structured handoff contract** for researcher outputs so architect/programmer can execute without rediscovery. Use a required field set plus a deterministic completeness gate before a task is marked ready for build.

This directly addresses the stated gap in approved initiative conversion quality and is compatible with current lobs-server orchestration/task model.

---

## Proposed Contract (v1)

Use this schema for every approved research opportunity that is intended to feed implementation.

```yaml
handoff_contract_version: "1.0"
initiative:
  id: "<uuid>"
  title: "<string>"
  source_reflection_id: "<uuid|null>"
  category: "<enum>"
  risk_tier: "A|B|C"
  selected_agent: "researcher|architect|programmer|..."

problem_statement:
  current_state: "What exists now"
  pain_or_gap: "What is failing / missing"
  impact: "Who is affected + magnitude"
  evidence:
    - source: "<url|file|db-ref>"
      note: "Evidence summary"

user_story:
  actor: "As a <user/persona>"
  need: "I want <capability>"
  outcome: "So that <value>"
  primary_jtbd: "optional"
  acceptance_scenarios:
    - "Given ... When ... Then ..."

system_touchpoints:
  components:
    - name: "app/routers/... or app/orchestrator/..."
      change_type: "new|modify|none"
      rationale: "Why this component"
  data_models:
    - "table/model names"
  APIs:
    - "endpoint(s) or external API touchpoints"
  dependencies:
    - "internal/external constraints"

success_metric:
  north_star: "single primary measurable outcome"
  guardrails:
    - metric: "quality/reliability/cost metric"
      threshold: "target"
  measurement_plan:
    baseline: "current"
    target_window: "e.g., 30 days post-release"
    instrumentation: "how measured"

mvp_boundary:
  in_scope:
    - "Must-have outcomes"
  out_of_scope:
    - "Explicitly deferred work"
  phase2_candidates:
    - "Next-step enhancements"

risk_flags:
  - risk: "Technical/product/process risk"
    severity: "low|medium|high"
    likelihood: "low|medium|high"
    mitigation: "planned mitigation"
    owner: "role"

build_handoff:
  recommended_next_agent: "architect|programmer"
  implementation_readiness: "ready|needs_architecture|needs_decision"
  open_questions:
    - "Question needing decision"
  required_artifacts:
    - "design doc / API sketch / migration plan / test plan"
```

---

## Required Completeness Checks (Gate)

A handoff is **implementation-ready** only if all checks pass:

1. **Problem clarity**: `problem_statement.current_state`, `pain_or_gap`, `impact` all non-empty.
2. **Evidence quality**: at least 2 evidence items, including at least 1 primary source (repo file, DB record, or vendor docs).
3. **User intent testable**: at least 1 Given/When/Then scenario in `acceptance_scenarios`.
4. **System mapping**: minimum 2 concrete touchpoints (code components and/or DB/API surfaces).
5. **Metric quality**: one numeric north-star and at least one guardrail metric.
6. **Scope hygiene**: at least 3 in-scope bullets and 2 out-of-scope bullets.
7. **Risk coverage**: minimum 3 risk flags with mitigation and owner.
8. **Execution path**: `recommended_next_agent` + `implementation_readiness` set, and open questions either empty or explicitly decision-routed.

### Suggested scoring rubric
- 8/8 checks: `ready`
- 6–7/8: `needs_architecture`
- <=5/8: `needs_research_revision`

---

## Retrofit: Next 3 Approved Research Initiatives

Selection basis: latest approved initiatives proposed by `researcher` after this initiative, excluding current initiative itself.

### 1) Initiative 8d5574f1-e253-4543-948c-12e58595a300
**Title:** Competitive capability matrix: Devin vs Cursor vs Replit vs CrewAI vs Lobs

**Contract packet (retrofit):**
- **Problem statement:** Current approved research lacks grounded competitor baselines; feature planning risks internal bias.
- **User story:** As product owner, I want an apples-to-apples competitor matrix so roadmap priorities track market reality.
- **System touchpoints:**
  - `docs/research/*` (new matrix artifact)
  - `app/orchestrator/*` and `agents/*` (for mapping competitor capability gaps to routing/agent behavior requirements)
  - `tasks` pipeline (initiative -> task conversion quality)
- **Success metric:**
  - North-star: roadmap decisions cite matrix in >=80% of agent-platform feature proposals over next planning cycle.
  - Guardrail: no unsupported competitor claim in final doc (100% source-backed rows).
- **MVP boundary:**
  - In scope: 5-platform feature matrix, pricing tiers, integration coverage, key differentiation summary.
  - Out of scope: building new features from findings; live benchmark automation.
- **Risk flags:** stale pricing data, vendor marketing bias, inconsistent feature definitions.

**Readiness:** `needs_architecture` (good research target; requires explicit downstream implementation mapping template).

---

### 2) Initiative ec98a228-067a-4e9d-b5e2-7653731fa635
**Title:** Personal AI agent market positioning and pricing research

**Contract packet (retrofit):**
- **Problem statement:** PAW positioning/pricing decisions are under-constrained; high risk of mismatch with willingness-to-pay.
- **User story:** As founder/PM, I want evidence-based ICP + pricing bands so launch packaging is defensible.
- **System touchpoints:**
  - `docs/research/*` and go-to-market documentation
  - Feature packaging decisions that affect `projects/tasks` prioritization
  - Potential pricing telemetry endpoints (future, likely `app/routers/status.py` or new analytics route)
- **Success metric:**
  - North-star: one approved positioning statement + 3 pricing packages tied to evidence.
  - Guardrail: each package has at least 2 market comparables.
- **MVP boundary:**
  - In scope: competitor pricing map, segmentation hypotheses, recommended launch packaging.
  - Out of scope: billing implementation, checkout integration, full financial model.
- **Risk flags:** survivorship bias in public pricing pages, weak sample diversity, conflating B2B and individual buyer behavior.

**Readiness:** `ready` for strategist/product decisions; `needs_architecture` for direct engineering follow-through.

---

### 3) Initiative e60bf308-da2f-4fae-9662-97853fd18e1a
**Title:** High-impact integration prioritization framework

**Contract packet (retrofit):**
- **Problem statement:** Integration sequencing is unclear; engineering effort risks low-leverage connectors first.
- **User story:** As roadmap owner, I want a value-vs-effort integration matrix so we build highest leverage integrations first.
- **System touchpoints:**
  - Integration-related routers/services (`app/routers/*`, potential new adapters/services)
  - Orchestrator/task automation capabilities depending on external systems
  - Security/auth surfaces for external APIs (token handling patterns)
- **Success metric:**
  - North-star: prioritized top-5 integration list with score rationale accepted for roadmap.
  - Guardrail: each integration evaluated on user value, implementation effort, reliability risk, and maintenance burden.
- **MVP boundary:**
  - In scope: scoring framework, ranked shortlist, dependency notes.
  - Out of scope: implementing connectors, OAuth flows, background sync pipelines.
- **Risk flags:** overfitting to current user anecdotes, underestimating auth/compliance complexity, maintenance toil after launch.

**Readiness:** `ready` for architecture planning of first 1–2 integrations.

---

## Implementation Guidance for lobs-server Workflow

1. Add this contract as required structure for research outputs attached to approved initiatives before build delegation.
2. Store completeness result (score + failed checks) alongside initiative/task metadata.
3. Require `implementation_readiness in {ready, needs_architecture}` before project-manager routes to programmer.
4. For `needs_architecture`, auto-create architect handoff with open questions and touchpoint list.

---

## Sources

- Project context and architecture:
  - `/Users/lobs/lobs-server/README.md`
  - `/Users/lobs/lobs-server/ARCHITECTURE.md`
  - `/Users/lobs/lobs-server/AGENTS.md`
- Initiative/task records (queried from local DB):
  - `/Users/lobs/lobs-server/data/lobs.db` table `agent_initiatives`
  - `/Users/lobs/lobs-server/data/lobs.db` table `tasks`
- Specific approved initiatives used for retrofit:
  - `8d5574f1-e253-4543-948c-12e58595a300`
  - `ec98a228-067a-4e9d-b5e2-7653731fa635`
  - `e60bf308-da2f-4fae-9662-97853fd18e1a`
