"""Template schemas."""
from pydantic import BaseModel, ConfigDict
from datetime import datetime


class TemplateItemBase(BaseModel):
    """Base template item schema."""
    title: str
    notes: str | None = None
    sort_order: int = 0


class TemplateItem(TemplateItemBase):
    """Full template item schema."""
    id: str
    template_id: str
    
    model_config = ConfigDict(from_attributes=True)


class TaskTemplateBase(BaseModel):
    """Base task template schema."""
    name: str
    description: str | None = None


class TaskTemplateCreate(TaskTemplateBase):
    """Schema for creating a task template."""
    id: str
    items: list[TemplateItemBase] = []


class TaskTemplateUpdate(BaseModel):
    """Schema for updating a task template."""
    name: str | None = None
    description: str | None = None
    items: list[TemplateItemBase] | None = None


class TaskTemplate(TaskTemplateBase):
    """Full task template schema."""
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)
