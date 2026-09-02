"""FastAPI shell: the thinnest possible adapter over the decision service. No business
logic lives here — every route just validates, delegates, and shapes a response. The
LLM/RAG layer is invoked from here too, always as an additive, never-fatal enrichment
step: an explanation or search failure can never break a decision.
"""
import uuid
from pathlib import Path
from typing import Optional

import pydantic
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, BackgroundTasks
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from . import anomaly
from .auth import create_access_token, get_current_user, require_role
from .config import settings
from .db import close_database, get_database
from .decision_service import decide
from .llm import explain_decision, generate_rule
from .logging_config import configure_logging, get_logger
from .providers import ProviderState
from .rule_store import RuleStore
from .async_decisions import AsyncDecisionStore
from .stores.shadow_metrics_store import ShadowMetricsStore
from .stores.candidate_store import CandidateRuleStore
from .stores.anomaly_tracker import AnomalyTracker
from .schemas import (
    AuthResponse,
    BatchRequest,
    DecisionRequest,
    AsyncDecisionRequest,
    LoginRequest,
    ProviderModeRequest,
    PublicUser,
    RuleGenerateRequest,
    RulesetVersionRequest,
    SignupRequest,
)
from .user_store import UserStore

RULESETS_DIR = Path(__file__).resolve().parent.parent / "rulesets"

configure_logging(settings.log_level)
logger = get_logger("decision_ledger.api")

app = FastAPI(title=settings.app_name, version="0.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Any localhost port is allowed in dev, so the dashboard still reaches the API when
    # Vite falls back to 5174/5175 because 5173 is taken (a common cause of a spurious
    # "Can't reach the API" banner). Tighten this for a real deployment.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.provider_state = ProviderState()
app.state.decisions_store: dict[str, dict] = {}
app.state.idempotency_index: dict[str, str] = {}


@app.on_event("startup")
def startup() -> None:
    # The JSON rule engine's authoritative store shares its MongoDB connection with the
    # user store; the vector index is a separate, fallback-only concern (see rule_store.py).
    app.state.store = RuleStore(settings)
    seed_result = app.state.store.seed_if_empty(RULESETS_DIR)
    app.state.user_store = UserStore(settings)
    app.state.async_store = AsyncDecisionStore(get_database(settings.mongodb_uri, settings.mongodb_database))
    app.state.shadow_metrics_store = ShadowMetricsStore(get_database(settings.mongodb_uri, settings.mongodb_database))
    app.state.candidate_store = CandidateRuleStore(get_database(settings.mongodb_uri, settings.mongodb_database))
    app.state.anomaly_tracker = AnomalyTracker(get_database(settings.mongodb_uri, settings.mongodb_database))
    logger.info(
        "app.startup",
        extra={
            "event": "ruleset.loaded",
            "llm_enabled": settings.llm_enabled,
            "seeded": seed_result.get("seeded", []),
            **app.state.store.stats(),
        },
    )


@app.on_event("shutdown")
def shutdown() -> None:
    close_database()


def _enrich_with_explanation(result: dict) -> None:
    """Attach a natural-language explanation in place. Never raises. Skipped when the
    decision service already attached an AI-assisted (vector-fallback) explanation."""
    if result.get("ai_assisted"):
        return
    try:
        result["explanation"] = explain_decision(result, settings)
    except Exception as exc:  # explanation is additive; a failure must not fail the call
        logger.warning("explain.error", extra={"event": "explain.error", "error": str(exc)})
        result["explanation"] = {"text": "", "mode": "unavailable", "degraded": True}


def _record_shadow_metrics(result: dict) -> None:
    """Persist any shadow-mode hits (risk_tiered_architecture.md §4 Tier 2) to the
    durable shadow_metrics collection. Additive and never-fatal, same posture as
    _enrich_with_explanation — a logging failure here must not fail the decision call."""
    try:
        app.state.shadow_metrics_store.record_hits(result["request_id"], result.get("shadow_metrics") or [])
    except Exception as exc:
        logger.warning("shadow_metrics.record_failed", extra={"event": "shadow_metrics.record_failed", "error": str(exc)})


def _maybe_trigger_anomaly(request: dict, result: dict) -> None:
    """Scheduled as a FastAPI background task (runs after the response is sent) from
    every route that produces a decision — see app/anomaly.py for why this can't live
    only in the async endpoint. Belt-and-suspenders try/except: maybe_trigger already
    never raises internally, but a background task's exception would otherwise vanish
    into FastAPI's log with no context of ours attached."""
    try:
        anomaly.maybe_trigger(request, result, app.state.anomaly_tracker, app.state.store, app.state.candidate_store, settings)
    except Exception as exc:
        logger.warning("anomaly.background_task_failed", extra={"event": "anomaly.background_task_failed", "error": str(exc)})


def _reject_if_deprecated(market: str) -> None:
    deprecated = app.state.store.deprecated_ruleset_ids(market=market)
    if deprecated:
        raise HTTPException(
            status_code=422,
            detail={
                "type": "https://verdict.example/errors/ruleset-deprecated",
                "title": "A ruleset required for this market has been deprecated",
                "status": 422,
                "detail": f"deprecated ruleset(s): {', '.join(deprecated)}",
            },
        )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0]
    field = ".".join(str(p) for p in first["loc"] if p != "body")
    return JSONResponse(
        status_code=422,
        content={
            "type": "https://verdict.example/errors/schema-validation",
            "title": "Request failed schema validation",
            "status": 422,
            "detail": f"{field}: {first['msg']}",
            "instance": str(request.url.path),
        },
    )


