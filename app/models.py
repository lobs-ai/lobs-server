"""SQLAlchemy models for all database tables."""

from datetime import datetime
from sqlalchemy import Column, String, Text, Boolean, Integer, Float, DateTime, ForeignKey, JSON, UniqueConstraint
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
    repo_path = Column(String)  # Absolute path to project repo on disk
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class Task(Base):
    """Task model."""
    __tablename__ = "tasks"
    
    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    status = Column(String, nullable=False)  # inbox/active/completed/rejected/waiting_on
    owner = Column(String)  # lobs/rafe
    work_state = Column(String, default="not_started")
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
    external_source = Column(String)  # github
    external_id = Column(String)
    external_updated_at = Column(DateTime)
    sync_state = Column(String)  # synced/local_changed/conflict
    conflict_payload = Column(JSON)
    workspace_id = Column(String, ForeignKey("workspaces.id"))
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    
    # Model routing
    model_tier = Column(String)  # Optional override: cheap/standard/strong (NULL = auto)
    
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
    last_processed_message_id = Column(String)  # Track last processed message to avoid re-processing
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


class Topic(Base):
    """Topic model for knowledge organization."""
    __tablename__ = "topics"
    
    id = Column(String, primary_key=True)
    title = Column(String, nullable=False, unique=True)
    description = Column(Text)
    icon = Column(String)  # emoji or icon name
    linked_project_id = Column(String, ForeignKey("projects.id"))
    auto_created = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


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
    topic_id = Column(String, ForeignKey("topics.id"))
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
    topic_id = Column(String, ForeignKey("topics.id"))
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


class TrackerEntry(Base):
    """Work tracker entry model for personal productivity tracking."""
    __tablename__ = "tracker_entries"
    
    id = Column(String, primary_key=True)
    type = Column(String, nullable=False)  # work_session/deadline/note
    raw_text = Column(Text, nullable=False)
    
    # Parsed fields
    duration = Column(Integer)  # minutes
    category = Column(String)
    due_date = Column(DateTime)
    estimated_minutes = Column(Integer)
    
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class TrackerNotification(Base):
    """Tracks sent notifications to prevent duplicates."""
    __tablename__ = "tracker_notifications"
    
    id = Column(String, primary_key=True)
    deadline_key = Column(String, nullable=False)  # e.g. "cse590-hw2-2026-02-16"
    notification_type = Column(String, nullable=False)  # "deadline_reminder", "analysis_update", etc
    message_summary = Column(Text)  # what was sent
    sent_at = Column(DateTime, default=func.now(), nullable=False)
    cooldown_hours = Column(Integer, default=12)  # don't re-notify within this window


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
    summary = Column(String)  # Work summary from .work-summary file


class OrchestratorSetting(Base):
    """Runtime-updatable orchestrator settings."""
    __tablename__ = "orchestrator_settings"

    key = Column(String, primary_key=True)
    value = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


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


