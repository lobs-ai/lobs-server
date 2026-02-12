"""Agent document schemas."""
from pydantic import BaseModel, ConfigDict
from datetime import datetime, date


class AgentDocumentBase(BaseModel):
    """Base agent document schema."""
    title: str
    filename: str
    relative_path: str
    content: str
    source: str  # writer/researcher
    status: str = "pending"  # pending/approved/rejected
    topic: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    date: date | None = None
    is_read: bool = False
    summary: str | None = None


class AgentDocumentCreate(AgentDocumentBase):
    """Schema for creating an agent document."""
    id: str


class AgentDocumentUpdate(BaseModel):
    """Schema for updating an agent document."""
    title: str | None = None
    filename: str | None = None
    relative_path: str | None = None
    content: str | None = None
    source: str | None = None
    status: str | None = None
    topic: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    date: date | None = None
    is_read: bool | None = None
    summary: str | None = None


class AgentDocument(AgentDocumentBase):
    """Full agent document schema."""
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)
