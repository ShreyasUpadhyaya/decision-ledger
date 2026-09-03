# DecisionLedger — REST API Specification

> **See also:** [ARCHITECTURE.md](ARCHITECTURE.md) for the design rationale behind these endpoints.

Base URL (local): `http://127.0.0.1:8000`
Content type: `application/json` unless noted.
All errors use an RFC-7807-style problem shape: `{type, title, status, detail, instance}`.

Interactive, auto-generated API docs are also served by the running app at `/docs`
(Swagger UI) and `/redoc`, with the raw schema at `/openapi.json`. This document is the
curated, submission-facing reference; the OpenAPI schema is the machine-readable one.

**Auth**: every route below except `/health`, `/ready`, `/v1/auth/*`, and
`GET /v1/decisions/{id}` requires `Authorization: Bearer <token>` (see `POST
/v1/auth/login`). Routes that mutate rules or rulesets (`/v1/rules/generate`,
`/v1/rules/search`, `/v1/rulesets/...` writes, `/v1/_test/providers/...`) additionally
require the `admin` role.

---

## Authentication

Sessions are stateless HS256 JWTs (see `app/auth.py`). Obtain a token from `signup` or
`login`, then send it as `Authorization: Bearer <token>` on every protected route. Token
lifetime is driven by `AUTH_TOKEN_TTL_MINUTES`. Two roles exist: `user` and `admin`;
admin-only routes are called out throughout this doc. An expired or malformed token → `401`;
a valid token lacking the required role → `403`.

### `POST /v1/auth/signup`
Create a user and return a session token. `username` ≥ 3 chars, `password` ≥ 6 chars,
`role` ∈ `admin`, `user`. A taken username → `409`.
```json
{ "username": "analyst", "password": "s3cret!", "role": "admin" }
```
**Response `200`**
```json
{ "token": "eyJhbGciOi...", "user": { "username": "analyst", "role": "admin" } }
```

### `POST /v1/auth/login`
Exchange credentials for a token. Bad credentials → `401`.
```json
{ "username": "analyst", "password": "s3cret!" }
```
Response is the same `AuthResponse` shape as signup.

### `GET /v1/auth/me`
Return the caller identity resolved from the bearer token. Requires any authenticated role.
```json
{ "username": "analyst", "role": "admin" }
```

---

## Health & readiness

### `GET /health`
Liveness probe.
```json
{ "status": "UP" }
```

### `GET /ready`
Readiness + capability report.
```json
{
  "status": "READY",
  "rulesets_loaded": 5,
  "rules_loaded": 36,
  "llm_enabled": false,
  "rag_backend": "mongodb"
}
```
`llm_enabled` is `true` only when `ENABLE_LLM_EXPLANATIONS=true` **and** an `OPENAI_API_KEY`
is present. `rag_backend` is `mongodb` when MongoDB Atlas Vector Search is the fallback
index, otherwise `memory`. `rules_loaded` is the live count of rules in the vector index
(grows as rules are authored) — the count of rules in the authoritative store is always
the same, since every write updates both together.

---

## Decisions

### `POST /v1/decisions`
Evaluate a checkout order across all five decision domains and return a composite verdict,
per-decision breakdown, full execution trace, and a natural-language explanation.

**Headers**

| Header | Required | Notes |
|---|---|---|
| `Idempotency-Key` | ✅ | Replaying the same key returns the stored decision with `replayed: true`. Missing key → `400`. |
| `Authorization: Bearer <token>` | ✅ | Any authenticated role. |

The JSON rule engine is evaluated first and is authoritative. Only if **every** active
ruleset for this request matched nothing at all does the platform fall back to a vector
search + LLM recommendation over the rule store — see `ai_assisted` below.

**Request body** (abbreviated — see `app/schemas.py` for the full model)
```json
{
  "request_id": "req_1001",
  "market": "DE",
  "channel": "ONESHOP_WEB",
  "requested_at": "2026-07-24T09:00:00Z",
  "customer": { "customer_id": "cus_1", "date_of_birth": "1990-01-01",
                "is_existing": true, "tenure_months": 24, "segment": "CONSUMER" },
  "order": { "total_amount": 899.0, "financed_amount": 899.0, "term_months": 24,
             "device_sku": "SM-A556-128", "line_count": 1,
             "tariff_code": "MAGENTA_M", "payment_method": "SEPA_DD" },
  "context": { "ip_country": "DE", "shipping_country": "DE",
               "device_fingerprint": "fp_1", "session_age_seconds": 300 },
  "test_signals": { "bureau": { "score": 618 } }
}
```
`test_signals` is an optional test-only override of the mock provider outputs.

