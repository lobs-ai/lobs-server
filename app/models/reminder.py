"""Reminder model."""
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.database import Base


class Reminder(Base):
    """Reminder model."""
    
    __tablename__ = "reminders"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    due_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)
