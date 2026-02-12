"""SQLAlchemy models for all database tables."""

from datetime import datetime
from sqlalchemy import Column, String, Text, Boolean, Integer, Float, DateTime, ForeignKey, JSON
from sqlalchemy.sql import func

from app.database import Base


class Project(Base):
    """Project model."""
    __tablename__ = "projects"
    
    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    notes = Column(Text)
    archived = Column(Boolean, default=False, nullable=False)
    type = Column(String, nullable=False)  # kanban/research/tracker
    sort_order = Column(Integer, default=0)
    tracking = Column(String)  # local/github
    github_repo = Column(String)
    github_label_filter = Column(JSON)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class Task(Base):
    """Task model."""
    __tablename__ = "tasks"
    
    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    status = Column(String, nullable=False)  # inbox/active/completed/rejected/waiting_on
    owner = Column(String)  # lobs/rafe
    work_state = Column(String)
    review_state = Column(String)
    project_id = Column(String, ForeignKey("projects.id"))
    notes = Column(Text)
    artifact_path = Column(String)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    sort_order = Column(Integer, default=0)
    blocked_by = Column(JSON)  # array
    pinned = Column(Boolean, default=False)
    shape = Column(String)
    github_issue_number = Column(Integer)
    agent = Column(String)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    
    # Escalation fields
    escalation_tier = Column(Integer, default=0)  # 0=none, 1=retry, 2=agent_switch, 3=diagnostic, 4=human
    retry_count = Column(Integer, default=0)
    failure_reason = Column(Text)
    last_retry_reason = Column(String)


class InboxItem(Base):
    """Inbox item model."""
    __tablename__ = "inbox_items"
    
    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    filename = Column(String)
    relative_path = Column(String)
    content = Column(Text)
    modified_at = Column(DateTime)
    is_read = Column(Boolean, default=False, nullable=False)
    summary = Column(Text)


class InboxThread(Base):
    """Inbox thread model."""
    __tablename__ = "inbox_threads"
    
    id = Column(String, primary_key=True)
    doc_id = Column(String, ForeignKey("inbox_items.id"))
    triage_status = Column(String)  # needs_response/pending/resolved
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class InboxMessage(Base):
    """Inbox message model."""
    __tablename__ = "inbox_messages"
    
    id = Column(String, primary_key=True)
    thread_id = Column(String, ForeignKey("inbox_threads.id"), nullable=False)
    author = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class AgentDocument(Base):
    """Agent document model."""
    __tablename__ = "agent_documents"
    
    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    filename = Column(String)
    relative_path = Column(String)
    content = Column(Text)
    content_is_truncated = Column(Boolean, default=False)
    source = Column(String)  # writer/researcher
    status = Column(String)  # pending/approved/rejected
    topic = Column(String)
    project_id = Column(String, ForeignKey("projects.id"))
    task_id = Column(String, ForeignKey("tasks.id"))
    date = Column(DateTime)
    is_read = Column(Boolean, default=False, nullable=False)
    summary = Column(Text)


class ResearchRequest(Base):
    """Research request model."""
    __tablename__ = "research_requests"
    
    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"))
    tile_id = Column(String)
    prompt = Column(Text)
    status = Column(String)
    response = Column(Text)
    author = Column(String)
    priority = Column(String)
    deliverables = Column(JSON)
    edit_history = Column(JSON)
    parent_request_id = Column(String, ForeignKey("research_requests.id"))
    assigned_worker = Column(String)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class TrackerItem(Base):
    """Tracker item model."""
    __tablename__ = "tracker_items"
    
    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    title = Column(String, nullable=False)
    status = Column(String)
    difficulty = Column(String)
    tags = Column(JSON)
    notes = Column(Text)
    links = Column(JSON)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class WorkerStatus(Base):
    """Worker status model (singleton)."""
    __tablename__ = "worker_status"
    
    id = Column(Integer, primary_key=True, default=1)  # singleton
    active = Column(Boolean, default=False, nullable=False)
    worker_id = Column(String)
    started_at = Column(DateTime)
    current_task = Column(String)
    tasks_completed = Column(Integer, default=0)
    last_heartbeat = Column(DateTime)
    ended_at = Column(DateTime)
    current_project = Column(String)
    task_log = Column(JSON)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)


class WorkerRun(Base):
    """Worker run history model."""
    __tablename__ = "worker_runs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    worker_id = Column(String)
    started_at = Column(DateTime)
    ended_at = Column(DateTime)
    tasks_completed = Column(Integer, default=0)
    timeout_reason = Column(String)
    model = Column(String)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    total_cost_usd = Column(Float)
    task_log = Column(JSON)
    commit_shas = Column(JSON)
    files_modified = Column(JSON)
    github_compare_url = Column(String)
    task_id = Column(String, ForeignKey("tasks.id"))
    succeeded = Column(Boolean)
    source = Column(String)


class AgentStatus(Base):
    """Agent status model."""
    __tablename__ = "agent_status"
    
    agent_type = Column(String, primary_key=True)
    status = Column(String)
    activity = Column(String)
    thinking = Column(String)
    current_task_id = Column(String, ForeignKey("tasks.id"))
    current_project_id = Column(String, ForeignKey("projects.id"))
    last_active_at = Column(DateTime)
    last_completed_task_id = Column(String, ForeignKey("tasks.id"))
    last_completed_at = Column(DateTime)
    stats = Column(JSON)


class TaskTemplate(Base):
    """Task template model."""
    __tablename__ = "task_templates"
    
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    items = Column(JSON)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class Reminder(Base):
    """Reminder model."""
    __tablename__ = "reminders"
    
    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    due_at = Column(DateTime, nullable=False)


class TextDump(Base):
    """Text dump model."""
    __tablename__ = "text_dumps"
    
    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"))
    text = Column(Text, nullable=False)
    status = Column(String)
    task_ids = Column(JSON)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class ResearchDoc(Base):
    """Research document model."""
    __tablename__ = "research_docs"
    
    project_id = Column(String, ForeignKey("projects.id"), primary_key=True)
    content = Column(Text)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class ResearchSource(Base):
    """Research source model."""
    __tablename__ = "research_sources"
    
    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    url = Column(String)
    title = Column(String)
    tags = Column(JSON)
    added_at = Column(DateTime, default=func.now(), nullable=False)
