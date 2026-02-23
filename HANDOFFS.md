# Agent Learning System - Implementation Handoffs

**Source Design:** [docs/agent-learning-system.md](docs/agent-learning-system.md)  
**Initiative:** agent-learning-system  
**Target Agent:** programmer  
**Created:** 2026-02-23  

---

## Milestone 1: Outcome Tracking + Memory Injection for Programmer

### Handoff 1.1: Database Schema + Outcome Tracker

**Priority:** High  
**Complexity:** Medium  
**Estimated Time:** 2-3 days  

**Context:**
Implement the foundational database schema and outcome tracking system. This creates structured records of task outcomes that will feed the learning system.

See design doc section "Data Model" and "Components - Outcome Tracker".

**Tasks:**
1. Create Alembic migration for `task_outcomes` table
   - All fields from design doc schema
   - Indexes on agent_type, outcome_type, task_category, reviewed_at
2. Create SQLAlchemy model `TaskOutcome` in `app/models.py`
3. Create Pydantic schemas in `app/schemas.py`:
   - `TaskOutcomeBase`, `TaskOutcomeCreate`, `TaskOutcome`
   - `TaskFeedbackRequest` (for human review endpoint)
4. Implement `app/orchestrator/outcome_tracker.py`:
   - `OutcomeTracker` class with methods:
     - `track_completion(task_id, worker_run_id, outcome_type, **kwargs)`
     - `track_human_feedback(task_id, feedback, review_state, category)`
     - `infer_task_category(task)` - classify from title/notes
     - `compute_context_hash(task)` - simple hash for similarity
5. Integrate `OutcomeTracker.track_completion()` into `app/orchestrator/worker.py`:
   - Call after successful task completion
   - Call after task failure
   - Pass worker_run_id, outcome type, duration, retry_count, escalation_tier
6. Create endpoint `PATCH /api/tasks/{task_id}/feedback` in `app/routers/tasks.py`:
   - Accept feedback, review_state, category
   - Call `OutcomeTracker.track_human_feedback()`
   - Update task.review_state field
7. Add endpoint `GET /api/learning/outcomes` for querying outcomes

**Acceptance Criteria:**
- Migration runs cleanly and creates table with all fields
- TaskOutcome records are created for every completed task
- Can submit human feedback via API and it's recorded correctly
- Task category inference works for common patterns (feature/bug/test/docs)
- Unit tests for OutcomeTracker methods
- Integration test: task completion → outcome creation → feedback → outcome update

**Files to Create:**
- `alembic/versions/XXXX_add_task_outcomes.py`
- `app/orchestrator/outcome_tracker.py`

**Files to Modify:**
- `app/models.py` (add TaskOutcome model)
- `app/schemas.py` (add TaskOutcome schemas)
- `app/orchestrator/worker.py` (integrate outcome tracking)
- `app/routers/tasks.py` (add feedback endpoint)

**Testing:**
```python
# Test outcome creation
async def test_track_completion():
    tracker = OutcomeTracker(db)
    outcome = await tracker.track_completion(
        task_id="T123",
        worker_run_id=456,
        outcome_type="success",
        duration_seconds=120
    )
    assert outcome.task_id == "T123"
    assert outcome.outcome_type == "success"

# Test feedback recording
async def test_track_human_feedback():
    tracker = OutcomeTracker(db)
    outcome = await tracker.track_human_feedback(
        task_id="T123",
        feedback="Missing unit tests",
        review_state="rejected",
        category="missing_tests"
    )
    assert outcome.human_feedback == "Missing unit tests"
    assert outcome.review_state == "rejected"
```

---

### Handoff 1.2: Lesson Extraction - Rule-Based

**Priority:** High  
**Complexity:** Medium  
**Estimated Time:** 3-4 days  

**Context:**
Implement rule-based pattern matching to extract actionable lessons from task outcomes. Start with 4-5 common patterns for programmer tasks.

