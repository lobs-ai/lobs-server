# lobs-server

FastAPI + SQLite backend for the lobs-control task management system.

## Overview

This server replaces the git-based JSON file backend with a REST API + SQLite database. The SwiftUI macOS dashboard communicates with this API instead of managing files directly.

## Features

- **FastAPI** - Modern async Python web framework
- **SQLite + SQLAlchemy** - Async database ORM
- **Alembic** - Database migrations
- **CORS enabled** - Ready for dashboard integration
- **REST API** - Full CRUD for all models

## Data Models

- **Projects** - Kanban/research/tracker projects
- **Tasks** - Dashboard tasks with status tracking
- **Inbox** - Threaded markdown documents
- **Documents** - Agent-generated content
- **Research** - Research requests, docs, sources, deliverables
- **Tracker** - Project tracker items
- **Worker** - Worker and agent status
- **Templates** - Task templates
- **Reminders** - Scheduled reminders
- **Text Dumps** - Batch text processing

## Setup

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run database migrations
alembic upgrade head

# Start server
./run.sh
```

## API Endpoints

All endpoints are prefixed with `/api`:

- `GET/POST /api/projects` - List/create projects
- `GET/PUT/DELETE /api/projects/{id}` - Get/update/delete project
- `POST /api/projects/{id}/archive` - Archive project
- `GET/POST /api/tasks` - List/create tasks (filterable)
- `GET/PUT/DELETE /api/tasks/{id}` - Get/update/delete task
- `PATCH /api/tasks/{id}/status` - Update task status
- `PATCH /api/tasks/{id}/work-state` - Update task work state
- `GET/POST /api/inbox` - List/create inbox items
- `GET /api/inbox/{id}/thread` - Get inbox thread
- `POST /api/inbox/{id}/thread/messages` - Add thread message
- `GET/POST /api/documents` - List/create documents
- `POST /api/documents/{id}/archive` - Archive document
- `GET/POST /api/research/requests` - Research requests
- `GET/PUT /api/research/{projectId}/doc` - Research document
- `GET/POST /api/research/{projectId}/sources` - Research sources
- `GET/POST /api/research/{projectId}/deliverables` - Research deliverables
- `GET/POST /api/tracker/{projectId}/items` - Tracker items
- `GET /api/worker/status` - Current worker status
- `GET /api/worker/history` - Worker run history
- `GET /api/worker/agents` - All agent statuses
- `GET/POST /api/templates` - Task templates
- `GET/POST /api/reminders` - Reminders
- `POST /api/text-dumps` - Text dumps
- `GET /api/health` - Health check

## Configuration

Environment variables (or `.env` file):

```env
DATABASE_URL=sqlite+aiosqlite:///~/lobs-server/data/lobs.db
HOST=0.0.0.0
PORT=8000
CORS_ORIGINS=["*"]
```

## Development

```bash
# Run with auto-reload
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Create new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head
```

## Database

SQLite database location: `~/lobs-server/data/lobs.db`

All timestamps are stored in UTC (ISO 8601 format).

## License

Private project.
