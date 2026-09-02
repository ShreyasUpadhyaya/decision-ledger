# DecisionLedger — Architecture

## 1. Design goal

A **configurable decision automation platform**: telecom e-commerce checkout orders are
evaluated by a deterministic, auditable JSON rule engine — the **primary and
authoritative** decision path — then, only when no rule matches anywhere, enriched by a
vector-search + LLM fallback for explainability and a best-effort recommendation. The AI
layer is never on the critical path of a deterministic verdict.

The single most important architectural rule:

> **The core is pure. The shell is impure. Dependencies only ever point inward.**

Nothing in `app/core/` imports anything that does I/O — no network, no database, no clock,
no environment. That is what makes every verdict reproducible and every test hermetic.

---

## 2. Layered structure

```
                 ┌─────────────────────────────────────────────┐
   HTTP  ───────▶│  app/main.py        FastAPI shell (adapter)  │
                 │  validate → delegate → shape → enrich        │
                 └───────────────┬───────────────────┬─────────┘
                                 │                   │
                 ┌───────────────▼─────────┐   ┌─────▼───────────────────┐
                 │  app/decision_service.py│   │  app/llm/  (additive)   │
                 │  JSON engine first,     │   │  explainer / generator │
                 │  vector+LLM fallback    │   │  / recommender          │
                 │  only if nothing matched│   │  (LLM or fallback)      │
                 └───────────────┬─────────┘   └─────────────────────────┘
                                 │
                 ┌───────────────▼─────────┐
                 │  app/orchestrator.py    │
                 │  IMPURE SHELL           │
                 │  fetch signals, freeze  │
                 │  context, aggregate     │
                 └───────────────┬─────────┘
                                 │
                 ┌───────────────▼───────────────────────────────────────┐
                 │  app/core/   PURE CORE  (no I/O, no clock, no env)     │
                 │  evaluator · conditions · operators · aggregator · linter │
                 └───────────────────────────────────────────────────────┘
                                 ▲
                 ┌───────────────┴──────────┐
                 │  app/rule_store.py       │   ┌──────────────────────────┐
                 │  RuleStore facade        │   │  app/providers.py        │
                 │  (dual-write, one call)  │   │  mock signals + failure  │
                 └──────┬─────────────┬─────┘   └──────────────────────────┘
                        │             │
         ┌──────────────▼───┐   ┌─────▼──────────────────┐
         │ stores/           │   │ stores/                │
         │ ruleset_repository│   │ vector_index            │
         │ AUTHORITATIVE     │   │ fallback-only, never     │
         │ versioned/audited │   │ authoritative            │
         │ (MongoDB)         │   │ (MongoDB Atlas / memory) │
         └───────────────────┘   └─────────────────────────┘
```

| Module | Purity | Responsibility |
|---|---|---|
| `core/operators.py` | pure | Compile a `{fact, op, value}` leaf to a callable, backed by `rule-engine`. |
| `core/conditions.py` | pure | Three-valued (`TRUE`/`FALSE`/`INDETERMINATE`) `all`/`any`/`none` evaluation with null safety. |
| `core/evaluator.py` | pure | The 4-phase engine. `evaluate_ruleset(context, ruleset, requested_at) → (decision, trace, shadow_hits)`. A rule with `is_shadow: true` evaluates for real but can never affect `decision` — only `shadow_hits`. |
| `core/aggregator.py` | pure | Fold a decision set into one order-level verdict, using each ruleset's own `composite_class` map. |
| `core/linter.py` | pure | Static checks a ruleset must pass before `RuleStore` will store it. |
| `orchestrator.py` | impure | Fetch signals, freeze context, run each active ruleset (by decision type), aggregate, shape response. |
| `providers.py` | impure | Bureau/fraud/identity/catalog signals + failure injection. `bureau` calls a live adapter when configured, else the mock default. |
| `adapters/bureau_adapter.py` | impure | Real credit-bureau HTTP client (`BUREAU_API_URL`), degrading to the mock provider on timeout/error or when no URL is set. |
| `stores/ruleset_repository.py` | impure | **Authoritative** rule source — versioned, Fernet-encrypted, audited MongoDB storage. The only thing the evaluator ever reads. |
| `stores/vector_index.py` | impure | **Fallback-only** semantic index — MongoDB Atlas Vector Search, or a dependency-free in-memory TF-IDF backend. |
| `rule_store.py` | impure | `RuleStore` facade composing both stores above; every write updates both in one call. |
| `decision_service.py` | impure | JSON engine first; vector search + LLM recommendation only if no rule matched anywhere; else a configured default fallback verdict. |
| `llm/*` | impure | Explanation, NL→rule generation, and the fallback recommendation — each with a deterministic offline fallback. |
| `async_decisions.py` | impure | MongoDB-backed async decision jobs (`PENDING → PROCESSING → COMPLETED/FAILED`), run on FastAPI `BackgroundTasks`, with optional webhook callback. |
| `auth.py` / `user_store.py` | impure | Stateless HS256 JWT minting + role guards; MongoDB-backed user signup/login (bcrypt-hashed, never returning the hash). |
| `db.py` / `encryption.py` / `audit.py` / `seed.py` | impure | MongoDB connection lifecycle, at-rest encryption, audit log, initial JSON seeding. |
| `config.py` / `logging_config.py` | impure | Typed settings; single-line JSON logs. |
| `main.py` | impure | FastAPI routes, CORS, auth, exception handling, enrichment. |