See design doc section "Data Model - outcome_learnings" and "Components - Lesson Extractor".

**Tasks:**
1. Create Alembic migration for `outcome_learnings` table
   - All fields from design doc schema
   - Unique constraint on (agent_type, pattern_key)
   - Indexes on agent_type, active, confidence
2. Create SQLAlchemy model `OutcomeLearning` in `app/models.py`
3. Create Pydantic schemas in `app/schemas.py`
4. Implement `app/orchestrator/lesson_extractor.py`:
   - `LessonExtractor` class with methods:
     - `extract_from_outcome(outcome_id)` - analyze single outcome
     - `extract_from_batch(outcome_ids)` - batch process
     - `detect_patterns(feedback_text, category)` - pattern matching
     - `create_learning(agent_type, pattern_key, description, prompt_injection, **kwargs)`
     - `update_learning_confidence(learning_id, new_outcome_id, succeeded)`
5. Implement initial pattern rules for programmer:
   ```python
   PROGRAMMER_PATTERNS = {
       "missing_tests": {
           "triggers": ["missing test", "no tests", "add tests", "needs tests"],
           "description": "Always write unit tests for new functions",
           "prompt_injection": "IMPORTANT: Write unit tests for all new functions. Past tasks were rejected for missing tests.",
           "applies_to": ["feature", "bug_fix"]
       },
       "error_handling": {
           "triggers": ["missing error handling", "no error handling", "handle errors"],
           "description": "Add proper error handling and validation",
           "prompt_injection": "Remember to add error handling and input validation. Past code was rejected for missing error checks.",
           "applies_to": ["feature", "bug_fix"]
       },
       "unclear_names": {
           "triggers": ["unclear variable", "rename", "better names", "descriptive name"],
           "description": "Use descriptive variable and function names",
           "prompt_injection": "Use clear, descriptive names for variables and functions. Avoid abbreviations.",
           "applies_to": ["feature", "refactor"]
       },
       "missing_docs": {
           "triggers": ["missing docstring", "no documentation", "add docstring", "document"],
           "description": "Add docstrings to functions and classes",
           "prompt_injection": "Add docstrings to all new functions and classes explaining purpose, args, and return values.",
           "applies_to": ["feature"]
       }
   }
   ```
6. Add background task or cron job to extract learnings:
   - Query outcomes with human_feedback that haven't been processed
   - Run pattern detection
   - Create or update learnings
7. Add CLI command: `python -m app.cli.extract_learnings --agent programmer`
8. Add endpoint `GET /api/learning/learnings?agent=programmer` to view learnings

**Acceptance Criteria:**
- Migration runs cleanly and creates table
- Pattern detection correctly identifies feedback patterns
- OutcomeLearning records are created from matched patterns
- Confidence updates when same pattern is reinforced
- Can run extraction manually via CLI
- Unit tests for pattern matching
- Integration test: feedback → extraction → learning creation

**Files to Create:**
- `alembic/versions/XXXX_add_outcome_learnings.py`
- `app/orchestrator/lesson_extractor.py`
- `app/cli/extract_learnings.py` (CLI command)

**Files to Modify:**
- `app/models.py` (add OutcomeLearning model)
- `app/schemas.py` (add OutcomeLearning schemas)

**Testing:**
```python
async def test_detect_pattern():
    extractor = LessonExtractor(db)
    pattern = extractor.detect_patterns(
        feedback="Missing unit tests for new API endpoints",
        category="missing_tests"
    )
    assert pattern["pattern_key"] == "missing_tests"
    assert "test" in pattern["prompt_injection"].lower()

async def test_create_learning():
    extractor = LessonExtractor(db)
    learning = await extractor.create_learning(
        agent_type="programmer",
        pattern_key="missing_tests",
        description="Always write unit tests",
        prompt_injection="IMPORTANT: Write unit tests...",
        source_outcome_ids=["O123"],
        confidence=0.7
    )
    assert learning.pattern_key == "missing_tests"
    assert learning.confidence == 0.7
```

