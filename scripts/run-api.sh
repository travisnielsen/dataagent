#!/bin/bash

# Run the FastAPI backend server

cd "$(dirname "$0")/../api" || exit 1

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Check for .env file
if [ ! -f ".env" ]; then
    echo "Warning: .env file not found. Copy .env.example to .env and configure it."
    echo "  cp api/.env.example api/.env"
    exit 1
fi

echo "Starting FastAPI server on http://localhost:8000..."
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
