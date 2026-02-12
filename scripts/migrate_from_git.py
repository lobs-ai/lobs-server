#!/usr/bin/env python3
"""
Migration script to import data from lobs-control git repo into lobs-server SQLite database.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from app.database import AsyncSessionLocal, init_db
from app.models import (
    Project, Task, InboxItem, InboxThread, InboxMessage, 
    AgentDocument, ResearchRequest, TrackerItem, WorkerStatus,
    WorkerRun, AgentStatus, TaskTemplate, Reminder
)


# Paths
CONTROL_REPO = Path.home() / "lobs-control"
TASKS_DIR = CONTROL_REPO / "state" / "tasks"
PROJECTS_FILE = CONTROL_REPO / "state" / "projects.json"
INBOX_DIRS = [CONTROL_REPO / "inbox", CONTROL_REPO / "state" / "inbox"]
REPORTS_DIR = CONTROL_REPO / "state" / "reports"
RESEARCH_DIR = CONTROL_REPO / "state" / "research"
WORKER_STATUS_FILE = CONTROL_REPO / "state" / "worker-status.json"
WORKER_HISTORY_FILE = CONTROL_REPO / "state" / "worker-history.json"
AGENTS_DIR = CONTROL_REPO / "state" / "agents"
INBOX_THREADS_DIR = CONTROL_REPO / "state" / "inbox-responses" / "threads"
TEMPLATES_DIR = CONTROL_REPO / "state" / "templates"
REMINDERS_FILE = CONTROL_REPO / "state" / "reminders.json"


def camel_to_snake(camel: str) -> str:
    """Convert camelCase to snake_case."""
    result = []
    for i, char in enumerate(camel):
        if char.isupper() and i > 0:
            result.append('_')
            result.append(char.lower())
        else:
            result.append(char.lower())
    return ''.join(result)


def convert_keys(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert all camelCase keys in dict to snake_case."""
    converted = {}
    for key, value in data.items():
        snake_key = camel_to_snake(key)
        converted[snake_key] = value
    return converted


def parse_iso_date(date_str: str | None) -> datetime | None:
    """Parse ISO 8601 date string to datetime."""
    if not date_str:
        return None
    try:
        # Handle both with and without microseconds
        if '.' in date_str:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        else:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


class MigrationStats:
    """Track migration statistics."""
    def __init__(self):
        self.projects = 0
        self.tasks = 0
        self.inbox_items = 0
        self.inbox_threads = 0
        self.inbox_messages = 0
        self.agent_documents = 0
        self.research_requests = 0
        self.tracker_items = 0
        self.worker_runs = 0
        self.agent_status = 0
        self.task_templates = 0
        self.reminders = 0
        self.warnings = []

    def warn(self, msg: str):
        """Add a warning message."""
        self.warnings.append(msg)
        print(f"⚠️  {msg}")

    def print_summary(self):
        """Print migration summary."""
        print("\n" + "="*60)
        print("Migration Summary")
        print("="*60)
        print(f"✅ Projects:          {self.projects}")
        print(f"✅ Tasks:             {self.tasks}")
        print(f"✅ Inbox Items:       {self.inbox_items}")
        print(f"✅ Inbox Threads:     {self.inbox_threads}")
        print(f"✅ Inbox Messages:    {self.inbox_messages}")
        print(f"✅ Agent Documents:   {self.agent_documents}")
        print(f"✅ Research Requests: {self.research_requests}")
        print(f"✅ Tracker Items:     {self.tracker_items}")
        print(f"✅ Worker Runs:       {self.worker_runs}")
        print(f"✅ Agent Status:      {self.agent_status}")
        print(f"✅ Task Templates:    {self.task_templates}")
        print(f"✅ Reminders:         {self.reminders}")
        print(f"\n⚠️  Warnings:          {len(self.warnings)}")
        print("="*60)


stats = MigrationStats()


