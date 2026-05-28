#!/usr/bin/env bash
# -----------------------------------------------------------
# start.sh - Production startup script
#
# Usage:
#   ./start.sh              # Build frontend + start all
#   ./start.sh --skip-build # Skip frontend build, use existing dist/
#
# Services started (by proxy.py):
#   1. Flask backend       -> http://localhost:3001
#   2. MCP server          -> http://localhost:3003   (AI chat capabilities)
#   3. Reverse proxy       -> http://localhost:8080
#      - Static files from dist/
#      - /api/* proxied to backend (incl. AI chat SSE)
#
# External prerequisite (NOT started here):
#   - OpenCode agent runtime: `opencode serve` on http://localhost:4096
#     (holds provider API keys in its own global config). proxy.py only warns
#     if it is unreachable.
#
# Environment variables:
#   PROXY_PORT   - Proxy listen port  (default: 8080)
#   BACKEND_URL  - Backend origin     (default: http://127.0.0.1:3001)
#   MCP_PYTHON   - Interpreter for the MCP server (default: mcp-server/.venv)
# -----------------------------------------------------------

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -W 2>/dev/null || pwd)"
PROJECT_DIR="$SCRIPT_DIR"
SERVER_DIR="$PROJECT_DIR/server"

SKIP_BUILD=false
for arg in "$@"; do
    case "$arg" in
        --skip-build) SKIP_BUILD=true ;;
    esac
done

echo "========================================"
echo "  Check-Manage Production Startup"
echo "========================================"
echo

# ----- Step 0: Kill residual processes ----------------------------------
echo "[0/3] Cleaning up residual processes ..."

# Kill processes on proxy port
PROXY_PORT="${PROXY_PORT:-8080}"
if command -v netstat.exe &>/dev/null; then
    pids=$(netstat.exe -ano 2>/dev/null \
        | grep -E "LISTENING" \
        | grep -E ":${PROXY_PORT}\s" \
        | awk '{print $NF}' \
        | sort -u)
    for pid in $pids; do
        echo "       Killing PID $pid (port $PROXY_PORT)"
        taskkill.exe /F /PID "$pid" 2>/dev/null || true
    done
fi

# Kill processes on backend port (3001) and MCP port (3003).
# NOTE: port 4096 (OpenCode) is intentionally left alone — it is an external
# service managed outside this script.
if command -v netstat.exe &>/dev/null; then
    for port in 3001 3003; do
        pids=$(netstat.exe -ano 2>/dev/null \
            | grep -E "LISTENING" \
            | grep -E ":${port}\s" \
            | awk '{print $NF}' \
            | sort -u)
        for pid in $pids; do
            echo "       Killing PID $pid (port $port)"
            taskkill.exe /F /PID "$pid" 2>/dev/null || true
        done
    done
fi

echo "       Done."
echo

# ----- Step 1: Database migration --------------------------------------
echo "[1/3] Running database migration ..."
(cd "$SERVER_DIR" && python init_db.py)
echo

# ----- Step 2: Build frontend ------------------------------------------
if [ "$SKIP_BUILD" = true ]; then
    echo "[2/3] Skipping frontend build (--skip-build)"
else
    echo "[2/3] Building frontend ..."
    (cd "$PROJECT_DIR" && npm run build)
fi

if [ ! -f "$PROJECT_DIR/dist/index.html" ]; then
    echo "[ERROR] dist/index.html not found. Run without --skip-build."
    exit 1
fi
echo

# ----- Step 3: Start proxy (which also starts backend) -----------------
echo "[3/3] Starting services ..."
echo
cd "$SERVER_DIR" && python proxy.py
