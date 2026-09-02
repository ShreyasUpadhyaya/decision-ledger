# AI Engineering Log — DecisionLedger Decision Automation Platform

This log documents how AI tooling was used to design and build the platform, which
generations were accepted, which were rejected or reworked, and how everything was
validated. It is written to be auditable: every "accepted" claim below corresponds to code
that ships in this repository and is covered by the test suite.

---

## 1. AI tools used

| Tool | Role in this project |
|---|---|
| **Claude / GPT-4-class assistant** | Primary pair-programmer: architecture of the pure 4-phase engine, the LLM/RAG layer design, the deterministic fallbacks, docs. |
| **GitHub Copilot** | In-editor autocomplete for boilerplate: Pydantic models, FastAPI route signatures, repetitive test fixtures. |
| **Cursor AI** | Multi-file edits and refactors (e.g. threading `settings` through `main.py`, extracting the `_enrich_with_explanation` helper). |

AI was used for **generation and refactoring**, never for **validation** — correctness is
established by the deterministic test suite, not by the model's confidence.

---

## 2. Representative prompts

**Engine design**
> "Design a pure, I/O-free 4-phase rule evaluator (GATE first-match-terminal, SCORING
> accumulative, TERMS first-match on accumulated score, OVERLAY tighten-only). It must take
> `requested_at` as a parameter and never read the system clock. Return `(decision, trace)`
> where the trace records every rule considered — matched, skipped, or not-evaluated."

**Three-valued logic**
> "Implement `all`/`any`/`none` over TRUE/FALSE/INDETERMINATE where a null fact yields
> INDETERMINATE, not FALSE. `all` short-circuits on FALSE; `any` short-circuits on TRUE;
> INDETERMINATE propagates only when it would change the result."

**LLM integration with a hard fallback requirement**
> "Build an explanation function with two implementations behind one interface: a LangChain
> chain when an API key is present, and a deterministic reason-code → English template when
> it isn't. It must NEVER raise — any LLM failure degrades to the template. Return the mode
> used so it is auditable."

**NL → rule generator**
> "Write a deterministic NL-to-JSON-rule parser: a fact vocabulary, comparator keywords,
> and action verbs. Validate every generated rule by compiling its condition tree with the
> production compiler. Turn 'Require a 50% deposit if financed amount exceeds 2000 for new
> customers' into an OVERLAY rule."

**RAG search**
> "Flatten each rule into a searchable document; use MongoDB Atlas Vector Search (OpenAI
> embeddings via `langchain-mongodb`) when a cluster and key are reachable, otherwise a
> dependency-free in-memory TF-IDF ranker so the endpoint works with no vector DB and no
> network. Report which backend answered."

---

## 3. Accepted code (used largely as generated)

- **Pure 4-phase evaluator** (`core/evaluator.py`) — phase ordering, first-match-wins gate
  short-circuit, tighten-only overlay semantics, and the full trace model. Accepted after
  the null-handling correction below.
- **Three-valued condition evaluation** (`core/conditions.py`) — the `all`/`any`/`none`
  truth tables and the negation map for `none`.
- **Deterministic explainer template** (`llm/explainer.py`) — reason-code phrasebook and
  the "lead with the verdict, omit clean passes, surface degraded signals" narrative shape.
- **NL→rule heuristic** (`llm/rule_generator.py`) — the fact vocabulary and action-verb
  routing. Accepted after the scoring-delta extraction fix below.
- **Dual-store rule facade** (`rule_store.py` over `stores/ruleset_repository.py` +
  `stores/vector_index.py`) — the authoritative, versioned MongoDB ruleset repository is the
  only thing the evaluator reads; the vector index (MongoDB Atlas Vector Search with OpenAI
  embeddings, or an in-memory TF-IDF fallback) is a search-only convenience. Seeded from JSON
  once; every write updates both stores in one call, so a generated rule is live and
  searchable immediately.
- **External-signal adapter with mock fallback** (`adapters/bureau_adapter.py`,
  `providers.py`) — a real credit-bureau HTTP call when `BUREAU_API_URL` is configured,
  degrading to the deterministic mock provider (and a confidence penalty) on timeout/error
  or when no URL is set.
- **Async decision jobs** (`async_decisions.py`, `main.py`) — `POST /v1/decisions/async`
  queues work on FastAPI `BackgroundTasks`, persists job state in MongoDB, and optionally
  POSTs the finished decision to a `webhook_url`.
