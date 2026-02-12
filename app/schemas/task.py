"""Task schemas."""
from pydantic import BaseModel, ConfigDict
from datetime import datetime


class DashboardTaskBase(BaseModel):
    """Base task schema."""
    title: str
    status: str  # inbox/active/completed/rejected/waiting_on
    owner: str  # lobs/rafe
    work_state: str = "not_started"  # not_started/in_progress/blocked
    review_state: str = "pending"  # pending/approved/changes_requested/rejected
    project_id: str | None = None
    notes: str | None = None
    artifact_path: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    sort_order: int = 0
    blocked_by: str | None = None
    pinned: bool = False
    shape: str | None = None  # deep/shallow/creative/waiting/admin
    github_issue_number: int | None = None
    agent: str | None = None


class DashboardTaskCreate(DashboardTaskBase):
    """Schema for creating a task."""
    id: str


class DashboardTaskUpdate(BaseModel):
    """Schema for updating a task."""
    title: str | None = None
    status: str | None = None
    owner: str | None = None
    work_state: str | None = None
    review_state: str | None = None
    project_id: str | None = None
    notes: str | None = None
    artifact_path: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    sort_order: int | None = None
    blocked_by: str | None = None
    pinned: bool | None = None
    shape: str | None = None
    github_issue_number: int | None = None
    agent: str | None = None


class TaskStatusUpdate(BaseModel):
    """Schema for updating task status."""
    status: str


class TaskWorkStateUpdate(BaseModel):
    """Schema for updating task work state."""
    work_state: str


class DashboardTask(DashboardTaskBase):
    """Full task schema."""
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)
