#!/bin/bash
set -e

cleanup() {
    echo "[SHUTDOWN] Terminating all background processes..."
    kill $(jobs -p) 2>/dev/null || true
    wait
    echo "[SHUTDOWN] Clean exit"
}

trap cleanup EXIT SIGTERM SIGINT

echo "[STARTUP] Starting Gunicorn webserver..."
uv run gunicorn \
    -w "${GUNICORN_WORKERS:-2}" \
    --threads "${GUNICORN_THREADS:-2}" \
    -b "0.0.0.0:${PORT:-10000}" \
    --access-logfile - \
    --error-logfile - \
    webserver:app &

WEBSERVER_PID=$!
echo "[STARTUP] Gunicorn started with PID $WEBSERVER_PID"

sleep 5 #locally it was fine..but adding 3 more sec for render cuz of shitty cpu

echo "[STARTUP] Starting Discord bot..."
uv run python main.py &

BOT_PID=$!
echo "[STARTUP] Discord bot started with PID $BOT_PID"

wait -n

echo "[ERROR] One process exited unexpectedly, shutting down..."
exit 1
# If we get here, one process died, so better kill everything lmao
