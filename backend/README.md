# DecisionLedger — Configurable Decision Automation Platform

**Problem Statement 4 · The Talent Hack · Deutsche Telekom Digital Labs**

A backend service that evaluates incoming order requests against configurable business
rules, produces a set of explainable decisions with commercial terms, and exposes REST
APIs for external integration.

**Domain:** telecom order eligibility — device financing, fraud screening, identity
verification, tariff eligibility and payment-method policy at digital checkout
(OneShop web / OneApp mobile).

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the layered design, [docs/API.md](docs/API.md)
for the full API reference, and [docs/AI_ENGINEERING_LOG.md](docs/AI_ENGINEERING_LOG.md) for
how the AI tooling was used, corrected, and validated. For how to run this alongside the
dashboard, see [README.md](../README.md) at the top of this submission.

## Implementation

The pure 4-phase evaluator, all 5 rulesets, mock signal providers with failure injection,
a FastAPI shell, **an LLM/RAG enhancement layer**, and **two MongoDB-backed stores**.

**The JSON rule engine is the primary, authoritative decision path.** Every request is
evaluated deterministically first (`app/orchestrator.py` + `app/core/`). Only when *no*
rule matched anywhere does the platform fall back to semantic search + an LLM
recommendation:

```
Incoming request
      │
      ▼
Evaluate with the JSON rule engine (deterministic, authoritative)
      │
      ├── Rule matched  ──────────────────────────► return the decision immediately
      │
      └── No rule matched anywhere
             │
             ▼
      Vector search over the rule store
             │
             ├── Similar rule(s) above threshold ─► LLM recommendation: APPROVE / REFER /
             │                                        DECLINE + explanation + confidence
             │
             └── No semantic match ───────────────► configured default fallback (REFER)
```

**Two stores, one facade (`app/rule_store.py`).** Every rule write goes through
`RuleStore`, which updates both in one call:
- an **authoritative, versioned, Fernet-encrypted, audited** ruleset repository
  (`app/stores/ruleset_repository.py`) — the only thing the evaluator ever reads from;
- a **MongoDB Atlas Vector Search** index (`app/stores/vector_index.py`) — embeddings +
  metadata + the original rule JSON, used only for the fallback search above and for
  `/v1/rules/search`. A dependency-free in-memory TF-IDF backend keeps everything working
  without Atlas/OpenAI (`VECTOR_BACKEND=memory`, the hermetic test suite's default, and
  what a local `docker compose` Mongo falls back to since self-hosted Mongo can't run
  Atlas Vector Search).

