"""Inbox processor - scans inbox threads for user responses and takes action.

Scans threads with triage_status='needs_response' for messages from user ('rafe'),
sends context to an LLM session for analysis, and executes the recommended actions:
- create_task: Create task(s) from the inbox item
- respond: Post a response to the thread (answer a question, acknowledge, etc.)
- escalate: Forward to Rafe on Discord for urgent items
- resolve: Mark thread as resolved
- pending: Mark as pending (acknowledged but not actionable yet)

Uses LLM-based analysis via Gateway sessions_spawn, same pattern as reflection processing.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    InboxItem,
    InboxThread as InboxThreadModel,
    InboxMessage as InboxMessageModel,
    Task,
    Project,
    AgentInitiative,
)
from app.orchestrator.config import GATEWAY_URL, GATEWAY_TOKEN, GATEWAY_SESSION_KEY
from app.orchestrator.initiative_decisions import InitiativeDecisionEngine

logger = logging.getLogger(__name__)

INBOX_ANALYSIS_PROMPT = """\
You are Lobs, the PM agent for a multi-agent AI assistant system. You are processing inbox thread responses from Rafe (the user/owner).

For each thread below, analyze Rafe's message(s) and determine what action to take.

## Context
- This is a personal AI assistant platform: lobs-server (FastAPI), Mission Control (macOS SwiftUI), lobs-mobile (iOS)
- Rafe is a busy grad student — be concise and action-oriented
- Inbox items may be: initiative escalations (tier-C needing approval), suggestions, reports, alerts
- When Rafe approves something, create a task. When he gives direction, incorporate it into the task.
- When Rafe asks a question, answer it based on available context.
- When Rafe says to break something into subtasks, create multiple tasks.

## Available Actions (per thread)
Return a JSON array with one object per thread:

```json
[
  {
    "thread_id": "<thread_id>",
    "doc_id": "<doc_id>",
    "action": "create_task" | "respond" | "resolve" | "pending",
    "response_message": "Message to post in the thread (required for all actions)",
    "tasks": [
      {
        "title": "Task title",
        "notes": "Detailed task description and context",
        "agent": "programmer|researcher|writer|reviewer|architect",
        "project_id": "lobs-server|lobs-dashboard|lobs-mobile|default"
      }
    ],
    "initiative_id": "<if this is a tier-C escalation, include the initiative ID to formally approve>"
  }
]
```

## Rules
- If Rafe says "yes", "do this", "approved", etc. → action=create_task, create the task(s)
- If Rafe says "break this down" or gives multi-part instructions → create multiple tasks
- If Rafe asks a question → action=respond, answer it
- If Rafe says "no", "skip", "reject" → action=resolve with acknowledgment
- If Rafe says "later", "not sure" → action=pending
- For tier-C escalations (title contains "APPROVAL NEEDED"), include the initiative_id from the content
- Always include a response_message summarizing what you did
- Be decisive — don't ask clarifying questions

## Threads to Process

{threads_context}

