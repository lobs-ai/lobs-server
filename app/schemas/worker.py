"""Worker and agent schemas."""
from pydantic import BaseModel, ConfigDict
from datetime import datetime


class WorkerStatus(BaseModel):
    """Worker status schema."""
    id: str
    active: bool
    worker_id: str | None = None
    started_at: datetime | None = None
    current_task: str | None = None
    tasks_completed: int
    last_heartbeat: datetime | None = None
    ended_at: datetime | None = None
    current_project: str | None = None
    task_log: list | None = None
    input_tokens: int
    output_tokens: int
    
    model_config = ConfigDict(from_attributes=True)


class WorkerRun(BaseModel):
    """Worker run schema."""
    id: str
    worker_id: str
    started_at: datetime
    ended_at: datetime | None = None
    tasks_completed: int
    model: str | None = None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    total_cost_usd: float
    task_log: list | None = None
    commit_shas: list | None = None
    files_modified: list | None = None
    task_id: str | None = None
    succeeded: bool
    source: str | None = None
    
    model_config = ConfigDict(from_attributes=True)


class AgentStatus(BaseModel):
    """Agent status schema."""
    id: str
    agent_type: str  # programmer/writer/researcher/etc
    status: str  # idle/working/thinking/finalizing
    activity: str | None = None
    thinking: str | None = None
    current_task_id: str | None = None
    current_project_id: str | None = None
    last_active_at: datetime | None = None
    stats: dict | None = None
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)
