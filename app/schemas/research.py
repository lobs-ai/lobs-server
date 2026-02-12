"""Research schemas."""
from pydantic import BaseModel, ConfigDict
from datetime import datetime


class ResearchRequestBase(BaseModel):
    """Base research request schema."""
    project_id: str
    tile_id: str | None = None
    prompt: str
    status: str = "open"  # open/in_progress/done/completed/blocked
    response: str | None = None
    author: str
    priority: str = "normal"  # low/normal/high/urgent
    deliverables: str | None = None
    edit_history: list | None = None
    parent_request_id: str | None = None
    assigned_worker: str | None = None


class ResearchRequestCreate(ResearchRequestBase):
    """Schema for creating a research request."""
    id: str


class ResearchRequestUpdate(BaseModel):
    """Schema for updating a research request."""
    tile_id: str | None = None
    prompt: str | None = None
    status: str | None = None
    response: str | None = None
    priority: str | None = None
    deliverables: str | None = None
    edit_history: list | None = None
    parent_request_id: str | None = None
    assigned_worker: str | None = None


class ResearchRequest(ResearchRequestBase):
    """Full research request schema."""
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class ResearchDocBase(BaseModel):
    """Base research doc schema."""
    content: str


class ResearchDocUpdate(BaseModel):
    """Schema for updating research doc."""
    content: str


class ResearchDoc(ResearchDocBase):
    """Full research doc schema."""
    id: str
    project_id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class ResearchSourceBase(BaseModel):
    """Base research source schema."""
    title: str
    url: str | None = None
    notes: str | None = None


class ResearchSourceCreate(ResearchSourceBase):
    """Schema for creating a research source."""
    id: str
    project_id: str


class ResearchSource(ResearchSourceBase):
    """Full research source schema."""
    id: str
    project_id: str
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class ResearchDeliverableBase(BaseModel):
    """Base research deliverable schema."""
    title: str
    filename: str
    content: str


class ResearchDeliverableCreate(ResearchDeliverableBase):
    """Schema for creating a research deliverable."""
    id: str
    project_id: str


class ResearchDeliverableUpdate(BaseModel):
    """Schema for updating a research deliverable."""
    title: str | None = None
    filename: str | None = None
    content: str | None = None


class ResearchDeliverable(ResearchDeliverableBase):
    """Full research deliverable schema."""
    id: str
    project_id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)
