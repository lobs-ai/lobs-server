# Developer Setup Guide

Complete setup instructions for local development on lobs-server.

**Last Updated:** 2026-02-23

---

## Prerequisites

### Required

- **Python 3.11+** (tested on 3.11, 3.12, 3.14)
- **Git** for version control
- **macOS/Linux** (Windows via WSL should work but is untested)

### Optional (for full functionality)

- **OpenClaw Gateway** — Required for orchestrator features (task execution via AI agents)
- **SQLite 3.35+** — Bundled with Python, but newer versions support better concurrency

### System Check

```bash
python3 --version  # Should be 3.11 or higher
git --version      # Any recent version
which sqlite3      # Verify SQLite is available
```

---

## Initial Setup

### 1. Clone Repository

```bash
git clone git@github.com:RafeSymonds/lobs-server.git
cd lobs-server
```

### 2. Create Virtual Environment

**Using venv (recommended):**

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

**Verify activation:**
```bash
which python  # Should point to .venv/bin/python
```

**Tip:** Add `.venv` activation to your shell profile for convenience:
```bash
# Add to ~/.zshrc or ~/.bashrc
alias lobs='cd ~/path/to/lobs-server && source .venv/bin/activate'
```

### 3. Install Dependencies

```bash
pip install --upgrade pip  # Ensure latest pip
pip install -r requirements.txt
```

**What gets installed:**

- `fastapi` — Web framework
- `uvicorn[standard]` — ASGI server (includes websocket support)
- `sqlalchemy` + `aiosqlite` — Async database ORM
- `alembic` — Database migrations (currently unused, reserved for future)
- `pydantic` + `pydantic-settings` — Data validation and config management
- `pytest` + `pytest-asyncio` + `httpx` — Testing framework
- `croniter` — Cron expression parsing (for recurring calendar events)
- `aiohttp` — Async HTTP client (for agent communication)
- `scipy` — Vector operations (for memory similarity search)

**Install time:** ~1-2 minutes on first run

**Troubleshooting:**

- If `pip install` fails with SSL errors: `pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt`
- If scipy fails to build: Install build tools (macOS: `xcode-select --install`, Linux: `sudo apt install build-essential`)

---

## Database Setup

### Automatic Initialization

The database is created **automatically on first run**. No manual setup required.

**Default location:** `./data/lobs.db`

**What happens on first startup:**
1. `data/` directory is created if missing
2. Empty SQLite database file is created
3. All tables are created via SQLAlchemy models
4. Database is configured with WAL mode (better concurrency)

### Manual Database Inspection

```bash
# Open database in SQLite CLI
sqlite3 data/lobs.db

# Useful commands:
.tables                    # List all tables
.schema tasks              # Show table schema
SELECT * FROM tasks;       # Query data
.quit                      # Exit
```

### Reset Database (Development)

```bash
# Delete database and start fresh
rm -f data/lobs.db data/lobs.db-shm data/lobs.db-wal
# Next server start will recreate it
```

**Warning:** This deletes all data. Not recommended for production.

### Database Schema

**Core tables:**
- `projects` — Top-level project containers
- `tasks` — Work items with kanban workflow
- `memories` — Memory system entries (daily notes, long-term memories)
- `topics` — Knowledge organization (research workspaces)
- `documents` — Rich documents attached to topics/projects
- `inbox` — Items pending human decision
- `calendar_events` — Calendar entries
- `api_tokens` — Authentication tokens
- `chat_messages` — WebSocket chat history
- `activity_log` — System activity timeline
- `agent_learnings` — Outcome-based agent improvements
- `token_usage` — Model cost tracking

See `app/models/` for full schema definitions.

---

## Configuration

### Environment Variables

Configuration is **optional** — defaults work for local development.

**To customize:**

```bash
# Copy template
cp .env.example .env

# Edit values
nano .env  # or your editor of choice
```

**Key settings for development:**

```bash
# Disable orchestrator if you don't have OpenClaw
ORCHESTRATOR_ENABLED=false

# Use a separate database for development
DATABASE_PATH=./data/dev.db

# Enable debug logging
LOG_LEVEL=DEBUG

# Disable backups during development
BACKUP_ENABLED=false
```

**Full configuration reference:** See [`.env.example`](.env.example)

### Without OpenClaw Gateway