---

### Handoff 1.3: Prompt Enhancement Integration

**Priority:** High  
**Complexity:** Medium  
**Estimated Time:** 2-3 days  

**Context:**
Integrate learning injection into task prompt generation. Query relevant learnings before task execution and inject them into the prompt.

See design doc section "Components - Prompt Enhancer".

**Tasks:**
1. Implement `app/orchestrator/prompt_enhancer.py`:
   - `PromptEnhancer` class with methods:
     - `enhance_prompt(agent_type, task, base_prompt)` - main entry point
     - `query_relevant_learnings(agent_type, task, limit=3)` - find applicable learnings
     - `select_learnings(learnings, max_count)` - choose which to inject
     - `inject_learnings(base_prompt, learnings)` - format and inject
2. Implement learning selection logic:
   - Match on task category (inferred from task)
   - Match on keywords in task title/notes
   - Filter by active=True and confidence > 0.5
   - Sort by confidence DESC
   - Limit to top 3
3. Implement "prefix" injection style:
   ```
   ## Lessons from Past Tasks
   - [learning 1 prompt_injection]
   - [learning 2 prompt_injection]
   - [learning 3 prompt_injection]
   
   ## Your Task
   [original prompt]
   ```
4. Add configuration flag `LEARNING_INJECTION_ENABLED` in `app/config.py` (default: False)
5. Integrate with `app/orchestrator/prompter.py`:
   - After building base prompt, call `PromptEnhancer.enhance_prompt()`
   - Only if LEARNING_INJECTION_ENABLED=True
   - Log which learnings were injected
6. Track which learnings were applied:
   - Update `times_applied`, `last_applied_at` on OutcomeLearning
   - Store applied learning_ids in TaskOutcome for later analysis
7. Add endpoint `GET /api/learning/prompt-preview?task_id=X` to preview enhanced prompt

**Acceptance Criteria:**
- PromptEnhancer correctly queries and filters relevant learnings
- Learnings are injected in readable format
- Integration with Prompter doesn't break existing prompts
- Can toggle learning injection via config flag
- Learnings application is tracked in DB
- Unit tests for query logic and injection
- Integration test: task → learning query → prompt enhancement

**Files to Create:**
- `app/orchestrator/prompt_enhancer.py`

**Files to Modify:**
- `app/orchestrator/prompter.py` (integrate enhancement)
- `app/config.py` (add LEARNING_INJECTION_ENABLED flag)
- `app/models.py` (add applied_learning_ids to TaskOutcome if needed)

**Testing:**
```python
async def test_query_relevant_learnings():
    enhancer = PromptEnhancer(db)
    task = Task(
        title="Add user authentication API",
        notes="Feature task",
        agent="programmer"
    )
    learnings = await enhancer.query_relevant_learnings("programmer", task, limit=3)
    assert len(learnings) <= 3
    assert all(l.active for l in learnings)
    assert all(l.confidence > 0.5 for l in learnings)

async def test_inject_learnings():
    enhancer = PromptEnhancer(db)
    base_prompt = "Implement the feature."
    learnings = [...]  # mock learnings
    enhanced = enhancer.inject_learnings(base_prompt, learnings)
    assert "Lessons from Past Tasks" in enhanced
    assert base_prompt in enhanced
```

---

### Handoff 1.4: Metrics & Validation Dashboard

**Priority:** Medium  
**Complexity:** Medium  
**Estimated Time:** 2-3 days  

**Context:**
Add observability and metrics to validate that the learning system is working and improving outcomes.

See design doc section "Success Metrics" and "Observability".

**Tasks:**
1. Add endpoint `GET /api/learning/stats`:
   - Query parameters: `agent`, `since` (datetime)
   - Return:
     - Total outcomes tracked
     - Outcomes with human feedback (coverage %)
     - Review acceptance rate (overall)
     - Review acceptance rate (with learnings applied vs without)
     - Learning count, average confidence
     - Top 5 learnings by application frequency
