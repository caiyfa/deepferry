#!/usr/bin/env bash
#
# DeepFerry — one-command dev launcher
#
# Brings up the full stack and opens the browser:
#   1. Docker backend:  MySQL + PostgreSQL + MCP server  (port 8000)
#   2. Frontend:        Vite dev server                  (port 5173)
#   3. Browser:         http://localhost:5173
#
# Usage:
#   ./dev.sh            Start everything (build Docker image if stale)
#   ./dev.sh --rebuild  Force-rebuild the Docker image before starting
#
# Ctrl+C stops the frontend. Docker services keep running so you can
# restart only the frontend with another ./dev.sh.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_URL="http://localhost:8000"
FRONTEND_URL="http://localhost:5173"
HEALTH_TIMEOUT=180  # seconds before giving up
VITE_PID=""

# ── Pretty output ──────────────────────────────────────────────────────
log()  { printf '\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── Wait until a URL responds 200 ──────────────────────────────────────
wait_for_url() {
  local url="$1" name="$2" elapsed=0
  while ! curl -sf "$url" >/dev/null 2>&1; do
    sleep 1
    elapsed=$((elapsed + 1))
    if [ "$elapsed" -ge "$HEALTH_TIMEOUT" ]; then
      die "Timed out waiting for $name ($url) after ${HEALTH_TIMEOUT}s"
    fi
  done
}

# ── Open browser (cross-platform) ──────────────────────────────────────
open_browser() {
  local url="$1"
  if command -v open >/dev/null 2>&1; then
    open "$url"                    # macOS
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url"                # Linux
  elif command -v start >/dev/null 2>&1; then
    start "$url"                   # Windows (Git Bash / WSL)
  else
    warn "Cannot auto-open browser. Visit: $url"
  fi
}

# ── Cleanup on exit ────────────────────────────────────────────────────
cleanup() {
  local rc=$?
  echo ""
  if [ -n "$VITE_PID" ] && kill -0 "$VITE_PID" 2>/dev/null; then
    log "Stopping frontend dev server..."
    kill "$VITE_PID" 2>/dev/null || true
    wait "$VITE_PID" 2>/dev/null || true
  fi
  ok "Frontend stopped. Docker services are still running."
  ok "Stop Docker with:  docker compose --profile full down"
  exit "$rc"
}
trap cleanup EXIT INT TERM

# ── Preflight ──────────────────────────────────────────────────────────
log "Preflight checks..."
command -v docker >/dev/null 2>&1 || die "docker not found in PATH"
docker info >/dev/null 2>&1 || die "Docker daemon is not running. Start Docker Desktop first."
command -v npm   >/dev/null 2>&1 || die "npm not found in PATH"
ok "Environment OK"

# ── 1. Start Docker backend ────────────────────────────────────────────
if [ "${1:-}" = "--rebuild" ]; then
  log "Rebuilding Docker image..."
  docker compose build deepferry
fi

log "Starting Docker services (MySQL + PostgreSQL + deepferry)..."
docker compose --profile full up -d --wait
ok "Docker services healthy"

log "Verifying backend health..."
wait_for_url "$BACKEND_URL/health" "backend"
ok "Backend ready: $(curl -sf "$BACKEND_URL/health")"

# ── 2. Start frontend dev server ───────────────────────────────────────
cd "$ROOT_DIR/frontend"

FRONTEND_ALREADY_UP=false
if curl -sf "$FRONTEND_URL" >/dev/null 2>&1; then
  FRONTEND_ALREADY_UP=true
  ok "Frontend already running on :5173 — reusing"
fi

if [ "$FRONTEND_ALREADY_UP" = "false" ]; then
  if [ ! -d node_modules ]; then
    log "Installing frontend dependencies (first run)..."
    npm install
  fi

  log "Starting Vite dev server..."
  npm run dev &
  VITE_PID=$!

  log "Waiting for Vite to be ready..."
  wait_for_url "$FRONTEND_URL" "frontend"
  ok "Frontend ready"
fi

# ── 3. Open browser ────────────────────────────────────────────────────
log "Opening browser → $FRONTEND_URL"
open_browser "$FRONTEND_URL"

echo ""
ok "DeepFerry is live!"
echo ""
echo "    Backend API :  $BACKEND_URL"
echo "    Frontend UI :  $FRONTEND_URL"
echo ""
echo "    Press Ctrl+C to stop the frontend."
echo "    ───────────────────────────────────────────────────────"
echo ""

# Block until Vite exits (Ctrl+C) — only when we started it
if [ "$FRONTEND_ALREADY_UP" = "false" ] && [ -n "$VITE_PID" ]; then
  wait "$VITE_PID"
else
  ok "Frontend was already running. Open $FRONTEND_URL anytime."
fi
