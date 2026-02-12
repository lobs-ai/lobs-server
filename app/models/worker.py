"""Worker and agent status models."""
from sqlalchemy import String, Boolean, Integer, Text, Float, JSON
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.database import Base


class WorkerStatus(Base):
    """Current worker status model."""
    
    __tablename__ = "worker_status"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    current_task: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tasks_completed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_heartbeat: Mapped[datetime | None] = mapped_column(nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    current_project: Mapped[str | None] = mapped_column(String(255), nullable=True)
    task_log: Mapped[list | None] = mapped_column(JSON, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class WorkerHistory(Base):
    """Worker history model (container for runs)."""
    
    __tablename__ = "worker_history"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class WorkerRun(Base):
    """Individual worker run."""
    
    __tablename__ = "worker_runs"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    tasks_completed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    task_log: Mapped[list | None] = mapped_column(JSON, nullable=True)
    commit_shas: Mapped[list | None] = mapped_column(JSON, nullable=True)
    files_modified: Mapped[list | None] = mapped_column(JSON, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)


class AgentStatus(Base):
    """Agent status model."""
    
    __tablename__ = "agent_status"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_type: Mapped[str] = mapped_column(String(100), nullable=False)  # programmer/writer/researcher/etc
    status: Mapped[str] = mapped_column(String(50), default="idle", nullable=False)  # idle/working/thinking/finalizing
    activity: Mapped[str | None] = mapped_column(String(255), nullable=True)
    thinking: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_project_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_active_at: Mapped[datetime | None] = mapped_column(nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