Rule conditions are evaluated via [`rule-engine`](https://github.com/zeroSteiner/rule-engine)
(PyPI: `rule-engine`), not a hand-rolled comparison per operator.

### LLM & RAG layer — works with no API key

Each AI capability has a **deterministic offline fallback** so a live demo never depends
on a key or the network (set `OPENAI_API_KEY` to light up the LangChain + MongoDB Atlas
Vector Search paths). The response always reports which mode/backend produced it.

| Capability | Endpoint | Fallback when no key |
|---|---|---|
| Natural-language explanation of a decision | attached to `POST /v1/decisions` | reason-code → English template |
| Vector-search fallback recommendation (no rule matched) | attached to `POST /v1/decisions` | similarity-weighted majority vote |
| NL → validated JSON rule, **stored in both stores & live at once** | `POST /v1/rules/generate` | deterministic phrase parser |
| Semantic rule search over the fallback index | `GET /v1/rules/search?q=...` | in-memory TF-IDF index |
| List active rules | `GET /v1/rules?ruleset_id=...` | — |
| Versioned ruleset lifecycle (publish/rollback/deprecate/audit) | `POST/GET /v1/rulesets/...` | — |

See [docs/API.md](docs/API.md) for the full API, [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
for the layered design, and [docs/AI_ENGINEERING_LOG.md](docs/AI_ENGINEERING_LOG.md) for how
the AI tooling was used, corrected, and validated.

### Setup

```bash
pip install -r requirements.txt
```

### Run the API

From the top of this submission, `./run.sh` brings up a local Mongo (Docker), this API, and
the dashboard together — see the top-level [README.md](../README.md). To run just the API
by hand:

```bash
uvicorn app.main:app --reload
```

Then, with the server running:

```bash
# health/readiness
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready

# evaluate a decision (Idempotency-Key is required)
curl -X POST http://127.0.0.1:8000/v1/decisions \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d @sample_request.json

# fetch a past decision plus its full trace
curl http://127.0.0.1:8000/v1/decisions/{decision_id}

# demo failure injection live (spec §8.4) — then retry the request above
curl -X POST http://127.0.0.1:8000/v1/_test/providers/bureau -d '{"mode":"TIMEOUT"}'
curl -X POST http://127.0.0.1:8000/v1/_test/providers/reset

# LLM/RAG — generate a rule from plain English, and search the ruleset
curl -X POST http://127.0.0.1:8000/v1/rules/generate \
  -H "Content-Type: application/json" \
  -d '{"text":"Require a 50% deposit if financed amount exceeds 2000 for new customers"}'
curl "http://127.0.0.1:8000/v1/rules/search?q=cap%20financing%20for%20young%20customers"
```

Ruleset lifecycle — add a new type, publish an update, or archive one, live:

```bash
# publish v1 of a brand-new ruleset type — no code change, it just starts appearing
# in POST /v1/decisions responses under its own decision_type
curl -X POST http://127.0.0.1:8000/v1/rulesets/de.my-new-ruleset/versions \
  -H "Content-Type: application/json" -d @my_ruleset.json
# a lint failure (unknown fact/operator, missing composite_class) 422s and stores nothing

curl http://127.0.0.1:8000/v1/rulesets                              # every ruleset_id + active version
curl http://127.0.0.1:8000/v1/rulesets/de.my-new-ruleset/versions    # full version history
curl http://127.0.0.1:8000/v1/rulesets/de.my-new-ruleset/versions/1  # exact historical content, forever
curl -X POST http://127.0.0.1:8000/v1/rulesets/de.my-new-ruleset/rollback
curl -X POST http://127.0.0.1:8000/v1/rulesets/de.my-new-ruleset/deprecate   # next /v1/decisions 422s
curl -X POST http://127.0.0.1:8000/v1/rulesets/de.my-new-ruleset/reactivate
```

### Run with Docker

```bash
docker compose up -d mongo   # local MongoDB only — see the top-level README.md for the full run
docker compose up --build    # or bring up the containerized API too, API on http://localhost:8000
```

### Configuration

All settings are environment-driven (`app/config.py`, via `pydantic-settings`). Copy
[.env.example](.env.example) to `.env` and adjust. `MONGODB_URI` and
`DECISION_LEDGER_ENCRYPTION_KEY` have no safe default for a real deployment (the ruleset
repository is MongoDB-backed and its content is Fernet-encrypted at rest); everything
else — LLM, vector backend — has a deterministic offline default so the platform still
runs end to end with no `.env` at all beyond those two.

A request body can optionally include `"test_signals": {"bureau": {"score": 618}, ...}`
to pin exact signal values deterministically instead of relying on the mock providers'
healthy defaults.

### Run the tests

```bash
pytest -q
```

197 tests, hermetic (mongomock + the in-memory vector index — no live MongoDB, Atlas, or
OpenAI key needed), running in under 2 seconds. Every layer — core/domain, the API, LLM/RAG,
the ruleset repository, the vector index, and the decision service's fallback path — runs
fully offline.

```bash
python -m pytest -q --no-header -p no:warnings   # same suite, quiet output
```

### Project layout

```
app/
├── core/                       # pure, zero I/O — conditions.py, evaluator.py, aggregator.py, operators.py, linter.py
├── stores/                     # the two backing stores behind RuleStore
│   ├── interfaces.py             # RulesetRepository / VectorRuleIndex protocols
│   ├── ruleset_repository.py      # authoritative, versioned, encrypted, audited (MongoDB)
│   └── vector_index.py            # fallback-only semantic index (MongoDB Atlas / in-memory)
├── rule_store.py                # RuleStore facade — dual-writes both stores in one call
├── decision_service.py          # JSON engine first, vector search + LLM fallback if no match
├── llm/                          # explainer.py, rule_generator.py, recommender.py (LLM + deterministic offline fallback)
├── providers.py                  # mock signal providers + failure injection
├── orchestrator.py                # the impure shell: context freezing, per-ruleset eval, aggregation
├── db.py, encryption.py, audit.py, seed.py   # ruleset repository infrastructure
├── config.py                      # pydantic-settings configuration
├── logging_config.py              # structured JSON logging
├── schemas.py                     # Pydantic request/response schemas
└── main.py                        # FastAPI routes
rulesets/              # one JSON file per decision type (seed data for the repository)
docs/                   # API.md, ARCHITECTURE.md, AI_ENGINEERING_LOG.md
tests/                  # one file per ruleset/concern, hermetic (mongomock + in-memory vector index)
Dockerfile, docker-compose.yml, .env.example
```

## Dashboard

The React + shadcn/ui frontend lives alongside this backend, in `../frontend` — see the
top-level [README.md](../README.md) to run both together. It talks to this API over
HTTP — CORS is already configured here for `localhost:5173`, its default dev port.
