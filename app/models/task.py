"""Task model."""
from sqlalchemy import String, Boolean, Integer, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.database import Base


class DashboardTask(Base):
    """Dashboard task model."""
    
    __tablename__ = "tasks"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)  # inbox/active/completed/rejected/waiting_on
    owner: Mapped[str] = mapped_column(String(50), nullable=False)  # lobs/rafe
    work_state: Mapped[str] = mapped_column(String(50), default="not_started", nullable=False)  # not_started/in_progress/blocked
    review_state: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)  # pending/approved/changes_requested/rejected
    project_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("projects.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    blocked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    shape: Mapped[str | None] = mapped_column(String(50), nullable=True)  # deep/shallow/creative/waiting/admin
    github_issue_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