@app.get("/health")
def health() -> dict:
    return {"status": "UP"}


@app.get("/ready")
def ready() -> dict:
    stats = app.state.store.stats()
    return {
        "status": "READY",
        "rulesets_loaded": stats["rulesets"],
        "rules_loaded": stats["rules"],
        "llm_enabled": settings.llm_enabled,
        "rag_backend": stats["backend"],
    }


# --- Auth ----------------------------------------------------------------
def _store_unavailable(exc: PyMongoError) -> HTTPException:
    """Map a MongoDB outage to a clean 500, with a structured log."""
    logger.error("auth.store_unavailable", extra={"event": "auth.store_unavailable", "error": str(exc)})
    return HTTPException(status_code=500, detail="user store unavailable")


@app.post("/v1/auth/signup", response_model=AuthResponse)
def auth_signup(payload: SignupRequest) -> AuthResponse:
    store = app.state.user_store
    try:
        if store.exists(payload.username):
            raise HTTPException(status_code=409, detail="username already taken")
        user = store.create(payload.username, payload.password, payload.role)
    except ValueError:
        # Lost the race to the unique index between exists() and create().
        raise HTTPException(status_code=409, detail="username already taken")
    except PyMongoError as exc:
        raise _store_unavailable(exc)

    token = create_access_token(user["username"], user["role"])
    logger.info("auth.signup", extra={"event": "auth.signup", "username": user["username"], "role": user["role"]})
    return AuthResponse(token=token, user=PublicUser(**user))


@app.post("/v1/auth/login", response_model=AuthResponse)
def auth_login(payload: LoginRequest) -> AuthResponse:
    store = app.state.user_store
    try:
        ok = store.verify(payload.username, payload.password)
        user = store.get(payload.username) if ok else None
    except PyMongoError as exc:
        raise _store_unavailable(exc)

    if not ok or not user:
        raise HTTPException(status_code=401, detail="invalid credentials")

    token = create_access_token(user["username"], user["role"])
    logger.info("auth.login", extra={"event": "auth.login", "username": user["username"], "role": user["role"]})
    return AuthResponse(token=token, user=PublicUser(**user))


@app.get("/v1/auth/me", response_model=PublicUser)
def auth_me(user: PublicUser = Depends(get_current_user)) -> PublicUser:
    return user