Most server features work **without OpenClaw**:
- ✅ Task CRUD, project management
- ✅ Memory system, topics, documents
- ✅ Chat, calendar, inbox
- ✅ API endpoints
- ❌ Automatic task orchestration (requires OpenClaw)
- ❌ Agent spawning

**To disable orchestrator:**
```bash
echo "ORCHESTRATOR_ENABLED=false" > .env
```

---

## Running the Server

### Option 1: Using run script (recommended)

```bash
./bin/run         # Listen on all interfaces (0.0.0.0:8000)
./bin/run local   # Localhost only (127.0.0.1:8000)
```

**What it does:**
- Activates virtual environment (if exists)
- Creates `logs/` directory
- Starts uvicorn with appropriate host binding

### Option 2: Direct uvicorn command

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Useful flags:**
- `--reload` — Auto-restart on file changes (development only)
- `--port 8001` — Use different port
- `--log-level debug` — Verbose logging
- `--workers 4` — Multiple worker processes (production only, breaks --reload)

### Verify Server is Running

```bash
# Health check (no auth required)
curl http://localhost:8000/api/health

# Expected: {"status":"ok","uptime":"0:00:05","database":"connected"}
```

### Access Interactive Docs

- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

---

## Authentication

### Generate API Token

```bash
python3 bin/generate_token.py my-dev-token
```

**Output:**
```
Token generated for 'my-dev-token':
  z5mr-WWjPxAAHvRd2ZULm7HLNW1oRubXmcMiBJoEmsU

Use as: Authorization: Bearer z5mr-WWjPxAAHvRd2ZULm7HLNW1oRubXmcMiBJoEmsU
```

**Save this token** — you'll need it for:
- API requests (`Authorization: Bearer <token>`)
- Mission Control app
- Mobile app
- Testing

### Use Token in Requests

```bash
# Set as environment variable for convenience
export LOBS_TOKEN="your-token-here"

# Make authenticated request
curl -H "Authorization: Bearer $LOBS_TOKEN" \
  http://localhost:8000/api/projects
```

### Token Management

```bash
# Generate additional tokens
python3 bin/generate_token.py mission-control
python3 bin/generate_token.py mobile-app
python3 bin/generate_token.py testing

# Tokens are stored in database (api_tokens table)
sqlite3 data/lobs.db "SELECT name, token, created_at FROM api_tokens;"
```

**Security note:** Tokens are stored as plaintext in the database. This is acceptable for single-user local development, but a production system should hash tokens.

---

## Running Tests

### Full Test Suite

```bash
source .venv/bin/activate
pytest
```

**Expected output:**
```
=================== test session starts ====================
collected 150 items

tests/test_agents.py ....                            [  2%]
tests/test_backup.py ....                            [  5%]
tests/test_calendar.py ..........                    [ 12%]
...
=================== 150 passed in 12.34s ===================
```

### Verbose Output

```bash
pytest -v  # Show test names
pytest -vv # Even more verbose
```

### Run Specific Tests

```bash
# Single test file
pytest tests/test_tasks.py

# Single test function
pytest tests/test_tasks.py::test_create_task

# Tests matching pattern
pytest -k "calendar"

# Tests by marker (if markers are defined)
pytest -m "slow"
```

### Coverage Report

```bash
# Install coverage tool (if not already installed)
pip install pytest-cov

# Run tests with coverage
pytest --cov=app --cov-report=html

# View report
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

### Test Database

Tests use an **in-memory SQLite database** by default (via `conftest.py`). Each test gets a fresh database.

**To inspect test database:**

Modify `tests/conftest.py` to use a file-based database:
```python
# Change:
SQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
# To:
SQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///./test.db"
```

Then inspect with `sqlite3 test.db` after running tests.

### Writing Tests

**Example test:**

```python
# tests/test_example.py
import pytest
from httpx import AsyncClient
from app.main import app