async def migrate_projects(session):
    """Migrate projects from projects.json."""
    if not PROJECTS_FILE.exists():
        stats.warn(f"Projects file not found: {PROJECTS_FILE}")
        return

    with open(PROJECTS_FILE) as f:
        data = json.load(f)
    
    projects_data = data.get("projects", [])
    
    for proj in projects_data:
        proj_dict = convert_keys(proj)
        
        # Map fields
        project = Project(
            id=proj_dict.get("id"),
            title=proj_dict.get("title"),
            notes=proj_dict.get("notes"),
            archived=proj_dict.get("archived", False),
            type=proj_dict.get("type", "kanban"),
            sort_order=proj_dict.get("sort_order", 0),
            tracking=proj_dict.get("tracking"),
            github_repo=proj_dict.get("github_repo"),
            github_label_filter=proj_dict.get("github_label_filter"),
            created_at=parse_iso_date(proj_dict.get("created_at")),
            updated_at=parse_iso_date(proj_dict.get("updated_at")),
        )
        
        session.add(project)
        stats.projects += 1
    
    await session.flush()
    print(f"✅ Migrated {stats.projects} projects")


async def migrate_tasks(session):
    """Migrate tasks from state/tasks/*.json."""
    if not TASKS_DIR.exists():
        stats.warn(f"Tasks directory not found: {TASKS_DIR}")
        return

    task_files = list(TASKS_DIR.glob("*.json"))
    
    for task_file in task_files:
        try:
            with open(task_file) as f:
                task_data = json.load(f)
            
            task_dict = convert_keys(task_data)
            
            task = Task(
                id=task_dict.get("id"),
                title=task_dict.get("title"),
                status=task_dict.get("status"),
                owner=task_dict.get("owner"),
                work_state=task_dict.get("work_state"),
                review_state=task_dict.get("review_state"),
                project_id=task_dict.get("project_id"),
                notes=task_dict.get("notes"),
                artifact_path=task_dict.get("artifact_path"),
                started_at=parse_iso_date(task_dict.get("started_at")),
                finished_at=parse_iso_date(task_dict.get("finished_at")),
                sort_order=task_dict.get("sort_order", 0),
                blocked_by=task_dict.get("blocked_by"),
                pinned=task_dict.get("pinned", False),
                shape=task_dict.get("shape"),
                github_issue_number=task_dict.get("github_issue_number"),
                agent=task_dict.get("agent"),
                created_at=parse_iso_date(task_dict.get("created_at")),
                updated_at=parse_iso_date(task_dict.get("updated_at")),
            )
            
            session.add(task)
            stats.tasks += 1
        except Exception as e:
            stats.warn(f"Failed to migrate task {task_file.name}: {e}")
    
    await session.flush()
    print(f"✅ Migrated {stats.tasks} tasks")


async def migrate_inbox_items(session):
    """Migrate inbox items from markdown files."""
    for inbox_dir in INBOX_DIRS:
        if not inbox_dir.exists():
            continue
        
        md_files = list(inbox_dir.glob("*.md"))
        
        for md_file in md_files:
            try:
                content = md_file.read_text()
                
                # Extract title from first line or filename
                lines = content.strip().split('\n')
                title = lines[0].strip('#').strip() if lines else md_file.stem
                
                # Get file modified time
                stat = md_file.stat()
                modified_at = datetime.fromtimestamp(stat.st_mtime)
                
                # Generate ID from filename
                item_id = md_file.stem
                
                inbox_item = InboxItem(
                    id=item_id,
                    title=title[:200],  # Truncate if too long
                    filename=md_file.name,
                    relative_path=str(md_file.relative_to(CONTROL_REPO)),
                    content=content,
                    modified_at=modified_at,
                    is_read=False,
                )
                
                session.add(inbox_item)
                stats.inbox_items += 1
            except Exception as e:
                stats.warn(f"Failed to migrate inbox item {md_file.name}: {e}")
    
    await session.flush()
    print(f"✅ Migrated {stats.inbox_items} inbox items")