class AgentCapability(Base):
    """Capabilities an agent can execute, used for dynamic routing."""
    __tablename__ = "agent_capabilities"
    __table_args__ = (
        UniqueConstraint("agent_type", "capability", name="ix_agent_capability_unique"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_type = Column(String, nullable=False, index=True)
    capability = Column(String, nullable=False, index=True)
    confidence = Column(Float, default=0.5, nullable=False)
    capability_metadata = Column(JSON)
    source = Column(String, default="identity", nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class AgentReflection(Base):
    """Structured outputs from scheduled strategic reflection/diagnostics."""
    __tablename__ = "agent_reflections"

    id = Column(String, primary_key=True)
    agent_type = Column(String, nullable=False, index=True)
    reflection_type = Column(String, nullable=False, index=True)  # strategic/diagnostic/daily_compression
    status = Column(String, nullable=False, default="pending", index=True)  # pending/completed/failed
    window_start = Column(DateTime)
    window_end = Column(DateTime)
    context_packet = Column(JSON)
    result = Column(JSON)
    inefficiencies = Column(JSON)
    system_risks = Column(JSON)
    missed_opportunities = Column(JSON)
    identity_adjustments = Column(JSON)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    completed_at = Column(DateTime)


class DiagnosticTriggerEvent(Base):
    """Auditable reactive diagnostic trigger events and outcomes."""
    __tablename__ = "diagnostic_trigger_events"

    id = Column(String, primary_key=True)
    trigger_type = Column(String, nullable=False, index=True)
    trigger_key = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, index=True)  # fired/suppressed/spawned/failed/completed
    suppression_reason = Column(String)
    agent_type = Column(String, index=True)
    task_id = Column(String, ForeignKey("tasks.id"), index=True)
    project_id = Column(String, ForeignKey("projects.id"), index=True)
    trigger_payload = Column(JSON)
    diagnostic_reflection_id = Column(String, ForeignKey("agent_reflections.id"), index=True)
    diagnostic_result = Column(JSON)
    remediation_task_ids = Column(JSON)
    outcome = Column(JSON)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class AgentInitiative(Base):
    """Initiatives proposed by agents and governed by Lobs sweep logic."""
    __tablename__ = "agent_initiatives"

    id = Column(String, primary_key=True)
    proposed_by_agent = Column(String, nullable=False, index=True)
    source_reflection_id = Column(String, ForeignKey("agent_reflections.id"))
    owner_agent = Column(String, index=True)
    title = Column(String, nullable=False)
    description = Column(Text)
    category = Column(String, nullable=False, index=True)
    risk_tier = Column(String, nullable=False, default="A", index=True)
    policy_lane = Column(String, nullable=False, default="review_required", index=True)
    policy_reason = Column(Text)
    status = Column(String, nullable=False, default="proposed", index=True)
    score = Column(Float)
    rationale = Column(Text)
    approved_by = Column(String)
    selected_agent = Column(String, index=True)
    selected_project_id = Column(String, ForeignKey("projects.id"))
    task_id = Column(String, ForeignKey("tasks.id"))
    decision_summary = Column(Text)
    learning_feedback = Column(Text)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class InitiativeDecisionRecord(Base):
    """Auditable decision records for every initiative evaluated by Lobs."""
    __tablename__ = "initiative_decision_records"

    id = Column(String, primary_key=True)
    initiative_id = Column(String, ForeignKey("agent_initiatives.id"), nullable=False, index=True)
    sweep_id = Column(String, ForeignKey("system_sweeps.id"), index=True)
    decision = Column(String, nullable=False, index=True)  # approve/defer/reject/review
    decided_by = Column(String, nullable=False, default="lobs")
    decision_summary = Column(Text)
    overlap_with_ids = Column(JSON)
    contradiction_with_ids = Column(JSON)
    capability_gap = Column(Boolean, default=False, nullable=False)
    source_reflection_ids = Column(JSON)
    task_id = Column(String, ForeignKey("tasks.id"))
    created_at = Column(DateTime, default=func.now(), nullable=False)


class AgentIdentityVersion(Base):
    """Versioned compressed identity/memory snapshots per agent."""
    __tablename__ = "agent_identity_versions"
    __table_args__ = (
        UniqueConstraint("agent_type", "version", name="ix_agent_identity_version_unique"),
    )

    id = Column(String, primary_key=True)
    agent_type = Column(String, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    identity_text = Column(Text, nullable=False)
    summary = Column(Text)
    active = Column(Boolean, default=False, nullable=False, index=True)
    window_start = Column(DateTime)
    window_end = Column(DateTime)
    changed_heuristics = Column(JSON)
    removed_rules = Column(JSON)
    validation_status = Column(String, nullable=False, default="pending", index=True)
    validation_reason = Column(Text)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class SystemSweep(Base):
    """Global Lobs sweep output after reflection/compression batches."""
    __tablename__ = "system_sweeps"

    id = Column(String, primary_key=True)
    sweep_type = Column(String, nullable=False, index=True)  # reflection_batch/daily_cleanup
    status = Column(String, nullable=False, default="pending", index=True)
    window_start = Column(DateTime)
    window_end = Column(DateTime)
    summary = Column(JSON)
    decisions = Column(JSON)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    completed_at = Column(DateTime)


class TaskTemplate(Base):
    """Task template model."""
    __tablename__ = "task_templates"
    
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    items = Column(JSON)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class ScheduledEvent(Base):
    """Scheduled event model for calendar/reminders/recurring tasks."""
    __tablename__ = "scheduled_events"
    
    id = Column(String, primary_key=True)  # uuid
    title = Column(String, nullable=False)
    description = Column(Text)
    
    # Type: "reminder" (notify user), "task" (create task for agent), "meeting" (personal calendar)
    event_type = Column(String, nullable=False)
    
    # Scheduling
    scheduled_at = Column(DateTime, nullable=False)  # when to fire (UTC)
    end_at = Column(DateTime)  # optional end time (for meetings/blocks)
    all_day = Column(Boolean, default=False)
    
    # Recurrence (optional)
    recurrence_rule = Column(String)  # cron expression, e.g. "0 9 * * 1-5" for weekdays 9am
    recurrence_end = Column(DateTime)  # when recurrence stops
    
    # Target
    target_type = Column(String, nullable=False)  # "self" (Rafe), "agent", "orchestrator"
    target_agent = Column(String)  # agent type if target_type is "agent"
    
    # Task template (for event_type="task")
    task_project_id = Column(String, ForeignKey("projects.id"))
    task_notes = Column(Text)
    task_priority = Column(String)  # optional priority
    
    # Status
    status = Column(String, nullable=False, default="pending")  # pending, fired, cancelled, recurring
    last_fired_at = Column(DateTime)
    next_fire_at = Column(DateTime)  # computed from recurrence
    fire_count = Column(Integer, default=0)
    
    # Metadata
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


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


class ChatSession(Base):
    """Chat session model."""
    __tablename__ = "chat_sessions"
    
    id = Column(String, primary_key=True)
    session_key = Column(String, nullable=False, unique=True, index=True)
    label = Column(String)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    last_message_at = Column(DateTime)


class ChatMessage(Base):
    """Chat message model."""
    __tablename__ = "chat_messages"
    
    id = Column(String, primary_key=True)
    session_key = Column(String, ForeignKey("chat_sessions.session_key"), nullable=False, index=True)
    role = Column(String, nullable=False)  # user/assistant/system
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    message_metadata = Column(JSON)


class Memory(Base):
    """Memory model for second brain feature."""
    __tablename__ = "memories"
    __table_args__ = (
        UniqueConstraint("path", "agent", name="ix_memories_path_agent"),
    )
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    path = Column(String, nullable=False, index=True)
    agent = Column(String, nullable=False, default="main", index=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    memory_type = Column(String, nullable=False)  # long_term/daily/custom
    date = Column(DateTime, nullable=True)  # for daily memories
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class APIToken(Base):
    """API token model for authentication."""
    __tablename__ = "api_tokens"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    last_used_at = Column(DateTime)
    active = Column(Boolean, default=True, nullable=False)


class Workspace(Base):
    """Workspace tenancy root."""
    __tablename__ = "workspaces"

    id = Column(String, primary_key=True)
    slug = Column(String, nullable=False, unique=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class WorkspaceFile(Base):
    """File metadata in a workspace."""
    __tablename__ = "workspace_files"
    __table_args__ = (UniqueConstraint("workspace_id", "path", name="ix_workspace_file_path_unique"),)

    id = Column(String, primary_key=True)
    workspace_id = Column(String, ForeignKey("workspaces.id"), nullable=False, index=True)
    path = Column(String, nullable=False)
    title = Column(String)
    content = Column(Text)
    content_hash = Column(String)
    file_metadata = Column(JSON)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class FileLink(Base):
    """Link graph between workspace files."""
    __tablename__ = "file_links"

    id = Column(String, primary_key=True)
    workspace_id = Column(String, ForeignKey("workspaces.id"), nullable=False, index=True)
    source_file_id = Column(String, ForeignKey("workspace_files.id"), nullable=False)
    target_file_id = Column(String, ForeignKey("workspace_files.id"), nullable=False)
    relation = Column(String, nullable=False, default="references")
    weight = Column(Float, default=1.0)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class AgentProfile(Base):
    """Agent profile/prompt registry."""
    __tablename__ = "agent_profiles"

    id = Column(String, primary_key=True)
    agent_type = Column(String, nullable=False, unique=True, index=True)
    display_name = Column(String)
    prompt_template = Column(Text)
    config = Column(JSON)
    policy_tier = Column(String, default="standard", nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class RoutineRegistry(Base):
    """Routine registry for automation policies.

    Phase 3 expands the registry from a passive catalog to an executable schedule.

    Notes:
    - `execution_policy` controls what happens when a routine is due:
      * auto: execute immediately
      * notify: create a notification item, do not execute
      * confirm: request confirmation via inbox item; requires manual trigger
    - `schedule` is expected to be a 5-field cron expression (M H DOM MON DOW).
    """

    __tablename__ = "routine_registry"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(Text)

    # Hook selection
    trigger = Column(String)  # optional: category/grouping
    hook = Column(String, nullable=True)  # execution hook key (e.g., "reflection_cycle")

    # Scheduling
    schedule = Column(String)  # cron expression
    schedule_timezone = Column(String, nullable=False, default="UTC")
    next_run_at = Column(DateTime, nullable=True, index=True)
    last_run_at = Column(DateTime, nullable=True)

    # Controls
    enabled = Column(Boolean, default=True, nullable=False)
    paused_until = Column(DateTime, nullable=True)
    cooldown_seconds = Column(Integer, nullable=True)
    max_runs_per_day = Column(Integer, nullable=True)
    pending_confirmation = Column(Boolean, default=False, nullable=False)

    # Policy
    execution_policy = Column(String, default="auto", nullable=False)  # auto|notify|confirm
    # Backward compat with older clients/tests
    policy_tier = Column(String, default="standard", nullable=False)

    # Stats
    run_count = Column(Integer, default=0, nullable=False)

    config = Column(JSON)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class RoutineAuditEvent(Base):
    """Audit trail of routine scheduling/execution decisions."""

    __tablename__ = "routine_audit_events"

    id = Column(String, primary_key=True)
    routine_id = Column(String, ForeignKey("routine_registry.id"), nullable=False, index=True)
    routine_name = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False)  # due|executed|notified|confirmation_requested|error
    status = Column(String, nullable=False, default="ok")  # ok|error
    message = Column(Text)
    event_metadata = Column(JSON)
    created_at = Column(DateTime, default=func.now(), nullable=False, index=True)


class KnowledgeRequest(Base):
    """Knowledge request model (successor to research requests)."""
    __tablename__ = "knowledge_requests"

    id = Column(String, primary_key=True)
    workspace_id = Column(String, ForeignKey("workspaces.id"))
    project_id = Column(String, ForeignKey("projects.id"))
    topic_id = Column(String, ForeignKey("topics.id"))
    prompt = Column(Text, nullable=False)
    status = Column(String, default="pending", nullable=False)
    response = Column(Text)
    source_research_request_id = Column(String, ForeignKey("research_requests.id"))
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class ModelUsageEvent(Base):
    """Normalized usage event across API and subscription routes."""
    __tablename__ = "model_usage_events"

    id = Column(String, primary_key=True)
    timestamp = Column(DateTime, default=func.now(), nullable=False, index=True)
    source = Column(String, nullable=False, default="unknown", index=True)
    provider = Column(String, nullable=False, index=True)
    model = Column(String, nullable=False, index=True)
    route_type = Column(String, nullable=False, default="api", index=True)  # api|subscription
    task_type = Column(String, nullable=False, default="other", index=True)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    cached_tokens = Column(Integer, nullable=False, default=0)
    requests = Column(Integer, nullable=False, default=1)
    latency_ms = Column(Integer)
    status = Column(String, nullable=False, default="success", index=True)
    estimated_cost_usd = Column(Float, nullable=False, default=0.0)
    error_code = Column(String)
    event_metadata = Column(JSON)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class ModelPricing(Base):
    """Versioned model pricing catalog for cost estimation."""
    __tablename__ = "model_pricing"

    id = Column(String, primary_key=True)
    provider = Column(String, nullable=False, index=True)
    model = Column(String, nullable=False, index=True)
    route_type = Column(String, nullable=False, default="api", index=True)
    input_per_1m_usd = Column(Float, nullable=False, default=0.0)
    output_per_1m_usd = Column(Float, nullable=False, default=0.0)
    cached_input_per_1m_usd = Column(Float, nullable=False, default=0.0)
    effective_date = Column(DateTime, nullable=False, default=func.now(), index=True)
    active = Column(Boolean, nullable=False, default=True, index=True)
    notes = Column(Text)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class ControlLoopEvent(Base):
    """Durable control-loop event queue and audit trail."""
    __tablename__ = "control_loop_events"

    id = Column(String, primary_key=True)
    event_type = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="pending", index=True)  # pending/completed/failed/ignored
    payload = Column(JSON)
    result = Column(JSON)
    created_at = Column(DateTime, default=func.now(), nullable=False, index=True)
    processed_at = Column(DateTime)


class ControlLoopHeartbeat(Base):
    """Latest control-loop heartbeat for status/observability."""
    __tablename__ = "control_loop_heartbeats"

    id = Column(String, primary_key=True)
    phase = Column(String, nullable=False)
    last_heartbeat_at = Column(DateTime, nullable=False, index=True)
    heartbeat_metadata = Column("metadata", JSON)
