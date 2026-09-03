# DecisionLedger — Decision Automation Platform

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[![Backend CI](https://github.com/ShreyasUpadhyaya/decision-ledger/actions/workflows/backend-tests.yml/badge.svg)](https://github.com/ShreyasUpadhyaya/decision-ledger/actions/workflows/backend-tests.yml)
[![Frontend CI](https://github.com/ShreyasUpadhyaya/decision-ledger/actions/workflows/frontend-build.yml/badge.svg)](https://github.com/ShreyasUpadhyaya/decision-ledger/actions/workflows/frontend-build.yml)

DecisionLedger is a backend service and dashboard that evaluate checkout and order
requests against configurable business rules and return a decision you can explain and
audit. Every request runs through a deterministic four-phase rule engine
(`GATE → SCORING → TERMS → OVERLAY`) that is the single source of truth for the
outcome, and every response carries a full audit trace of which rule fired and why.

An LLM/RAG layer runs only on the fallback path — when no rule matches the request at
all — and every AI capability has a deterministic offline fallback, so the platform
runs end to end with **no API keys configured at all**.

It was built for **The Talent Hack**, a 24-hour AI hackathon presented by Cursor and
Deutsche Telekom Digital Labs, where it placed in the top 5 of 9,000+ submissions.

## What's in this folder

```
backend/     FastAPI + the rule engine, MongoDB-backed stores, LLM/RAG layer, 197 tests
frontend/    React + shadcn/ui dashboard ("Decision Cockpit")
run.sh       one-command startup for both, plus local MongoDB
stop.sh      stops everything run.sh started
```

See [backend/README.md](backend/README.md) and [backend/docs/](backend/docs/) for the
full architecture, API reference, and engineering log; [frontend/README.md](frontend/README.md)
for the dashboard.

## Quickstart

**Prerequisites:** [Docker Desktop](https://www.docker.com/products/docker-desktop/)
(for local MongoDB), Python 3.11+, Node 18+. No API keys required.

```bash
./run.sh
```

(On Windows, run this from **Git Bash**, not PowerShell/cmd.)

The script is idempotent — re-run it any time — and brings up three things:

1. A local MongoDB container (rulesets, users, audit log).
2. The FastAPI backend on **http://127.0.0.1:8000** (API docs at `/docs`).
3. The React dashboard on **http://localhost:5173**.

On the first run it generates `backend/.env` from `backend/.env.example` with a fresh
encryption key, so there is nothing to configure by hand. Once everything is up, open
**http://localhost:5173**, pick a scenario, and send a request.

To stop everything:

```bash
./stop.sh
```

### Running it by hand instead

```bash
# Terminal 1 — MongoDB
cd backend && docker compose up -d mongo

# Terminal 2 — backend
cd backend
cp .env.example .env   # then set DECISION_LEDGER_ENCRYPTION_KEY (see comment in the file)
pip install -r requirements.txt
uvicorn app.main:app --reload

# Terminal 3 — frontend
cd frontend
npm install
npm run dev
```

## Tests

```bash
cd backend
pip install -r requirements.txt
python -m pytest -q
```

197 tests, fully hermetic — `mongomock` in place of MongoDB, an in-memory vector index,
no OpenAI key, no network — running in under 2 seconds.

## Architecture, in one picture

```
Incoming request
      │
      ▼
Evaluate with the JSON rule engine (deterministic, authoritative)
      │
      ├── Rule matched ───────────────────────► return the decision immediately
      │
      └── No rule matched anywhere
             │
             ▼
      Vector search over the rule store
             │
             ├── Similar rule(s) above threshold ─► LLM recommendation + explanation + confidence
             │
             └── No semantic match ───────────────► configured safe default (e.g. REFER)
```

Full detail in [backend/docs/ARCHITECTURE.md](backend/docs/ARCHITECTURE.md).