async def migrate_agent_documents(session):
    """Migrate agent documents from reports/ and research/ directories."""
    
    # Reports
    if REPORTS_DIR.exists():
        for subdir in ["pending", "approved", "rejected"]:
            report_subdir = REPORTS_DIR / subdir
            if not report_subdir.exists():
                continue
            
            for md_file in report_subdir.glob("*.md"):
                try:
                    content = md_file.read_text()
                    lines = content.strip().split('\n')
                    title = lines[0].strip('#').strip() if lines else md_file.stem
                    
                    stat = md_file.stat()
                    date = datetime.fromtimestamp(stat.st_mtime)
                    
                    doc = AgentDocument(
                        id=f"report-{md_file.stem}",
                        title=title[:200],
                        filename=md_file.name,
                        relative_path=str(md_file.relative_to(CONTROL_REPO)),
                        content=content,
                        source="writer",
                        status=subdir,
                        date=date,
                        is_read=(subdir != "pending"),
                    )
                    
                    session.add(doc)
                    stats.agent_documents += 1
                except Exception as e:
                    stats.warn(f"Failed to migrate report {md_file.name}: {e}")
    
    # Research
    if RESEARCH_DIR.exists():
        for topic_dir in RESEARCH_DIR.iterdir():
            if not topic_dir.is_dir():
                continue
            
            for md_file in topic_dir.glob("*.md"):
                try:
                    content = md_file.read_text()
                    lines = content.strip().split('\n')
                    title = lines[0].strip('#').strip() if lines else md_file.stem
                    
                    stat = md_file.stat()
                    date = datetime.fromtimestamp(stat.st_mtime)
                    
                    doc = AgentDocument(
                        id=f"research-{topic_dir.name}-{md_file.stem}",
                        title=title[:200],
                        filename=md_file.name,
                        relative_path=str(md_file.relative_to(CONTROL_REPO)),
                        content=content,
                        source="researcher",
                        status="approved",
                        topic=topic_dir.name,
                        date=date,
                        is_read=False,
                    )
                    
                    session.add(doc)
                    stats.agent_documents += 1
                except Exception as e:
                    stats.warn(f"Failed to migrate research doc {md_file.name}: {e}")
    
    await session.flush()
    print(f"✅ Migrated {stats.agent_documents} agent documents")


async def migrate_worker_status(session):
    """Migrate worker status from worker-status.json."""
    if not WORKER_STATUS_FILE.exists():
        stats.warn(f"Worker status file not found: {WORKER_STATUS_FILE}")
        return

    try:
        with open(WORKER_STATUS_FILE) as f:
            data = json.load(f)
        
        data_dict = convert_keys(data)
        
        worker_status = WorkerStatus(
            id=1,  # Singleton
            active=data_dict.get("active", False),
            worker_id=data_dict.get("worker_id"),
            started_at=parse_iso_date(data_dict.get("started_at")),
            current_task=data_dict.get("current_task"),
            tasks_completed=data_dict.get("tasks_completed", 0),
            last_heartbeat=parse_iso_date(data_dict.get("last_heartbeat")),
            ended_at=parse_iso_date(data_dict.get("ended_at")),
            current_project=data_dict.get("current_project"),
            task_log=data_dict.get("task_log"),
            input_tokens=data_dict.get("input_tokens", 0),
            output_tokens=data_dict.get("output_tokens", 0),
        )
        
        session.add(worker_status)
        print(f"✅ Migrated worker status")
    except Exception as e:
        stats.warn(f"Failed to migrate worker status: {e}")
    
    await session.flush()


async def migrate_worker_runs(session):
    """Migrate worker runs from worker-history.json."""
    if not WORKER_HISTORY_FILE.exists():
        stats.warn(f"Worker history file not found: {WORKER_HISTORY_FILE}")
        return

    try:
        with open(WORKER_HISTORY_FILE) as f:
            data = json.load(f)
        
        runs = data.get("runs", [])
        
        for run_data in runs:
            run_dict = convert_keys(run_data)
            
            worker_run = WorkerRun(
                worker_id=run_dict.get("worker_id"),
                started_at=parse_iso_date(run_dict.get("started_at")),
                ended_at=parse_iso_date(run_dict.get("ended_at")),
                tasks_completed=run_dict.get("tasks_completed", 0),
                timeout_reason=run_dict.get("timeout_reason"),
                model=run_dict.get("model"),
                input_tokens=run_dict.get("input_tokens", 0),
                output_tokens=run_dict.get("output_tokens", 0),
                total_tokens=run_dict.get("total_tokens", 0),
                total_cost_usd=run_dict.get("total_cost_usd"),
                task_log=run_dict.get("task_log"),
                commit_shas=run_dict.get("commit_shas"),
                files_modified=run_dict.get("files_modified"),
                github_compare_url=run_dict.get("github_compare_url"),
                task_id=run_dict.get("task_id"),
                succeeded=run_dict.get("succeeded"),
                source=run_dict.get("source"),
            )
            
            session.add(worker_run)
            stats.worker_runs += 1
        
        await session.flush()
        print(f"✅ Migrated {stats.worker_runs} worker runs")
    except Exception as e:
        stats.warn(f"Failed to migrate worker history: {e}")


