#!/usr/bin/env python3
"""
Migration script to import data from ~/lobs-control into lobs-server SQLite database.
Usage: cd ~/lobs-server && source .venv/bin/activate && python scripts/migrate_from_git.py
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional
import hashlib

from sqlalchemy import create_engine, insert
from sqlalchemy.orm import sessionmaker

# Add parent directory to path to import models
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models import (
    Base, Project, Task, InboxItem, AgentDocument, ResearchRequest,
    TrackerItem, WorkerStatus, WorkerRun, AgentStatus, TaskTemplate,
    Reminder
)


def snake_case(camel: str) -> str:
    """Convert camelCase to snake_case."""
    result = []
    for i, char in enumerate(camel):
        if char.isupper() and i > 0:
            result.append('_')
        result.append(char.lower())
    return ''.join(result)


def convert_keys(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Convert all camelCase keys to snake_case."""
    if not isinstance(obj, dict):
        return obj
    return {snake_case(k): v for k, v in obj.items()}


def parse_iso_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO 8601 date string to datetime."""
    if not date_str:
        return None
    try:
        # Handle various ISO formats
        if 'T' in date_str:
            # Remove timezone info if present for simplicity
            date_str = date_str.replace('Z', '+00:00')
            if '+' in date_str or date_str.count('-') > 2:
                # Has timezone
                return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            return datetime.fromisoformat(date_str)
        return datetime.fromisoformat(date_str)
    except (ValueError, AttributeError) as e:
        print(f"  ⚠️  Failed to parse date '{date_str}': {e}")
        return None


def file_hash(filepath: Path) -> str:
    """Generate hash for file to use as ID."""
    rel_path = str(filepath).replace(str(Path.home()), '~')
    return hashlib.sha256(rel_path.encode()).hexdigest()[:16]


class Migrator:
    """Handles migration from git-based state to SQLite."""
    
    def __init__(self, control_dir: Path, db_path: Path):
        self.control_dir = control_dir
        self.db_path = db_path
        self.stats = {
            'projects': 0,
            'tasks': 0,
            'inbox_items': 0,
            'agent_documents': 0,
            'research_requests': 0,
            'tracker_items': 0,
            'worker_status': 0,
            'worker_runs': 0,
            'agent_statuses': 0,
            'task_templates': 0,
            'reminders': 0,
            'errors': 0,
        }
        
        # Create engine and session
        self.engine = create_engine(f'sqlite:///{db_path}', echo=False)
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine)
        self.session = Session()
    
    def migrate_all(self):
        """Run all migrations."""
        print(f"🚀 Starting migration from {self.control_dir}")
        print(f"📊 Database: {self.db_path}\n")
        
        self.migrate_projects()
        self.migrate_tasks()
        self.migrate_worker_status()
        self.migrate_worker_history()
        self.migrate_agent_statuses()
        self.migrate_reminders()
        self.migrate_templates()
        self.migrate_research_requests()
        self.migrate_tracker_items()
        self.migrate_inbox_items()
        self.migrate_agent_documents()
        
        self.session.commit()
        self.print_summary()
    
    def migrate_projects(self):
        """Migrate projects from state/projects.json."""
        print("📁 Migrating projects...")
        projects_file = self.control_dir / 'state' / 'projects.json'
        
        if not projects_file.exists():
            print(f"  ⚠️  {projects_file} not found, skipping\n")
            return
        
        try:
            data = json.loads(projects_file.read_text())
            projects = data.get('projects', [])
            
            for proj in projects:
                # Extract github subfields
                github = proj.get('github', {})
                
                project = Project(
                    id=proj['id'],
                    title=proj['title'],
                    notes=proj.get('notes'),
                    archived=proj.get('archived', False),
                    type=proj.get('type', 'kanban'),
                    sort_order=proj.get('sortOrder', 0),
                    tracking=proj.get('tracking'),
                    github_repo=github.get('repo') if github else None,
                    github_label_filter=github.get('labelFilter') if github else None,
                )
                
                self.session.merge(project)
                self.stats['projects'] += 1
            
            print(f"  ✅ Migrated {len(projects)} projects\n")
        except Exception as e:
            print(f"  ❌ Error migrating projects: {e}\n")
            self.stats['errors'] += 1
    
    def migrate_tasks(self):
        """Migrate tasks from state/tasks/*.json."""
        print("📋 Migrating tasks...")
        tasks_dir = self.control_dir / 'state' / 'tasks'
        
        if not tasks_dir.exists():
            print(f"  ⚠️  {tasks_dir} not found, skipping\n")
            return
        
        task_files = list(tasks_dir.glob('*.json'))
        
        for task_file in task_files:
            try:
                data = json.loads(task_file.read_text())
                
                task = Task(
                    id=data['id'],
                    title=data['title'],
                    status=data.get('status', 'inbox'),
                    owner=data.get('owner'),
                    work_state=data.get('workState'),
                    review_state=data.get('reviewState'),
                    project_id=data.get('projectId'),
                    notes=data.get('notes'),
                    artifact_path=data.get('artifactPath'),
                    started_at=parse_iso_date(data.get('startedAt')),
                    finished_at=parse_iso_date(data.get('finishedAt')),
                    sort_order=data.get('sortOrder', 0),
                    blocked_by=data.get('blockedBy'),
                    pinned=data.get('pinned', False),
                    shape=data.get('shape'),
                    github_issue_number=data.get('githubIssueNumber'),
                    agent=data.get('agent'),
                    created_at=parse_iso_date(data.get('createdAt')) or datetime.now(),
                    updated_at=parse_iso_date(data.get('updatedAt')) or datetime.now(),
                )
                
                self.session.merge(task)
                self.stats['tasks'] += 1
            except Exception as e:
                print(f"  ⚠️  Error migrating {task_file.name}: {e}")
                self.stats['errors'] += 1
        
        print(f"  ✅ Migrated {self.stats['tasks']} tasks\n")
    
    def migrate_worker_status(self):
        """Migrate worker status from state/worker-status.json."""
        print("⚙️  Migrating worker status...")
        status_file = self.control_dir / 'state' / 'worker-status.json'
        
        if not status_file.exists():
            print(f"  ⚠️  {status_file} not found, skipping\n")
            return
        
        try:
            data = json.loads(status_file.read_text())
            
            status = WorkerStatus(
                id=1,  # singleton
                active=data.get('active', False),
                worker_id=data.get('workerId'),
                started_at=parse_iso_date(data.get('startedAt')),
                current_task=data.get('currentTask'),
                tasks_completed=data.get('tasksCompleted', 0),
                last_heartbeat=parse_iso_date(data.get('lastHeartbeat')),
                ended_at=parse_iso_date(data.get('endedAt')),
                current_project=data.get('currentProject'),
                task_log=data.get('taskLog'),
                input_tokens=data.get('inputTokens', 0),
                output_tokens=data.get('outputTokens', 0),
            )
            
            self.session.merge(status)
            self.stats['worker_status'] += 1
            print(f"  ✅ Migrated worker status\n")
        except Exception as e:
            print(f"  ❌ Error migrating worker status: {e}\n")
            self.stats['errors'] += 1
    
    def migrate_worker_history(self):
        """Migrate worker history from state/worker-history.json."""
        print("📜 Migrating worker history...")
        history_file = self.control_dir / 'state' / 'worker-history.json'
        
        if not history_file.exists():
            print(f"  ⚠️  {history_file} not found, skipping\n")
            return
        
        try:
            data = json.loads(history_file.read_text())
            runs = data.get('runs', [])
            
            for run_data in runs:
                run = WorkerRun(
                    worker_id=run_data.get('workerId'),
                    started_at=parse_iso_date(run_data.get('startedAt')),
                    ended_at=parse_iso_date(run_data.get('endedAt')),
                    tasks_completed=run_data.get('tasksCompleted', 0),
                    timeout_reason=run_data.get('timeoutReason'),
                    model=run_data.get('model'),
                    input_tokens=run_data.get('inputTokens', 0),
                    output_tokens=run_data.get('outputTokens', 0),
                    total_tokens=run_data.get('totalTokens', 0),
                    total_cost_usd=run_data.get('totalCostUsd'),
                    task_log=run_data.get('taskLog'),
                    commit_shas=run_data.get('commitShas'),
                    files_modified=run_data.get('filesModified'),
                    github_compare_url=run_data.get('githubCompareUrl'),
                    task_id=run_data.get('taskId'),
                    succeeded=run_data.get('succeeded'),
                    source=run_data.get('source'),
                )
                
                self.session.add(run)
                self.stats['worker_runs'] += 1
            
            print(f"  ✅ Migrated {len(runs)} worker runs\n")
        except Exception as e:
            print(f"  ❌ Error migrating worker history: {e}\n")
            self.stats['errors'] += 1
    
    def migrate_agent_statuses(self):
        """Migrate agent statuses from state/agents/*.json."""
        print("🤖 Migrating agent statuses...")
        agents_dir = self.control_dir / 'state' / 'agents'
        
        if not agents_dir.exists():
            print(f"  ⚠️  {agents_dir} not found, skipping\n")
            return
        
        agent_files = list(agents_dir.glob('*.json'))
        
        for agent_file in agent_files:
            try:
                data = json.loads(agent_file.read_text())
                agent_type = agent_file.stem  # filename without .json
                
                status = AgentStatus(
                    agent_type=agent_type,
                    status=data.get('status'),
                    activity=data.get('activity'),
                    thinking=data.get('thinking'),
                    current_task_id=data.get('currentTaskId'),
                    current_project_id=data.get('currentProjectId'),
                    last_active_at=parse_iso_date(data.get('lastActiveAt')),
                    last_completed_task_id=data.get('lastCompletedTaskId'),
                    last_completed_at=parse_iso_date(data.get('lastCompletedAt')),
                    stats=data.get('stats'),
                )
                
                self.session.merge(status)
                self.stats['agent_statuses'] += 1
            except Exception as e:
                print(f"  ⚠️  Error migrating {agent_file.name}: {e}")
                self.stats['errors'] += 1
        
        print(f"  ✅ Migrated {self.stats['agent_statuses']} agent statuses\n")
    
    def migrate_reminders(self):
        """Migrate reminders from state/reminders.json."""
        print("⏰ Migrating reminders...")
        reminders_file = self.control_dir / 'state' / 'reminders.json'
        
        if not reminders_file.exists():
            print(f"  ⚠️  {reminders_file} not found, skipping\n")
            return
        
        try:
            data = json.loads(reminders_file.read_text())
            reminders = data.get('reminders', [])
            
            for rem_data in reminders:
                reminder = Reminder(
                    id=rem_data['id'],
                    title=rem_data['title'],
                    due_at=parse_iso_date(rem_data['dueAt']),
                )
                
                self.session.merge(reminder)
                self.stats['reminders'] += 1
            
            print(f"  ✅ Migrated {len(reminders)} reminders\n")
        except Exception as e:
            print(f"  ❌ Error migrating reminders: {e}\n")
            self.stats['errors'] += 1
    
    def migrate_templates(self):
        """Migrate templates from state/templates/*.json."""
        print("📄 Migrating templates...")
        templates_dir = self.control_dir / 'state' / 'templates'
        
        if not templates_dir.exists():
            print(f"  ⚠️  {templates_dir} not found, skipping\n")
            return
        
        template_files = list(templates_dir.glob('*.json'))
        
        for template_file in template_files:
            try:
                data = json.loads(template_file.read_text())
                
                template = TaskTemplate(
                    id=data.get('id', template_file.stem),
                    name=data.get('name', template_file.stem),
                    description=data.get('description'),
                    items=data.get('items'),
                )
                
                self.session.merge(template)
                self.stats['task_templates'] += 1
            except Exception as e:
                print(f"  ⚠️  Error migrating {template_file.name}: {e}")
                self.stats['errors'] += 1
        
        print(f"  ✅ Migrated {self.stats['task_templates']} templates\n")
    
    def migrate_research_requests(self):
        """Migrate research requests from state/research/*/requests/*.json."""
        print("🔬 Migrating research requests...")
        research_dir = self.control_dir / 'state' / 'research'
        
        if not research_dir.exists():
            print(f"  ⚠️  {research_dir} not found, skipping\n")
            return
        
        # Find all requests/*.json files
        request_files = list(research_dir.glob('*/requests/*.json'))
        
        for req_file in request_files:
            try:
                data = json.loads(req_file.read_text())
                
                # Extract project from path: research/<project>/requests/<file>
                project_id = req_file.parent.parent.name
                
                request = ResearchRequest(
                    id=data.get('id', req_file.stem),
                    project_id=project_id,
                    tile_id=data.get('tileId'),
                    prompt=data.get('prompt'),
                    status=data.get('status'),
                    response=data.get('response'),
                    author=data.get('author'),
                    priority=data.get('priority'),
                    deliverables=data.get('deliverables'),
                    edit_history=data.get('editHistory'),
                    parent_request_id=data.get('parentRequestId'),
                    assigned_worker=data.get('assignedWorker'),
                    created_at=parse_iso_date(data.get('createdAt')) or datetime.now(),
                    updated_at=parse_iso_date(data.get('updatedAt')) or datetime.now(),
                )
                
                self.session.merge(request)
                self.stats['research_requests'] += 1
            except Exception as e:
                print(f"  ⚠️  Error migrating {req_file}: {e}")
                self.stats['errors'] += 1
        
        print(f"  ✅ Migrated {self.stats['research_requests']} research requests\n")
    
    def migrate_tracker_items(self):
        """Migrate tracker items from state/tracker/*/items/*.json."""
        print("📊 Migrating tracker items...")
        tracker_dir = self.control_dir / 'state' / 'tracker'
        
        if not tracker_dir.exists():
            print(f"  ⚠️  {tracker_dir} not found, skipping\n")
            return
        
        # Find all items/*.json files
        item_files = list(tracker_dir.glob('*/items/*.json'))
        
        for item_file in item_files:
            try:
                data = json.loads(item_file.read_text())
                
                # Extract project from path: tracker/<project>/items/<file>
                project_id = item_file.parent.parent.name
                
                item = TrackerItem(
                    id=data.get('id', item_file.stem),
                    project_id=project_id,
                    title=data['title'],
                    status=data.get('status'),
                    difficulty=data.get('difficulty'),
                    tags=data.get('tags'),
                    notes=data.get('notes'),
                    links=data.get('links'),
                    created_at=parse_iso_date(data.get('createdAt')) or datetime.now(),
                    updated_at=parse_iso_date(data.get('updatedAt')) or datetime.now(),
                )
                
                self.session.merge(item)
                self.stats['tracker_items'] += 1
            except Exception as e:
                print(f"  ⚠️  Error migrating {item_file}: {e}")
                self.stats['errors'] += 1
        
        print(f"  ✅ Migrated {self.stats['tracker_items']} tracker items\n")
    
    def migrate_inbox_items(self):
        """Migrate inbox items from inbox/*.md and state/inbox/*.json."""
        print("📥 Migrating inbox items...")
        
        # Migrate markdown files from inbox/
        inbox_dir = self.control_dir / 'inbox'
        if inbox_dir.exists():
            md_files = list(inbox_dir.glob('*.md'))
            
            for md_file in md_files:
                try:
                    content = md_file.read_text()
                    stat = md_file.stat()
                    
                    # Extract title from first heading or filename
                    title = md_file.stem
                    if content.startswith('#'):
                        first_line = content.split('\n')[0]
                        title = first_line.lstrip('#').strip()
                    
                    item = InboxItem(
                        id=file_hash(md_file),
                        title=title,
                        filename=md_file.name,
                        relative_path=str(md_file.relative_to(self.control_dir)),
                        content=content,
                        modified_at=datetime.fromtimestamp(stat.st_mtime),
                        is_read=False,
                    )
                    
                    self.session.merge(item)
                    self.stats['inbox_items'] += 1
                except Exception as e:
                    print(f"  ⚠️  Error migrating {md_file.name}: {e}")
                    self.stats['errors'] += 1
        
        # Migrate JSON suggestions from state/inbox/
        state_inbox_dir = self.control_dir / 'state' / 'inbox'
        if state_inbox_dir.exists():
            json_files = list(state_inbox_dir.glob('*.json'))
            
            for json_file in json_files:
                try:
                    data = json.loads(json_file.read_text())
                    
                    item = InboxItem(
                        id=data.get('id', file_hash(json_file)),
                        title=data.get('title', json_file.stem),
                        filename=json_file.name,
                        relative_path=str(json_file.relative_to(self.control_dir)),
                        content=json.dumps(data, indent=2),
                        modified_at=datetime.fromtimestamp(json_file.stat().st_mtime),
                        is_read=data.get('isRead', False),
                        summary=data.get('summary'),
                    )
                    
                    self.session.merge(item)
                    self.stats['inbox_items'] += 1
                except Exception as e:
                    print(f"  ⚠️  Error migrating {json_file.name}: {e}")
                    self.stats['errors'] += 1
        
        print(f"  ✅ Migrated {self.stats['inbox_items']} inbox items\n")
    
    def migrate_agent_documents(self):
        """Migrate agent documents from state/reports/**/*.md and state/research/**/*.md."""
        print("📝 Migrating agent documents...")
        
        # Migrate reports
        reports_dir = self.control_dir / 'state' / 'reports'
        if reports_dir.exists():
            md_files = list(reports_dir.glob('**/*.md'))
            
            for md_file in md_files:
                try:
                    content = md_file.read_text()
                    stat = md_file.stat()
                    
                    # Extract title from first heading or filename
                    title = md_file.stem
                    if content.startswith('#'):
                        first_line = content.split('\n')[0]
                        title = first_line.lstrip('#').strip()
                    
                    # Determine status from path (pending/approved/rejected)
                    status = 'pending'
                    if 'pending' in str(md_file):
                        status = 'pending'
                    elif 'approved' in str(md_file):
                        status = 'approved'
                    elif 'rejected' in str(md_file):
                        status = 'rejected'
                    
                    doc = AgentDocument(
                        id=file_hash(md_file),
                        title=title,
                        filename=md_file.name,
                        relative_path=str(md_file.relative_to(self.control_dir)),
                        content=content,
                        content_is_truncated=False,
                        source='writer',
                        status=status,
                        date=datetime.fromtimestamp(stat.st_mtime),
                        is_read=status != 'pending',
                    )
                    
                    self.session.merge(doc)
                    self.stats['agent_documents'] += 1
                except Exception as e:
                    print(f"  ⚠️  Error migrating {md_file}: {e}")
                    self.stats['errors'] += 1
        
        # Migrate research documents
        research_dir = self.control_dir / 'state' / 'research'
        if research_dir.exists():
            md_files = list(research_dir.glob('**/*.md'))
            
            for md_file in md_files:
                try:
                    # Skip files in requests/ subdirs
                    if 'requests' in md_file.parts:
                        continue
                    
                    content = md_file.read_text()
                    stat = md_file.stat()
                    
                    # Extract title from first heading or filename
                    title = md_file.stem
                    if content.startswith('#'):
                        first_line = content.split('\n')[0]
                        title = first_line.lstrip('#').strip()
                    
                    # Extract project from path: research/<project>/<file>
                    project_id = None
                    if len(md_file.relative_to(research_dir).parts) > 1:
                        project_id = md_file.relative_to(research_dir).parts[0]
                    
                    doc = AgentDocument(
                        id=file_hash(md_file),
                        title=title,
                        filename=md_file.name,
                        relative_path=str(md_file.relative_to(self.control_dir)),
                        content=content,
                        content_is_truncated=False,
                        source='researcher',
                        status='approved',  # Research docs are generally approved
                        topic=project_id,
                        project_id=project_id,
                        date=datetime.fromtimestamp(stat.st_mtime),
                        is_read=False,
                    )
                    
                    self.session.merge(doc)
                    self.stats['agent_documents'] += 1
                except Exception as e:
                    print(f"  ⚠️  Error migrating {md_file}: {e}")
                    self.stats['errors'] += 1
        
        print(f"  ✅ Migrated {self.stats['agent_documents']} agent documents\n")
    
    def print_summary(self):
        """Print migration summary."""
        print("\n" + "="*60)
        print("📊 MIGRATION SUMMARY")
        print("="*60)
        
        for key, count in self.stats.items():
            if count > 0:
                emoji = "✅" if key != 'errors' else "❌"
                print(f"{emoji} {key.replace('_', ' ').title()}: {count}")
        
        print("="*60)
        print(f"✨ Migration complete! Database: {self.db_path}\n")
    
    def __del__(self):
        """Clean up session."""
        if hasattr(self, 'session'):
            self.session.close()


def main():
    """Main entry point."""
    # Resolve paths
    home = Path.home()
    control_dir = home / 'lobs-control'
    db_path = home / 'lobs-server' / 'data' / 'lobs.db'
    
    # Ensure data directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    if not control_dir.exists():
        print(f"❌ Error: {control_dir} not found!")
        sys.exit(1)
    
    # Run migration
    migrator = Migrator(control_dir, db_path)
    migrator.migrate_all()


if __name__ == '__main__':
    main()
