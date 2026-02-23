# Handoff: Learning System Phase 1.4 - Metrics & Validation

**Initiative:** agent-learning-system  
**Phase:** 1.4  
**To:** Programmer  
**Priority:** High  
**Estimated Complexity:** Small (2-3 days)  
**Depends On:** Phase 1.1, 1.2, 1.3 (all previous phases)

---

## Context

Complete the agent learning system Milestone 1 by adding observability, metrics tracking, and A/B test analysis. This phase proves the system works and measures improvement.

**Design Document:** `/Users/lobs/lobs-server/docs/agent-learning-system.md` (Section: Phase 1.4)

---

## Objectives

1. Add metrics API endpoint to track learning system performance
2. Implement A/B test analysis (control vs treatment comparison)
3. Add dashboard/monitoring support
4. Create validation tools to verify system health
5. Document measurement methodology

---

## Technical Specifications

### 1. Metrics API Endpoint

**File:** `app/routers/learning.py` (extend existing)

```python
"""Learning system stats and metrics."""

from typing import Optional
from datetime import datetime, timedelta
from pydantic import BaseModel
from sqlalchemy import select, func, and_

from app.models import TaskOutcome, OutcomeLearning


class LearningStats(BaseModel):
    agent_type: str
    baseline_acceptance_rate: float
    current_acceptance_rate: float
    improvement_pct: float
    learning_count: int
    active_learning_count: int
    avg_confidence: float
    application_rate: float
    tasks_with_outcomes: int
    tasks_with_learnings: int
    control_group_size: int
    treatment_group_size: int
    control_success_rate: float
    treatment_success_rate: float
    statistical_significance: Optional[str]


@router.get("/stats", response_model=LearningStats)
async def get_learning_stats(
    agent: str = "programmer",
    since_days: int = 14,
    db: AsyncSession = Depends(get_db),
):
    """
    Get learning system performance metrics.
    
    Compares control group (learning_disabled=True) vs treatment group
    to measure actual improvement.
    """
    since_time = datetime.utcnow() - timedelta(days=since_days)
    
    # Query outcomes in time window
    outcomes_query = select(TaskOutcome).where(
        and_(
            TaskOutcome.agent_type == agent,
            TaskOutcome.created_at >= since_time,
        )
    )
    result = await db.execute(outcomes_query)
    all_outcomes = result.scalars().all()
    
    if not all_outcomes:
        raise HTTPException(404, f"No outcomes found for agent {agent}")
    
    # Split into control vs treatment
    control = [o for o in all_outcomes if o.learning_disabled]
    treatment = [o for o in all_outcomes if not o.learning_disabled]
    
    # Calculate success rates
    control_success = sum(1 for o in control if o.success) / len(control) if control else 0.0
    treatment_success = sum(1 for o in treatment if o.success) / len(treatment) if treatment else 0.0
    
    # Calculate improvement
    if control_success > 0:
        improvement_pct = ((treatment_success - control_success) / control_success) * 100
    else:
        improvement_pct = 0.0
    
    # Statistical significance (Chi-squared test)
    significance = _calculate_significance(control, treatment)
    
    # Query learnings
    learnings_query = select(OutcomeLearning).where(
        OutcomeLearning.agent_type == agent
    )
    result = await db.execute(learnings_query)
    learnings = result.scalars().all()
    
    active_learnings = [l for l in learnings if l.is_active]
    avg_confidence = sum(l.confidence for l in active_learnings) / len(active_learnings) if active_learnings else 0.0
    
    # Application rate: how many treatment tasks got learnings
    tasks_with_learnings = sum(1 for o in treatment if o.applied_learnings and len(o.applied_learnings) > 0)
    application_rate = tasks_with_learnings / len(treatment) if treatment else 0.0
    
    return LearningStats(
        agent_type=agent,
        baseline_acceptance_rate=control_success,
        current_acceptance_rate=treatment_success,
        improvement_pct=improvement_pct,
        learning_count=len(learnings),
        active_learning_count=len(active_learnings),
        avg_confidence=avg_confidence,
        application_rate=application_rate,
        tasks_with_outcomes=len(all_outcomes),
        tasks_with_learnings=tasks_with_learnings,
        control_group_size=len(control),
        treatment_group_size=len(treatment),
        control_success_rate=control_success,
        treatment_success_rate=treatment_success,
        statistical_significance=significance,
    )


def _calculate_significance(control: List[TaskOutcome], treatment: List[TaskOutcome]) -> str:
    """
    Calculate statistical significance using Chi-squared test.
    
    Returns:
        "significant" if p < 0.05, "not_significant" otherwise, or "insufficient_data"
    """
    if len(control) < 10 or len(treatment) < 10:
        return "insufficient_data"
    
    try:
        from scipy.stats import chi2_contingency
        
        control_success = sum(1 for o in control if o.success)
        control_failure = len(control) - control_success
        treatment_success = sum(1 for o in treatment if o.success)
        treatment_failure = len(treatment) - treatment_success
        
        contingency_table = [
            [control_success, control_failure],
            [treatment_success, treatment_failure],
        ]
        
        chi2, p_value, dof, expected = chi2_contingency(contingency_table)
        
        if p_value < 0.05:
            return "significant"
        else:
            return "not_significant"
            
    except ImportError:
        logger.warning("scipy not installed, cannot calculate statistical significance")
        return "unavailable"
    except Exception as e:
        logger.error(f"Error calculating significance: {e}")
        return "error"


@router.get("/health", response_model=dict)
async def get_learning_health(
    agent: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Health check for learning system.
    
    Returns:
        Status indicators and any issues detected
    """
    issues = []
    
    # Check: Are outcomes being created?
    recent_outcomes_query = select(func.count(TaskOutcome.id)).where(
        TaskOutcome.created_at >= datetime.utcnow() - timedelta(hours=24)
    )
    if agent:
        recent_outcomes_query = recent_outcomes_query.where(TaskOutcome.agent_type == agent)
    
    result = await db.execute(recent_outcomes_query)
    recent_outcomes = result.scalar()
    
    if recent_outcomes == 0:
        issues.append("no_recent_outcomes")
    
    # Check: Are learnings being created?
    recent_learnings_query = select(func.count(OutcomeLearning.id)).where(
        OutcomeLearning.created_at >= datetime.utcnow() - timedelta(days=7)
    )
    if agent:
        recent_learnings_query = recent_learnings_query.where(OutcomeLearning.agent_type == agent)
    
    result = await db.execute(recent_learnings_query)
    recent_learnings = result.scalar()
    
    if recent_learnings == 0:
        issues.append("no_recent_learnings")
    
    # Check: Are learnings being applied?
    applied_query = select(func.count(TaskOutcome.id)).where(
        and_(
            TaskOutcome.created_at >= datetime.utcnow() - timedelta(days=7),
            TaskOutcome.applied_learnings.isnot(None),
        )
    )
    if agent:
        applied_query = applied_query.where(TaskOutcome.agent_type == agent)
    
    result = await db.execute(applied_query)
    applied_count = result.scalar()
    
    if applied_count == 0:
        issues.append("no_learnings_applied")
    
    # Check: Any low-confidence learnings that should be deactivated?
    low_conf_query = select(func.count(OutcomeLearning.id)).where(
        and_(
            OutcomeLearning.is_active == True,
            OutcomeLearning.confidence < 0.3,
            OutcomeLearning.failure_count > 3,
        )
    )
    if agent:
        low_conf_query = low_conf_query.where(OutcomeLearning.agent_type == agent)
    
    result = await db.execute(low_conf_query)
    low_conf_count = result.scalar()
    
    if low_conf_count > 0:
        issues.append(f"low_confidence_learnings:{low_conf_count}")
    
    status = "healthy" if not issues else "degraded"
    
    return {
        "status": status,
        "issues": issues,
        "recent_outcomes_24h": recent_outcomes,
        "recent_learnings_7d": recent_learnings,
        "applied_learnings_7d": applied_count,
        "low_confidence_active": low_conf_count,
    }
```