2. Calculate baseline metrics:
   - Query outcomes before learning system was enabled
   - Calculate acceptance rate: `accepted / (accepted + rejected)`
3. Calculate current metrics:
   - Query recent outcomes with applied_learning_ids
   - Split into "with learnings" and "without learnings"
   - Calculate acceptance rate for each group
4. Add A/B test control:
   - Randomly disable learning injection for 20% of tasks
   - Track which tasks had learning disabled
   - Compare acceptance rates
5. Add logging:
   - `[LEARNING]` prefix for all logs
   - Log when learnings injected: `[LEARNING] Injected 3 learnings into prompt for task T123: ['missing_tests', 'error_handling', 'unclear_names']`
   - Log when learnings created: `[LEARNING] Created learning 'missing_tests' from 5 outcomes (confidence=0.8)`
6. Add simple dashboard section to Status page:
   - Learning stats widget
   - Recent learnings list
   - Acceptance rate trend over time
7. Add alerts (optional):
   - Alert if outcome coverage drops below 80%
   - Alert if extraction fails repeatedly

**Acceptance Criteria:**
- Stats endpoint returns accurate metrics
- Baseline vs current acceptance rate is calculated correctly
- A/B test control group is implemented
- Logging provides good observability
- Dashboard shows learning system status
- Can demonstrate improvement (or lack thereof) with data

**Files to Create:**
- `app/routers/learning.py` (learning endpoints)

**Files to Modify:**
- `app/routers/__init__.py` (register learning router)
- Frontend dashboard (if applicable)

**Testing:**
```python
async def test_calculate_acceptance_rate():
    # Create mock outcomes
    outcomes = [
        TaskOutcome(outcome_type="human_approved", ...),
        TaskOutcome(outcome_type="human_rejected", ...),
        TaskOutcome(outcome_type="human_approved", ...),
    ]
    rate = calculate_acceptance_rate(outcomes)
    assert rate == 2/3  # 2 approved out of 3

async def test_stats_endpoint():
    response = await client.get("/api/learning/stats?agent=programmer")
    assert response.status_code == 200
    data = response.json()
    assert "total_outcomes" in data
    assert "acceptance_rate" in data
    assert "learning_count" in data
```

---

## Milestone 2: Strategy A/B Testing Framework

### Handoff 2.1: Prompt Strategies Schema + Manager

**Priority:** Medium  
**Complexity:** Medium  
**Estimated Time:** 3-4 days  
**Dependencies:** Milestone 1 complete  

**Context:**
Implement A/B testing framework to compare different prompt enhancement strategies and converge to the best performer.

See design doc section "Data Model - prompt_strategies" and "Components - Strategy Manager".

**Tasks:**
1. Create migration for `prompt_strategies` table
2. Create SQLAlchemy model `PromptStrategy` in `app/models.py`
3. Implement `app/orchestrator/strategy_manager.py`:
   - `StrategyManager` class with methods:
     - `select_strategy(agent_type)` - weighted random selection
     - `record_outcome(strategy_id, task_id, outcome_type)`
     - `update_strategy_metrics(strategy_id)`
     - `analyze_strategies(agent_type)` - compare performance
     - `adjust_weights(agent_type)` - increase weight of best performer
4. Define initial strategy variants for programmer:
   - **baseline:** Top 3 learnings, prefix injection
   - **verbose:** Top 5 learnings, inline injection with examples
   - **minimal:** Top 2 learnings, suffix injection
5. Seed database with initial strategies
6. Modify `PromptEnhancer.enhance_prompt()` to accept `strategy` parameter
7. Integrate with worker.py:
   - Select strategy before task execution
   - Store strategy_id in TaskOutcome
