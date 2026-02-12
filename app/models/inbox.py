"""Inbox models."""
from sqlalchemy import String, Boolean, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.database import Base


class InboxItem(Base):
    """Inbox item model (markdown documents)."""
    
    __tablename__ = "inbox_items"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    modified_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class InboxThread(Base):
    """Inbox thread model (conversation threads within documents)."""
    
    __tablename__ = "inbox_threads"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(36), ForeignKey("inbox_items.id"), nullable=False)
    triage_status: Mapped[str] = mapped_column(String(50), default="needs_response", nullable=False)  # needs_response/pending/resolved
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class InboxMessage(Base):
    """Individual message within an inbox thread."""
    
    __tablename__ = "inbox_messages"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(36), ForeignKey("inbox_threads.id"), nullable=False)
    author: Mapped[str] = mapped_column(String(100), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)
