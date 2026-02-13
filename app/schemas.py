"""Pydantic schemas for request/response models."""

from datetime import datetime, timezone
from typing import Optional, Any
from pydantic import BaseModel, ConfigDict, field_serializer


# Project schemas
class ProjectBase(BaseModel):
    title: str
    notes: Optional[str] = None
    archived: bool = False
    type: str = "kanban"  # kanban/research/tracker
    sort_order: int = 0
    tracking: Optional[str] = None
    github_repo: Optional[str] = None
    github_label_filter: Optional[Any] = None


class ProjectCreate(ProjectBase):
    id: str


class ProjectUpdate(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None
    archived: Optional[bool] = None
    type: Optional[str] = None
    sort_order: Optional[int] = None
    tracking: Optional[str] = None
    github_repo: Optional[str] = None
    github_label_filter: Optional[Any] = None


class Project(ProjectBase):
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# Task schemas
class TaskBase(BaseModel):
    title: str
    status: str
    owner: Optional[str] = None
    work_state: Optional[str] = None
    review_state: Optional[str] = None
    project_id: Optional[str] = None
    notes: Optional[str] = None
    artifact_path: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    sort_order: int = 0
    blocked_by: Optional[list[str]] = None
    pinned: bool = False
    shape: Optional[str] = None
    github_issue_number: Optional[int] = None
    agent: Optional[str] = None


class TaskCreate(TaskBase):
    id: str


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    owner: Optional[str] = None
    work_state: Optional[str] = None
    review_state: Optional[str] = None
    project_id: Optional[str] = None
    notes: Optional[str] = None
    artifact_path: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    sort_order: Optional[int] = None
    blocked_by: Optional[list[str]] = None
    pinned: Optional[bool] = None
    shape: Optional[str] = None
    github_issue_number: Optional[int] = None
    agent: Optional[str] = None


class TaskStatusUpdate(BaseModel):
    status: str


class TaskWorkStateUpdate(BaseModel):
    work_state: str


class TaskReviewStateUpdate(BaseModel):
    review_state: str


class Task(TaskBase):
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# Inbox schemas
class InboxItemBase(BaseModel):
    title: str
    filename: Optional[str] = None
    relative_path: Optional[str] = None
    content: Optional[str] = None
    modified_at: Optional[datetime] = None
    is_read: bool = False
    summary: Optional[str] = None


class InboxItemCreate(InboxItemBase):
    id: str


class InboxItemUpdate(BaseModel):
    title: Optional[str] = None
    filename: Optional[str] = None
    relative_path: Optional[str] = None
    content: Optional[str] = None
    modified_at: Optional[datetime] = None
    is_read: Optional[bool] = None
    summary: Optional[str] = None


class InboxItem(InboxItemBase):
    id: str
    
    model_config = ConfigDict(from_attributes=True)


class InboxThreadBase(BaseModel):
    doc_id: str
    triage_status: Optional[str] = None


class InboxThreadCreate(InboxThreadBase):
    id: str


class InboxThread(InboxThreadBase):
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class InboxMessageBase(BaseModel):
    author: str
    text: str


class InboxMessageCreate(InboxMessageBase):
    id: str
    thread_id: str


class InboxMessage(InboxMessageBase):
    id: str
    thread_id: str
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class InboxTriageUpdate(BaseModel):
    triage_status: str


# AgentDocument schemas
class AgentDocumentBase(BaseModel):
    title: str
    filename: Optional[str] = None
    relative_path: Optional[str] = None
    content: Optional[str] = None
    content_is_truncated: bool = False
    source: Optional[str] = None
    status: Optional[str] = None
    topic: Optional[str] = None
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    date: Optional[datetime] = None
    is_read: bool = False
    summary: Optional[str] = None


class AgentDocumentCreate(AgentDocumentBase):
    id: str


class AgentDocumentUpdate(BaseModel):
    title: Optional[str] = None
    filename: Optional[str] = None
    relative_path: Optional[str] = None
    content: Optional[str] = None
    content_is_truncated: Optional[bool] = None
    source: Optional[str] = None
    status: Optional[str] = None
    topic: Optional[str] = None
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    date: Optional[datetime] = None
    is_read: Optional[bool] = None
    summary: Optional[str] = None


class AgentDocument(AgentDocumentBase):
    id: str
    
    model_config = ConfigDict(from_attributes=True)


# ResearchRequest schemas
class ResearchRequestBase(BaseModel):
    project_id: Optional[str] = None
    tile_id: Optional[str] = None
    prompt: Optional[str] = None
    status: Optional[str] = None
    response: Optional[str] = None
    author: Optional[str] = None
    priority: Optional[str] = None
    deliverables: Optional[Any] = None
    edit_history: Optional[Any] = None
    parent_request_id: Optional[str] = None
    assigned_worker: Optional[str] = None


class ResearchRequestCreate(ResearchRequestBase):
    id: str


class ResearchRequestUpdate(BaseModel):
    tile_id: Optional[str] = None
    prompt: Optional[str] = None
    status: Optional[str] = None
    response: Optional[str] = None
    author: Optional[str] = None
    priority: Optional[str] = None
    deliverables: Optional[Any] = None
    edit_history: Optional[Any] = None
    parent_request_id: Optional[str] = None
    assigned_worker: Optional[str] = None


class ResearchRequest(ResearchRequestBase):
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# TrackerItem schemas
class TrackerItemBase(BaseModel):
    title: str
    status: Optional[str] = None
    difficulty: Optional[str] = None
    tags: Optional[list[str]] = None
    notes: Optional[str] = None
    links: Optional[Any] = None


class TrackerItemCreate(TrackerItemBase):
    id: str
    project_id: str


class TrackerItemUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    difficulty: Optional[str] = None
    tags: Optional[list[str]] = None
    notes: Optional[str] = None
    links: Optional[Any] = None


class TrackerItem(TrackerItemBase):
    id: str
    project_id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# WorkerStatus schemas
class WorkerStatusBase(BaseModel):
    active: bool = False
    worker_id: Optional[str] = None
    started_at: Optional[datetime] = None
    current_task: Optional[str] = None
    tasks_completed: int = 0
    last_heartbeat: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    current_project: Optional[str] = None
    task_log: Optional[Any] = None
    input_tokens: int = 0
    output_tokens: int = 0


class WorkerStatusUpdate(BaseModel):
    active: Optional[bool] = None
    worker_id: Optional[str] = None
    started_at: Optional[datetime] = None
    current_task: Optional[str] = None
    tasks_completed: Optional[int] = None
    last_heartbeat: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    current_project: Optional[str] = None
    task_log: Optional[Any] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


class WorkerStatus(WorkerStatusBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)


# WorkerRun schemas
class WorkerRunBase(BaseModel):
    worker_id: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    tasks_completed: int = 0
    timeout_reason: Optional[str] = None
    model: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: Optional[float] = None
    task_log: Optional[Any] = None
    commit_shas: Optional[list[str]] = None
    files_modified: Optional[list[str]] = None
    github_compare_url: Optional[str] = None
    task_id: Optional[str] = None
    succeeded: Optional[bool] = None
    source: Optional[str] = None


class WorkerRunCreate(WorkerRunBase):
    pass


class WorkerRun(WorkerRunBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)


# AgentStatus schemas
class AgentStatusBase(BaseModel):
    status: Optional[str] = None
    activity: Optional[str] = None
    thinking: Optional[str] = None
    current_task_id: Optional[str] = None
    current_project_id: Optional[str] = None
    last_active_at: Optional[datetime] = None
    last_completed_task_id: Optional[str] = None
    last_completed_at: Optional[datetime] = None
    stats: Optional[Any] = None


class AgentStatusUpdate(AgentStatusBase):
    pass


class AgentStatus(AgentStatusBase):
    agent_type: str
    
    model_config = ConfigDict(from_attributes=True)


# TaskTemplate schemas
class TaskTemplateBase(BaseModel):
    name: str
    description: Optional[str] = None
    items: Optional[Any] = None


class TaskTemplateCreate(TaskTemplateBase):
    id: str


class TaskTemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    items: Optional[Any] = None


class TaskTemplate(TaskTemplateBase):
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# Reminder schemas
class ReminderBase(BaseModel):
    title: str
    due_at: datetime


class ReminderCreate(ReminderBase):
    id: str


class Reminder(ReminderBase):
    id: str
    
    model_config = ConfigDict(from_attributes=True)


# TextDump schemas
class TextDumpBase(BaseModel):
    project_id: Optional[str] = None
    text: str
    status: Optional[str] = None
    task_ids: Optional[list[str]] = None


class TextDumpCreate(TextDumpBase):
    id: str


class TextDumpUpdate(BaseModel):
    project_id: Optional[str] = None
    text: Optional[str] = None
    status: Optional[str] = None
    task_ids: Optional[list[str]] = None


class TextDump(TextDumpBase):
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# ResearchDoc schemas
class ResearchDocBase(BaseModel):
    content: Optional[str] = None


class ResearchDocUpdate(ResearchDocBase):
    pass


class ResearchDoc(ResearchDocBase):
    project_id: str
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# ResearchSource schemas
class ResearchSourceBase(BaseModel):
    url: Optional[str] = None
    title: Optional[str] = None
    tags: Optional[list[str]] = None


class ResearchSourceCreate(ResearchSourceBase):
    id: str
    project_id: str


class ResearchSource(ResearchSourceBase):
    id: str
    project_id: str
    added_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# Memory schemas
class MemoryBase(BaseModel):
    path: str
    agent: str = "main"
    title: str
    content: str
    memory_type: str  # long_term/daily/custom
    date: Optional[datetime] = None


class MemoryCreate(BaseModel):
    title: str
    content: str
    memory_type: str
    agent: str = "main"
    date: Optional[datetime] = None
    path: Optional[str] = None  # auto-generated for daily/long_term


class MemoryUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None


class Memory(MemoryBase):
    id: int
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class MemoryListItem(BaseModel):
    """Memory list item without full content."""
    id: int
    path: str
    agent: str
    title: str
    memory_type: str
    date: Optional[datetime] = None
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class MemorySearchResult(BaseModel):
    """Memory search result with snippet."""
    id: int
    path: str
    agent: str
    title: str
    snippet: str
    memory_type: str
    date: Optional[datetime] = None
    score: float


# Status/System Health schemas
class ServerHealth(BaseModel):
    """Server health status."""
    status: str
    uptime_seconds: int
    version: str


class OrchestratorHealth(BaseModel):
    """Orchestrator health status."""
    running: bool
    paused: bool


class WorkersHealth(BaseModel):
    """Workers health status."""
    active: int
    total_completed: int
    total_failed: int


class TasksHealth(BaseModel):
    """Tasks health status."""
    active: int
    waiting: int
    blocked: int
    completed_today: int


class MemoriesHealth(BaseModel):
    """Memories health status."""
    total: int
    today_entries: int


class InboxHealth(BaseModel):
    """Inbox health status."""
    unread: int


class SystemOverview(BaseModel):
    """Combined system health overview."""
    server: ServerHealth
    orchestrator: OrchestratorHealth
    workers: WorkersHealth
    agents: list[dict[str, Any]]
    tasks: TasksHealth
    memories: MemoriesHealth
    inbox: InboxHealth


class ActivityEvent(BaseModel):
    """Activity timeline event."""
    type: str
    title: str
    timestamp: datetime
    details: str = ""


class CostPeriod(BaseModel):
    """Cost tracking for a time period."""
    tokens_in: int
    tokens_out: int
    estimated_cost: float


class AgentCostBreakdown(BaseModel):
    """Cost breakdown by agent type."""
    type: str
    tokens_total: int
    runs: int


class CostSummary(BaseModel):
    """Token/cost tracking summary."""
    today: CostPeriod
    week: CostPeriod
    month: CostPeriod
    by_agent: list[AgentCostBreakdown]
