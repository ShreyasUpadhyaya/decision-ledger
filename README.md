# DecisionLedger — Decision Automation Platform

A backend + dashboard that evaluates checkout/order requests against configurable
business rules and returns explainable, auditable decisions. Built for **The Talent
Hack**, a 24-hour AI hackathon presented by Cursor and Deutsche Telekom Digital Labs
(Top 5 of 9,000+ submissions).

A deterministic four-phase rule engine (`GATE → SCORING → TERMS → OVERLAY`) is the
single source of truth for every decision. Every response carries a full audit trace of
which rule fired and why. An LLM/RAG layer only ever runs on the fallback path — when no
rule matches anything — and every AI capability has a deterministic offline fallback, so
the platform runs end-to-end with **no API keys configured at all**.

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

This brings up, idempotently:
1. A local MongoDB container (rulesets, users, audit log).
2. The FastAPI backend on **http://127.0.0.1:8000** (API docs at `/docs`).
3. The React dashboard on **http://localhost:5173**.

First run auto-generates `backend/.env` from `backend/.env.example` with a fresh
encryption key — nothing to configure by hand. Open **http://localhost:5173**, pick a
scenario, and send a request.

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
