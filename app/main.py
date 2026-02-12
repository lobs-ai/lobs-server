"""Main FastAPI application."""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

from app.config import settings
from app.database import init_db, AsyncSessionLocal
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
    reminders,
    agents,
    orchestrator,
)
from app.routers import text_dumps

logger = logging.getLogger(__name__)

# Global orchestrator instance
orchestrator_engine = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global orchestrator_engine
    
    # Startup
    settings.ensure_data_dir()
    await init_db()
    
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
    
    yield
    
    # Shutdown
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

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, prefix=settings.API_PREFIX)
app.include_router(projects.router, prefix=settings.API_PREFIX)
app.include_router(tasks.router, prefix=settings.API_PREFIX)
app.include_router(inbox.router, prefix=settings.API_PREFIX)
app.include_router(documents.router, prefix=settings.API_PREFIX)
app.include_router(research.router, prefix=settings.API_PREFIX)
app.include_router(tracker.router, prefix=settings.API_PREFIX)
app.include_router(worker.router, prefix=settings.API_PREFIX)
app.include_router(templates.router, prefix=settings.API_PREFIX)
app.include_router(reminders.router, prefix=settings.API_PREFIX)
app.include_router(text_dumps.router, prefix=settings.API_PREFIX)
app.include_router(agents.router, prefix=settings.API_PREFIX)
app.include_router(orchestrator.router, prefix=settings.API_PREFIX)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "lobs-server",
        "version": "0.1.0",
        "docs": "/docs",
        "api": settings.API_PREFIX,
    }
