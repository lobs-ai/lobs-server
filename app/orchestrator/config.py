"""Orchestrator configuration."""

import os
from pathlib import Path
from typing import Any

# Polling interval in seconds
POLL_INTERVAL = 10

# Maximum number of concurrent workers
MAX_WORKERS = 5

# Worker health monitoring timeouts (in seconds)
WORKER_WARNING_TIMEOUT = 1800  # 30 minutes
WORKER_KILL_TIMEOUT = 3600  # 1 hour
WORKER_HEARTBEAT_TIMEOUT = 300  # 5 minutes

# Base directory for repos (will be configured via app.config)
BASE_DIR = Path(os.environ.get("LOBS_PROJECTS_DIR", str(Path.home())))

# Worker results directory
WORKER_RESULTS_DIR = Path.home() / ".openclaw" / "worker-results"
WORKER_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Agent types
AGENT_TYPES = ("architect", "programmer", "researcher", "reviewer", "writer")