@pytest.mark.asyncio
async def test_create_project(client: AsyncClient):
    """Test creating a project via API."""
    response = await client.post(
        "/api/projects",
        json={"name": "Test Project", "description": "Test"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Project"
    assert "id" in data
```

**Tips:**
- Use `@pytest.mark.asyncio` for async tests
- Use `client` fixture from `conftest.py` (provides authenticated AsyncClient)
- Tests in `tests/` directory are auto-discovered
- Follow naming convention: `test_*.py` files, `test_*` functions

**Test utilities:** See `tests/helpers/` for shared test utilities.

---

## Development Workflow

### Hot Reload

Server auto-restarts on code changes when using `--reload`:

```bash
uvicorn app.main:app --reload
```

**What triggers reload:**
- Python file changes in `app/`
- Changes to imported modules
- Does NOT reload on: `.env` changes (requires manual restart)

### Code Quality

**Linting (recommended):**

```bash
# Install ruff
pip install ruff

# Check for issues
ruff check app/

# Auto-fix issues
ruff check app/ --fix

# Format code
ruff format app/
```

**Type checking (optional):**

```bash
pip install mypy
mypy app/ --ignore-missing-imports
```

**Configuration:** See `.bandit`, `pyproject.toml` for tool configs.

### Logging

**View logs in real-time:**

```bash
tail -f logs/lobs.log
```

**Log levels:**
- `DEBUG` — Verbose (SQL queries, state changes)
- `INFO` — Normal operations (task assigned, backup created)
- `WARNING` — Potential issues (retry, fallback)
- `ERROR` — Failures (API error, database issue)

**Change log level:**
```bash
# In .env
LOG_LEVEL=DEBUG

# Or at runtime
uvicorn app.main:app --log-level debug
```

### Database Migrations

**Current state:** No formal migration system.

**Schema changes:**
1. Edit models in `app/models/`
2. Delete dev database: `rm data/lobs.db`
3. Restart server (tables auto-create)

**For production:** Manual SQL migration scripts (future: Alembic integration)

### Git Workflow

**Before committing:**

```bash
# Run tests
pytest

# Check code quality
ruff check app/

# Format code
ruff format app/

# Verify server starts
./bin/run &
sleep 3
curl http://localhost:8000/api/health
pkill -f "uvicorn app.main:app"
```

**Commit messages:** Follow conventional commits format (see `CONTRIBUTING.md`)

---

## Common Development Tasks

### Add a New API Endpoint

1. **Define route in router:**
   ```python
   # app/routers/example.py
   from fastapi import APIRouter
   router = APIRouter(prefix="/api/example", tags=["example"])
   
   @router.get("/items")
   async def list_items():
       return {"items": []}
   ```

2. **Register router in main app:**
   ```python
   # app/main.py
   from app.routers import example
   app.include_router(example.router)
   ```

3. **Add tests:**
   ```python
   # tests/test_example.py
   async def test_list_items(client):
       response = await client.get("/api/example/items")
       assert response.status_code == 200
   ```

### Add a New Database Model

1. **Define model:**
   ```python
   # app/models/example.py
   from sqlalchemy import Column, String, Integer
   from app.models.base import Base
   
   class Example(Base):
       __tablename__ = "examples"
       id = Column(Integer, primary_key=True)
       name = Column(String, nullable=False)
   ```

2. **Import in models/__init__.py:**
   ```python
   from app.models.example import Example
   ```

3. **Delete database to recreate schema:**
   ```bash
   rm data/lobs.db && ./bin/run
   ```

### Debug a Failing Test

```bash
# Run with print statements visible
pytest -s tests/test_example.py

# Drop into debugger on failure
pytest --pdb tests/test_example.py

# Use ipdb for better debugging
pip install ipdb
# Add `import ipdb; ipdb.set_trace()` in test
```

### Profile Performance

```bash
# Install profiling tools
pip install py-spy

# Profile running server
py-spy top --pid $(pgrep -f "uvicorn app.main:app")

# Record flamegraph
py-spy record -o profile.svg --pid $(pgrep -f "uvicorn app.main:app")
```

---

## Troubleshooting

### "Database is locked"

**Cause:** Another process is accessing the database, or WAL mode isn't enabled.

**Fix:**
```bash
# Ensure WAL mode is set (should be automatic)
sqlite3 data/lobs.db "PRAGMA journal_mode=WAL;"

# Check for other processes
lsof data/lobs.db

# If stuck, restart server and check logs
```

### "Port 8000 already in use"

**Cause:** Another process is using port 8000.

**Fix:**
```bash
# Find process using port
lsof -i :8000
# Or: sudo lsof -t -i :8000

# Kill process
kill $(lsof -t -i :8000)

# Or use a different port
uvicorn app.main:app --port 8001
```

### "ModuleNotFoundError: No module named 'app'"

**Cause:** Virtual environment not activated, or wrong working directory.

**Fix:**
```bash
# Ensure you're in project root
cd /path/to/lobs-server

# Activate venv
source .venv/bin/activate

# Verify Python location
which python  # Should be .venv/bin/python

# Reinstall if needed
pip install -r requirements.txt
```

### Tests Fail with "Database not found"

**Cause:** Tests use in-memory database by default; check `conftest.py` for issues.

**Fix:**
```bash
# Run tests with verbose output
pytest -vv tests/test_example.py

# Check conftest.py is present
ls tests/conftest.py

# Ensure pytest-asyncio is installed
pip install pytest-asyncio
```

### Orchestrator Not Starting

**Symptoms:** Server starts but no tasks execute automatically.

**Diagnosis:**
```bash
# Check logs
tail -f logs/lobs.log | grep -i orchestrator

# Check OpenClaw Gateway status
curl http://localhost:18789/status
```

**Common causes:**
- OpenClaw Gateway not running → Start it
- Wrong `OPENCLAW_GATEWAY_TOKEN` → Check `.env` matches Gateway config
- `ORCHESTRATOR_ENABLED=false` → Enable it

**Workaround:** Disable orchestrator if not needed:
```bash
echo "ORCHESTRATOR_ENABLED=false" > .env
```

### Import Errors After Dependency Update

**Cause:** Cached bytecode or outdated dependencies.

**Fix:**
```bash
# Clear Python cache
find . -type d -name __pycache__ -exec rm -r {} +
find . -type f -name "*.pyc" -delete

# Reinstall dependencies
pip install --upgrade --force-reinstall -r requirements.txt

# Restart server
```

### Tests Pass Locally but Fail in CI

**Common causes:**
- Different Python version
- Missing environment variables
- File path assumptions
- Timing issues (use `pytest-timeout`)

**Debug:**
```bash
# Test with minimal environment
unset $(env | grep LOBS | cut -d= -f1)
pytest

# Test with specific Python version
python3.11 -m pytest
```

---

## IDE Setup

### VS Code

**Recommended extensions:**
- Python (Microsoft)
- Pylance
- Ruff
- SQLite Viewer

**Settings (.vscode/settings.json):**
```json
{
  "python.defaultInterpreterPath": ".venv/bin/python",
  "python.testing.pytestEnabled": true,
  "python.testing.pytestArgs": ["-v"],
  "python.linting.enabled": true,
  "editor.formatOnSave": true,
  "python.formatting.provider": "none",
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff"
  }
}
```

### PyCharm

1. **Open project** → PyCharm auto-detects `lobs-server/`
2. **Configure interpreter:** Settings → Project → Python Interpreter → Add → Existing → `.venv/bin/python`
3. **Enable pytest:** Settings → Tools → Python Integrated Tools → Testing → pytest
4. **Run configurations:** Add "FastAPI" run config pointing to `app.main:app`

---

## Next Steps

Now that your development environment is ready:

1. **Explore the codebase:**
   - Read [ARCHITECTURE.md](ARCHITECTURE.md) for system design
   - Browse `app/routers/` to understand API structure
   - Check `app/orchestrator/` for task execution logic

2. **Review documentation:**
   - [AGENTS.md](AGENTS.md) — Complete API reference
   - [CONTRIBUTING.md](CONTRIBUTING.md) — Development workflow
   - [docs/](docs/) — Implementation guides

3. **Run the test suite:**
   ```bash
   pytest -v
   ```

4. **Make your first change:**
   - Pick an issue from GitHub Issues
   - Create a feature branch: `git checkout -b feature/my-feature`
   - Write code + tests
   - Submit PR

5. **Join the ecosystem:**
   - Set up [lobs-mission-control](https://github.com/RafeSymonds/lobs-mission-control) (desktop app)
   - Install mobile app (if available)
   - Connect everything via API tokens

---

## Getting Help

- **Documentation:** See [docs/README.md](docs/README.md) for full doc index
- **Known Issues:** Check [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md)
- **API Reference:** [AGENTS.md](AGENTS.md)
- **Architecture:** [ARCHITECTURE.md](ARCHITECTURE.md)
- **Contributing:** [CONTRIBUTING.md](CONTRIBUTING.md)

---

**Happy coding!** 🚀