# --- Decisions (JSON engine primary, vector search + LLM fallback) -----------
@app.post("/v1/decisions")
def post_decision(
    payload: DecisionRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    _user: PublicUser = Depends(get_current_user),
) -> dict:
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    if idempotency_key in app.state.idempotency_index:
        decision_id = app.state.idempotency_index[idempotency_key]
        stored = dict(app.state.decisions_store[decision_id])
        stored["replayed"] = True
        return stored

    request_dict = payload.model_dump()
    _reject_if_deprecated(request_dict["market"])
    signal_overrides = request_dict.pop("test_signals", None)
    logger.info("decision.request", extra={"event": "decision.request", "request_id": request_dict.get("request_id")})
    result = decide(request_dict, app.state.store, app.state.provider_state, signal_overrides, settings)

    decision_id = f"dec_{uuid.uuid4().hex[:10]}"
    result["decision_id"] = decision_id
    result["replayed"] = False
    _enrich_with_explanation(result)
    _record_shadow_metrics(result)
    background_tasks.add_task(_maybe_trigger_anomaly, request_dict, result)

    app.state.decisions_store[decision_id] = result
    app.state.idempotency_index[idempotency_key] = decision_id
    degraded = [s for s, v in result.get("signal_health", {}).items() if v != "OK"]
    logger.info(
        "decision.response",
        extra={
            "event": "decision.response",
            "decision_id": decision_id,
            "composite_verdict": result["composite_verdict"],
            "ai_assisted": result.get("ai_assisted", False),
            "degraded_signals": degraded,
        },
    )
    return result


def _process_async(job_id: str) -> None:
    async_store = app.state.async_store
    job = async_store.get_job(job_id)
    if not job:
        return
    async_store.update_status(job_id, "PROCESSING")
    try:
        request_dict = job["request"]
        signal_overrides = request_dict.pop("test_signals", None)
        result = decide(request_dict, app.state.store, app.state.provider_state, signal_overrides, settings)
        
        result["decision_id"] = f"dec_{uuid.uuid4().hex[:10]}"
        result["replayed"] = False
        _enrich_with_explanation(result)
        _record_shadow_metrics(result)
        # _process_async already runs off the request path (scheduled via
        # BackgroundTasks in post_async_decision), so this runs inline rather than
        # nesting another background task.
        _maybe_trigger_anomaly(request_dict, result)

        app.state.decisions_store[result["decision_id"]] = result
        async_store.update_result(job_id, result, "COMPLETED")
        
        webhook_url = job.get("webhook_url")
        if webhook_url:
            httpx.post(webhook_url, json=result, timeout=10.0)
    except Exception as exc:
        async_store.update_result(job_id, {"error": str(exc)}, "FAILED")


@app.post("/v1/decisions/async", status_code=202)
def post_async_decision(
    payload: AsyncDecisionRequest,
    background_tasks: BackgroundTasks,
    _user: PublicUser = Depends(get_current_user),
) -> dict:
    job = app.state.async_store.create_job(payload.model_dump(), payload.webhook_url)
    background_tasks.add_task(_process_async, job["job_id"])
    return {"job_id": job["job_id"], "status": "PENDING"}


@app.get("/v1/decisions/async/{job_id}")
def get_async_decision(job_id: str, _user: PublicUser = Depends(get_current_user)) -> dict:
    job = app.state.async_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.get("/v1/decisions/async")
def list_async_decisions(
    status: Optional[str] = None,
    limit: int = 20,
    _user: PublicUser = Depends(get_current_user),
) -> dict:
    return {"jobs": app.state.async_store.list_jobs(status, limit)}


@app.get("/v1/decisions")
def list_decisions(limit: int = 20, _user: PublicUser = Depends(get_current_user)) -> dict:
    ordered = list(app.state.decisions_store.values())[::-1]
    return {"decisions": ordered[:limit]}


@app.get("/v1/decisions/{decision_id}")
def get_decision(decision_id: str) -> dict:
    stored = app.state.decisions_store.get(decision_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="decision not found")
    return stored