**Response `200`**
```json
{
  "decision_id": "dec_ea554d9e82",
  "request_id": "req_1001",
  "composite_verdict": "APPROVE",
  "terms": { "max_financed_amount": 899.0, "max_term_months": 24 },
  "decisions": [
    { "type": "DEVICE_FINANCING", "verdict": "APPROVE", "pre_overlay_verdict": null,
      "terms": {"...": "..."}, "reason_codes": ["BASE_SCORE", "..."],
      "matched_rules": ["DF-100", "..."], "score": 80 }
  ],
  "signal_confidence": 1.0,
  "signal_health": { "bureau": "OK", "account_history": "OK", "fraud": "OK",
                     "identity": "OK", "device_catalog": "OK" },
  "ruleset_versions": { "de.device-financing": 1 },
  "traces": { "DEVICE_FINANCING": [ { "rule_id": "DF-014", "phase": "GATE",
              "outcome": "FALSE" } ] },
  "explanation": {
    "text": "The order was approved. The resulting commercial terms are ...",
    "mode": "template",
    "degraded": false
  },
  "ai_assisted": false,
  "replayed": false
}
```
`explanation.mode` is `"llm"` when produced by the LLM, `"template"` when produced by the
deterministic fallback. The explanation step never fails a decision.

**When no rule matched anywhere** (`ai_assisted: true`), the response additionally
carries:
```json
{
  "ai_assisted": true,
  "confidence": 0.83,
  "similar_rules": [
    { "ruleset_id": "de.device-financing", "rule_id": "DF-201", "phase": "TERMS",
      "verdict": "APPROVE", "reason_codes": ["BASE_SCORE"], "facts": ["computed.risk_score"],
      "status": "ACTIVE", "score": 0.91 }
  ],
  "explanation": { "text": "No deterministic rule matched...", "mode": "heuristic", "degraded": false }
}
```
`composite_verdict` here comes from the recommendation (or `FALLBACK_VERDICT`, default
`REFER`, if no similar rule cleared `VECTOR_SEARCH_THRESHOLD`), not from a ruleset.

**Composite aggregation**

| Any decision verdict | Composite |
|---|---|
| `DECLINE` / `BLOCK` / `FAIL` | `DECLINE` |
| `REVIEW` / `REFER` / `STEP_UP_KYC` / `DOWNGRADE_OFFER` | `REFER` |
| otherwise | financing verdict (`APPROVE` / `APPROVE_WITH_DEPOSIT`) |

### `GET /v1/decisions/{decision_id}`
Retrieve a stored decision. `404` if unknown.

### `GET /v1/decisions?limit=20`
List decision history, most recent first.

### `POST /v1/decisions/batch`
Evaluate many requests; one malformed item is isolated and reported, the rest still run.
```json
{ "requests": [ { "...decision request..." }, { "...": "..." } ] }
```
Response: `{ "results": [ {decision...}, {"index": 1, "error": {"status": 422, "detail": "..."}} ] }`

### `POST /v1/decisions/async`
Queue a decision for background evaluation and return immediately with a job handle
(`202 Accepted`). The request body is a normal decision request plus two optional fields:
`webhook_url` (the completed decision is `POST`ed there when done) and `callback_headers`.
Any authenticated role.
```json
{ "...decision request...", "webhook_url": "https://client.example/hooks/verdict" }
```
**Response `202`**
```json
{ "job_id": "async_1a2b3c4d5e6f", "status": "PENDING" }
```

### `GET /v1/decisions/async/{job_id}`
Poll a queued job. `status` moves `PENDING → PROCESSING → COMPLETED` (or `FAILED`).
`result` is `null` until the job finishes, then holds the full decision (or `{ "error": ... }`
on failure). Unknown job → `404`.
```json
{
  "job_id": "async_1a2b3c4d5e6f",
  "status": "COMPLETED",
  "request": { "...": "..." },
  "result": { "decision_id": "dec_...", "composite_verdict": "APPROVE", "...": "..." },
  "webhook_url": null,
  "created_at": "2026-07-24T09:00:00Z",
  "updated_at": "2026-07-24T09:00:01Z",
  "completed_at": "2026-07-24T09:00:01Z"
}
```

### `GET /v1/decisions/async?status=PENDING&limit=20`
List queued jobs, most recent first, optionally filtered by `status`.
```json
{ "jobs": [ { "job_id": "async_...", "status": "COMPLETED", "...": "..." } ] }
```

---

## Rules (LLM & RAG — fallback index, not the source of truth)

### `POST /v1/rules/generate`
Convert a plain-English policy into a validated JSON rule and, unless disabled, **persist
it through `RuleStore`** — a new version in the authoritative ruleset repository AND an
updated vector-index entry, in one call — so it is searchable and used in the next
decision. *Admin only.*

**Request**
```json
{
  "text": "Require a 50% deposit if financed amount exceeds 2000 for new customers",
  "ruleset_id": "de.device-financing",
  "persist": true
}
```
`ruleset_id` (default `de.device-financing`) is the ruleset the rule is stored into and
evaluated within. `persist` (default `true`) writes it to the store and makes it live; set
`false` to preview without storing.