async def migrate_agent_status(session):
    """Migrate agent status from state/agents/*.json."""
    if not AGENTS_DIR.exists():
        stats.warn(f"Agents directory not found: {AGENTS_DIR}")
        return

    for agent_file in AGENTS_DIR.glob("*.json"):
        try:
            with open(agent_file) as f:
                data = json.load(f)
            
            data_dict = convert_keys(data)
            
            agent_status = AgentStatus(
                agent_type=agent_file.stem,
                status=data_dict.get("status"),
                activity=data_dict.get("activity"),
                thinking=data_dict.get("thinking"),
                current_task_id=data_dict.get("current_task_id"),
                current_project_id=data_dict.get("current_project_id"),
                last_active_at=parse_iso_date(data_dict.get("last_active_at")),
                last_completed_task_id=data_dict.get("last_completed_task_id"),
                last_completed_at=parse_iso_date(data_dict.get("last_completed_at")),
                stats=data_dict.get("stats"),
            )
            
            session.add(agent_status)
            stats.agent_status += 1
        except Exception as e:
            stats.warn(f"Failed to migrate agent status {agent_file.name}: {e}")
    
    await session.flush()
    print(f"✅ Migrated {stats.agent_status} agent statuses")


async def migrate_inbox_threads(session):
    """Migrate inbox threads from state/inbox-responses/threads/*.json."""
    if not INBOX_THREADS_DIR.exists():
        stats.warn(f"Inbox threads directory not found: {INBOX_THREADS_DIR}")
        return

    for thread_file in INBOX_THREADS_DIR.glob("*.json"):
        try:
            with open(thread_file) as f:
                data = json.load(f)
            
            data_dict = convert_keys(data)
            
            # Create thread
            thread = InboxThread(
                id=data_dict.get("id") or thread_file.stem,
                doc_id=data_dict.get("doc_id"),
                triage_status=data_dict.get("triage_status"),
                created_at=parse_iso_date(data_dict.get("created_at")),
                updated_at=parse_iso_date(data_dict.get("updated_at")),
            )
            
            session.add(thread)
            stats.inbox_threads += 1
            
            # Create messages
            messages = data_dict.get("messages", [])
            for idx, msg_data in enumerate(messages):
                msg_dict = convert_keys(msg_data) if isinstance(msg_data, dict) else {}
                
                # Generate unique ID by combining thread ID and message index
                # (original IDs are sometimes duplicated across threads)
                msg_id = msg_dict.get("id")
                unique_msg_id = f"{thread.id}-{idx}" if msg_id else f"{thread.id}-{idx}"
                
                message = InboxMessage(
                    id=unique_msg_id,
                    thread_id=thread.id,
                    author=msg_dict.get("author"),
                    text=msg_dict.get("text"),
                    created_at=parse_iso_date(msg_dict.get("created_at")),
                )
                
                session.add(message)
                stats.inbox_messages += 1
        except Exception as e:
            stats.warn(f"Failed to migrate inbox thread {thread_file.name}: {e}")
    
    await session.flush()
    print(f"✅ Migrated {stats.inbox_threads} inbox threads with {stats.inbox_messages} messages")


async def migrate_research_requests(session):
    """Migrate research requests from state/research/*/requests/*.json."""
    if not RESEARCH_DIR.exists():
        stats.warn(f"Research directory not found: {RESEARCH_DIR}")
        return

    for topic_dir in RESEARCH_DIR.iterdir():
        if not topic_dir.is_dir():
            continue
        
        requests_dir = topic_dir / "requests"
        if not requests_dir.exists():
            continue
        
        for req_file in requests_dir.glob("*.json"):
            try:
                with open(req_file) as f:
                    data = json.load(f)
                
                data_dict = convert_keys(data)
                
                research_req = ResearchRequest(
                    id=data_dict.get("id") or req_file.stem,
                    project_id=data_dict.get("project_id"),
                    tile_id=data_dict.get("tile_id"),
                    prompt=data_dict.get("prompt"),
                    status=data_dict.get("status"),
                    response=data_dict.get("response"),
                    author=data_dict.get("author"),
                    priority=data_dict.get("priority"),
                    deliverables=data_dict.get("deliverables"),
                    edit_history=data_dict.get("edit_history"),
                    parent_request_id=data_dict.get("parent_request_id"),
                    assigned_worker=data_dict.get("assigned_worker"),
                    created_at=parse_iso_date(data_dict.get("created_at")),
                    updated_at=parse_iso_date(data_dict.get("updated_at")),
                )
                
                session.add(research_req)
                stats.research_requests += 1
            except Exception as e:
                stats.warn(f"Failed to migrate research request {req_file.name}: {e}")
    
    await session.flush()
    print(f"✅ Migrated {stats.research_requests} research requests")