@app.post("/v1/decisions/batch")
def post_decisions_batch(payload: BatchRequest, background_tasks: BackgroundTasks) -> dict:
    results = []
    for index, raw_item in enumerate(payload.requests):
        try:
            item = DecisionRequest(**raw_item)
            request_dict = item.model_dump()
            _reject_if_deprecated(request_dict["market"])
            signal_overrides = request_dict.pop("test_signals", None)
            result = decide(request_dict, app.state.store, app.state.provider_state, signal_overrides, settings)
            decision_id = f"dec_{uuid.uuid4().hex[:10]}"
            result["decision_id"] = decision_id
            result["replayed"] = False
            _enrich_with_explanation(result)
            _record_shadow_metrics(result)
            background_tasks.add_task(_maybe_trigger_anomaly, request_dict, result)
            app.state.decisions_store[decision_id] = result
            results.append(result)
        except HTTPException as exc:
            results.append({"index": index, "error": {"status": exc.status_code, "detail": exc.detail}})
        except pydantic.ValidationError as exc:
            results.append({"index": index, "error": {"status": 422, "detail": exc.errors()[0]["msg"]}})
        except Exception as exc:  # isolate one bad item from the rest of the batch
            results.append({"index": index, "error": {"status": 422, "detail": str(exc)}})
    return {"results": results}


# --- Rule authoring: NL generation + semantic search (fallback index) --------
@app.post("/v1/rules/generate")
def post_rule_generate(
    payload: RuleGenerateRequest,
    _user: PublicUser = Depends(require_role("admin")),
) -> dict:
    """Turn plain-English policy into a validated JSON rule and, unless disabled, persist
    it — a new authoritative ruleset version AND an updated vector index entry, in one
    call — so it is searchable and live in the next decision."""
    # Give generation the target ruleset's own verdict vocabulary — e.g. a "decline"
    # instruction against de.fraud must produce "BLOCK", not the generic "DECLINE".
    # An unknown ruleset_id here just means no context; add_rule's own KeyError below
    # still reports that clearly.
    ruleset_context: Optional[dict] = None
    try:
        target = app.state.store.get_ruleset(payload.ruleset_id)
        ruleset_context = {
            "composite_class": target.get("composite_class", {}),
            "verdict_severity": target.get("verdict_severity", []),
        }
    except KeyError:
        pass

    result = generate_rule(payload.text, settings, ruleset_context)
    result["persisted"] = False
    result["ruleset_id"] = payload.ruleset_id

    if payload.persist and result["valid"]:
        try:
            app.state.store.add_rule(payload.ruleset_id, result["rule"])
            result["persisted"] = True
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    elif payload.persist and not result["valid"]:
        result["warnings"] = result.get("warnings", []) + ["rule failed validation; not stored"]

    logger.info(
        "rule.generated",
        extra={
            "event": "rule.generated",
            "mode": result["mode"],
            "valid": result["valid"],
            "persisted": result["persisted"],
            "ruleset_id": payload.ruleset_id,
        },
    )
    return result


@app.get("/v1/rules/search")
def get_rule_search(
    q: str = Query(..., min_length=1),
    k: int = Query(default=6, ge=1, le=25),
    _user: PublicUser = Depends(require_role("admin")),
) -> dict:
    """Semantic search over the fallback vector index."""
    results = app.state.store.search(q, k)
    return {"query": q, "backend": app.state.store.backend, "count": len(results), "results": results}


@app.get("/v1/rules")
def list_rules(ruleset_id: Optional[str] = Query(default=None)) -> dict:
    """List the rules currently active, optionally for a single ruleset."""
    store = app.state.store
    ids = [ruleset_id] if ruleset_id else store.ruleset_ids()
    unknown = [rid for rid in ids if rid not in store.ruleset_ids()]
    if unknown:
        raise HTTPException(status_code=404, detail=f"unknown ruleset: {unknown[0]}")
    return {"rulesets": {rid: store.list_rules(rid) for rid in ids}}


# --- Ruleset lifecycle: versioned, authoritative, audited --------------------
@app.get("/v1/rulesets")
def list_rulesets(_user: PublicUser = Depends(get_current_user)) -> dict:
    store = app.state.store
    rulesets = []
    for ruleset_id in store.ruleset_ids():
        active = store.get_ruleset(ruleset_id)
        rulesets.append(
            {
                "ruleset_id": ruleset_id,
                "active_version": active["version"],
                "decision_type": active["decision_type"],
                "market": active.get("market"),
                "deprecated_at": store.deprecated_at(ruleset_id),
            }
        )
    return {"rulesets": rulesets}


