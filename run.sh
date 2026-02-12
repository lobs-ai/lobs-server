#!/bin/bash
# Start lobs-server

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Ensure log directory exists
mkdir -p logs

# Start uvicorn server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level warning
