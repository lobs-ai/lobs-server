# Learning System Quick Start

## Overview

The agent learning system tracks task outcomes and provides metrics for measuring agent performance improvement over time. This is Phase 1.4 (Metrics & Validation) of the learning system.

## Prerequisites

- Database migration run: `python migrations/create_learning_tables.py`
- Tables created: `task_outcomes`, `outcome_learnings`

## Usage

### 1. Validate System Health

```bash
python app/cli/validate_learning.py
```

This runs 6 health checks:
1. Outcome tracking coverage
2. Learning creation
3. Learning application rate
4. A/B test balance
5. Learning confidence health
6. Success rates (control vs treatment)

### 2. View Metrics via API

**Get performance stats:**
```bash
curl "http://localhost:8000/api/learning/stats?agent=programmer&since_days=14" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Response includes:**
- Control group size and success rate
- Treatment group size and success rate
- Improvement percentage
- Statistical significance (Chi-squared p-value)
- Learning counts and confidence
- Application rate

**Check system health:**
```bash
curl "http://localhost:8000/api/learning/health?agent=programmer" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Response includes:**
- Status: "healthy" or "degraded"
- Issues detected
- Recent outcome and learning counts
- Low-confidence learning warnings

### 3. Monitor Daily

Run validation daily to track system health:
```bash
# Add to crontab or scheduled job
0 9 * * * cd /path/to/lobs-server && python app/cli/validate_learning.py >> logs/learning-validation.log
```

## What to Watch For

### Healthy System
- ✅ Recent outcomes being created (24h)
- ✅ Learnings being extracted (7d)
- ✅ Learnings being applied to tasks
- ✅ A/B split ~20% control group
- ✅ Average confidence ≥0.5
- ✅ Improvement >10% over baseline

### Warning Signs
- ⚠️ No recent outcomes → Check task execution
- ⚠️ No learnings created → Run extraction (Phase 1.2)
- ⚠️ No learnings applied → Check PromptEnhancer (Phase 1.3)
- ⚠️ A/B split <15% or >25% → Adjust sampling rate
- ⚠️ Many low-confidence learnings → Review quality
- ⚠️ Negative improvement → System may be degrading performance

## Database Schema

### task_outcomes
Records every task completion with success/failure and context.

Key fields:
- `success`: Task succeeded (code merged, review passed, etc.)
- `agent_type`: programmer, researcher, writer, etc.
- `learning_disabled`: True for control group (no learnings applied)
- `applied_learnings`: Array of learning IDs used for this task
- `human_feedback`: Manual feedback from code review

### outcome_learnings
Extracted patterns and lessons from outcomes.

Key fields:
- `pattern_name`: Unique identifier (e.g., "missing_tests")
- `lesson_text`: What the agent should remember
- `confidence`: 0.0-1.0 based on success/failure ratio
- `success_count` / `failure_count`: Tracking effectiveness
- `is_active`: Whether this learning is currently being applied

## Next Steps

1. **Collect Data**: Execute tasks to generate outcomes
2. **Extract Patterns**: Run Phase 1.2 (LessonExtractor) to create learnings
3. **Apply Learnings**: Run Phase 1.3 (PromptEnhancer) to inject into prompts
4. **Measure Impact**: Use this Phase 1.4 metrics system to validate improvement

## Troubleshooting

**Tables don't exist:**
```bash
python migrations/create_learning_tables.py
```

**No data in stats:**
- System needs real task execution first
- Check `task_outcomes` table has rows
- Ensure tasks are completing (not just starting)

**Significance shows "unavailable":**
- Install scipy: `pip install scipy>=1.11.0`
- Or accept "unavailable" for small samples

**Health shows "degraded":**
- Review the `issues` array in response
- Address each issue based on warning message
- Re-run validation after fixes

## Reference

- [Full Design Doc](./agent-learning-system.md)
- [Phase 1.1 Handoff](./handoffs/learning-phase-1.1-database-tracking.md)
- [Phase 1.4 Handoff](./handoffs/learning-phase-1.4-metrics-validation.md)