**Response `200`**
```json
{
  "rule": {
    "id": "GEN-0D18AC", "phase": "OVERLAY", "priority": 450, "enabled": true,
    "when": { "all": [
      { "fact": "order.financed_amount", "op": "GREATER_THAN", "value": 2000 },
      { "fact": "customer.is_existing", "op": "IS_FALSE" } ] },
    "then": { "verdict": "APPROVE_WITH_DEPOSIT",
              "terms": { "deposit_pct": 50 },
              "reason_codes": ["GENERATED_DEPOSIT_REQUIREMENT"] }
  },
  "mode": "heuristic",
  "confidence": 0.9,
  "warnings": [],
  "valid": true,
  "validation_errors": [],
  "source_text": "Require a 50% deposit if financed amount exceeds 2000 for new customers",
  "persisted": true,
  "ruleset_id": "de.device-financing"
}
```
`mode` is `"llm"` or `"heuristic"`. Every returned rule is validated by compiling its
condition tree with the production compiler; `valid` reflects that check. A rule only
persists when `valid` is true. An unknown `ruleset_id` returns `422`.

### `GET /v1/rules/search?q=...&k=6`
Semantic search over the fallback vector index (including any rule authored at runtime).
*Admin only.* This is the same search the decision service uses when no rule matches.
```json
{
  "query": "cap financing for young customers",
  "backend": "mongodb",
  "count": 4,
  "results": [
    { "ruleset_id": "de.device-financing", "rule_id": "OV-410", "phase": "OVERLAY",
      "verdict": "APPROVE_WITH_DEPOSIT", "reason_codes": ["YOUNG_ADULT_CAP"],
      "facts": ["customer.date_of_birth"], "status": "ACTIVE", "score": 0.52 }
  ]
}
```

### `GET /v1/rules?ruleset_id=...`
List the rules currently active in the authoritative store, optionally filtered to one
ruleset (omit `ruleset_id` for all). Unknown ruleset → `404`.
```json
{ "rulesets": { "de.device-financing": [ { "id": "DF-014", "phase": "GATE", "...": "..." } ] } }
```

---

## Ruleset lifecycle (authoritative, versioned, audited)

All mutation routes here are *admin only*; reads require any authenticated user.

### `GET /v1/rulesets`
List every known ruleset with its active version and deprecation status.

### `POST /v1/rulesets/{ruleset_id}/versions`
Publish a new version of a ruleset — the full envelope (see `RulesetVersionRequest` in
`app/schemas.py`: `decision_type`, `market`, `score_fact`, `verdict_severity`,
`composite_class`, `default_outcome`, `rules`, `default_terms_by_verdict`). Runs
`core/linter.py` first; a failing lint returns `422` with `type:
".../ruleset-lint-failed"` and nothing is stored. On success, every rule in the new
version is also indexed into the vector store.

### `GET /v1/rulesets/{ruleset_id}/versions` / `GET .../versions/{version}`
List version metadata, or fetch one version's full content.

### `POST /v1/rulesets/{ruleset_id}/rollback`
Reactivate the previous version.

### `POST /v1/rulesets/{ruleset_id}/deprecate` / `POST .../reactivate`
A deprecated ruleset's market is rejected at `/v1/decisions` time (`422`,
`type: ".../ruleset-deprecated"`) but remains readable/auditable.

### `GET /v1/rulesets/{ruleset_id}/audit` / `GET /v1/audit?limit=50`
The audit trail (`CREATE_VERSION`, `ROLLBACK`, `DEPRECATE`, `REACTIVATE`) for one ruleset
or across all of them.

---

## Test / failure-injection surface

### `POST /v1/_test/providers/{provider}`
Force a mock provider into a failure mode. `provider` ∈ `bureau`, `account_history`,
`fraud`, `identity`, `device_catalog`.
```json
{ "mode": "TIMEOUT" }
```
Modes: `OK`, `TIMEOUT`, `ERROR`, `SLOW`, `STALE`. Unknown provider/mode → `422`.

### `POST /v1/_test/providers/reset`
Reset every provider back to `OK`.

---

## Operator reference

| Family | Operators |
|---|---|
| Numeric | `GREATER_THAN`, `GTE`, `LESS_THAN`, `LTE`, `EQUALS`, `NOT_EQUALS`, `BETWEEN` |
| Boolean | `IS_TRUE`, `IS_FALSE` |
| String | `EQUALS`, `CONTAINS`, `STARTS_WITH`, `ENDS_WITH`, `MATCHES_REGEX`, `IN_LIST`, `NOT_IN_LIST` |
| Date/Time | `BEFORE`, `AFTER`, `BETWEEN_DATES`, `WITHIN_LAST_DAYS`, `AGE_IN_YEARS_GTE` |
| Null-safety | `IS_NULL`, `IS_NOT_NULL`, `IS_EMPTY` |

A null fact evaluates a leaf to `INDETERMINATE` (never a crash, never a silent `FALSE`),
except the null-safety operators which are defined on null.
