"""Shared test fixtures and configuration."""

import pytest
import pytest_asyncio
from datetime import datetime
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.main import app
from app.database import Base, get_db
from app.config import settings

# Use in-memory SQLite for tests
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

# Create test engine
test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
)

# Create test session factory
TestSessionLocal = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_test_db() -> AsyncSession:
    """Override database dependency for tests."""
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@pytest_asyncio.fixture(scope="function", autouse=True)
async def setup_test_db():
    """Create tables before each test and drop after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield
    
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session():
    """Provide a database session for tests."""
    async with TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client():
    """Provide an async HTTP client for testing."""
    # Override the database dependency
    app.dependency_overrides[get_db] = get_test_db
    
    # Disable orchestrator for tests
    settings.ORCHESTRATOR_ENABLED = False
    
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        yield ac
    
    # Clean up
    app.dependency_overrides.clear()


# Sample data fixtures
@pytest_asyncio.fixture
async def sample_project(client: AsyncClient):
    """Create a sample project."""
    project_data = {
        "id": "test-project-1",
        "title": "Test Project",
        "notes": "This is a test project",
        "archived": False,
        "type": "kanban",
        "sort_order": 0
    }
    response = await client.post("/api/projects", json=project_data)
    assert response.status_code == 200
    return response.json()


@pytest_asyncio.fixture
async def sample_task(client: AsyncClient, sample_project):
    """Create a sample task."""
    task_data = {
        "id": "test-task-1",
        "title": "Test Task",
        "status": "inbox",
        "project_id": sample_project["id"],
        "notes": "This is a test task",
        "sort_order": 0,
        "pinned": False
    }
    response = await client.post("/api/tasks", json=task_data)
    assert response.status_code == 200
    return response.json()


@pytest_asyncio.fixture
async def sample_inbox_item(client: AsyncClient):
    """Create a sample inbox item."""
    item_data = {
        "id": "test-inbox-1",
        "title": "Test Inbox Item",
        "filename": "test.txt",
        "content": "Test content",
        "is_read": False
    }
    response = await client.post("/api/inbox", json=item_data)
    assert response.status_code == 200
    return response.json()


@pytest_asyncio.fixture
async def sample_document(client: AsyncClient):
    """Create a sample document."""
    doc_data = {
        "id": "test-doc-1",
        "title": "Test Document",
        "content": "Test document content",
        "source": "writer",
        "status": "pending",
        "is_read": False,
        "content_is_truncated": False
    }
    response = await client.post("/api/documents", json=doc_data)
    assert response.status_code == 200
    return response.json()


@pytest_asyncio.fixture
async def sample_template(client: AsyncClient):
    """Create a sample template."""
    template_data = {
        "id": "test-template-1",
        "name": "Test Template",
        "description": "A test template",
        "items": [{"title": "Task 1"}, {"title": "Task 2"}]
    }
    response = await client.post("/api/templates", json=template_data)
    assert response.status_code == 200
    return response.json()


@pytest_asyncio.fixture
async def sample_reminder(client: AsyncClient):
    """Create a sample reminder."""
    reminder_data = {
        "id": "test-reminder-1",
        "title": "Test Reminder",
        "due_at": "2026-12-31T23:59:59Z"
    }
    response = await client.post("/api/reminders", json=reminder_data)
    assert response.status_code == 200
    return response.json()