---

## 2.1 Repository layout

```
DTDL_Hackathon/
├── app/
│   ├── main.py                 FastAPI shell (routes, CORS, auth, enrichment)
│   ├── decision_service.py     JSON engine → vector+LLM fallback orchestration
│   ├── orchestrator.py         signal fetch, context freeze, per-ruleset run, aggregate
│   ├── providers.py            mock signals + failure injection (live bureau adapter)
│   ├── rule_store.py           RuleStore facade (dual-write over the two stores)
│   ├── async_decisions.py      MongoDB-backed async decision jobs
│   ├── auth.py / user_store.py  JWT guards + MongoDB user store (bcrypt)
│   ├── audit.py / encryption.py / db.py / seed.py   Mongo lifecycle, Fernet, audit, seeding
│   ├── config.py / logging_config.py / schemas.py   settings, JSON logs, request models
│   ├── core/                   PURE: operators, conditions, evaluator, aggregator, linter
│   ├── stores/                 ruleset_repository (authoritative) + vector_index (fallback)
│   ├── llm/                    explainer, rule_generator, recommender (each w/ offline fallback)
│   └── adapters/               bureau_adapter (external credit-bureau HTTP client)
├── rulesets/                   5 seed rulesets (de.device-financing, .fraud, .identity,
│                               .payment-policy, .tariff-eligibility)
├── tests/                      137 hermetic tests (mongomock + in-memory vector backend)
├── docs/                       API.md · ARCHITECTURE.md · AI_ENGINEERING_LOG.md
├── Dockerfile / docker-compose.yml
└── requirements.txt
```

> Not on the live path: `ruleset_store.py` and `ruleset_loader.py` are earlier in-memory
> store iterations superseded by `rule_store.py` + `stores/`; they remain only for their
> standalone unit tests and are not imported by the running app.

---

## 3. The 4-phase evaluator

Each ruleset is evaluated in four fixed phases (`core/evaluator.py`):

1. **GATE** — hard stops. First match wins, terminal, short-circuits everything below
   (e.g. `account.days_past_due > 60` → `DECLINE`).
2. **SCORING** — every matching rule fires; `score_delta`s accumulate into
   `computed.risk_score`.
3. **TERMS** — first match wins on the accumulated score, establishing baseline commercial
   terms (verdict + terms). No match → the ruleset's own `default_outcome`.
4. **OVERLAY** — tighten-only post-decision caps. An overlay can *tighten* an existing
   verdict along a severity ladder or *shrink* an existing numeric term, but it can never
   loosen a verdict or fabricate a term that wasn't there.

Determinism comes from three choices: rules within a phase are sorted by `priority`; "now"
is always the frozen `requested_at`, never a live clock; and the whole function is free of
side effects, so re-running it on the same context yields the same trace byte for byte.

A decision is considered **unmatched** (see §5) only if every phase fell through with zero
rules firing — `matched_rules` empty — meaning the TERMS phase used the bare
`default_outcome`.

