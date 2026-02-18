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

# Control-plane identities (not execution workers)
# Includes synthetic/internal routing agents (e.g., sink) that should never be
# considered real execution agents for reflection/sweep/capability logic.
CONTROL_PLANE_AGENTS = ("lobs", "project-manager", "sink")

# OpenClaw Gateway API configuration
GATEWAY_URL = os.environ.get("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")
GATEWAY_TOKEN = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "341c3e8015df9c77f6ed4cba1359403135994364caf7c668")