- **FastAPI routes** (`main.py`) — validation → delegate → shape, plus the additive
  `_enrich_with_explanation` step wrapped in a never-fatal `try/except`.
- **JSON logging formatter** (`logging_config.py`).

---

## 4. Rejected or modified code

| # | AI-suggested code | Why it was wrong | Fix applied |
|---|---|---|---|
| 1 | **`STARTS_WITH`/`ENDS_WITH` via manual string quoting** into a `rule-engine` regex. | A backslash from `re.escape` was mangled by `rule-engine`'s own unicode-escape decoding of string literals, so escaped inputs matched incorrectly. | Switched to `json.dumps(...)` for the literal so the escape survives intact (`core/operators.py`). |
| 2 | **Null facts treated as `FALSE`.** | A degraded/absent signal would *silently satisfy or fail* a rule instead of being "unknown", corrupting scoring and hiding provider outages. | Null → `INDETERMINATE` at the leaf, with three-valued combinators above it (`core/conditions.py`). |
| 3 | **`lifespan()` startup handler** for app state. | The existing API tests access `app.state` before entering the `TestClient` context, so `lifespan` (which only runs inside the context manager) left state unset and broke them. | Reverted to module-import-time state initialization. |
| 4 | **Explanation skip-set `{APPROVE, CLEAR, PASS}`.** | The payment-policy domain's clean verdict is `ANY`; it leaked into the narrative as "the Payment Policy check was any." | Added `ANY` to the clean-pass set and glossed the payment/identity verdicts (`llm/explainer.py`). |
| 5 | **Score-delta extraction via "first number after the anchor".** | For "add **15** points … tenure at least **24** months", it grabbed `24` because the value *precedes* the word "points". | Anchored the regex on the `points`/`add` token itself (`llm/rule_generator.py`). |
| 6 | **Unconditional `import langchain_openai` / `import langchain_mongodb`** at module top. | Would crash `import app` on any machine without the optional heavy deps, or with no cluster/key — the exact demo machine. | Lazy imports inside `build_vector_index`, wrapped so an unreachable cluster or missing key degrades to the in-memory TF-IDF fallback (`stores/vector_index.py`). |
| 7 | **Compiled operator left on the rule dict** when a rule went into the store. | `compile_condition` attaches a `_compiled` dataclass to each leaf; storing/serialising that rule then failed (not JSON-safe). | `_clean_rule` strips the scratch key before any write; conditions are compiled only in the evaluator-facing assembly cache. |
| 8 | **First RAG draft was ChromaDB-backed.** | A local Chroma store didn't fit the "one MongoDB deployment backs users, rulesets, and vectors" goal, and its collection-name/embedding quirks surfaced only at call time. | Migrated the vector index to **MongoDB Atlas Vector Search** (`langchain-mongodb` + OpenAI embeddings) behind the same `VectorRuleIndex` interface, keeping the dependency-free in-memory TF-IDF backend for offline/test runs (`stores/vector_index.py`). |

---

## 5. Validation strategy

- **137 automated tests** (`pytest -q`), all hermetic — `mongomock` in place of MongoDB,
  the in-memory TF-IDF backend in place of Atlas Vector Search, no OpenAI key — the same mode
  a live demo can run in.
  - Core & domain: per-ruleset outcomes, operator families, three-valued/null edge cases.
  - Signal failures: every provider × every mode, asserting fail-safe forcing of FRAUD and
    IDENTITY.
  - External adapter: bureau HTTP success, timeout, and no-URL-configured fallback.
  - Auth & async: signup/login/role guards, and the async-job lifecycle
    (`PENDING → PROCESSING → COMPLETED/FAILED`).
  - Ruleset repository & store: versioning, rollback, deprecate/reactivate, encryption
    round-trip, audit writes, and the dual-write path.
  - API: idempotent replay, batch error isolation, schema-validation problem responses,
    failure-injection endpoints, the full ruleset-version lifecycle over HTTP.
  - LLM/RAG: template explanation content, LLM path via a mock (and its fallback on error),
    NL→rule for every phase, **every generated rule re-compiled by the production compiler**,
    ranked search relevance.
- **Determinism as a testable property** — the evaluator is a pure function of
  `(context, ruleset, requested_at)`, so tests assert exact traces and scores rather than
  fuzzy expectations.
- **AI output was never trusted on inspection alone** — each of the corrections in §4 was
  caught by running the code and the tests, not by reading the diff.
