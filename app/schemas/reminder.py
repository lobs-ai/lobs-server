"""Reminder schemas."""
from pydantic import BaseModel, ConfigDict
from datetime import datetime


class ReminderBase(BaseModel):
    """Base reminder schema."""
    title: str
    due_at: datetime


class ReminderCreate(ReminderBase):
    """Schema for creating a reminder."""
    id: str


class Reminder(ReminderBase):
    """Full reminder schema."""
    id: str
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)
