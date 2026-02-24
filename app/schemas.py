"""Pydantic schemas for request/response models."""

from datetime import datetime, timezone
from typing import Optional, Any
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, field_serializer


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
    repo_path: Optional[str] = None


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
    repo_path: Optional[str] = None


class Project(ProjectBase):
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# Task schemas
class TaskBase(BaseModel):
    title: str
    status: str
    owner: Optional[str] = "lobs"
    work_state: Optional[str] = "not_started"
    review_state: Optional[str] = None
    project_id: Optional[str] = None
    notes: Optional[str] = None
    artifact_path: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    sort_order: Optional[int] = 0
    blocked_by: Optional[list[str]] = None
    pinned: Optional[bool] = False
    shape: Optional[str] = None
    github_issue_number: Optional[int] = None
    agent: Optional[str] = None
    model_tier: Optional[str] = None  # micro/small/medium/standard/strong (NULL = auto)
    external_source: Optional[str] = None
    external_id: Optional[str] = None
    external_updated_at: Optional[datetime] = None
    sync_state: Optional[str] = None
    conflict_payload: Optional[Any] = None
    workspace_id: Optional[str] = None


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
    model_tier: Optional[str] = None  # micro/small/medium/standard/strong (NULL = auto)
    external_source: Optional[str] = None
    external_id: Optional[str] = None
    external_updated_at: Optional[datetime] = None
    sync_state: Optional[str] = None
    conflict_payload: Optional[Any] = None
    workspace_id: Optional[str] = None


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
# Topic schemas
class TopicBase(BaseModel):
    title: str
    description: Optional[str] = None
    icon: Optional[str] = None
    linked_project_id: Optional[str] = None
    auto_created: bool = False


class TopicCreate(TopicBase):
    id: str


class TopicUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    linked_project_id: Optional[str] = None
    auto_created: Optional[bool] = None


class Topic(TopicBase):
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class AgentDocumentBase(BaseModel):
    title: str
    filename: Optional[str] = None
    relative_path: Optional[str] = None
    content: Optional[str] = None
    content_is_truncated: bool = False
    source: Optional[str] = None
    status: Optional[str] = None
    topic_id: Optional[str] = None
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
    topic_id: Optional[str] = None
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
    topic_id: Optional[str] = None
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
    topic_id: Optional[str] = None
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


# TrackerEntry schemas (personal work tracker)
class TrackerEntryBase(BaseModel):
    type: str  # work_session/deadline/note
    raw_text: str
    duration: Optional[int] = None  # minutes
    category: Optional[str] = None
    due_date: Optional[datetime] = None
    estimated_minutes: Optional[int] = None
    commitment_type: Optional[str] = None  # class/interview/scrim/personal/other
    priority_score: Optional[int] = None
    next_action: Optional[str] = None
    escalation_task_id: Optional[str] = None
    last_escalated_at: Optional[datetime] = None


class TrackerEntryCreate(TrackerEntryBase):
    id: str


class TrackerEntryUpdate(BaseModel):
    type: Optional[str] = None
    raw_text: Optional[str] = None
    duration: Optional[int] = None
    category: Optional[str] = None
    due_date: Optional[datetime] = None
    estimated_minutes: Optional[int] = None
    commitment_type: Optional[str] = None
    priority_score: Optional[int] = None
    next_action: Optional[str] = None
    escalation_task_id: Optional[str] = None
    last_escalated_at: Optional[datetime] = None


class TrackerEntry(TrackerEntryBase):
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class TrackerSummary(BaseModel):
    """Summary statistics for work tracker."""
    total_entries: int
    work_sessions_count: int
    total_minutes_logged: int
    deadlines_count: int
    upcoming_deadlines: int
    notes_count: int
    categories: dict[str, int]  # category -> count
    last_7_days_minutes: int


class DeadlineEntry(BaseModel):
    """Deadline entry for listing."""
    id: str
    raw_text: str
    category: Optional[str]
    due_date: datetime
    estimated_minutes: Optional[int]
    commitment_type: Optional[str] = None
    priority_score: Optional[int] = None
    next_action: Optional[str] = None
    escalation_task_id: Optional[str] = None
    created_at: datetime


# TrackerNotification schemas
class TrackerNotification(BaseModel):
    id: str
    deadline_key: str
    notification_type: str
    message_summary: str | None = None
    sent_at: datetime
    cooldown_hours: int = 12
    model_config = ConfigDict(from_attributes=True)