### Three-valued logic & null safety
A missing/null fact makes a leaf `INDETERMINATE`, not `FALSE`. `all` is `FALSE` if any
child is `FALSE`, else `INDETERMINATE` if any child is, else `TRUE`; `any` is the dual;
`none` negates `any`. This is what lets a degraded signal flow through the ruleset as
"unknown" instead of silently satisfying or failing a rule.

---

## 4. Orchestration & signal degradation

`orchestrator.py` is the only place that knows about signals. It fetches all five, computes
a `signal_confidence` (1.0 minus per-provider penalties for any fallback applied), freezes
a single immutable `context`, and evaluates every *active* ruleset the ruleset repository
hands it — keyed by `decision_type`, never a hardcoded `ruleset_id`, so adding a new
ruleset type requires zero orchestrator changes.

Two providers get **fail-safe forcing** rather than trusting the ruleset default: if the
**fraud** provider is degraded the FRAUD decision is forced to `REVIEW`, and if **identity**
is degraded it is forced to `STEP_UP_KYC`. This prevents an all-`INDETERMINATE` scoring
phase from quietly resolving to a permissive default. Bureau/account degradation instead
flows through as null facts plus a confidence overlay (`OV-401`).

---

## 5. Decision flow: JSON engine primary, vector + LLM fallback

`decision_service.decide()` is the only caller of both `evaluate_decision` and the
fallback path:

```
Evaluate with the JSON rule engine
      │
      ├── Rule matched somewhere → return the deterministic decision immediately
      │
      └── Every active ruleset unmatched (see §3)
             │
             ▼
      Vector search over the rule store (RuleStore.search)
             │
             ├── Hit(s) at/above VECTOR_SEARCH_THRESHOLD
             │      → llm.recommend_from_similar_rules(): APPROVE/REFER/DECLINE +
             │        confidence + explanation, mode "llm" or "heuristic"
             │
             └── No hit above threshold → FALLBACK_VERDICT (default REFER)
```

The response carries `ai_assisted: true` only when the vector+LLM path actually produced
the verdict — the plain default-fallback branch is not "AI-assisted," it's a safety net.
This fallback triggers at the whole-request level (every active ruleset came back
unmatched), matching the flow above; a per-decision-type fallback (partial AI-assisted
decisions within one request) is a reasonable future variant if that granularity is
wanted.

---

## 6. Two stores, one facade

`RuleStore` (`app/rule_store.py`) is the single abstraction the rest of the app talks to.
It composes two independent, swappable pieces (`app/stores/interfaces.py` defines both as
`typing.Protocol`s):

- **`RulesetRepository`** (`stores/ruleset_repository.py`) — MongoDB-backed, versioned
  (every write is a new immutable version), Fernet-encrypted at rest
  (`encryption.py`), and audited (`audit.py`). **Authoritative**: the orchestrator reads
  only from `active_by_decision_type()`.
- **`VectorRuleIndex`** (`stores/vector_index.py`) — MongoDB Atlas Vector Search (real
  embeddings via `langchain-mongodb` + `OpenAIEmbeddings`) or a dependency-free in-memory
  TF-IDF backend. **Never authoritative** — used only by `RuleStore.search()`, which
  backs both `/v1/rules/search` and the decision service's fallback.

Every write goes through `RuleStore` so both stores move together:
- `add_rule`/`update_rule` — splice one rule into the ruleset's current content, publish
  a new (audited) version in the repository, then index that same rule into the vector
  store. One call, both stores updated — this is what `POST /v1/rules/generate` (with
  `persist: true`) calls.
- `create_ruleset` — publish a whole new ruleset version (first version for a brand-new
  `ruleset_id`, or an update to an existing one) and index every one of its rules. Used by
  `seed.py` on first boot and by `POST /v1/rulesets/{id}/versions`.
- `rollback`/`deprecate`/`reactivate` — authoritative-store lifecycle operations;
  `rollback` also re-indexes the rules of the version being rolled back to (a rule id
  present only in the version being rolled *away from* stays in the vector index until
  next touched — acceptable since the index is a search convenience, never a decision
  authority).

A ruleset must pass `core/linter.py` (unknown facts/operators, missing
`default_outcome`, a verdict with no `composite_class` entry) before any write is
accepted.

---

## 7. LLM layer — additive and never-fatal