@app.post("/v1/rulesets/{ruleset_id}/versions")
def create_ruleset_version(
    ruleset_id: str,
    payload: RulesetVersionRequest,
    user: PublicUser = Depends(require_role("admin")),
) -> dict:
    try:
        return app.state.store.create_ruleset(ruleset_id, payload.model_dump(), published_by=user.username)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "type": "https://verdict.example/errors/ruleset-lint-failed",
                "title": "Ruleset failed linting or validation",
                "status": 422,
                "errors": exc.args[0] if exc.args else str(exc),
            },
        )


@app.get("/v1/rulesets/{ruleset_id}/versions")
def list_ruleset_versions(ruleset_id: str, _user: PublicUser = Depends(get_current_user)) -> dict:
    versions = app.state.store.list_versions(ruleset_id)
    if not versions:
        raise HTTPException(status_code=404, detail="ruleset not found")
    return {
        "versions": [
            {"version": v["version"], "status": v["status"], "content_hash": v["content_hash"], "published_at": v["published_at"]}
            for v in versions
        ]
    }


@app.get("/v1/rulesets/{ruleset_id}/versions/{version}")
def get_ruleset_version(ruleset_id: str, version: int, _user: PublicUser = Depends(get_current_user)) -> dict:
    try:
        return app.state.store.get_version(ruleset_id, version)
    except ValueError:
        raise HTTPException(status_code=404, detail="version not found")


