#!/bin/sh
set -e

echo "Starting PWA proxy on port 5004 (background)..."
python pwa/server.py &
PWA_PID=$!

echo "Starting Trading App backend on port 5003..."
trap "kill $PWA_PID 2>/dev/null; exit" TERM INT
exec python server.py
