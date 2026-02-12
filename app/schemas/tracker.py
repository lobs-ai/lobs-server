"""Tracker schemas."""
from pydantic import BaseModel, ConfigDict
from datetime import datetime


class TrackerItemBase(BaseModel):
    """Base tracker item schema."""
    title: str
    status: str = "not_started"  # not_started/in_progress/done/skipped
    difficulty: int | None = None
    tags: list | None = None
    notes: str | None = None
    links: list | None = None


class TrackerItemCreate(TrackerItemBase):
    """Schema for creating a tracker item."""
    id: str
    project_id: str


class TrackerItemUpdate(BaseModel):
    """Schema for updating a tracker item."""
    title: str | None = None
    status: str | None = None
    difficulty: int | None = None
    tags: list | None = None
    notes: str | None = None
    links: list | None = None


class TrackerItem(TrackerItemBase):
    """Full tracker item schema."""
    id: str
    project_id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)