---

### 2. Validation CLI Tool

**File:** `app/cli/validate_learning.py`

```python
"""CLI tool to validate learning system health."""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.database import AsyncSessionLocal
from sqlalchemy import select, func, and_
from app.models import TaskOutcome, OutcomeLearning

import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def validate():
    """Run validation checks on learning system."""
    async with AsyncSessionLocal() as db:
        print("\n=== Learning System Validation ===\n")
        
        # Check 1: Outcome tracking coverage
        print("1. Outcome Tracking Coverage")
        outcome_count = await db.scalar(select(func.count(TaskOutcome.id)))
        print(f"   Total outcomes: {outcome_count}")
        
        recent = await db.scalar(
            select(func.count(TaskOutcome.id)).where(
                TaskOutcome.created_at >= datetime.utcnow() - timedelta(days=7)
            )
        )
        print(f"   Recent (7d): {recent}")
        
        if outcome_count < 10:
            print("   ⚠️  Low outcome count - system needs more data")
        else:
            print("   ✅ Sufficient outcome data")
        
        # Check 2: Learning creation
        print("\n2. Learning Creation")
        learning_count = await db.scalar(select(func.count(OutcomeLearning.id)))
        active_count = await db.scalar(
            select(func.count(OutcomeLearning.id)).where(
                OutcomeLearning.is_active == True
            )
        )
        print(f"   Total learnings: {learning_count}")
        print(f"   Active: {active_count}")
        
        if learning_count == 0:
            print("   ⚠️  No learnings created - run extraction")
        else:
            print("   ✅ Learnings exist")
        
        # Check 3: Learning application
        print("\n3. Learning Application")
        applied = await db.scalar(
            select(func.count(TaskOutcome.id)).where(
                TaskOutcome.applied_learnings.isnot(None)
            )
        )
        application_rate = (applied / outcome_count * 100) if outcome_count > 0 else 0
        print(f"   Tasks with learnings: {applied}/{outcome_count} ({application_rate:.1f}%)")
        
        if application_rate < 30:
            print("   ⚠️  Low application rate - check PromptEnhancer")
        else:
            print("   ✅ Good application rate")
        
        # Check 4: A/B test balance
        print("\n4. A/B Test Balance")
        control = await db.scalar(
            select(func.count(TaskOutcome.id)).where(
                TaskOutcome.learning_disabled == True
            )
        )
        treatment = await db.scalar(
            select(func.count(TaskOutcome.id)).where(
                and_(
                    TaskOutcome.learning_disabled == False,
                    TaskOutcome.learning_disabled.isnot(None),
                )
            )
        )
        control_pct = (control / (control + treatment) * 100) if (control + treatment) > 0 else 0
        print(f"   Control: {control} ({control_pct:.1f}%)")
        print(f"   Treatment: {treatment} ({100-control_pct:.1f}%)")
        
        if not (15 <= control_pct <= 25):
            print("   ⚠️  A/B split off target (should be ~20%)")
        else:
            print("   ✅ A/B split balanced")
        
        # Check 5: Learning confidence health
        print("\n5. Learning Confidence Health")
        stmt = select(OutcomeLearning).where(OutcomeLearning.is_active == True)
        result = await db.execute(stmt)
        active_learnings = result.scalars().all()
        
        if active_learnings:
            avg_conf = sum(l.confidence for l in active_learnings) / len(active_learnings)
            low_conf = [l for l in active_learnings if l.confidence < 0.3]
            high_conf = [l for l in active_learnings if l.confidence >= 0.7]
            
            print(f"   Avg confidence: {avg_conf:.2f}")
            print(f"   High confidence (≥0.7): {len(high_conf)}")
            print(f"   Low confidence (<0.3): {len(low_conf)}")
            
            if len(low_conf) > len(active_learnings) / 2:
                print("   ⚠️  Many low-confidence learnings - review quality")
            else:
                print("   ✅ Confidence levels healthy")
        else:
            print("   ⚠️  No active learnings")
        
        # Check 6: Success rates
        print("\n6. Success Rates")
        control_outcomes = await db.execute(
            select(TaskOutcome).where(TaskOutcome.learning_disabled == True)
        )
        control_list = control_outcomes.scalars().all()
        
        treatment_outcomes = await db.execute(
            select(TaskOutcome).where(TaskOutcome.learning_disabled == False)
        )
        treatment_list = treatment_outcomes.scalars().all()
        
        if control_list and treatment_list:
            control_success = sum(1 for o in control_list if o.success) / len(control_list)
            treatment_success = sum(1 for o in treatment_list if o.success) / len(treatment_list)
            improvement = ((treatment_success - control_success) / control_success * 100) if control_success > 0 else 0
            
            print(f"   Control success: {control_success:.1%}")
            print(f"   Treatment success: {treatment_success:.1%}")
            print(f"   Improvement: {improvement:+.1f}%")
            
            if improvement < 0:
                print("   ⚠️  Negative improvement - system may be hurting performance")
            elif improvement < 5:
                print("   ⚠️  Low improvement - needs more time or tuning")
            else:
                print("   ✅ Positive improvement detected")
        else:
            print("   ⚠️  Insufficient data for A/B comparison")
        
        print("\n=== Validation Complete ===\n")


if __name__ == "__main__":
    asyncio.run(validate())
```

