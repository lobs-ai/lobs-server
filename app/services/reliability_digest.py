"""Reliability digest generator - daily summary of system health and issues."""

from datetime import datetime, timedelta
from typing import Dict, List, Any
from collections import defaultdict
from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    WorkerRun,
    Task,
    AgentInitiative,
    InboxItem,
)


class ReliabilityDigestGenerator:
    """Generate actionable reliability digests from system state."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def generate_digest(self, hours: int = 24) -> Dict[str, Any]:
        """
        Generate a digest of reliability issues from the last N hours.
        
        Returns a structured dict with:
        - recurring_failures: Failure patterns that repeat
        - blocked_tasks: Tasks stuck in blocked state
        - at_risk_initiatives: Initiatives with concerning signals
        - summary_stats: Overall health metrics
        """
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)

        # Gather data in parallel
        recurring_failures = await self._find_recurring_failures(cutoff_time)
        blocked_tasks = await self._find_blocked_tasks()
        at_risk_initiatives = await self._find_at_risk_initiatives()
        summary_stats = await self._calculate_summary_stats(cutoff_time)

        return {
            "recurring_failures": recurring_failures,
            "blocked_tasks": blocked_tasks,
            "at_risk_initiatives": at_risk_initiatives,
            "summary_stats": summary_stats,
            "generated_at": datetime.utcnow().isoformat(),
            "hours_covered": hours,
        }

    async def _find_recurring_failures(self, cutoff_time: datetime) -> List[Dict[str, Any]]:
        """
        Identify failure patterns that have repeated in the time window.
        
        A "recurring failure" is a failure reason that appears 2+ times.
        """
        # Get all failed runs since cutoff
        query = select(WorkerRun).where(
            and_(
                WorkerRun.started_at >= cutoff_time,
                WorkerRun.succeeded == False,
                WorkerRun.summary.isnot(None),
            )
        ).order_by(WorkerRun.started_at.desc())

        result = await self.db.execute(query)
        failed_runs = result.scalars().all()

        # Group by failure signature (summary text)
        failure_groups = defaultdict(list)
        for run in failed_runs:
            # Use summary as the grouping key (could be enhanced with NLP)
            signature = self._extract_failure_signature(run.summary or "")
            failure_groups[signature].append({
                "task_id": run.task_id,
                "worker_id": run.worker_id,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "summary": run.summary,
                "model": run.model,
            })

        # Filter to only recurring (2+ occurrences) and sort by impact
        recurring = []
        for signature, runs in failure_groups.items():
            if len(runs) >= 2:
                recurring.append({
                    "failure_pattern": signature,
                    "occurrence_count": len(runs),
                    "first_seen": runs[-1]["started_at"],
                    "last_seen": runs[0]["started_at"],
                    "affected_tasks": list({r["task_id"] for r in runs if r["task_id"]}),
                    "sample_runs": runs[:3],  # Show up to 3 examples
                })

        # Sort by occurrence count descending
        recurring.sort(key=lambda x: x["occurrence_count"], reverse=True)
        return recurring

    async def _find_blocked_tasks(self) -> List[Dict[str, Any]]:
        """
        Find tasks currently in blocked state or with high escalation tier.
        """
        query = select(Task).where(
            or_(
                Task.work_state == "blocked",
                Task.escalation_tier >= 3,  # Diagnostic or human intervention needed
            )
        ).order_by(Task.escalation_tier.desc(), Task.updated_at.desc())

        result = await self.db.execute(query)
        tasks = result.scalars().all()

        blocked = []
        for task in tasks:
            blocked.append({
                "task_id": task.id,
                "title": task.title,
                "work_state": task.work_state,
                "escalation_tier": task.escalation_tier,
                "retry_count": task.retry_count,
                "failure_reason": task.failure_reason,
                "blocked_by": task.blocked_by,
                "updated_at": task.updated_at.isoformat(),
                "agent": task.agent,
            })

        return blocked

    async def _find_at_risk_initiatives(self) -> List[Dict[str, Any]]:
        """
        Find initiatives with concerning signals:
        - Approved but no task created after 48h
        - Task created but stuck/failed
        - Risk tier C or higher with pending status
        """
        # Get initiatives approved recently but without tasks
        two_days_ago = datetime.utcnow() - timedelta(days=2)
        
        query = select(AgentInitiative).where(
            or_(
                # Approved but no task after 48h
                and_(
                    AgentInitiative.status == "approved",
                    AgentInitiative.task_id.is_(None),
                    AgentInitiative.updated_at < two_days_ago,
                ),
                # High-risk initiatives still pending
                and_(
                    AgentInitiative.risk_tier.in_(["C", "D"]),
                    AgentInitiative.status == "proposed",
                ),
            )
        ).order_by(AgentInitiative.risk_tier.desc(), AgentInitiative.updated_at.desc())

        result = await self.db.execute(query)
        initiatives = result.scalars().all()

        at_risk = []
        for init in initiatives:
            risk_reason = ""
            if init.status == "approved" and not init.task_id:
                risk_reason = "Approved but no task created (48h+)"
            elif init.risk_tier in ["C", "D"]:
                risk_reason = f"High risk tier ({init.risk_tier}) still pending"

            at_risk.append({
                "initiative_id": init.id,
                "title": init.title,
                "category": init.category,
                "risk_tier": init.risk_tier,
                "status": init.status,
                "proposed_by": init.proposed_by_agent,
                "selected_agent": init.selected_agent,
                "task_id": init.task_id,
                "risk_reason": risk_reason,
                "updated_at": init.updated_at.isoformat(),
            })

        return at_risk

    async def _calculate_summary_stats(self, cutoff_time: datetime) -> Dict[str, Any]:
        """Calculate high-level health metrics."""
        # Total runs in period
        total_runs_query = select(func.count(WorkerRun.id)).where(
            WorkerRun.started_at >= cutoff_time
        )
        total_runs = (await self.db.execute(total_runs_query)).scalar() or 0

        # Failed runs in period
        failed_runs_query = select(func.count(WorkerRun.id)).where(
            and_(
                WorkerRun.started_at >= cutoff_time,
                WorkerRun.succeeded == False,
            )
        )
        failed_runs = (await self.db.execute(failed_runs_query)).scalar() or 0

        # Currently blocked tasks
        blocked_tasks_query = select(func.count(Task.id)).where(
            Task.work_state == "blocked"
        )
        blocked_count = (await self.db.execute(blocked_tasks_query)).scalar() or 0

        # High escalation tasks
        escalated_tasks_query = select(func.count(Task.id)).where(
            Task.escalation_tier >= 3
        )
        escalated_count = (await self.db.execute(escalated_tasks_query)).scalar() or 0

        # Pending initiatives
        pending_initiatives_query = select(func.count(AgentInitiative.id)).where(
            AgentInitiative.status == "proposed"
        )
        pending_initiatives = (await self.db.execute(pending_initiatives_query)).scalar() or 0

        success_rate = (
            ((total_runs - failed_runs) / total_runs * 100) if total_runs > 0 else 100.0
        )

        return {
            "total_runs": total_runs,
            "failed_runs": failed_runs,
            "success_rate_pct": round(success_rate, 1),
            "blocked_tasks": blocked_count,
            "escalated_tasks": escalated_count,
            "pending_initiatives": pending_initiatives,
        }

    def _extract_failure_signature(self, summary: str) -> str:
        """
        Extract a normalized failure signature from a summary.
        
        Simple implementation: lowercase, truncate to first 100 chars.
        Could be enhanced with NLP to cluster similar errors.
        """
        if not summary:
            return "unknown_failure"
        
        # Normalize and truncate
        normalized = summary.lower().strip()[:100]
        
        # Common patterns to group by
        if "blocked:" in normalized or "blocked by" in normalized:
            return "task_blocked"
        if "timeout" in normalized or "timed out" in normalized:
            return "timeout"
        if "missing" in normalized or "not found" in normalized:
            return "missing_dependency"
        if "permission" in normalized or "denied" in normalized:
            return "permission_error"
        
        return normalized


def format_digest_markdown(digest: Dict[str, Any]) -> str:
    """
    Format digest data as concise, actionable markdown.
    
    Designed for inbox delivery - emphasizes deltas and action items.
    """
    lines = []
    
    # Header
    lines.append("# 🔍 Reliability Digest")
    lines.append(f"**Period:** Last {digest['hours_covered']}h")
    lines.append(f"**Generated:** {digest['generated_at']}")
    lines.append("")

    # Summary stats
    stats = digest["summary_stats"]
    lines.append("## 📊 Summary")
    lines.append(f"- **Runs:** {stats['total_runs']} total, {stats['failed_runs']} failed ({stats['success_rate_pct']}% success)")
    lines.append(f"- **Blocked tasks:** {stats['blocked_tasks']}")
    lines.append(f"- **Escalated tasks:** {stats['escalated_tasks']}")
    lines.append(f"- **Pending initiatives:** {stats['pending_initiatives']}")
    lines.append("")

    # Recurring failures
    failures = digest["recurring_failures"]
    if failures:
        lines.append("## 🔁 Recurring Failures")
        for f in failures[:5]:  # Top 5
            lines.append(f"### {f['failure_pattern']}")
            lines.append(f"- **Occurrences:** {f['occurrence_count']}")
            lines.append(f"- **First seen:** {f['first_seen']}")
            lines.append(f"- **Affected tasks:** {', '.join(f['affected_tasks'][:5])}")
            if f["sample_runs"]:
                lines.append(f"- **Example:** {f['sample_runs'][0]['summary'][:100]}")
            lines.append("")
    else:
        lines.append("## ✅ No Recurring Failures")
        lines.append("")

    # Blocked tasks
    blocked = digest["blocked_tasks"]
    if blocked:
        lines.append("## 🚫 Blocked Tasks")
        for task in blocked[:10]:  # Top 10
            lines.append(f"### [{task['task_id'][:8]}] {task['title']}")
            lines.append(f"- **State:** {task['work_state']} | Escalation: {task['escalation_tier']} | Retries: {task['retry_count']}")
            if task["failure_reason"]:
                lines.append(f"- **Reason:** {task['failure_reason'][:150]}")
            if task["blocked_by"]:
                lines.append(f"- **Blocked by:** {task['blocked_by']}")
            lines.append("")
    else:
        lines.append("## ✅ No Blocked Tasks")
        lines.append("")

    # At-risk initiatives
    at_risk = digest["at_risk_initiatives"]
    if at_risk:
        lines.append("## ⚠️ At-Risk Initiatives")
        for init in at_risk[:5]:  # Top 5
            lines.append(f"### [{init['initiative_id'][:8]}] {init['title']}")
            lines.append(f"- **Risk:** {init['risk_tier']} | Category: {init['category']}")
            lines.append(f"- **Reason:** {init['risk_reason']}")
            lines.append(f"- **Proposed by:** {init['proposed_by']} | Selected: {init['selected_agent'] or 'none'}")
            lines.append("")
    else:
        lines.append("## ✅ No At-Risk Initiatives")
        lines.append("")

    # Footer with action prompt
    if failures or blocked or at_risk:
        lines.append("---")
        lines.append("**Action required:** Review and triage flagged items above.")
    
    return "\n".join(lines)


async def send_digest_to_inbox(db: AsyncSession, digest: Dict[str, Any]) -> str:
    """
    Send the digest to the inbox.
    
    Returns the created inbox item ID.
    """
    from uuid import uuid4
    
    markdown = format_digest_markdown(digest)
    
    # Create inbox item
    item = InboxItem(
        id=str(uuid4()),
        title=f"Reliability Digest ({digest['hours_covered']}h)",
        content=markdown,
        modified_at=datetime.utcnow(),
        is_read=False,
        summary=f"System health summary: {digest['summary_stats']['failed_runs']} failures, {digest['summary_stats']['blocked_tasks']} blocked tasks",
    )
    
    db.add(item)
    await db.flush()
    
    return item.id
