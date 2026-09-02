"""The impure shell (spec §5.4): resolves rulesets, fetches signals, freezes a context,
invokes the pure evaluator per ruleset, aggregates, and shapes the response. Nothing in
`core/` imports this module — the dependency only ever points inward.
"""
import datetime
from typing import Optional

from .core.aggregator import aggregate
from .core.evaluator import evaluate_ruleset
from .providers import CONFIDENCE_PENALTY, ProviderState, fetch_signal

_SIGNAL_PROVIDERS = ("bureau", "account_history", "fraud", "identity", "device_catalog")


def _parse_date(value: str) -> datetime.date:
    return datetime.date.fromisoformat(value)


def _parse_datetime(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _fresh_context(base_context: dict) -> dict:
    ctx = dict(base_context)
    ctx["computed"] = dict(base_context["computed"])
    return ctx


def _fetch_all_signals(state: ProviderState, overrides: Optional[dict]) -> dict:
    return {provider: fetch_signal(state, provider, overrides) for provider in _SIGNAL_PROVIDERS}


def _signal_health_and_confidence(results: dict) -> tuple[dict, float]:
    health = {}
    penalty_total = 0.0
    for provider, result in results.items():
        if result["status"] == "OK":
            health[provider] = "OK"
        elif result["status"] == "STALE":
            health[provider] = "STALE"
        else:
            health[provider] = "DEGRADED"
        if result["fallback_applied"]:
            penalty_total += CONFIDENCE_PENALTY.get(provider, 0.0)
    return health, max(0.0, 1.0 - penalty_total)


def _forced_decision(decision_type: str, verdict: str, reason_code: str, terms: Optional[dict]) -> dict:
    return {
        "type": decision_type,
        "verdict": verdict,
        "pre_overlay_verdict": None,
        "terms": terms,
        "reason_codes": [reason_code],
        "matched_rules": [],
        "score": None,
    }


# A degraded provider forces a safe verdict directly, bypassing that ruleset entirely —
# never let an all-null scoring phase quietly resolve to a ruleset's own permissive
# default (§3.2 of the examples doc). This is genuine per-decision-type business logic
# about which signal a ruleset depends on and what "safe" means for it, not an artifact
# of hardcoded wiring — a new ruleset type needs an entry here only if it *also* wants
# a forced-fallback strategy; otherwise confidence-penalty degradation (§8.2) already
# applies generically via null-fact propagation (§6.6).
_FORCED_FALLBACKS = {
    "FRAUD": ("fraud", "REVIEW", "FRAUD_PROVIDER_UNAVAILABLE", None),
    "IDENTITY": ("identity", "STEP_UP_KYC", "IDENTITY_PROVIDER_UNAVAILABLE", {"required_document": "SELFIE_WITH_ID"}),
}


def evaluate_decision(
    request: dict,
    rulesets_by_type: dict[str, dict],
    provider_state: ProviderState,
    signal_overrides: Optional[dict] = None,
) -> dict:
    requested_at = _parse_datetime(request["requested_at"])

    signal_results = _fetch_all_signals(provider_state, signal_overrides)
    signal_health, signal_confidence = _signal_health_and_confidence(signal_results)

    base_context = {
        "customer": {**request["customer"], "date_of_birth": _parse_date(request["customer"]["date_of_birth"])},
        "order": request["order"],
        "context": request["context"],
        "market": request["market"],
        "bureau": signal_results["bureau"]["data"],
        "account": signal_results["account_history"]["data"],
        "fraud": signal_results["fraud"]["data"],
        "identity": signal_results["identity"]["data"],
        "device": signal_results["device_catalog"]["data"],
        "computed": {"signal_confidence": signal_confidence},
    }

    traces: dict[str, list] = {}
    decision_set: list[dict] = []
    shadow_metrics: list[dict] = []

    # One generic loop over whatever rulesets are active — adding a new ruleset type
    # (using an existing signal with standard confidence-based degradation) requires
    # zero changes here. This is the crux fix: the orchestrator never names a
    # ruleset_id, only whatever decision_type the ruleset store hands it.
    for decision_type, ruleset in rulesets_by_type.items():
        if decision_type in _FORCED_FALLBACKS:
            provider_name, verdict, reason, terms = _FORCED_FALLBACKS[decision_type]
            if signal_results[provider_name]["status"] != "OK":
                decision_set.append(_forced_decision(decision_type, verdict, reason, terms))
                traces[decision_type] = [
                    {"note": f"{provider_name} provider unavailable; ruleset bypassed, forced to {verdict}"}
                ]
                continue

        ctx = _fresh_context(base_context)
        decision, trace, shadow_hits = evaluate_ruleset(ctx, ruleset, requested_at)
        decision["type"] = decision_type

        if decision_type == "DEVICE_FINANCING" and decision.get("terms") and "deposit_pct" in decision["terms"]:
            pct = decision["terms"]["deposit_pct"]
            decision["terms"]["deposit_amount"] = round(request["order"]["financed_amount"] * pct / 100, 2)

        decision_set.append(decision)
        traces[decision_type] = trace
        for hit in shadow_hits:
            shadow_metrics.append({
                "ruleset_id": ruleset["ruleset_id"],
                "decision_type": decision_type,
                **hit,
            })

    for decision in decision_set:
        if decision["verdict"] == "REVIEW" and decision.get("terms") and "sla_hours" in decision["terms"]:
            decision["review_task"] = {"sla_hours": decision["terms"]["sla_hours"]}
            decision["terms"] = None

    composite = aggregate(decision_set, rulesets_by_type)

    return {
        "request_id": request["request_id"],
        "composite_verdict": composite["composite_verdict"],
        "terms": composite["terms"],
        "decisions": decision_set,
        "signal_confidence": round(signal_confidence, 2),
        "signal_health": signal_health,
        "ruleset_versions": {rs["ruleset_id"]: rs["version"] for rs in rulesets_by_type.values()},
        "traces": traces,
        "shadow_metrics": shadow_metrics,
    }
