# Agent Learning System - Implementation Plan

**Initiative:** agent-learning-system  
**Design Doc:** [docs/agent-learning-system.md](./agent-learning-system.md)  
**Status:** Ready for Implementation  
**Target Completion:** 4 weeks from start

---

## Overview

This implementation plan breaks the agent learning system into 4 sequential phases. Each phase builds on the previous one and can be tested independently.

**Goal:** Deliver a self-improving AI agent system where programmer agents learn from code review feedback and automatically improve future task execution by >10%.

---

## Timeline

```
Week 1-2: Phase 1.1 + 1.2 (Database + Extraction)
Week 2-3: Phase 1.3 (Prompt Enhancement)
Week 3-4: Phase 1.4 (Metrics + Validation)
Week 4: Integration testing, deployment, monitoring
```

---

## Phase 1.1: Database & Tracking (3-5 days)

**Handoff:** [docs/handoffs/learning-phase-1.1-database-tracking.md](./handoffs/learning-phase-1.1-database-tracking.md)

**Deliverables:**
- ✅ `task_outcomes` table (migration + model)
- ✅ `OutcomeTracker` service class
- ✅ Worker integration for automatic outcome creation
- ✅ POST `/api/learning/tasks/{id}/feedback` endpoint
- ✅ Unit + integration tests

**Success Criteria:**
- Outcomes created for 80%+ of completed tasks
- Feedback endpoint working
- Tests passing
- No impact on task execution performance

**Dependencies:** None

---

## Phase 1.2: Pattern Extraction (3-5 days)

**Handoff:** [docs/handoffs/learning-phase-1.2-pattern-extraction.md](./handoffs/learning-phase-1.2-pattern-extraction.md)

**Deliverables:**
- ✅ `outcome_learnings` table (migration + model)
- ✅ `LessonExtractor` service class
- ✅ 5+ pattern detectors for programmer agent
- ✅ CLI: `python app/cli/extract_learnings.py`
- ✅ GET `/api/learning/learnings` endpoint
- ✅ POST `/api/learning/extract` endpoint
- ✅ Unit + integration tests

**Success Criteria:**
- 5-10 high-confidence learnings created
- Pattern detection >80% accurate
- Tests passing
- CLI working

**Dependencies:** Phase 1.1 complete

---

## Phase 1.3: Prompt Enhancement (3-5 days)

**Handoff:** [docs/handoffs/learning-phase-1.3-prompt-enhancement.md](./handoffs/learning-phase-1.3-prompt-enhancement.md)

**Deliverables:**
- ✅ `PromptEnhancer` service class
- ✅ Integration with `prompter.py`
- ✅ Feature flag: `LEARNING_INJECTION_ENABLED`
- ✅ Learning confidence feedback loop
- ✅ Unit + integration tests

**Success Criteria:**
- Learnings injected into 50%+ of eligible tasks
- A/B test: 20% control group, 80% treatment
- No regression in task completion time
- Tests passing

**Dependencies:** Phase 1.1 + 1.2 complete

**Breaking Change:** `Prompter.build_task_prompt()` API changes - requires updating callers

---

## Phase 1.4: Metrics & Validation (2-3 days)

**Handoff:** [docs/handoffs/learning-phase-1.4-metrics-validation.md](./handoffs/learning-phase-1.4-metrics-validation.md)

**Deliverables:**
- ✅ GET `/api/learning/stats` endpoint (A/B test analysis)
- ✅ GET `/api/learning/health` endpoint
- ✅ CLI: `python app/cli/validate_learning.py`
- ✅ Statistical significance calculation
- ✅ Unit + integration tests
- ✅ Results documentation

**Success Criteria:**
- >10% improvement in code review acceptance rate
- Statistical significance p < 0.05
- All health checks passing
- Tests passing

**Dependencies:** Phase 1.1 + 1.2 + 1.3 complete

---

## Post-Implementation: Monitoring & Tuning (ongoing)

After all phases complete:

