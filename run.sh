#!/usr/bin/env bash
# DecisionLedger — one-command startup for this submission.
#
# Brings up, idempotently (safe to re-run):
#   1. Local MongoDB (Docker) — the ruleset store, users, and audit log.
#   2. The FastAPI backend on :8000
#   3. The React dashboard on :5173
#
# Requires: Docker Desktop, Python 3.11+, Node 18+. No API keys needed — the
# platform runs end-to-end on deterministic offline fallbacks with none configured.
#
# Run it from this folder:  ./run.sh   (Windows: run it from Git Bash)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
STATE_DIR="$ROOT/.run"
mkdir -p "$STATE_DIR"

BACKEND_LOG="$STATE_DIR/backend.log"
FRONTEND_LOG="$STATE_DIR/frontend.log"
BACKEND_PID_FILE="$STATE_DIR/backend.pid"
FRONTEND_PID_FILE="$STATE_DIR/frontend.pid"

say() { printf '\n\033[1;36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$1"; }
ok() { printf '\033[1;32m\xe2\x9c\x93\033[0m %s\n' "$1"; }

port_responds() { curl -fs -m 2 "$1" >/dev/null 2>&1; }
pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

wait_for() {
  local url="$1" label="$2" timeout="${3:-30}" waited=0
  until port_responds "$url"; do
    sleep 1
    waited=$((waited + 1))
    if [ "$waited" -ge "$timeout" ]; then
      warn "$label didn't respond within ${timeout}s (see logs)."
      return 1
    fi
  done
  return 0
}

# --- 0. Backend .env (auto-generated on first run) ---------------------------
if [ ! -f "$BACKEND_DIR/.env" ]; then
  say "First run: generating backend/.env"
  cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
  KEY="$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null \
      || python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")"
  # Portable in-place edit (works on both GNU and BSD/macOS sed).
  sed -i.bak "s#^DECISION_LEDGER_ENCRYPTION_KEY=.*#DECISION_LEDGER_ENCRYPTION_KEY=\"$KEY\"#" "$BACKEND_DIR/.env" && rm -f "$BACKEND_DIR/.env.bak"
  ok "backend/.env created with a generated encryption key"
  warn "No OPENAI_API_KEY set — LLM explanations run on the deterministic template fallback (this is expected and fine)."
fi

# --- 1. MongoDB (Docker) ------------------------------------------------------
say "MongoDB (local, Docker)"
if ! docker info >/dev/null 2>&1; then
  warn "Docker daemon isn't running. Start Docker Desktop, then re-run ./run.sh."
  exit 1
fi
ok "Docker daemon is up"

( cd "$BACKEND_DIR" && docker compose up -d mongo )
waited=0
until [ "$(docker inspect -f '{{.State.Health.Status}}' decisionledger-mongo 2>/dev/null)" = "healthy" ]; do
  sleep 1
  waited=$((waited + 1))
  if [ "$waited" -ge 30 ]; then
    warn "Mongo container didn't report healthy within 30s — continuing anyway."
    break
  fi
done
ok "Mongo is up (mongodb://localhost:27017/)"

# --- 2. Backend (FastAPI) ------------------------------------------------------
say "Backend (FastAPI)"
if port_responds "http://127.0.0.1:8000/health"; then
  ok "Backend already running at http://127.0.0.1:8000"
elif pid_alive "$BACKEND_PID_FILE"; then
  warn "A tracked backend process is running but not answering — check $BACKEND_LOG"
else
  ( cd "$BACKEND_DIR" && pip install -q -r requirements.txt && nohup python -m uvicorn app.main:app --port 8000 >"$BACKEND_LOG" 2>&1 & echo $! >"$BACKEND_PID_FILE" )
  if wait_for "http://127.0.0.1:8000/health" "Backend" 60; then
    ok "Backend up at http://127.0.0.1:8000"
  else
    warn "Backend failed to start — last 20 log lines:"
    tail -n 20 "$BACKEND_LOG" || true
    exit 1
  fi
fi

# --- 3. Frontend (Vite dev server) --------------------------------------------
say "Frontend (React dashboard)"
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  say "Installing frontend dependencies (first run only)..."
  ( cd "$FRONTEND_DIR" && npm install )
fi

if port_responds "http://localhost:5173"; then
  ok "Frontend already running at http://localhost:5173"
elif pid_alive "$FRONTEND_PID_FILE"; then
  warn "A tracked frontend process is running but not answering — check $FRONTEND_LOG"
else
  # Invoked directly via node, not `npm run dev`/`npx vite` — on Windows Git Bash, npm's
  # generated .cmd shim for vite fails with a `'"node"' is not recognized` error. Calling
  # node on vite's entry point directly sidesteps that shim entirely (harmless elsewhere).
  ( cd "$FRONTEND_DIR" && nohup node node_modules/vite/bin/vite.js --port 5173 >"$FRONTEND_LOG" 2>&1 & echo $! >"$FRONTEND_PID_FILE" )
  if wait_for "http://localhost:5173" "Frontend" 30; then
    ok "Frontend up at http://localhost:5173"
  else
    warn "Frontend failed to start — last 20 log lines:"
    tail -n 20 "$FRONTEND_LOG" || true
    exit 1
  fi
fi

say "DecisionLedger is up"
cat <<EOF
  Frontend:  http://localhost:5173
  Backend:   http://127.0.0.1:8000
  API docs:  http://127.0.0.1:8000/docs

  Logs:      $BACKEND_LOG
             $FRONTEND_LOG
  Stop:      ./stop.sh
EOF
