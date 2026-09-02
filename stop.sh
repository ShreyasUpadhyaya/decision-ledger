#!/usr/bin/env bash
# Stops the backend/frontend processes run.sh started, and the local Mongo container.
# Safe to run even if nothing is up.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT/backend"
STATE_DIR="$ROOT/.run"

kill_port() {
  local port="$1" label="$2"
  if command -v netstat >/dev/null 2>&1 && command -v taskkill >/dev/null 2>&1; then
    # Windows / Git Bash: kill by whichever PID actually owns the port — the PID
    # `nohup cmd & echo $!` captured can be an MSYS wrapper, not the real process.
    local pid
    pid="$(netstat -ano 2>/dev/null | grep -E ":${port}[[:space:]].*LISTENING" | awk '{print $NF}' | sort -u | head -n1)"
    if [ -n "${pid:-}" ]; then
      taskkill //F //PID "$pid" >/dev/null 2>&1 && echo "Stopped $label (pid $pid, port $port)"
    fi
  elif command -v lsof >/dev/null 2>&1; then
    # macOS / Linux
    local pid
    pid="$(lsof -ti tcp:"$port" 2>/dev/null | head -n1)"
    if [ -n "${pid:-}" ]; then
      kill "$pid" 2>/dev/null && echo "Stopped $label (pid $pid, port $port)"
    fi
  fi
}

kill_port 8000 "backend"
kill_port 5173 "frontend"
rm -f "$STATE_DIR/backend.pid" "$STATE_DIR/frontend.pid"

( cd "$BACKEND_DIR" && docker compose stop mongo ) 2>/dev/null && echo "Stopped mongo container"

echo "Done. Mongo's data volume is preserved — 'docker compose down -v' inside backend/ wipes it for a clean slate."
