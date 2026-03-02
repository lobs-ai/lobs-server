"""Initiative decisioning and conversion to executable work.

Tier A/B: Lobs has full autonomy — approve, rescope, create tasks.
Tier C: Lobs should escalate to Rafe (instruction-based, not enforced in code).
        Lobs is instructed not to approve tier-C items directly — instead,
        escalate them so Rafe can review via inbox.
        The 'awaiting_rafe' status is still used for escalated items.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentCapability, AgentInitiative, AgentReflection, InboxItem, InitiativeDecisionRecord, Project, Task

logger = logging.getLogger(__name__)


class InitiativeDecisionEngine:
    """Applies Lobs decisions to initiatives and converts approved ideas into tasks."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def suggest_agent(self, initiative: AgentInitiative) -> str:
        agent, _has_capability_match = await self.suggest_agent_with_diagnostics(initiative)
        return agent

    async def suggest_agent_with_diagnostics(self, initiative: AgentInitiative) -> tuple[str, bool]:
        text = "\n".join(
            [
                initiative.title or "",
                initiative.description or "",
                initiative.category or "",
            ]
        ).lower()

        result = await self.db.execute(select(AgentCapability))
        rows = result.scalars().all()
        # inbox-responder should never be assigned to execute real work tasks
        raw_fallback = initiative.owner_agent or initiative.proposed_by_agent or "programmer"
        fallback = "programmer" if raw_fallback == "inbox-responder" else raw_fallback
        if not rows:
            return fallback, False

        scores: dict[str, float] = defaultdict(float)
        for row in rows:
            capability = (row.capability or "").strip().lower()
            if not capability:
                continue
            tokens = [tok for tok in capability.split() if tok]
            if not tokens:
                continue
            matches = sum(1 for tok in tokens if tok in text)
            if matches == 0:
                continue
            overlap = matches / len(tokens)
            scores[row.agent_type] += overlap * float(row.confidence or 0.5)

        # Remove inbox-responder from scoring — it's for triage, not task execution
        scores.pop("inbox-responder", None)

        if not scores:
            return fallback, False

        best_agent, _score = max(scores.items(), key=lambda item: item[1])
        return best_agent, True

    async def decide(
        self,
        initiative: AgentInitiative,
        *,
        decision: str,
        revised_title: str | None = None,
        revised_description: str | None = None,
        selected_agent: str | None = None,
        selected_project_id: str | None = None,
        decision_summary: str | None = None,
        learning_feedback: str | None = None,
        decided_by: str = "lobs",
        sweep_id: str | None = None,
        overlap_with_ids: list[str] | None = None,
        contradiction_with_ids: list[str] | None = None,
        capability_gap: bool = False,
    ) -> dict[str, Any]:
        decision = decision.strip().lower()
        decided_by = (decided_by or "lobs").strip().lower()

        # Normalize common aliases from clients/UI (approved/deferred/rejected).
        aliases = {
            "approved": "approve",
            "deferred": "defer",
            "rejected": "reject",
        }
        decision = aliases.get(decision, decision)

        # Validate decision values
        valid_decisions = {"approve", "defer", "reject", "escalate"}
        if decision not in valid_decisions:
            raise ValueError(f"decision must be one of: {', '.join(sorted(valid_decisions))}")

        # Only lobs and rafe can make decisions
        if decided_by not in {"lobs", "rafe"}:
            raise PermissionError("initiative decisions must be made by lobs or rafe")

        # Handle escalation: create Rafe inbox item, set status to awaiting_rafe
        if decision == "escalate":
            title = (revised_title or initiative.title or "Untitled initiative").strip()
            description = (revised_description or initiative.description or "").strip()

            initiative.selected_agent = selected_agent or await self.suggest_agent(initiative)
            initiative.selected_project_id = selected_project_id or await self._default_project_id()
            initiative.decision_summary = decision_summary
            initiative.status = "awaiting_rafe"
            initiative.approved_by = None  # Not yet approved

            # Create Rafe inbox item
            await self._create_rafe_inbox_item(
                initiative,
                lobs_recommendation=decision_summary or "Lobs recommends approval",
                revised_title=revised_title,
                revised_description=revised_description,
            )

            self.db.add(
                InitiativeDecisionRecord(
                    id=str(uuid.uuid4()),
                    initiative_id=initiative.id,
                    sweep_id=sweep_id,
                    decision="escalate",
                    decided_by=decided_by,
                    decision_summary=decision_summary,
                    overlap_with_ids=overlap_with_ids or [],
                    contradiction_with_ids=contradiction_with_ids or [],
                    capability_gap=bool(capability_gap),
                    source_reflection_ids=[initiative.source_reflection_id] if initiative.source_reflection_id else [],
                    task_id=None,
                )
            )

            # Commit with retry-on-lock logic (exponential backoff)
            for _attempt in range(5):
                try:
                    await self.db.commit()
                    break  # Success - exit the retry loop
                except Exception as _e:
                    if _attempt < 4:
                        await asyncio.sleep(_attempt * 0.5)
                        await self.db.rollback()
                    else:
                        logger.error("[ORCHESTRATOR] Failed to commit after 5 attempts: %s", _e, exc_info=True)
                        try:
                            await self.db.rollback()
                        except Exception:
                            pass
            return {
                "initiative_id": initiative.id,
                "status": "awaiting_rafe",
                "task_id": None,
                "selected_agent": initiative.selected_agent,
                "selected_project_id": initiative.selected_project_id,
                "escalated": True,
            }

        # Standard approve/defer/reject flow
        if decision == "approve":
            if initiative.status not in {"lobs_review", "proposed", "awaiting_rafe", "deferred"}:
                raise ValueError(f"initiative in status '{initiative.status}' is not approvable")
            # Rafe approval required for tier-C in awaiting_rafe
            if initiative.status == "awaiting_rafe" and decided_by != "rafe":
                raise PermissionError("only rafe can approve tier-C initiatives in awaiting_rafe status")

        title = (revised_title or initiative.title or "Untitled initiative").strip()
        description = (revised_description or initiative.description or "").strip()

        initiative.selected_agent = selected_agent or await self.suggest_agent(initiative)
        initiative.selected_project_id = selected_project_id or await self._default_project_id()
        initiative.approved_by = decided_by
        initiative.decision_summary = decision_summary
        initiative.learning_feedback = learning_feedback

        if decision == "approve":
            task = await self._create_task_from_initiative(
                initiative,
                title=title,
                description=description,
            )
            initiative.task_id = task.id
            initiative.status = "approved"
        elif decision == "defer":
            initiative.status = "deferred"
        else:
            initiative.status = "rejected"

        self.db.add(
            InitiativeDecisionRecord(
                id=str(uuid.uuid4()),
                initiative_id=initiative.id,
                sweep_id=sweep_id,
                decision=decision,
                decided_by=decided_by,
                decision_summary=decision_summary,
                overlap_with_ids=overlap_with_ids or [],
                contradiction_with_ids=contradiction_with_ids or [],
                capability_gap=bool(capability_gap),
                source_reflection_ids=[initiative.source_reflection_id] if initiative.source_reflection_id else [],
                task_id=initiative.task_id,
            )
        )

        await self._record_feedback_reflection(
            initiative,
            decision=decision,
            title=title,
            description=description,
            learning_feedback=learning_feedback,
            decision_summary=decision_summary,
            decided_by=decided_by,
        )

        # Commit with retry-on-lock logic (exponential backoff)
        for _attempt in range(5):
            try:
                await self.db.commit()
                break  # Success - exit the retry loop
            except Exception as _e:
                if _attempt < 4:
                    await asyncio.sleep(_attempt * 0.5)
                    await self.db.rollback()
                else:
                    logger.error("[ORCHESTRATOR] Failed to commit after 5 attempts: %s", _e, exc_info=True)
                    try:
                        await self.db.rollback()
                    except Exception:
                        pass

        return {
            "initiative_id": initiative.id,
            "status": initiative.status,
            "task_id": initiative.task_id,
            "selected_agent": initiative.selected_agent,
            "selected_project_id": initiative.selected_project_id,
        }

    async def _create_task_from_initiative(
        self,
        initiative: AgentInitiative,
        *,
        title: str,
        description: str,
    ) -> Task:
        project_id = initiative.selected_project_id or await self._default_project_id()
        if not project_id:
            raise ValueError("No project available for initiative task creation")

        notes = (
            f"Converted by Lobs from initiative {initiative.id}.\n"
            f"Source reflection: {initiative.source_reflection_id or 'unknown'}\n\n"
            f"Original proposer: {initiative.proposed_by_agent}\n"
            f"Category: {initiative.category}\n"
            f"Risk tier: {initiative.risk_tier}\n"
            f"Selected agent: {initiative.selected_agent}\n\n"
            f"{description}"
        )

        task = Task(
            id=str(uuid.uuid4()),
            title=title,
            status="active",
            work_state="not_started",
            project_id=project_id,
            notes=notes,
            owner="lobs",
            agent=initiative.selected_agent,
        )
        self.db.add(task)
        return task

    async def _create_rafe_inbox_item(
        self,
        initiative: AgentInitiative,
        *,
        lobs_recommendation: str,
        revised_title: str | None = None,
        revised_description: str | None = None,
    ) -> None:
        """Create an inbox item for Rafe to review a tier-C initiative."""
        title = revised_title or initiative.title or "Untitled"
        description = revised_description or initiative.description or "(no description)"

        content = (
            f"## 🚨 Tier-C Initiative — Needs Your Approval\n\n"
            f"**{title}**\n\n"
            f"**Proposed by:** {initiative.proposed_by_agent}\n"
            f"**Category:** {initiative.category}\n"
            f"**Risk tier:** {initiative.risk_tier}\n\n"
            f"**Description:**\n{description}\n\n"
            f"---\n\n"
            f"**Lobs's recommendation:** {lobs_recommendation}\n\n"
        )
        if revised_title and revised_title != initiative.title:
            content += f"**Lobs rescoped title:** {revised_title}\n"
        if revised_description and revised_description != initiative.description:
            content += f"**Lobs rescoped scope:** {revised_description}\n"

        content += (
            f"\n---\n\n"
            f"**To approve:** Tell Lobs to approve initiative `{initiative.id[:8]}`\n"
            f"**To reject:** Tell Lobs to reject initiative `{initiative.id[:8]}`\n"
        )

        item_id = str(uuid.uuid4())
        filename = f"escalation_{initiative.id[:8]}_{int(datetime.now(timezone.utc).timestamp())}.md"
        self.db.add(
            InboxItem(
                id=item_id,
                title=f"🚨 [APPROVAL NEEDED] {title[:70]}",
                filename=filename,
                relative_path=f"inbox/{filename}",
                content=content,
                is_read=False,
                summary=f"tier_c_escalation:{initiative.id}",
                modified_at=datetime.now(timezone.utc),
            )
        )
        logger.info(
            "[DECISION] Created Rafe inbox item for tier-C initiative: %s",
            initiative.id[:8],
        )

    async def _record_feedback_reflection(
        self,
        initiative: AgentInitiative,
        *,
        decision: str,
        title: str,
        description: str,
        learning_feedback: str | None,
        decision_summary: str | None,
        decided_by: str,
    ) -> None:
        feedback_payload = {
            "initiative_id": initiative.id,
            "decision": decision,
            "selected_agent": initiative.selected_agent,
            "selected_project_id": initiative.selected_project_id,
            "task_id": initiative.task_id,
            "title_used": title,
            "description_used": description,
            "decision_summary": decision_summary,
            "learning_feedback": learning_feedback,
            "decided_by": decided_by,
        }

        self.db.add(
            AgentReflection(
                id=str(uuid.uuid4()),
                agent_type=initiative.proposed_by_agent,
                reflection_type="initiative_feedback",
                status="completed",
                window_start=datetime.now(timezone.utc),
                window_end=datetime.now(timezone.utc),
                context_packet={"source": "lobs_decision"},
                result=feedback_payload,
                completed_at=datetime.now(timezone.utc),
            )
        )

    async def _default_project_id(self) -> str | None:
        preferred = await self.db.get(Project, "lobs-server")
        if preferred:
            return preferred.id
        result = await self.db.execute(select(Project).order_by(Project.created_at.asc()).limit(1))
        first = result.scalar_one_or_none()
        return first.id if first else None
