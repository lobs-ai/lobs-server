"""Main FastAPI application."""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

# Configure logging FIRST, before any other imports that might log
from app.config import settings
from app.logging_config import setup_logging

setup_logging(
    log_level=settings.LOG_LEVEL,
    log_format=settings.LOG_FORMAT,
    log_dir=settings.LOG_DIR,
)

from app.database import init_db, AsyncSessionLocal
from app.backup import backup_manager
from app.middleware import RequestLoggingMiddleware, NetworkGuardMiddleware
from app.auth import require_auth
from fastapi import Depends
from app.routers import (
    health,
    projects,
    tasks,
    inbox,
    documents,
    research,
    tracker,
    worker,
    templates,
    calendar,
    agents,
    orchestrator_reflections,
    orchestrator_admin,
    orchestrator_workers,
    backup,
    chat,
    memories,
    status,
    topics,
    workspaces,
    governance,
    intent,
    usage,
    webhooks,
    learning,
    knowledge,
    reliability,
)
from app.routers import text_dumps
from app.routers.workflows import router as workflows_router, runs_router as workflow_runs_router, events_router as workflow_events_router, subs_router as workflow_subs_router
from app.routers import integrations as integrations_router
from app.routers import learning as learning_router
from app.routers import brief as brief_router

logger = logging.getLogger(__name__)

# Global orchestrator instance
orchestrator_engine = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global orchestrator_engine
    
    # Startup
    from datetime import datetime, timezone
    app.state.start_time = datetime.now(timezone.utc)
    
    settings.ensure_data_dir()
    await init_db()
    
    # Sync memories from filesystem on startup
    try:
        from app.services.memory_sync import sync_agent_memories
        async with AsyncSessionLocal() as db:
            logger.info("Syncing agent memories from filesystem...")
            stats = await sync_agent_memories(db)
            await db.commit()
            logger.info(f"Memory sync completed: {stats}")
    except Exception as e:
        logger.error(f"Failed to sync memories on startup: {e}", exc_info=True)
    
    # Start backup manager
    try:
        await backup_manager.start()
    except Exception as e:
        logger.error(f"Failed to start backup manager: {e}", exc_info=True)
    
    # Start orchestrator if enabled
    if settings.ORCHESTRATOR_ENABLED:
        try:
            from app.orchestrator import OrchestratorEngine
            orchestrator_engine = OrchestratorEngine(AsyncSessionLocal)
            await orchestrator_engine.start()
            logger.info("Orchestrator started successfully")
        except Exception as e:
            logger.error(f"Failed to start orchestrator: {e}", exc_info=True)
            orchestrator_engine = None
    else:
        logger.info("Orchestrator disabled via config")
    
    # Store orchestrator in app state for access by routers
    app.state.orchestrator = orchestrator_engine
    
    # Start chat typing indicator cleanup task
    from app.services.chat_manager import manager as chat_manager
    import asyncio
    cleanup_task = asyncio.create_task(chat_manager.cleanup_typing_indicators())
    
    yield
    
    # Stop chat cleanup task
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    
    # Shutdown
    # Stop backup manager
    try:
        await backup_manager.stop()
    except Exception as e:
        logger.error(f"Error stopping backup manager: {e}", exc_info=True)
    
    # Stop orchestrator
    if orchestrator_engine:
        try:
            await orchestrator_engine.stop(timeout=60.0)
            logger.info("Orchestrator stopped successfully")
        except Exception as e:
            logger.error(f"Error stopping orchestrator: {e}", exc_info=True)


app = FastAPI(
    title="lobs-server",
    description="FastAPI + SQLite REST API for task and project management",
    version="0.1.0",
    lifespan=lifespan,
)

# Request logging middleware
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(NetworkGuardMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
# Health endpoint stays public (no auth)
app.include_router(health.router, prefix=settings.API_PREFIX)

# All other endpoints require authentication
app.include_router(projects.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(tasks.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(inbox.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(documents.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(topics.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(research.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(tracker.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(worker.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(templates.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(calendar.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(text_dumps.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(agents.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(orchestrator_reflections.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(orchestrator_admin.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(orchestrator_workers.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(backup.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(chat.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(memories.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(status.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(workspaces.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(governance.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(intent.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(usage.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(usage.routing_router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(learning.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(knowledge.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(reliability.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(brief_router.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])

# Workflow engine
app.include_router(workflows_router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(workflow_runs_router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(workflow_events_router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(workflow_subs_router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])

# Webhooks - receive endpoint is public (external services), management requires auth
app.include_router(integrations_router.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(learning_router.router, prefix=settings.API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(webhooks.router, prefix=settings.API_PREFIX)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "lobs-server",
        "version": "0.1.0",
        "docs": "/docs",
        "api": settings.API_PREFIX,
    }
