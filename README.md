# lobs-server

FastAPI + SQLite REST API for task and project management.

## Features

- **Async SQLAlchemy** with aiosqlite
- **16 data models** covering projects, tasks, inbox, documents, research, tracking, workers, agents, templates, reminders, and more
- **Complete REST API** with pagination support
- **CORS enabled** for all origins
- **Auto-generated OpenAPI docs** at `/docs`

## Quick Start

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run server
./run.sh
```

Server will start at `http://0.0.0.0:8000`

API documentation: `http://localhost:8000/docs`

## Configuration

Environment variables:
- `DATABASE_PATH` - Path to SQLite database (default: `./data/lobs.db`)

## API Endpoints

### Core Resources
- **Projects**: `/api/projects`
- **Tasks**: `/api/tasks`
- **Inbox**: `/api/inbox`
- **Documents**: `/api/documents`
- **Research**: `/api/research/{project_id}`
- **Tracker**: `/api/tracker/{project_id}/items`
- **Worker**: `/api/worker/status`, `/api/worker/history`
- **Templates**: `/api/templates`
- **Reminders**: `/api/reminders`
- **Text Dumps**: `/api/text-dumps`
- **Agents**: `/api/agents`
- **Health**: `/api/health`

All list endpoints support `limit` and `offset` query parameters for pagination.

## Data Models

1. **Project** - Project management (kanban/research/tracker types)
2. **Task** - Task tracking with states, blocking, GitHub integration
3. **InboxItem** - Inbox items for triage
4. **InboxThread** - Discussion threads on inbox items
5. **InboxMessage** - Messages within threads
6. **AgentDocument** - Documents created by agents (writer/researcher)
7. **ResearchRequest** - Research task requests
8. **TrackerItem** - Items in tracker projects
9. **WorkerStatus** - Current worker status (singleton)
10. **WorkerRun** - Historical worker run data
11. **AgentStatus** - Status tracking for different agent types
12. **TaskTemplate** - Reusable task templates
13. **Reminder** - Time-based reminders
14. **TextDump** - Text dump processing queue
15. **ResearchDoc** - Research documentation per project
16. **ResearchSource** - Research source references

## Development

The database is automatically created on first run at `./data/lobs.db`.

All timestamps are stored in UTC and returned in ISO 8601 format.