---

### 3. Scheduled Learning Extraction Job

**File:** `app/orchestrator/learning_scheduler.py` (optional)

```python
"""Scheduled jobs for learning system maintenance."""

import asyncio
import logging
from datetime import datetime

from app.database import AsyncSessionLocal
from app.orchestrator.lesson_extractor import LessonExtractor

logger = logging.getLogger(__name__)


async def run_learning_extraction():
    """
    Run learning extraction from recent outcomes.
    
    Should be called periodically (hourly or daily).
    """
    try:
        async with AsyncSessionLocal() as db:
            logger.info("[LEARNING] Starting scheduled extraction")
            
            # Extract for all agent types
            for agent_type in ["programmer", "researcher", "writer"]:
                learnings = await LessonExtractor.extract_recent(
                    db=db,
                    agent_type=agent_type,
                    since_hours=24,
                )
                logger.info(
                    f"[LEARNING] Extracted {len(learnings)} learnings for {agent_type}"
                )
    
    except Exception as e:
        logger.error(f"[LEARNING] Extraction job failed: {e}", exc_info=True)


async def run_learning_maintenance():
    """
    Maintenance: deactivate low-performing learnings, merge duplicates, etc.
    
    Run daily or weekly.
    """
    try:
        async with AsyncSessionLocal() as db:
            logger.info("[LEARNING] Starting scheduled maintenance")
            
            # TODO: Implement maintenance tasks
            # - Deactivate learnings with very low confidence
            # - Merge duplicate learnings
            # - Archive old learnings
            # - Recompute confidence scores
            
    except Exception as e:
        logger.error(f"[LEARNING] Maintenance job failed: {e}", exc_info=True)


# Integration with orchestrator engine (optional)
# Add to orchestrator/engine.py polling loop:
#
# last_extraction = datetime.min
# EXTRACTION_INTERVAL = timedelta(hours=1)
#
# while True:
#     # ... existing polling logic ...
#     
#     if datetime.now() - last_extraction > EXTRACTION_INTERVAL:
#         await run_learning_extraction()
#         last_extraction = datetime.now()
```

