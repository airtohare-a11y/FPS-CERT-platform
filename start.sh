#!/bin/bash
# MECHgg startup script
# Handles port conflicts, installs deps, starts server

echo "=== MECHgg Starting ==="

# Kill anything on port 8000 (leftover from previous run)
pkill -f "uvicorn main:app" 2>/dev/null || true
pkill -f "uvicorn" 2>/dev/null || true
sleep 1
sleep 1

# Install dependencies quietly
echo "Installing dependencies..."
pip install -r requirements.txt -q 2>&1 | tail -3

# Start uvicorn
echo "Starting server on port 8000..."
exec python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --log-level info