class TrackerNotificationCreate(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    deadline_key: str
    notification_type: str
    message_summary: str | None = None
    cooldown_hours: int = 12


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
# ScheduledEvent schemas (replaces Reminder)
class ScheduledEventBase(BaseModel):
    title: str
    description: Optional[str] = None
    event_type: str  # reminder, task, meeting
    scheduled_at: datetime
    end_at: Optional[datetime] = None
    all_day: bool = False
    recurrence_rule: Optional[str] = None
    recurrence_end: Optional[datetime] = None
    target_type: str  # self, agent, orchestrator
    target_agent: Optional[str] = None
    task_project_id: Optional[str] = None
    task_notes: Optional[str] = None
    task_priority: Optional[str] = None


class ScheduledEventCreate(ScheduledEventBase):
    id: str


class ScheduledEventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    event_type: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    all_day: Optional[bool] = None
    recurrence_rule: Optional[str] = None
    recurrence_end: Optional[datetime] = None
    target_type: Optional[str] = None
    target_agent: Optional[str] = None
    task_project_id: Optional[str] = None
    task_notes: Optional[str] = None
    task_priority: Optional[str] = None
    status: Optional[str] = None


class ScheduledEventResponse(ScheduledEventBase):
    id: str
    status: str
    last_fired_at: Optional[datetime] = None
    next_fire_at: Optional[datetime] = None
    fire_count: int
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class ScheduledEventList(BaseModel):
    """List of scheduled events with metadata."""
    events: list[ScheduledEventResponse]
    total: int


class CalendarDayEvents(BaseModel):
    """Events grouped by a single date."""
    date: str  # YYYY-MM-DD
    events: list[ScheduledEventResponse]


class CalendarView(BaseModel):
    """Events grouped by date for calendar view."""
    start_date: str
    end_date: str
    days: list[CalendarDayEvents]


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


class WorkspaceBase(BaseModel):
    slug: str
    name: str
    description: Optional[str] = None
    is_default: bool = False


class WorkspaceCreate(WorkspaceBase):
    id: str


class WorkspaceUpdate(BaseModel):
    slug: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    is_default: Optional[bool] = None


class Workspace(WorkspaceBase):
    id: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class WorkspaceFileBase(BaseModel):
    workspace_id: str
    path: str
    title: Optional[str] = None
    content: Optional[str] = None
    content_hash: Optional[str] = None
    file_metadata: Optional[Any] = None


class WorkspaceFileCreate(WorkspaceFileBase):
    id: str


class WorkspaceFile(WorkspaceFileBase):
    id: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class FileLinkBase(BaseModel):
    workspace_id: str
    source_file_id: str
    target_file_id: str
    relation: str = "references"
    weight: float = 1.0


class FileLinkCreate(FileLinkBase):
    id: str


class FileLink(FileLinkBase):
    id: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AgentProfileBase(BaseModel):
    agent_type: str
    display_name: Optional[str] = None
    prompt_template: Optional[str] = None
    config: Optional[Any] = None
    policy_tier: str = "standard"
    active: bool = True


class AgentProfileCreate(AgentProfileBase):
    id: str


class AgentProfile(AgentProfileBase):
    id: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class RoutineRegistryBase(BaseModel):
    name: str
    description: Optional[str] = None

    trigger: Optional[str] = None
    hook: Optional[str] = None

    schedule: Optional[str] = None
    schedule_timezone: str = "UTC"
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None

    enabled: bool = True
    paused_until: Optional[datetime] = None
    cooldown_seconds: Optional[int] = None
    max_runs_per_day: Optional[int] = None
    pending_confirmation: bool = False

    execution_policy: str = "auto"  # auto|notify|confirm
    # Backward compat: retained for older clients
    policy_tier: str = "standard"

    run_count: int = 0

    config: Optional[Any] = None


class RoutineRegistryCreate(RoutineRegistryBase):
    id: str


class RoutineRegistry(RoutineRegistryBase):
    id: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class RoutineAuditEventBase(BaseModel):
    routine_id: str
    routine_name: str
    action: str
    status: str = "ok"
    message: Optional[str] = None
    event_metadata: Optional[Any] = None


class RoutineAuditEventCreate(RoutineAuditEventBase):
    id: str


class RoutineAuditEvent(RoutineAuditEventBase):
    id: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class KnowledgeRequestBase(BaseModel):
    workspace_id: Optional[str] = None
    project_id: Optional[str] = None
    topic_id: Optional[str] = None
    prompt: str
    status: str = "pending"
    response: Optional[str] = None
    source_research_request_id: Optional[str] = None


class KnowledgeRequestCreate(KnowledgeRequestBase):
    id: str


class KnowledgeRequest(KnowledgeRequestBase):
    id: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ModelUsageEventCreate(BaseModel):
    timestamp: Optional[datetime] = None
    source: str = "unknown"
    provider: Optional[str] = None
    model: str
    route_type: str = "api"  # api|subscription
    task_type: str = "other"
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    requests: int = 1
    latency_ms: Optional[int] = None
    status: str = "success"
    estimated_cost_usd: Optional[float] = None
    error_code: Optional[str] = None
    event_metadata: Optional[Any] = None


class ModelUsageEvent(ModelUsageEventCreate):
    id: str
    estimated_cost_usd: float
    timestamp: datetime
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ModelPricingBase(BaseModel):
    provider: str
    model: str
    route_type: str = "api"
    input_per_1m_usd: float = 0.0
    output_per_1m_usd: float = 0.0
    cached_input_per_1m_usd: float = 0.0
    effective_date: Optional[datetime] = None
    active: bool = True
    notes: Optional[str] = None


class ModelPricingCreate(ModelPricingBase):
    id: Optional[str] = None


class ModelPricing(ModelPricingBase):
    id: str
    effective_date: datetime
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class UsageProviderSummary(BaseModel):
    provider: str
    requests: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    estimated_cost_usd: float
    avg_latency_ms: Optional[float] = None
    error_rate: float


class UsageModelSummary(BaseModel):
    provider: str
    model: str
    route_type: str
    requests: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    estimated_cost_usd: float
    avg_latency_ms: Optional[float] = None
    error_rate: float


class UsageSummaryResponse(BaseModel):
    window: str
    period_start: datetime
    period_end: datetime
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_cached_tokens: int
    total_estimated_cost_usd: float
    by_provider: list[UsageProviderSummary]
    by_model: list[UsageModelSummary]


class UsageProjectionResponse(BaseModel):
    month_start: datetime
    now: datetime
    month_to_date_cost_usd: float
    current_daily_burn_usd: float
    projected_month_end_cost_usd: float


class BudgetLimits(BaseModel):
    monthly_total_usd: float = 0.0
    daily_alert_usd: float = 0.0
    per_provider_monthly_usd: dict[str, float] = Field(default_factory=dict)
    per_task_hard_cap_usd: float = 0.0


class RoutingPolicy(BaseModel):
    subscription_first_task_types: list[str] = Field(default_factory=lambda: ["inbox", "quick_summary", "triage"])
    subscription_providers: list[str] = Field(default_factory=list)
    subscription_models: list[str] = Field(default_factory=list)
    fallback_chains: dict[str, list[str]] = Field(default_factory=dict)
    quality_preference: list[str] = Field(default_factory=lambda: ["claude", "openai", "kimi", "minimax"])

    # Backward compatibility aliases (legacy field name)
    gemini_first_task_types: list[str] = Field(default_factory=list)


# Webhook schemas
class WebhookRegistrationBase(BaseModel):
    name: str
    provider: str  # github/slack/linear/custom
    secret: str
    event_filters: Optional[list[str]] = None
    target_action: str  # create_task/trigger_agent/update_project/custom
    action_config: Optional[dict[str, Any]] = None
    active: bool = True


class WebhookRegistrationCreate(WebhookRegistrationBase):
    pass


class WebhookRegistrationUpdate(BaseModel):
    name: Optional[str] = None
    secret: Optional[str] = None
    event_filters: Optional[list[str]] = None
    target_action: Optional[str] = None
    action_config: Optional[dict[str, Any]] = None
    active: Optional[bool] = None


class WebhookRegistration(WebhookRegistrationBase):
    id: str
    created_at: datetime
    updated_at: datetime
    last_received_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)


class WebhookEventBase(BaseModel):
    registration_id: Optional[str] = None
    provider: str
    event_type: str
    payload: dict[str, Any]
    headers: Optional[dict[str, str]] = None
    signature_valid: bool = False
    status: str = "pending"
    processing_result: Optional[dict[str, Any]] = None


class WebhookEvent(WebhookEventBase):
    id: str
    created_at: datetime
    processed_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)


class WebhookDeliveryBase(BaseModel):
    event_id: str
    attempt: int = 1
    status: str  # success/failed/retrying
    error_message: Optional[str] = None
    next_retry_at: Optional[datetime] = None


class WebhookDelivery(WebhookDeliveryBase):
    id: str
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)
