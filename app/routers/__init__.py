"""API routers."""
from app.routers.projects import router as projects_router
from app.routers.tasks import router as tasks_router
from app.routers.inbox import router as inbox_router
from app.routers.documents import router as documents_router
from app.routers.research import router as research_router
from app.routers.tracker import router as tracker_router
from app.routers.worker import router as worker_router
from app.routers.templates import router as templates_router
from app.routers.reminders import router as reminders_router
from app.routers.text_dumps import router as text_dumps_router
from app.routers.health import router as health_router

__all__ = [
    "projects_router",
    "tasks_router",
    "inbox_router",
    "documents_router",
    "research_router",
    "tracker_router",
    "worker_router",
    "templates_router",
    "reminders_router",
    "text_dumps_router",
    "health_router",
]
