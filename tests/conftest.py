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
async def test_token(db_session):
    """Create a test API token."""
    import secrets
    from app.models import APIToken
    
    token = secrets.token_urlsafe(32)
    api_token = APIToken(token=token, name="test-token")
    db_session.add(api_token)
    await db_session.commit()
    
    return token


@pytest_asyncio.fixture
async def client(test_token):
    """Provide an async HTTP client for testing."""
    # Override the database dependency
    app.dependency_overrides[get_db] = get_test_db
    
    # Disable orchestrator for tests
    settings.ORCHESTRATOR_ENABLED = False
    
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {test_token}"}
    ) as ac:
        # Store test_token as an attribute for WebSocket tests
        ac.test_token = test_token
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
def auth_headers(test_token):
    """Provide authorization headers."""
    return {"Authorization": f"Bearer {test_token}"}


@pytest_asyncio.fixture
def project_id(sample_project):
    """Provide a project ID."""
    return sample_project["id"]