---

### 4. Documentation Update

**File:** Update `docs/agent-learning-system.md` with actual results

Add a new section after implementation:

```markdown
## Milestone 1 Results (Updated: YYYY-MM-DD)

### Deployment Timeline
- Phase 1.1 (Database): Completed YYYY-MM-DD
- Phase 1.2 (Extraction): Completed YYYY-MM-DD
- Phase 1.3 (Enhancement): Completed YYYY-MM-DD
- Phase 1.4 (Metrics): Completed YYYY-MM-DD

### Performance Metrics (2 weeks post-deployment)

**Programmer Agent:**
- Baseline (control) acceptance rate: XX%
- Treatment acceptance rate: XX%
- Improvement: +XX%
- Statistical significance: [significant/not_significant]
- Active learnings: N patterns
- Application rate: XX% of tasks

**Key Learnings Created:**
1. [missing_tests] - confidence: 0.XX, success: XX/XX
2. [unclear_names] - confidence: 0.XX, success: XX/XX
3. [missing_validation] - confidence: 0.XX, success: XX/XX

### Issues Encountered
- [List any problems and how they were resolved]

### Next Steps
- [Based on results, what should be done next?]
```

---

## Testing Requirements

### Unit Tests

**File:** `tests/test_learning_stats.py`

```python
"""Tests for learning stats API."""

import pytest
from httpx import AsyncClient
from app.models import TaskOutcome, OutcomeLearning

@pytest.mark.asyncio
async def test_stats_endpoint(client: AsyncClient, db):
    """Test GET /api/learning/stats."""
    # Create test data
    for i in range(20):
        outcome = TaskOutcome(
            id=f"outcome-{i}",
            task_id=f"task-{i}",
            agent_type="programmer",
            success=i % 2 == 0,  # 50% success
            learning_disabled=i % 5 == 0,  # 20% control group
        )
        db.add(outcome)
    await db.commit()
    
    response = await client.get("/api/learning/stats?agent=programmer")
    assert response.status_code == 200
    
    data = response.json()
    assert data["agent_type"] == "programmer"
    assert data["control_group_size"] == 4  # 20% of 20
    assert data["treatment_group_size"] == 16
    assert 0 <= data["control_success_rate"] <= 1
    assert 0 <= data["treatment_success_rate"] <= 1

@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient, db):
    """Test GET /api/learning/health."""
    response = await client.get("/api/learning/health")
    assert response.status_code == 200
    
    data = response.json()
    assert "status" in data
    assert "issues" in data
    assert data["status"] in ["healthy", "degraded"]
```