8. Add periodic job to adjust weights (every 50 tasks or daily)
9. Add endpoints:
   - `GET /api/learning/strategies` - list strategies and performance
   - `POST /api/learning/strategies/{id}/adjust` - manual weight adjustment

**Acceptance Criteria:**
- Strategies are created and tracked correctly
- Strategy selection is weighted random
- Performance metrics update as tasks complete
- Weights adjust automatically based on performance
- After 100 tasks, best strategy has higher weight
- Unit tests for selection logic and metric calculation

---

## Milestone 3: Expand to Researcher Agent

### Handoff 3.1: Researcher-Specific Patterns

**Priority:** Low  
**Complexity:** Medium  
**Estimated Time:** 3-4 days  
**Dependencies:** Milestone 1 complete  

**Context:**
Extend the learning system to researcher agent with research-specific outcome patterns.

**Tasks:**
1. Define researcher-specific patterns in LessonExtractor:
   ```python
   RESEARCHER_PATTERNS = {
       "good_sources": {
           "triggers": ["excellent source", "good find", "authoritative"],
           "description": "Prefer academic papers and official documentation",
           "prompt_injection": "Focus on academic papers, official docs, and authoritative sources. These have been most valuable in past research.",
           "applies_to": ["research", "investigation"]
       },
       "bad_sources": {
           "triggers": ["unreliable", "poor source", "outdated"],
           "description": "Avoid blog posts and forums for technical research",
           "prompt_injection": "Avoid relying on blog posts and forums. Prefer primary sources and documentation.",
           "applies_to": ["research"]
       },
       "depth": {
           "triggers": ["surface level", "needs more depth", "dig deeper"],
           "description": "Provide comprehensive analysis, not just summaries",
           "prompt_injection": "Provide comprehensive analysis with multiple perspectives. Past research was too surface-level.",
           "applies_to": ["research"]
       }
   }
   ```
2. Add researcher outcome tracking to research request completion
3. Add researcher feedback mechanism (quality rating)
4. Integrate prompt enhancement for researcher tasks
5. Measure improvement in research quality ratings

**Acceptance Criteria:**
- Researcher patterns defined and tested
- Outcomes tracked for research tasks
- Learnings applied to researcher prompts
- Quality improvement measured (if ratings exist)

---

## General Testing Guidelines

For all handoffs:

1. **Unit Tests:**
   - Test each method in isolation
   - Mock database interactions
   - Test edge cases (empty results, null values, etc)

2. **Integration Tests:**
   - Test end-to-end flows
   - Use test database
   - Verify DB state changes

3. **Manual Validation:**
   - Review actual prompts being generated
   - Verify learnings make sense
   - Check that injection doesn't break formatting

4. **Performance:**
   - Learning query should be < 100ms
   - Pattern matching should be < 50ms
   - Don't block task execution

5. **Error Handling:**
   - Gracefully handle missing data
   - Don't fail task if learning system fails
   - Log errors but continue execution

---

## Notes for Programmer

- **Read the design doc first:** [docs/agent-learning-system.md](docs/agent-learning-system.md)
- **Start with Handoff 1.1** - the others depend on it
- **Keep it simple:** Don't over-engineer. Ship v1, iterate based on data
- **Make it observable:** Log everything, make metrics visible
- **Test incrementally:** Don't wait until everything is done to test
- **Ask questions:** If anything is unclear, ask for clarification

---

## Progress Tracking

- [ ] Handoff 1.1: Database Schema + Outcome Tracker
- [ ] Handoff 1.2: Lesson Extraction - Rule-Based
- [ ] Handoff 1.3: Prompt Enhancement Integration
- [ ] Handoff 1.4: Metrics & Validation Dashboard
- [ ] Handoff 2.1: Prompt Strategies Schema + Manager
- [ ] Handoff 3.1: Researcher-Specific Patterns

**Milestone 1 Target:** 2-3 weeks  
**Milestone 2 Target:** +1 week  
**Milestone 3 Target:** +1-2 weeks  

---

*End of handoffs*