Return ONLY the JSON array. No explanation.
"""


class InboxProcessor:
    """Processes inbox threads using LLM analysis via Gateway spawn."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._project_ids: set[str] | None = None

    async def _get_valid_project_ids(self) -> set[str]:
        if self._project_ids is None:
            result = await self.db.execute(select(Project.id))
            self._project_ids = {row[0] for row in result.all()}
        return self._project_ids

    async def process_threads(self) -> dict[str, Any]:
        """Main entry point - scans and processes all eligible threads."""
        stats = {
            "threads_scanned": 0,
            "threads_processed": 0,
            "tasks_created": 0,
            "threads_resolved": 0,
            "threads_marked_pending": 0,
            "errors": 0,
        }

        try:
            eligible = await self._get_eligible_threads()
            stats["threads_scanned"] = len(eligible)

            if not eligible:
                return stats

            logger.info(f"[INBOX] Found {len(eligible)} thread(s) to process")

            # Build context for LLM
            threads_context = self._build_threads_context(eligible)

            # Get LLM analysis
            actions = await self._analyze_with_llm(threads_context)

            if not actions:
                logger.warning("[INBOX] LLM returned no actions")
                return stats

            # Execute actions
            thread_map = {t.id: (t, item, msgs) for t, item, msgs in eligible}

            for action in actions:
                thread_id = action.get("thread_id")
                if thread_id not in thread_map:
                    logger.warning(f"[INBOX] Unknown thread_id in LLM response: {thread_id}")
                    continue

                thread, inbox_item, messages = thread_map[thread_id]
                try:
                    result = await self._execute_action(action, thread, inbox_item, messages)
                    stats["threads_processed"] += 1
                    stats["tasks_created"] += result.get("tasks_created", 0)
                    stats["threads_resolved"] += result.get("resolved", 0)
                    stats["threads_marked_pending"] += result.get("marked_pending", 0)
                except Exception as e:
                    logger.error(f"[INBOX] Failed to execute action for thread {thread_id[:8]}: {e}", exc_info=True)
                    stats["errors"] += 1

            await self.db.commit()

            if stats["threads_processed"] > 0:
                logger.info(
                    f"[INBOX] Processed {stats['threads_processed']} thread(s): "
                    f"created {stats['tasks_created']} task(s), "
                    f"resolved {stats['threads_resolved']}, "
                    f"pending {stats['threads_marked_pending']}"
                )

        except Exception as e:
            logger.error(f"[INBOX] Error during processing: {e}", exc_info=True)
            stats["errors"] += 1
            await self.db.rollback()

        return stats

    async def _get_eligible_threads(self) -> list[tuple]:
        """Get threads that need processing (have new user messages without lobs response)."""
        result = await self.db.execute(
            select(InboxThreadModel).where(
                InboxThreadModel.triage_status == "needs_response"
            )
        )
        threads = result.scalars().all()

        eligible = []
        for thread in threads:
            msg_result = await self.db.execute(
                select(InboxMessageModel)
                .where(InboxMessageModel.thread_id == thread.id)
                .order_by(InboxMessageModel.created_at.asc())
            )
            messages = msg_result.scalars().all()

            # Must have at least one message from rafe
            rafe_msgs = [m for m in messages if m.author.lower() == "rafe"]
            if not rafe_msgs:
                continue

            # Check if the last message is from rafe (lobs hasn't responded yet)
            if messages and messages[-1].author.lower() != "rafe":
                continue

            inbox_item = await self.db.get(InboxItem, thread.doc_id)
            if not inbox_item:
                continue

            eligible.append((thread, inbox_item, messages))

        return eligible

    def _build_threads_context(self, eligible: list[tuple]) -> str:
        """Build context string for LLM prompt."""
        parts = []
        for thread, inbox_item, messages in eligible:
            # Extract initiative ID if present
            initiative_id = ""
            if inbox_item.summary and "tier_c_escalation:" in inbox_item.summary:
                initiative_id = inbox_item.summary.split("tier_c_escalation:")[-1].strip()

            part = (
                f"### Thread: {thread.id}\n"
                f"**Doc ID:** {inbox_item.id}\n"
                f"**Title:** {inbox_item.title}\n"
            )
            if initiative_id:
                part += f"**Initiative ID:** {initiative_id}\n"

            # Include inbox item content (truncated)
            content = (inbox_item.content or "")[:1500]
            part += f"**Content:**\n{content}\n\n"

            part += "**Thread messages:**\n"
            for msg in messages:
                part += f"- [{msg.author}]: {msg.text}\n"

            parts.append(part)

        return "\n---\n\n".join(parts)

    async def _analyze_with_llm(self, threads_context: str) -> list[dict[str, Any]]:
        """Send threads to LLM via Gateway sessions_spawn and get action recommendations."""
        prompt = INBOX_ANALYSIS_PROMPT.format(threads_context=threads_context)

        try:
            async with aiohttp.ClientSession() as session:
                parent_key = f"{GATEWAY_SESSION_KEY}-inbox-{uuid.uuid4().hex[:8]}"
                label = f"inbox-processor-{uuid.uuid4().hex[:8]}"

                resp = await session.post(
                    f"{GATEWAY_URL}/tools/invoke",
                    headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                    json={
                        "tool": "sessions_spawn",
                        "sessionKey": parent_key,
                        "args": {
                            "task": prompt,
                            "model": "sonnet",
                            "runTimeoutSeconds": 120,
                            "cleanup": "delete",
                            "label": label,
                        }
                    },
                    timeout=aiohttp.ClientTimeout(total=180),
                )

                data = await resp.json()

                if not data.get("ok"):
                    logger.error(f"[INBOX] Gateway spawn failed: {data}")
                    return []

                # Extract result from the spawn response
                result_text = data.get("result", {}).get("text", "")
                if not result_text:
                    # Try getting from session history
                    child_key = data.get("result", {}).get("childSessionKey", "")
                    if child_key:
                        result_text = await self._get_session_result(session, child_key)

                if not result_text:
                    logger.warning("[INBOX] No result from LLM session")
                    return []

                return self._parse_llm_response(result_text)

        except Exception as e:
            logger.error(f"[INBOX] LLM analysis failed: {e}", exc_info=True)
            return []

    async def _get_session_result(self, session: aiohttp.ClientSession, session_key: str) -> str:
        """Fetch result from a completed session's history."""
        try:
            resp = await session.post(
                f"{GATEWAY_URL}/tools/invoke",
                headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
                json={
                    "tool": "sessions_history",
                    "sessionKey": GATEWAY_SESSION_KEY,
                    "args": {
                        "sessionKey": session_key,
                        "limit": 2,
                    }
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
            data = await resp.json()
            messages = data.get("result", {}).get("messages", [])
            for msg in reversed(messages):
                if msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                return block.get("text", "")
                    elif isinstance(content, str):
                        return content
        except Exception as e:
            logger.error(f"[INBOX] Failed to fetch session result: {e}")
        return ""

    def _parse_llm_response(self, text: str) -> list[dict[str, Any]]:
        """Parse LLM JSON response, handling markdown code blocks."""
        text = text.strip()
        # Strip markdown code blocks
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]  # remove opening ```json
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
            logger.warning(f"[INBOX] LLM returned non-array: {type(result)}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"[INBOX] Failed to parse LLM response: {e}\nText: {text[:500]}")
            return []

    async def _execute_action(
        self,
        action: dict[str, Any],
        thread: InboxThreadModel,
        inbox_item: InboxItem,
        messages: list,
    ) -> dict[str, Any]:
        """Execute a single action from LLM analysis."""
        stats = {"tasks_created": 0, "resolved": 0, "marked_pending": 0}

        action_type = action.get("action", "resolve")
        response_msg = action.get("response_message", "")

        logger.info(
            f"[INBOX] Thread {thread.id[:8]} → {action_type} "
            f"(title: {inbox_item.title[:50]})"
        )

        if action_type == "create_task":
            tasks = action.get("tasks", [])
            if not tasks:
                # Fallback: create one task from the inbox item
                tasks = [{
                    "title": inbox_item.title[:80],
                    "notes": f"From inbox item. User direction: {messages[-1].text if messages else 'approved'}",
                    "agent": "programmer",
                    "project_id": "lobs-server",
                }]

            for task_spec in tasks:
                task = await self._create_task(task_spec, inbox_item)
                if task:
                    stats["tasks_created"] += 1

            # Handle initiative approval if present
            initiative_id = action.get("initiative_id")
            if initiative_id:
                await self._approve_initiative(initiative_id)

            thread.triage_status = "resolved"
            stats["resolved"] = 1

        elif action_type == "respond":
            # Just post the response, keep as needs_response or resolve
            thread.triage_status = action.get("new_status", "resolved")
            if thread.triage_status == "resolved":
                stats["resolved"] = 1

        elif action_type == "pending":
            thread.triage_status = "pending"
            stats["marked_pending"] = 1

        elif action_type == "resolve":
            thread.triage_status = "resolved"
            stats["resolved"] = 1

        # Post response message
        if response_msg:
            await self._add_thread_message(thread.id, "lobs", response_msg)

        thread.updated_at = datetime.now(timezone.utc)
        return stats

    async def _create_task(self, task_spec: dict[str, Any], inbox_item: InboxItem) -> Task | None:
        """Create a task from LLM-provided spec."""
        valid = await self._get_valid_project_ids()
        project_id = task_spec.get("project_id", "lobs-server")
        if project_id not in valid:
            project_id = "lobs-server"

        agent = (task_spec.get("agent") or "programmer").strip().lower()
        title = task_spec.get("title", inbox_item.title[:80])
        notes = task_spec.get("notes", "")

        # Add source context
        notes = (
            f"{notes}\n\n"
            f"---\n"
            f"*Created from inbox item: {inbox_item.title}*"
        )

        task = Task(
            id=str(uuid.uuid4()),
            title=title,
            status="active",
            work_state="not_started",
            owner="lobs",
            project_id=project_id,
            agent=agent,
            notes=notes,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.db.add(task)

        logger.info(
            f"[INBOX] Created task {task.id[:8]}: {title[:50]} "
            f"(project={project_id}, agent={agent})"
        )
        return task

    async def _approve_initiative(self, initiative_id: str) -> None:
        """Formally approve a tier-C initiative as Rafe."""
        try:
            initiative = await self.db.get(AgentInitiative, initiative_id)
            if not initiative:
                # Try prefix match
                result = await self.db.execute(
                    select(AgentInitiative).where(
                        AgentInitiative.id.startswith(initiative_id)
                    )
                )
                initiative = result.scalar_one_or_none()

            if not initiative:
                logger.warning(f"[INBOX] Initiative not found for approval: {initiative_id}")
                return

            if initiative.status == "awaiting_rafe":
                engine = InitiativeDecisionEngine(self.db)
                await engine.decide(
                    initiative,
                    decision="approve",
                    decided_by="rafe",
                    decision_summary="Approved by Rafe via inbox thread",
                )
                logger.info(f"[INBOX] Formally approved initiative {initiative_id[:8]} as Rafe")
        except Exception as e:
            logger.error(f"[INBOX] Failed to approve initiative {initiative_id[:8]}: {e}")

    async def _add_thread_message(self, thread_id: str, author: str, text: str) -> None:
        """Add a message to an inbox thread."""
        message = InboxMessageModel(
            id=str(uuid.uuid4()),
            thread_id=thread_id,
            author=author,
            text=text,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(message)
        logger.debug(f"[INBOX] Added message to thread {thread_id[:8]}: {text[:50]}")