### Integration Tests

```python
"""Integration test for full metrics flow."""

@pytest.mark.asyncio
async def test_metrics_reflect_improvement(client: AsyncClient, db):
    """Test that metrics correctly show improvement from learnings."""
    
    # Phase 1: Control group performs at baseline
    for i in range(10):
        outcome = TaskOutcome(
            id=f"control-{i}",
            task_id=f"task-{i}",
            agent_type="programmer",
            success=i < 6,  # 60% success
            learning_disabled=True,
        )
        db.add(outcome)
    
    # Phase 2: Treatment group performs better
    for i in range(10):
        outcome = TaskOutcome(
            id=f"treatment-{i}",
            task_id=f"task-{i+10}",
            agent_type="programmer",
            success=i < 8,  # 80% success
            learning_disabled=False,
            applied_learnings=["learning-1"],
        )
        db.add(outcome)
    
    await db.commit()
    
    # Check stats
    response = await client.get("/api/learning/stats?agent=programmer")
    data = response.json()
    
    assert data["control_success_rate"] == 0.6
    assert data["treatment_success_rate"] == 0.8
    assert abs(data["improvement_pct"] - 33.33) < 1  # (0.8-0.6)/0.6 * 100
```

---

## Acceptance Criteria

- ✅ GET `/api/learning/stats` endpoint returns all required metrics
- ✅ Statistical significance calculated using Chi-squared test
- ✅ GET `/api/learning/health` endpoint detects common issues
- ✅ Validation CLI tool (`validate_learning.py`) runs all checks
- ✅ Documentation includes placeholder for actual results
- ✅ Metrics show >10% improvement in code review acceptance (or document why not)
- ✅ A/B test balance is 15-25% control group
- ✅ Application rate is >50% for eligible tasks
- ✅ System logs metrics to orchestrator dashboard
- ✅ Unit tests pass
- ✅ Integration tests pass
- ✅ Manual validation: run validation tool and verify output

---

## Success Criteria (Milestone 1 Complete)

This phase completes Milestone 1. Success means:

1. **✅ System is functional:** All components work end-to-end
2. **✅ Measurable improvement:** >10% improvement in programmer acceptance rate
3. **✅ Statistical validity:** p < 0.05 for A/B test results
4. **✅ Good coverage:** >80% of programmer tasks have outcomes
5. **✅ High application rate:** >50% of treatment tasks get learnings
6. **✅ System stability:** No performance regressions or crashes

If any criterion is not met, document the blocker and create follow-up tasks.

---

## Rollout Plan

1. **Week 1:** Deploy to dev environment, validate with test data
2. **Week 2:** Deploy to production with `LEARNING_INJECTION_ENABLED=false`
3. **Week 3:** Enable for 20% of tasks (flip feature flag to true)
4. **Week 4:** Monitor metrics, collect data
5. **Week 5:** Analyze results, tune if needed
6. **Week 6:** Full rollout (100% of non-control tasks)

---

## Monitoring Checklist

Daily:
- [ ] Check `/api/learning/health` - any issues?
- [ ] Run `python app/cli/validate_learning.py` - all green?
- [ ] Check orchestrator logs for `[LEARNING]` errors

Weekly:
- [ ] Review `/api/learning/stats` - is improvement holding?
- [ ] Review active learnings - any to deactivate?
- [ ] Check A/B balance - still ~20% control?

Monthly:
- [ ] Deep analysis of patterns - which learnings help most?
- [ ] Review low-confidence learnings - deactivate or improve?
- [ ] Plan next milestone (strategies, researcher, etc.)

---

## Dependencies

- **Required:** Phase 1.1, 1.2, 1.3 all complete
- **Optional:** `scipy` package for statistical significance (add to requirements.txt)

---

## Notes

- If improvement is negative or insignificant after 2 weeks, pause rollout and investigate
- Keep control group permanent (don't remove) to track long-term trends
- Consider adding dashboard visualization (Grafana, etc.) for metrics

---

## Questions?

Contact architect or reference design doc: `/Users/lobs/lobs-server/docs/agent-learning-system.md`
