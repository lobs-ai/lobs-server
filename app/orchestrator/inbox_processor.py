"""Inbox processor - scans inbox threads for user responses and takes action.

Scans threads with triage_status='needs_response' for messages from user ('rafe'),
analyzes the response, and determines actions:
- create_tasks: Create one or more tasks from the response
- resolve: Mark thread as resolved (no further action needed)
- pending: Mark as pending (acknowledged but not actionable yet)
- respond: Post a follow-up message (future feature)

Uses rule-based analysis (keyword matching) initially. Can be upgraded to AI-based later.
"""

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    InboxItem,
    InboxThread as InboxThreadModel,
    InboxMessage as InboxMessageModel,
    Task,
)

logger = logging.getLogger(__name__)


class InboxProcessor:
    """Processes inbox threads for user responses and takes automated actions."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def process_threads(self) -> dict[str, Any]:
        """
        Main entry point - scans and processes all eligible threads.
        
        Returns:
            Stats dict with counts of processed threads, tasks created, etc.
        """
        stats = {
            "threads_scanned": 0,
            "threads_processed": 0,
            "tasks_created": 0,
            "threads_resolved": 0,
            "threads_marked_pending": 0,
            "errors": 0,
        }

        try:
            # Find threads needing processing:
            # - triage_status = 'needs_response'
            # - has at least one message from 'rafe'
            # - has new messages since last processing
            eligible_threads = await self._get_eligible_threads()
            stats["threads_scanned"] = len(eligible_threads)

            if not eligible_threads:
                logger.debug("[INBOX] No threads need processing")
                return stats

            logger.info(f"[INBOX] Processing {len(eligible_threads)} thread(s)")

            for thread in eligible_threads:
                try:
                    thread_stats = await self._process_thread(thread)
                    stats["threads_processed"] += 1
                    stats["tasks_created"] += thread_stats.get("tasks_created", 0)
                    stats["threads_resolved"] += thread_stats.get("resolved", 0)
                    stats["threads_marked_pending"] += thread_stats.get("marked_pending", 0)
                except Exception as e:
                    logger.error(
                        f"[INBOX] Failed to process thread {thread.id[:8]}: {e}",
                        exc_info=True
                    )
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
            logger.error(f"[INBOX] Error during thread processing: {e}", exc_info=True)
            stats["errors"] += 1
            await self.db.rollback()

        return stats

    async def _get_eligible_threads(self) -> list[InboxThreadModel]:
        """
        Get threads that need processing.
        
        Returns threads where:
        - triage_status = 'needs_response'
        - At least one message from 'rafe'
        - Has new messages since last_processed_message_id
        """
        result = await self.db.execute(
            select(InboxThreadModel).where(
                InboxThreadModel.triage_status == "needs_response"
            )
        )
        threads = result.scalars().all()

        eligible = []
        for thread in threads:
            # Get messages for this thread
            messages_result = await self.db.execute(
                select(InboxMessageModel)
                .where(InboxMessageModel.thread_id == thread.id)
                .order_by(InboxMessageModel.created_at.asc())
            )
            messages = messages_result.scalars().all()

            # Check if there's at least one message from 'rafe'
            has_rafe_message = any(msg.author.lower() == "rafe" for msg in messages)
            if not has_rafe_message:
                continue

            # Check if there are new messages since last processing
            last_processed_id = getattr(thread, "last_processed_message_id", None)
            if last_processed_id:
                # Find if there are any messages after the last processed one
                found_last = False
                has_new = False
                for msg in messages:
                    if found_last:
                        has_new = True
                        break
                    if msg.id == last_processed_id:
                        found_last = True
                
                if not has_new:
                    continue
            
            eligible.append(thread)

        return eligible

    async def _process_thread(self, thread: InboxThreadModel) -> dict[str, Any]:
        """
        Process a single thread.
        
        Steps:
        1. Load inbox item content
        2. Load all thread messages
        3. Analyze latest response from 'rafe'
        4. Determine action (create_tasks/resolve/pending)
        5. Execute action
        6. Update thread status and tracking
        """
        stats = {"tasks_created": 0, "resolved": 0, "marked_pending": 0}

        # 1. Load inbox item
        inbox_item = await self.db.get(InboxItem, thread.doc_id)
        if not inbox_item:
            logger.warning(f"[INBOX] Thread {thread.id[:8]} has no inbox item")
            return stats

        # 2. Load messages
        messages_result = await self.db.execute(
            select(InboxMessageModel)
            .where(InboxMessageModel.thread_id == thread.id)
            .order_by(InboxMessageModel.created_at.asc())
        )
        messages = messages_result.scalars().all()

        # Get the latest message from 'rafe' (case-insensitive)
        rafe_messages = [msg for msg in messages if msg.author.lower() == "rafe"]
        if not rafe_messages:
            logger.warning(f"[INBOX] Thread {thread.id[:8]} has no messages from rafe")
            return stats

        latest_rafe_message = rafe_messages[-1]

        # 3. Analyze response
        action = self._analyze_response(
            user_message=latest_rafe_message.text,
            inbox_content=inbox_item.content or "",
            inbox_title=inbox_item.title,
        )

        logger.info(
            f"[INBOX] Thread {thread.id[:8]} ({inbox_item.title}): "
            f"action={action['type']}"
        )

        # 4. Execute action
        if action["type"] == "create_tasks":
            tasks_created = await self._create_tasks_from_action(
                action=action,
                inbox_item=inbox_item,
                user_message=latest_rafe_message.text,
            )
            stats["tasks_created"] = tasks_created
            
            # Mark thread as resolved after creating tasks
            thread.triage_status = "resolved"
            stats["resolved"] = 1

        elif action["type"] == "resolve":
            thread.triage_status = "resolved"
            stats["resolved"] = 1

        elif action["type"] == "pending":
            thread.triage_status = "pending"
            stats["marked_pending"] = 1

        elif action["type"] == "respond":
            # Future: post a follow-up message
            # For now, leave as needs_response
            logger.info(
                f"[INBOX] Thread {thread.id[:8]} needs clarification "
                f"(respond action not implemented yet)"
            )

        # 5. Update tracking
        thread.last_processed_message_id = latest_rafe_message.id
        thread.updated_at = datetime.now(timezone.utc)

        return stats

    def _analyze_response(
        self,
        user_message: str,
        inbox_content: str,
        inbox_title: str,
    ) -> dict[str, Any]:
        """
        Analyze user response and determine action.
        
        Rule-based approach (can be upgraded to AI later):
        - Approval keywords → create_tasks
        - Rejection keywords → resolve
        - Specific instructions → create_task with custom instructions
        - Otherwise → respond (needs clarification)
        
        Returns:
            Action dict with:
            - type: 'create_tasks' | 'resolve' | 'pending' | 'respond'
            - tasks: list of task dicts (for create_tasks action)
            - reason: explanation string
        """
        msg_lower = user_message.lower().strip()

        # Approval patterns
        approval_patterns = [
            r'\b(yes|yep|yeah|sure|ok|okay|sounds good|approved?|approve|do it|go ahead|do these?)\b',
            r'\b(let\'s do (it|this|these))\b',
            r'\b(make these tasks?)\b',
            r'\b(create (these )?tasks?)\b',
        ]
        
        # Rejection patterns
        rejection_patterns = [
            r'\b(no|nope|nah|skip|ignore|don\'t|cancel|reject)\b',
            r'\b(not (now|interested))\b',
        ]

        # Pending/defer patterns
        pending_patterns = [
            r'\b(later|maybe|think about|consider|not sure)\b',
            r'\b(need (to|more) (think|time))\b',
        ]

        # Check for approval
        for pattern in approval_patterns:
            if re.search(pattern, msg_lower):
                # Parse tasks from inbox content
                tasks = self._parse_tasks_from_content(inbox_content, inbox_title)
                return {
                    "type": "create_tasks",
                    "tasks": tasks,
                    "reason": "User approved suggestions"
                }

        # Check for rejection
        for pattern in rejection_patterns:
            if re.search(pattern, msg_lower):
                return {
                    "type": "resolve",
                    "tasks": [],
                    "reason": "User rejected"
                }

        # Check for pending
        for pattern in pending_patterns:
            if re.search(pattern, msg_lower):
                return {
                    "type": "pending",
                    "tasks": [],
                    "reason": "User wants to defer"
                }

        # Check if user is giving specific instructions
        # (message is longer than just a keyword, contains actionable verbs)
        if len(user_message.split()) > 5:
            action_verbs = [
                'create', 'make', 'add', 'build', 'implement', 'fix', 'update',
                'write', 'change', 'modify', 'remove', 'delete', 'refactor'
            ]
            if any(verb in msg_lower for verb in action_verbs):
                # Create a single task from the user's instructions
                return {
                    "type": "create_tasks",
                    "tasks": [{
                        "title": f"Follow up: {inbox_title[:50]}",
                        "notes": user_message,
                        "project_id": self._extract_project_id(inbox_content),
                    }],
                    "reason": "User provided specific instructions"
                }

        # Default: needs clarification
        return {
            "type": "respond",
            "tasks": [],
            "reason": "Need clarification"
        }

    def _parse_tasks_from_content(
        self,
        content: str,
        title: str,
    ) -> list[dict[str, Any]]:
        """
        Parse actionable tasks from inbox item content.
        
        Looks for:
        - Numbered lists (1. task, 2. task)
        - Bullet points (- task, * task)
        - Suggestions/recommendations sections
        
        Returns list of task dicts with:
        - title: task title
        - notes: additional context
        - project_id: inferred project (or "default")
        """
        tasks = []

        # Try to extract project ID from content
        project_id = self._extract_project_id(content)

        # Pattern 1: Numbered lists (1. task, 2. task, etc.)
        numbered_pattern = r'^\s*(\d+)[.):]\s*(.+)$'
        
        # Pattern 2: Bullet points (-, *, •)
        bullet_pattern = r'^\s*[-*•]\s*(.+)$'

        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Check for numbered items
            match = re.match(numbered_pattern, line)
            if match:
                task_text = match.group(2).strip()
                if self._is_actionable(task_text):
                    tasks.append({
                        "title": task_text[:100],  # Truncate long titles
                        "notes": f"From inbox: {title}\n\nContext:\n{content[:500]}",
                        "project_id": project_id,
                    })
                continue

            # Check for bullet points
            match = re.match(bullet_pattern, line)
            if match:
                task_text = match.group(1).strip()
                if self._is_actionable(task_text):
                    tasks.append({
                        "title": task_text[:100],
                        "notes": f"From inbox: {title}\n\nContext:\n{content[:500]}",
                        "project_id": project_id,
                    })

        # If no tasks found, create a single task from the inbox title
        if not tasks:
            tasks.append({
                "title": title[:100],
                "notes": f"From inbox discussion:\n\n{content[:1000]}",
                "project_id": project_id,
            })

        return tasks

    def _is_actionable(self, text: str) -> bool:
        """Check if text looks like an actionable task (not just a heading or comment)."""
        text_lower = text.lower()
        
        # Filter out section headings
        if text.endswith(':'):
            return False
        
        # Filter out very short items (likely not tasks) - but allow 2+ words
        if len(text.split()) < 2:
            return False

        # Filter out common non-task phrases
        non_task_phrases = [
            'suggestions:', 'recommendations:', 'next steps:', 'notes:',
            'summary:', 'background:', 'context:', 'overview:'
        ]
        if any(phrase in text_lower for phrase in non_task_phrases):
            return False

        return True

    def _extract_project_id(self, content: str) -> str:
        """
        Try to extract project ID from content.
        
        Looks for patterns like:
        - Project: project-id
        - project_id: project-id
        - #project-id
        
        Returns "default" if no project found.
        """
        content_lower = content.lower()

        # Pattern 1: "Project: project-id" or "project_id: project-id"
        project_pattern = r'project[_\s]*(?:id)?[:\s]+([a-z0-9_-]+)'
        match = re.search(project_pattern, content_lower)
        if match:
            return match.group(1)

        # Pattern 2: Hashtag project reference (#project-id)
        hashtag_pattern = r'#([a-z0-9_-]+)'
        match = re.search(hashtag_pattern, content_lower)
        if match:
            project_id = match.group(1)
            # Verify it looks like a project ID (not just a random hashtag)
            if len(project_id) > 2 and '-' in project_id:
                return project_id

        # Default project
        return "default"

    async def _create_tasks_from_action(
        self,
        action: dict[str, Any],
        inbox_item: InboxItem,
        user_message: str,
    ) -> int:
        """
        Create tasks from the action.
        
        Returns:
            Number of tasks created
        """
        tasks = action.get("tasks", [])
        if not tasks:
            logger.warning("[INBOX] No tasks in create_tasks action")
            return 0

        created_count = 0
        for task_dict in tasks:
            try:
                task_id = str(uuid.uuid4())
                
                # Add user response to notes for context
                notes = task_dict.get("notes", "")
                if user_message:
                    notes += f"\n\nUser response: {user_message}"

                task = Task(
                    id=task_id,
                    title=task_dict["title"],
                    status="active",
                    work_state="not_started",
                    owner="lobs",
                    project_id=task_dict.get("project_id", "default"),
                    notes=notes,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                
                self.db.add(task)
                created_count += 1
                
                logger.info(
                    f"[INBOX] Created task {task_id[:8]}: {task.title[:50]}"
                )

            except Exception as e:
                logger.error(f"[INBOX] Failed to create task: {e}", exc_info=True)

        return created_count
