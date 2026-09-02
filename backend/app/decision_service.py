"""The merged decision flow: JSON rule engine first, vector search + LLM as fallback.

    Incoming request
          |
          v
    Evaluate using the JSON Rule Engine (app.orchestrator.evaluate_decision)
          |
          +-- Rule matched somewhere -> return the deterministic decision immediately.
          |
          +-- No matching rule anywhere -> vector search over the rule store
                 |
                 +-- Similar rule(s) found above threshold -> LLM explain/recommend ->
                 |      APPROVE / REFER / DECLINE with an AI explanation + confidence.
                 |
                 +-- No semantic match -> the configured default fallback verdict.

A per-ruleset decision counts as "matched" if it fired at least one rule anywhere (GATE,
SCORING, or TERMS) — i.e. ``matched_rules`` is non-empty. It only counts as unmatched if
every phase fell through and the ruleset's own ``default_outcome`` was used. The fallback
below only triggers when *every* active ruleset for the request was unmatched: this
mirrors the flow's single in/out path. A per-decision-type fallback (partial AI-assisted
decisions) is a reasonable variant to add later if that granularity is wanted.
"""
from typing import Any

from .llm import recommend_from_similar_rules
from .logging_config import get_logger
from .orchestrator import evaluate_decision

logger = get_logger(__name__)


def _request_to_query_text(request: dict) -> str:
    """A short natural-language-ish blob to embed/search against — the same kind of
    facts a rule's own searchable text is built from (see stores/vector_index.py)."""
    order = request.get("order", {})
    customer = request.get("customer", {})
    parts = [
        request.get("market", ""),
        order.get("device_sku", ""),
        order.get("tariff_code", ""),
        order.get("payment_method", ""),
        f"financed_amount {order.get('financed_amount')}",
        f"term_months {order.get('term_months')}",
        f"tenure_months {customer.get('tenure_months')}",
        f"is_existing {customer.get('is_existing')}",
    ]
    return " ".join(str(p) for p in parts if p not in (None, ""))


def _all_unmatched(result: dict) -> bool:
    decisions = result.get("decisions", [])
    if not decisions:
        return True
    return all(not d.get("matched_rules") for d in decisions)


def decide(
    request: dict,
    store: Any,
    provider_state: Any,
    signal_overrides: Any,
    settings: Any,
) -> dict:
    """Evaluate one request through the JSON engine first; fall back to vector search +
    LLM recommendation only if the engine matched nothing at all for this request."""
    rulesets_by_type = store.active_by_decision_type(market=request.get("market"))
    result = evaluate_decision(request, rulesets_by_type, provider_state, signal_overrides)
    result["ai_assisted"] = False

    if not _all_unmatched(result):
        return result

    logger.info("decision.no_rule_matched", extra={"event": "decision.no_rule_matched", "request_id": request.get("request_id")})

    query = _request_to_query_text(request)
    hits = store.search(query, k=6) if query else []
    threshold = getattr(settings, "vector_search_threshold", 0.75)
    strong_hits = [h for h in hits if (h.get("score") or 0.0) >= threshold]

    if strong_hits:
        recommendation = recommend_from_similar_rules(request, strong_hits, settings)
        result["composite_verdict"] = recommendation["verdict"]
        result["ai_assisted"] = True
        result["confidence"] = recommendation["confidence"]
        result["similar_rules"] = strong_hits
        result["explanation"] = {
            "text": recommendation["explanation"],
            "mode": recommendation["mode"],
            "degraded": False,
        }
        logger.info(
            "decision.vector_fallback",
            extra={
                "event": "decision.vector_fallback",
                "request_id": request.get("request_id"),
                "verdict": recommendation["verdict"],
                "confidence": recommendation["confidence"],
                "hits": len(strong_hits),
            },
        )
    else:
        fallback_verdict = getattr(settings, "fallback_verdict", "REFER")
        result["composite_verdict"] = fallback_verdict
        result["confidence"] = 0.0
        result["similar_rules"] = []
        logger.info(
            "decision.default_fallback",
            extra={"event": "decision.default_fallback", "request_id": request.get("request_id"), "verdict": fallback_verdict},
        )

    return result
