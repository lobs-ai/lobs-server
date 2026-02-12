#!/bin/bash
# Start lobs-server

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Start uvicorn server
uvicorn app.main:app --host 0.0.0.0 --port 8000