Three capabilities, each with **one interface and two implementations** (live LangChain
vs. deterministic offline fallback), selected at runtime:

- **Explainer** (`llm/explainer.py`) — renders the composite verdict, driving decisions,
  degraded signals and terms into a paragraph. The fallback is a reason-code phrasebook.
- **Rule generator** (`llm/rule_generator.py`) — NL → JSON rule. The fallback is a
  deterministic phrase parser (fact vocabulary + comparator keywords + action verbs). Every
  candidate, LLM or heuristic, is validated by compiling its condition tree with the *same*
  compiler production uses, then **written through `RuleStore`** (both stores) so it is
  live at once.
- **Recommender** (`llm/recommender.py`) — turns vector-search hits into an
  APPROVE/REFER/DECLINE recommendation with a confidence and an explanation, only invoked
  by `decision_service` when the JSON engine found no match. The fallback is a
  similarity-weighted majority vote over the hits' verdicts.

Because the fallbacks are always present, the platform is fully functional with **no API
key and no network**. `POST /v1/decisions` calls the explainer inside a `try/except` that
degrades to an empty explanation rather than failing the decision, and the vector-search
fallback itself degrades to `FALLBACK_VERDICT` if search finds nothing.

---

## 8. Cross-cutting concerns

- **Configuration** (`config.py`) — one typed `settings` object via `pydantic-settings`;
  `core/` never reads the environment.
- **Logging** (`logging_config.py`) — every line is one JSON object. Lifecycle events:
  `ruleset.loaded`, `decision.request`, `decision.response`, `decision.no_rule_matched`,
  `decision.vector_fallback`, `decision.default_fallback`, `signal.degraded`,
  `rulestore.rule_added`, `rule.generated`.
- **Idempotency** — `Idempotency-Key` maps to a stored decision; replays return it verbatim
  with `replayed: true`.
- **Async decisions** (`async_decisions.py`) — `POST /v1/decisions/async` returns a `job_id`
  immediately (`202`) and runs the same `decide()` on a FastAPI `BackgroundTask`. Job state
  is persisted in MongoDB (`PENDING → PROCESSING → COMPLETED/FAILED`) and pollable at
  `/v1/decisions/async/{job_id}`; a supplied `webhook_url` receives the finished decision.
- **External signal integration** (`adapters/bureau_adapter.py`) — when `BUREAU_API_URL` is
  set, the `bureau` signal is fetched over HTTP; any timeout/error (or an unset URL) falls
  back to the deterministic mock, so a network fault degrades a signal, never the platform.
- **Performance** — the ruleset repository compiles conditions once per version and caches
  them (`active_by_decision_type`), so a request never pays parsing cost; the vector index
  is checked only on the (rare) no-match path.
- **Auth** — stateless HS256 JWT (`auth.py`); ruleset mutation routes (`versions`,
  `rollback`, `deprecate`, `reactivate`, rule generate/search) require the `admin` role,
  same as the pre-merge rule-authoring endpoints.

---

## 9. Testing strategy

All **137 tests** are offline and deterministic — `mongomock` in place of MongoDB, the
in-memory TF-IDF backend in place of Atlas, no OpenAI key:

- **Core/domain** — per-ruleset behaviour, operator edge cases, three-valued logic,
  linter checks.
- **Signal failures** — every provider × every failure mode, verifying fail-safe forcing.
- **External adapter** — bureau HTTP success, timeout fallback, and no-URL-configured path.
- **Ruleset repository** — versioning, rollback, deprecate/reactivate, collision
  detection, encryption round-trip, audit log writes.
- **RuleStore** — the dual-write path (`add_rule`/`create_ruleset` update both stores in
  one call), search ranking, seeding.
- **Decision service** — the vector+LLM fallback only fires when nothing matched, and
  falls back to the default verdict when no similar rule clears the threshold.
- **Auth & async** — signup/login/role guards and the async-job lifecycle.
- **API** — idempotency, batch isolation, validation problems, failure injection, auth,
  the full ruleset-version lifecycle over HTTP.
- **LLM/RAG** — template & LLM (mocked) explanation, NL→rule for every phase, engine
  compilability of generated rules.

Run: `pytest -q` from the `DTDL_Hackathon/` directory.
