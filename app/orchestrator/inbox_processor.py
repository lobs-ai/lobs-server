"""Inbox processor - scans inbox threads for user responses and takes action.

Scans threads with triage_status='needs_response' for messages from user ('rafe'),
analyzes the response, and determines actions:
- create_task: Create a single task from the inbox item
- resolve: Mark thread as resolved (no further action needed)
- pending: Mark as pending (acknowledged but not actionable yet)

Uses deterministic server-side analysis so control flow stays inside lobs-server.
"""

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    InboxItem,
    InboxThread as InboxThreadModel,
    InboxMessage as InboxMessageModel,
    Task,
    Project,
)

logger = logging.getLogger(__name__)


# Known project IDs and their keywords for matching
PROJECT_KEYWORDS = {
    "lobs-server": ["lobs-server", "server", "fastapi", "api", "backend"],
    "lobs-dashboard": ["lobs-dashboard", "mission control", "dashboard", "macos app", "swiftui"],
    "lobs-mobile": ["lobs-mobile", "mobile", "ios"],
    "flock": ["flock", "social", "event planning"],
    "prairielearn": ["prairielearn", "prairie"],
}


class InboxProcessor:
    """Processes inbox threads for user responses and takes automated actions."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._project_ids: set[str] | None = None

    async def _get_valid_project_ids(self) -> set[str]:
        """Cache valid project IDs from DB."""
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
            eligible_threads = await self._get_eligible_threads()
            stats["threads_scanned"] = len(eligible_threads)

            if not eligible_threads:
                return stats

            logger.info(f"[INBOX] Found {len(eligible_threads)} thread(s) to process")

            for thread, inbox_item, messages in eligible_threads:
                try:
                    result = await self._process_thread(thread, inbox_item, messages)
                    stats["threads_processed"] += 1
                    stats["tasks_created"] += result.get("tasks_created", 0)
                    stats["threads_resolved"] += result.get("resolved", 0)
                    stats["threads_marked_pending"] += result.get("marked_pending", 0)
                except Exception as e:
                    logger.error(f"[INBOX] Failed to process thread {thread.id[:8]}: {e}", exc_info=True)
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
        """Get threads that need processing (have new user messages)."""
        result = await self.db.execute(
            select(InboxThreadModel).where(
                InboxThreadModel.triage_status == "needs_response"
            )
        )
        threads = result.scalars().all()

        eligible = []
        for thread in threads:
            # Load messages
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

            # Check for new messages since last processing
            last_processed = getattr(thread, "last_processed_message_id", None)
            if last_processed:
                msg_ids = [m.id for m in messages]
                if last_processed in msg_ids:
                    idx = msg_ids.index(last_processed)
                    new_msgs = messages[idx + 1:]
                    new_rafe = [m for m in new_msgs if m.author.lower() == "rafe"]
                    if not new_rafe:
                        continue

            # Load inbox item
            inbox_item = await self.db.get(InboxItem, thread.doc_id)
            if not inbox_item:
                continue

            eligible.append((thread, inbox_item, messages))

        return eligible

    async def _process_thread(
        self,
        thread: InboxThreadModel,
        inbox_item: InboxItem,
        messages: list,
    ) -> dict[str, Any]:
        """Process a single thread based on user's response."""
        stats = {"tasks_created": 0, "resolved": 0, "marked_pending": 0}

        # Get latest rafe message
        rafe_msgs = [m for m in messages if m.author.lower() == "rafe"]
        if not rafe_msgs:
            return stats
        latest = rafe_msgs[-1]

        # Analyze response using deterministic server rules
        action = await self._analyze_response(latest.text, inbox_item)

        logger.info(
            f"[INBOX] Thread {thread.id[:8]} → {action['type']} "
            f"(title: {inbox_item.title[:50]})"
        )

        response_message = action.get("response_message")

        if action["type"] == "create_task":
            # Use action-provided task fields if available
            task_title = action.get("task_title") or inbox_item.title
            task_notes = action.get("task_notes")
            agent_type = action.get("agent_type")
            project_id = action.get("project_id", "default")
            
            task = await self._create_task_from_action(
                inbox_item=inbox_item,
                user_message=latest.text,
                task_title=task_title,
                task_notes=task_notes,
                agent_type=agent_type,
                project_id=project_id,
            )
            if task:
                stats["tasks_created"] = 1
                if not response_message:
                    agent_name = task.agent or "programmer"
                    response_message = f"✅ Created task: {task.title} (assigned to {agent_name})"
            thread.triage_status = "resolved"
            stats["resolved"] = 1

        elif action["type"] == "resolve":
            thread.triage_status = "resolved"
            stats["resolved"] = 1
            if not response_message:
                response_message = "👍 Resolved — no action needed"

        elif action["type"] == "pending":
            thread.triage_status = "pending"
            stats["marked_pending"] = 1
            if not response_message:
                response_message = "⏸️ Marked as pending"

        # Add response message to thread
        if response_message:
            await self._add_thread_message(thread.id, "lobs", response_message)

        # Track last processed message
        if hasattr(thread, "last_processed_message_id"):
            thread.last_processed_message_id = latest.id
        thread.updated_at = datetime.now(timezone.utc)

        return stats

    async def _analyze_response(
        self, user_message: str, inbox_item: InboxItem
    ) -> dict[str, Any]:
        """
        Analyze user response with deterministic rules.
        """
        logger.debug("[INBOX] Using deterministic response analysis")
        return self._analyze_response_fallback(user_message, inbox_item)

    def _analyze_response_fallback(self, user_message: str, inbox_item: InboxItem) -> dict[str, Any]:
        """Determine action from user's response. More permissive pattern matching."""
        msg = user_message.strip().lower()

        # Approval patterns - more permissive, allow conversational responses
        # Match messages starting with approval words
        if re.match(r'^yes\b', msg):
            # Any message starting with "yes" = approval
            project_id = self._extract_project_id(inbox_item)
            return {"type": "create_task", "project_id": project_id}
        
        if re.match(r'^do\b', msg):
            # Any message starting with "do" (do this, do that, do it) = approval
            project_id = self._extract_project_id(inbox_item)
            return {"type": "create_task", "project_id": project_id}
        
        # Check for approval phrases anywhere in message
        approval_phrases = [
            r'looks?\s+good',
            r'sounds?\s+good', 
            r'go\s+ahead',
            r'lgtm',
            r'ship\s+it',
            r'approved?',
        ]
        for pattern in approval_phrases:
            if re.search(pattern, msg):
                project_id = self._extract_project_id(inbox_item)
                return {"type": "create_task", "project_id": project_id}

        # Rejection: more natural patterns
        rejection_patterns = [
            r'^(no|nope|nah)\b',  # Start with no
            r'^skip\b',
            r'^ignore\b',
            r'^pass\b',
            r'^reject',
            r'(don\'?t|do\s+not)\s+(do|create|make)',
            r'not\s+(now|interested|needed|necessary)',
        ]
        for pattern in rejection_patterns:
            if re.search(pattern, msg):
                return {"type": "resolve"}

        # Pending: deferral responses
        pending_patterns = [
            r'^(later|maybe|not\s+sure|let\s+me\s+think)\b',
            r'(i\'?ll?\s+)?(think|decide)\s+(about|on|later)',
        ]
        for pattern in pending_patterns:
            if re.search(pattern, msg):
                return {"type": "pending"}

        # Longer messages with action verbs → create task
        # This catches conversational instructions like "integrate this with..."
        words = user_message.split()
        if len(words) >= 3:
            action_verbs = ['create', 'make', 'add', 'build', 'implement', 'fix',
                           'update', 'write', 'change', 'remove', 'refactor', 
                           'integrate', 'intergrate',  # typo that appears in real data
                           'investigate', 'try', 'test']
            if any(v in msg for v in action_verbs):
                project_id = self._extract_project_id(inbox_item)
                return {"type": "create_task", "project_id": project_id}

        # Default: don't act (leave as needs_response)
        logger.debug(f"[INBOX] No clear action from: {user_message[:50]}")
        return {"type": "no_action"}

    def _extract_project_id(self, inbox_item: InboxItem) -> str:
        """Extract project ID from inbox item content/title."""
        text = f"{inbox_item.title} {inbox_item.content or ''}".lower()

        # Check for explicit project references
        match = re.search(r'\*\*project:\*\*\s*(\S+)', text)
        if match:
            return match.group(1)

        # Check keyword matches
        for pid, keywords in PROJECT_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                return pid

        return "default"

    async def _create_task_from_action(
        self,
        inbox_item: InboxItem,
        user_message: str,
        task_title: str | None = None,
        task_notes: str | None = None,
        agent_type: str | None = None,
        project_id: str = "default",
    ) -> Task | None:
        """Create ONE task from an inbox item with LLM-provided details."""
        # Validate project exists
        valid = await self._get_valid_project_ids()
        if project_id not in valid:
            project_id = "default"

        task_id = str(uuid.uuid4())

        # Use LLM-provided title or fallback
        title = task_title or inbox_item.title[:80]

        # Use LLM-provided notes or build default
        if task_notes:
            notes = task_notes
        else:
            notes = f"## From Inbox\n**{inbox_item.title}**\n\n"
            if inbox_item.content:
                # Truncate long content
                content = inbox_item.content
                if len(content) > 2000:
                    content = content[:2000] + "\n\n[truncated]"
                notes += content
            notes += f"\n\n---\n**User direction:** {user_message}"

        # Strict assignment policy: only accept explicit assignment from action.
        normalized_agent: str | None = (agent_type or "").strip().lower() or None

        task = Task(
            id=task_id,
            title=title,
            status="active",
            work_state="not_started",
            owner="lobs",
            project_id=project_id,
            agent=normalized_agent,
            notes=notes,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        self.db.add(task)
        logger.info(
            f"[INBOX] Created task {task_id[:8]}: {task.title[:50]} "
            f"(project={project_id}, agent={normalized_agent or 'unassigned'})"
        )
        return task

    async def _add_thread_message(
        self,
        thread_id: str,
        author: str,
        text: str
    ) -> None:
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
