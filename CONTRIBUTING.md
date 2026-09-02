# Contributing to DecisionLedger

Thanks for taking the time to contribute! This document covers how to set up a
development environment, run the tests, and get a change merged.

## Development environment

You'll need Python 3.11+ and Node 18+. Docker Desktop is only required if you want
to run the full stack locally (see [Running the app locally](#running-the-app-locally)).

**Backend**

```bash
pip install -r backend/requirements.txt
```

**Frontend**

```bash
cd frontend
npm install
```

## Running the tests

The backend test suite is the thing to run before opening a pull request:

```bash
cd backend
python -m pytest -q
```

There are 197 tests. The suite is hermetic and fast — it uses `mongomock` in place
of MongoDB and an in-memory vector index, needs no OpenAI key and no network, and
runs in a couple of seconds. If it isn't green on a clean checkout, please open an
issue rather than a pull request.

## Running the app locally

The root [README.md](README.md) has a one-command quickstart (`./run.sh`) that brings
up MongoDB, the backend, and the dashboard together, plus instructions for starting
each piece by hand. Follow that — it isn't duplicated here so the two don't drift.

## Commit message conventions

This repository uses [Conventional Commits](https://www.conventionalcommits.org/).
Prefix each commit subject with the type of change:

- `feat:` — a new feature
- `fix:` — a bug fix
- `docs:` — documentation only
- `chore:` — tooling, packaging, or housekeeping with no product change
- `test:` — adding or adjusting tests
- `ci:` — CI configuration

Keep the subject line short and in the imperative mood (e.g.
`fix: reject empty ruleset on import`). This matches the existing history — please
keep it consistent.

## Pull request process

1. Fork the repository and create a topic branch from `main`.
2. Make your change, add or update tests if behavior changed, and run the backend
   test suite locally.
3. Open a pull request against `main`. Describe what changed and why.
4. CI must pass before a pull request can be merged.

Keep pull requests focused — unrelated changes bundled together are hard to review.

## Changes to rule-evaluation semantics

The deterministic rule engine under `backend/app/core/` is the single source of
truth for every decision the platform makes. Pull requests that change how rules are
evaluated — the four-phase pipeline, matching, scoring, terms, or overlay behavior —
get extra scrutiny and should be discussed first by opening an issue that explains
the motivation and the expected impact on existing decisions. Please don't send an
unsolicited pull request that alters rule-evaluation semantics; start the
conversation first.
