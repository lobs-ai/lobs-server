"""Learning system stats and metrics API."""

import logging
from typing import Optional, List
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import TaskOutcome, OutcomeLearning

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/learning", tags=["learning"])


class LearningStats(BaseModel):
    """Learning system performance metrics."""
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


class LearningHealth(BaseModel):
    """Learning system health status."""
    status: str
    issues: List[str]
    recent_outcomes_24h: int
    recent_learnings_7d: int
    applied_learnings_7d: int
    low_confidence_active: int


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
    since_time = datetime.now(timezone.utc) - timedelta(days=since_days)
    
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
        raise HTTPException(404, f"No outcomes found for agent {agent} in last {since_days} days")
    
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


@router.get("/health", response_model=LearningHealth)
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
        TaskOutcome.created_at >= datetime.now(timezone.utc) - timedelta(hours=24)
    )
    if agent:
        recent_outcomes_query = recent_outcomes_query.where(TaskOutcome.agent_type == agent)
    
    result = await db.execute(recent_outcomes_query)
    recent_outcomes = result.scalar()
    
    if recent_outcomes == 0:
        issues.append("no_recent_outcomes")
    
    # Check: Are learnings being created?
    recent_learnings_query = select(func.count(OutcomeLearning.id)).where(
        OutcomeLearning.created_at >= datetime.now(timezone.utc) - timedelta(days=7)
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
            TaskOutcome.created_at >= datetime.now(timezone.utc) - timedelta(days=7),
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
    
    return LearningHealth(
        status=status,
        issues=issues,
        recent_outcomes_24h=recent_outcomes,
        recent_learnings_7d=recent_learnings,
        applied_learnings_7d=applied_count,
        low_confidence_active=low_conf_count,
    )
