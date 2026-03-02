#!/usr/bin/env bash
# ci-test.sh — Safe test runner for lobs-server
# Ensures orchestrator is disabled and no contention with live server.
set -euo pipefail

cd "$(dirname "$0")/.."
source .venv/bin/activate

# Extra safety: ensure orchestrator is disabled
export ORCHESTRATOR_ENABLED=false

# Run tests
python3 -m pytest tests/ -q --tb=short -x 2>&1
