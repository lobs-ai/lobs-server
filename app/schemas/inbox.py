"""Inbox schemas."""
from pydantic import BaseModel, ConfigDict
from datetime import datetime


class InboxItemBase(BaseModel):
    """Base inbox item schema."""
    title: str
    filename: str
    relative_path: str
    content: str
    modified_at: datetime
    is_read: bool = False
    summary: str | None = None


class InboxItemCreate(InboxItemBase):
    """Schema for creating an inbox item."""
    id: str


class InboxItem(InboxItemBase):
    """Full inbox item schema."""
    id: str
    
    model_config = ConfigDict(from_attributes=True)


class InboxMessageBase(BaseModel):
    """Base inbox message schema."""
    author: str
    text: str


class InboxMessageCreate(InboxMessageBase):
    """Schema for creating an inbox message."""
    id: str


class InboxMessage(InboxMessageBase):
    """Full inbox message schema."""
    id: str
    thread_id: str
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class InboxThreadBase(BaseModel):
    """Base inbox thread schema."""
    doc_id: str
    triage_status: str = "needs_response"  # needs_response/pending/resolved


class InboxThreadCreate(InboxThreadBase):
    """Schema for creating an inbox thread."""
    id: str


class InboxThread(InboxThreadBase):
    """Full inbox thread schema."""
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)
