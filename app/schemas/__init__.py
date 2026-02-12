"""Pydantic schemas."""
from app.schemas.project import Project, ProjectCreate, ProjectUpdate
from app.schemas.task import DashboardTask, DashboardTaskCreate, DashboardTaskUpdate, TaskStatusUpdate, TaskWorkStateUpdate
from app.schemas.inbox import InboxItem, InboxItemCreate, InboxThread, InboxThreadCreate, InboxMessage, InboxMessageCreate
from app.schemas.document import AgentDocument, AgentDocumentCreate, AgentDocumentUpdate
from app.schemas.research import (
    ResearchRequest, ResearchRequestCreate, ResearchRequestUpdate,
    ResearchDoc, ResearchDocUpdate,
    ResearchSource, ResearchSourceCreate,
    ResearchDeliverable, ResearchDeliverableCreate, ResearchDeliverableUpdate
)
from app.schemas.tracker import TrackerItem, TrackerItemCreate, TrackerItemUpdate
from app.schemas.worker import WorkerStatus, WorkerRun, AgentStatus
from app.schemas.template import TaskTemplate, TaskTemplateCreate, TaskTemplateUpdate, TemplateItem
from app.schemas.reminder import Reminder, ReminderCreate
from app.schemas.text_dump import TextDump, TextDumpCreate

__all__ = [
    "Project", "ProjectCreate", "ProjectUpdate",
    "DashboardTask", "DashboardTaskCreate", "DashboardTaskUpdate", "TaskStatusUpdate", "TaskWorkStateUpdate",
    "InboxItem", "InboxItemCreate", "InboxThread", "InboxThreadCreate", "InboxMessage", "InboxMessageCreate",
    "AgentDocument", "AgentDocumentCreate", "AgentDocumentUpdate",
    "ResearchRequest", "ResearchRequestCreate", "ResearchRequestUpdate",
    "ResearchDoc", "ResearchDocUpdate",
    "ResearchSource", "ResearchSourceCreate",
    "ResearchDeliverable", "ResearchDeliverableCreate", "ResearchDeliverableUpdate",
    "TrackerItem", "TrackerItemCreate", "TrackerItemUpdate",
    "WorkerStatus", "WorkerRun", "AgentStatus",
    "TaskTemplate", "TaskTemplateCreate", "TaskTemplateUpdate", "TemplateItem",
    "Reminder", "ReminderCreate",
    "TextDump", "TextDumpCreate",
]
