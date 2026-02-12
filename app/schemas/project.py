"""Project schemas."""
from pydantic import BaseModel, ConfigDict
from datetime import datetime


class ProjectBase(BaseModel):
    """Base project schema."""
    title: str
    notes: str | None = None
    archived: bool = False
    type: str  # kanban/research/tracker
    sort_order: int = 0
    tracking: str = "local"  # local/github
    github_config: dict | None = None


class ProjectCreate(ProjectBase):
    """Schema for creating a project."""
    id: str


class ProjectUpdate(BaseModel):
    """Schema for updating a project."""
    title: str | None = None
    notes: str | None = None
    archived: bool | None = None
    type: str | None = None
    sort_order: int | None = None
    tracking: str | None = None
    github_config: dict | None = None


class Project(ProjectBase):
    """Full project schema."""
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)