### Week 1 Post-Launch
- Monitor `/api/learning/health` daily
- Run validation CLI daily
- Check for errors in logs
- Verify A/B balance (~20% control)

### Week 2-4 Post-Launch
- Collect data (aim for 100+ programmer tasks)
- Analyze `/api/learning/stats` weekly
- Review active learnings
- Tune confidence thresholds if needed

### Month 2
- Deep analysis: which patterns help most?
- Deactivate low-confidence learnings
- Add new patterns based on feedback
- Plan Milestone 2 (strategy optimization)

---

## Rollback Plan

If system causes problems:

**Level 1: Disable Enhancement**
```bash
# Set environment variable
LEARNING_INJECTION_ENABLED=false
# Restart server
```
Outcome tracking continues, but no learnings injected.

**Level 2: Disable Tracking**
```python
# In worker.py, comment out OutcomeTracker calls
# await OutcomeTracker.track_completion(...)  # DISABLED
```
System fully disabled, no DB writes.

**Level 3: Database Rollback**
```bash
# Run migrations in reverse
alembic downgrade -1  # Remove outcome_learnings
alembic downgrade -1  # Remove task_outcomes
```
Complete removal of learning system.

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Negative improvement | Keep control group, can disable quickly with feature flag |
| Performance regression | Profile each phase, set time budgets, defensive coding |
| Bad learnings propagate | Confidence thresholds, human review, easy deactivation |
| A/B test imbalance | Monitor daily, adjust randomization if needed |
| System breaks tasks | Catch all exceptions, best-effort only, extensive testing |

---

## Future Milestones

After Milestone 1 validates the approach:

**Milestone 2: Strategy Optimization (2 weeks)**
- A/B test different prompt injection strategies
- Find optimal format, length, style
- Auto-adjust based on performance

**Milestone 3: Expand to Researcher (2-3 weeks)**
- Define researcher-specific patterns
- Generalize system for multiple agent types
- Add advanced features (decay, conflict detection)

**Milestone 4: ML Upgrade (4+ weeks)**
- Replace rule-based extraction with ML models
- Semantic similarity for context matching
- Automated pattern discovery

---

## Open Questions

- [ ] Should extraction run on schedule or trigger-based?
- [ ] What's the right confidence decay rate?
- [ ] How to handle contradictory learnings?
- [ ] Should we allow manual learning creation/editing?
- [ ] Integration with reflection system?

**Decision:** Document answers as implementation progresses.

---

## Communication Plan

**Before Start:**
- Share design doc with team
- Get approval on API changes (Prompter breaking change)
- Agree on success metrics

**During Implementation:**
- Daily: Update task status in project tracker
- Weekly: Share progress summary (what's done, what's next, blockers)
- Blockers: Escalate immediately, don't wait

**After Deployment:**
- Week 1: Daily metrics report
- Week 2-4: Weekly metrics report
- Month 2: Final results + recommendations

---

## Success Definition

Milestone 1 is successful if:

1. ✅ All 4 phases deployed to production
2. ✅ System stable (no crashes, no performance regression)
3. ✅ >10% improvement in code review acceptance rate
4. ✅ Statistical significance achieved (p < 0.05)
5. ✅ 80%+ outcome coverage for programmer tasks
6. ✅ 50%+ learning application rate
7. ✅ Team confidence in system (not seeing repeated mistakes)

If any criterion fails, document why and create follow-up tasks.

---

## Getting Started

**For Programmer:**

1. Read the design doc: [docs/agent-learning-system.md](./agent-learning-system.md)
2. Start with Phase 1.1: [docs/handoffs/learning-phase-1.1-database-tracking.md](./handoffs/learning-phase-1.1-database-tracking.md)
3. Create feature branch: `git checkout -b feature/agent-learning-system`
4. Implement, test, commit
5. Mark handoff complete, move to next phase
6. When all phases done, request code review

**Questions?** Contact architect or create GitHub issue.

---

**Last Updated:** 2026-02-23  
**Next Review:** After Phase 1.1 complete
