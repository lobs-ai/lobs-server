"""CLI tool to validate learning system health."""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

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
                TaskOutcome.created_at >= datetime.now(timezone.utc) - timedelta(days=7)
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
        total_ab = control + treatment
        control_pct = (control / total_ab * 100) if total_ab > 0 else 0
        print(f"   Control: {control} ({control_pct:.1f}%)")
        print(f"   Treatment: {treatment} ({100-control_pct:.1f}%)")
        
        if total_ab == 0:
            print("   ⚠️  No A/B test data yet")
        elif not (15 <= control_pct <= 25):
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
