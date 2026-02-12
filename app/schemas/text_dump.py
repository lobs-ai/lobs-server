"""Text dump schemas."""
from pydantic import BaseModel, ConfigDict
from datetime import datetime


class TextDumpBase(BaseModel):
    """Base text dump schema."""
    text: str
    project_id: str | None = None
    status: str = "pending"  # pending/processing/completed
    task_ids: list | None = None


class TextDumpCreate(TextDumpBase):
    """Schema for creating a text dump."""
    id: str


class TextDump(TextDumpBase):
    """Full text dump schema."""
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)