@app.post("/v1/rulesets/{ruleset_id}/rollback")
def rollback_ruleset(ruleset_id: str, _user: PublicUser = Depends(require_role("admin"))) -> dict:
    try:
        return app.state.store.rollback(ruleset_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/v1/rulesets/{ruleset_id}/deprecate")
def deprecate_ruleset(ruleset_id: str, _user: PublicUser = Depends(require_role("admin"))) -> dict:
    try:
        app.state.store.deprecate(ruleset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ruleset_id": ruleset_id, "deprecated_at": app.state.store.deprecated_at(ruleset_id)}


@app.post("/v1/rulesets/{ruleset_id}/reactivate")
def reactivate_ruleset(ruleset_id: str, _user: PublicUser = Depends(require_role("admin"))) -> dict:
    try:
        app.state.store.reactivate(ruleset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ruleset_id": ruleset_id, "deprecated_at": None}


@app.get("/v1/rulesets/{ruleset_id}/shadow-metrics")
def get_shadow_metrics(ruleset_id: str, _user: PublicUser = Depends(require_role("admin"))) -> dict:
    """Per-rule shadow-hit counts for one ruleset (risk_tiered_architecture.md §4 Tier
    2) — what the dashboard's "this shadow rule would have declined N transactions"
    card reads."""
    if ruleset_id not in app.state.store.ruleset_ids():
        raise HTTPException(status_code=404, detail=f"unknown ruleset: {ruleset_id}")
    return {"ruleset_id": ruleset_id, "rules": app.state.shadow_metrics_store.aggregate_by_rule(ruleset_id)}


@app.post("/v1/rulesets/{ruleset_id}/rules/{rule_id}/publish")
def publish_shadow_rule(
    ruleset_id: str,
    rule_id: str,
    user: PublicUser = Depends(require_role("admin")),
) -> dict:
    """Tier 2 -> live: an admin has reviewed the shadow metrics and is satisfied — flip
    is_shadow off so the rule's verdict starts actually reaching customers."""
    try:
        rule = app.state.store.publish_shadow_rule(ruleset_id, rule_id, actor=user.username)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    logger.info(
        "rulestore.shadow_rule_published",
        extra={"event": "rulestore.shadow_rule_published", "ruleset_id": ruleset_id, "rule_id": rule_id},
    )
    return {"ruleset_id": ruleset_id, "rule_id": rule_id, "is_shadow": rule.get("is_shadow", False)}


@app.get("/v1/candidate-rules")
def list_candidate_rules(
    status: Optional[str] = Query(default="PENDING_REVIEW"),
    limit: int = Query(default=50, ge=1, le=200),
    _user: PublicUser = Depends(require_role("admin")),
) -> dict:
    """Tier 3 queue (risk_tiered_architecture.md §4): HIGH-risk generated rules waiting
    on a human. Defaults to the actionable subset; pass status= explicitly (or empty)
    to see APPROVED/DISCARDED history too."""
    return {"candidates": app.state.candidate_store.list(status=status or None, limit=limit)}


@app.get("/v1/candidate-rules/{candidate_id}")
def get_candidate_rule(candidate_id: str, _user: PublicUser = Depends(require_role("admin"))) -> dict:
    candidate = app.state.candidate_store.get(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="candidate rule not found")
    return candidate


def _pending_candidate_or_404(candidate_id: str) -> dict:
    candidate = app.state.candidate_store.get(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="candidate rule not found")
    if candidate["status"] != "PENDING_REVIEW":
        raise HTTPException(status_code=409, detail=f"candidate rule is {candidate['status']}, not PENDING_REVIEW")
    return candidate


@app.post("/v1/candidate-rules/{candidate_id}/approve")
def approve_candidate_rule(candidate_id: str, user: PublicUser = Depends(require_role("admin"))) -> dict:
    """An admin has reviewed a HIGH-risk generated rule and signed off — splice it into
    the active ruleset via the normal RuleStore.add_rule path, forced live (never
    shadow) since approval means "I want this enforced now", not "test it quietly"."""
    candidate = _pending_candidate_or_404(candidate_id)
    rule = {**candidate["rule"], "is_shadow": False}
    try:
        app.state.store.add_rule(candidate["ruleset_id"], rule, actor=user.username)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    app.state.candidate_store.update_status(candidate_id, "APPROVED")
    logger.info(
        "candidate_rule.approved",
        extra={"event": "candidate_rule.approved", "candidate_id": candidate_id, "ruleset_id": candidate["ruleset_id"]},
    )
    return {"candidate_id": candidate_id, "status": "APPROVED", "ruleset_id": candidate["ruleset_id"], "rule_id": rule["id"]}


@app.post("/v1/candidate-rules/{candidate_id}/discard")
def discard_candidate_rule(candidate_id: str, _user: PublicUser = Depends(require_role("admin"))) -> dict:
    candidate = _pending_candidate_or_404(candidate_id)
    app.state.candidate_store.update_status(candidate_id, "DISCARDED")
    logger.info("candidate_rule.discarded", extra={"event": "candidate_rule.discarded", "candidate_id": candidate_id})
    return {"candidate_id": candidate_id, "status": "DISCARDED"}


@app.get("/v1/rulesets/{ruleset_id}/audit")
def get_ruleset_audit(ruleset_id: str, _user: PublicUser = Depends(require_role("admin"))) -> dict:
    db = get_database()
    cursor = db.audit_log.find({"ruleset_id": ruleset_id}, sort=[("timestamp", -1)])
    events = []
    for doc in cursor:
        events.append({
            "action": doc["action"],
            "actor": doc["actor"],
            "version": doc.get("version"),
            "timestamp": doc["timestamp"].isoformat(),
            "details": doc.get("details", {}),
        })
    return {"audit": events}


@app.get("/v1/audit")
def list_audit_events(limit: int = 50, _user: PublicUser = Depends(require_role("admin"))) -> dict:
    db = get_database()
    cursor = db.audit_log.find({}, sort=[("timestamp", -1)]).limit(limit)
    events = []
    for doc in cursor:
        events.append({
            "ruleset_id": doc["ruleset_id"],
            "action": doc["action"],
            "actor": doc["actor"],
            "version": doc.get("version"),
            "timestamp": doc["timestamp"].isoformat(),
            "details": doc.get("details", {}),
        })
    return {"audit": events}


# --- Test-only failure injection ---------------------------------------------
@app.post("/v1/_test/providers/reset")
def reset_provider_modes(_user: PublicUser = Depends(require_role("admin"))) -> dict:
    app.state.provider_state.reset()
    return {"status": "reset"}


@app.post("/v1/_test/providers/{provider}")
def set_provider_mode(
    provider: str,
    payload: ProviderModeRequest,
    _user: PublicUser = Depends(require_role("admin")),
) -> dict:
    try:
        app.state.provider_state.set_mode(provider, payload.mode)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    logger.info("signal.degraded", extra={"event": "signal.degraded", "provider": provider, "mode": payload.mode})
    return {"provider": provider, "mode": payload.mode}