async def migrate_tracker_items(session):
    """Migrate tracker items from state/tracker/*/items/*.json."""
    tracker_base = CONTROL_REPO / "state" / "tracker"
    
    if not tracker_base.exists():
        stats.warn(f"Tracker directory not found: {tracker_base}")
        return

    for project_dir in tracker_base.iterdir():
        if not project_dir.is_dir():
            continue
        
        items_dir = project_dir / "items"
        if not items_dir.exists():
            continue
        
        for item_file in items_dir.glob("*.json"):
            try:
                with open(item_file) as f:
                    data = json.load(f)
                
                data_dict = convert_keys(data)
                
                tracker_item = TrackerItem(
                    id=data_dict.get("id") or item_file.stem,
                    project_id=project_dir.name,
                    title=data_dict.get("title"),
                    status=data_dict.get("status"),
                    difficulty=data_dict.get("difficulty"),
                    tags=data_dict.get("tags"),
                    notes=data_dict.get("notes"),
                    links=data_dict.get("links"),
                    created_at=parse_iso_date(data_dict.get("created_at")),
                    updated_at=parse_iso_date(data_dict.get("updated_at")),
                )
                
                session.add(tracker_item)
                stats.tracker_items += 1
            except Exception as e:
                stats.warn(f"Failed to migrate tracker item {item_file.name}: {e}")
    
    await session.flush()
    print(f"✅ Migrated {stats.tracker_items} tracker items")


async def migrate_task_templates(session):
    """Migrate task templates from state/templates/*.json."""
    if not TEMPLATES_DIR.exists():
        stats.warn(f"Templates directory not found: {TEMPLATES_DIR}")
        return

    for template_file in TEMPLATES_DIR.glob("*.json"):
        try:
            with open(template_file) as f:
                data = json.load(f)
            
            data_dict = convert_keys(data)
            
            template = TaskTemplate(
                id=data_dict.get("id") or template_file.stem,
                name=data_dict.get("name"),
                description=data_dict.get("description"),
                items=data_dict.get("items"),
                created_at=parse_iso_date(data_dict.get("created_at")),
                updated_at=parse_iso_date(data_dict.get("updated_at")),
            )
            
            session.add(template)
            stats.task_templates += 1
        except Exception as e:
            stats.warn(f"Failed to migrate task template {template_file.name}: {e}")
    
    await session.flush()
    print(f"✅ Migrated {stats.task_templates} task templates")


async def migrate_reminders(session):
    """Migrate reminders from state/reminders.json."""
    if not REMINDERS_FILE.exists():
        stats.warn(f"Reminders file not found: {REMINDERS_FILE}")
        return

    try:
        with open(REMINDERS_FILE) as f:
            data = json.load(f)
        
        reminders = data.get("reminders", [])
        
        for reminder_data in reminders:
            reminder_dict = convert_keys(reminder_data)
            
            reminder = Reminder(
                id=reminder_dict.get("id"),
                title=reminder_dict.get("title"),
                due_at=parse_iso_date(reminder_dict.get("due_at")),
            )
            
            session.add(reminder)
            stats.reminders += 1
        
        await session.flush()
        print(f"✅ Migrated {stats.reminders} reminders")
    except Exception as e:
        stats.warn(f"Failed to migrate reminders: {e}")


async def main():
    """Main migration function."""
    print("🚀 Starting migration from lobs-control to lobs-server database")
    print(f"📂 Control repo: {CONTROL_REPO}")
    print()
    
    # Initialize database (create tables if they don't exist)
    await init_db()
    
    # Create session
    async with AsyncSessionLocal() as session:
        try:
            # Run all migrations
            await migrate_projects(session)
            await migrate_tasks(session)
            await migrate_inbox_items(session)
            await migrate_agent_documents(session)
            await migrate_worker_status(session)
            await migrate_worker_runs(session)
            await migrate_agent_status(session)
            await migrate_inbox_threads(session)
            await migrate_research_requests(session)
            await migrate_tracker_items(session)
            await migrate_task_templates(session)
            await migrate_reminders(session)
            
            # Commit all changes
            await session.commit()
            
            print("\n✅ Migration completed successfully!")
            stats.print_summary()
            
        except Exception as e:
            await session.rollback()
            print(f"\n❌ Migration failed: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
