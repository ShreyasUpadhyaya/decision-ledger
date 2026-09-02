"""The Anomaly Trigger (risk_tiered_architecture.md §2 & §5 step 6): closes the loop from
a live traffic spike to a routed rule with zero human input for the trigger itself —
only the routing destination (auto-enforce / shadow / human review) depends on risk.

Called from ``main.py`` as a FastAPI background task after a decision's response has
already been sent, from every path that produces a decision (sync, async, batch) — not
from ``app/async_decisions.py`` alone, which only ever sees traffic submitted to the
async endpoint. Never raises: this runs after the response, so nothing here can be
allowed to surface to a caller.
"""
from typing import Any, Optional

from .core.linter import lint_ruleset
from .llm import assess_risk, generate_rule
from .logging_config import get_logger

logger = get_logger("decision_ledger.anomaly")

# (dimension label, dotted path on the *request*, NL phrase). Read off the request, not
# a signal result — ip_country is client-supplied context, never fetched from a
# provider, so this never depends on provider health.
_TRACKED_DIMENSIONS: list[tuple[str, str, str]] = [
    ("ip_country", "context.ip_country", "the ip_country is"),
]

_WINDOW_SECONDS = 300  # 5 minutes — risk_tiered_architecture.md §2's example threshold
_THRESHOLD = 10  # > 10 declines from one dimension value inside the window


def _dotted_get(d: dict, path: str) -> Optional[Any]:
    node: Any = d
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _condition_leaf_count(node: Optional[dict]) -> int:
    """rule_generator.py's heuristic path has no vocabulary for context.* facts like
    ip_country (see its _FACT_VOCAB) — asked to parse "the ip_country is ZZ" with no
    OpenAI key configured, it silently returns a *structurally valid*, unconditional
    `{"all": []}` GATE DECLINE: a rule that blocks every order, not just ip_country=ZZ
    ones. `generate_rule`'s own validation compiles fine (an empty `all` is a
    legitimate vacuous-TRUE condition elsewhere) so it can't catch this — the emptiness
    is only meaningful in the context of "this was supposed to be scoped to one
    dimension value". This is that context-specific check: refuse to route anything
    with zero leaf conditions into ANY tier, including shadow, rather than let a
    population-wide rule through under the banner of a risk-tiering safety mechanism."""
    if not isinstance(node, dict):
        return 0
    if "fact" in node:
        return 1
    return sum(
        _condition_leaf_count(child)
        for combinator in ("all", "any", "none")
        for child in (node.get(combinator) or [])
    )


def _references_unknown_facts(rule: dict) -> bool:
    """Neither generation path reliably knows about context.* facts: the heuristic path
    has no vocabulary for them at all (see _condition_leaf_count above), and the LLM
    prompt's own fact list omits them too — observed producing "ip_country" instead of
    "context.ip_country" with a real key configured, a condition that compiles fine but
    would never match anything (resolve_fact finds no top-level "ip_country" key).
    Reuses the same UNKNOWN_FACT/UNKNOWN_OPERATOR check RuleStore.add_rule runs before
    publishing, so a bad fact path is caught here — at generation time, for every tier
    — rather than only at HITL approval time (Tier 3 never runs through add_rule until
    approved) or silently inside Tier 1/2's swallowed add_rule ValueError."""
    verdict = (rule.get("then") or {}).get("verdict") or "GENERATED"
    probe = {
        "default_outcome": {"verdict": verdict},
        "composite_class": {verdict: "DECLINE_CLASS"},
        "rules": [rule],
    }
    return not lint_ruleset(probe)["passed"]


def _rule_already_present(rule_id: str, ruleset_id: str, store: Any) -> bool:
    try:
        existing = store.get_ruleset(ruleset_id)
    except KeyError:
        return False
    return any(r["id"] == rule_id for r in existing["rules"])


def _route(rule: dict, risk: dict, ruleset_id: str, store: Any, candidate_store: Any, source_text: str, actor: str) -> str:
    level = risk["risk_level"]
    if level == "LOW":
        store.add_rule(ruleset_id, {**rule, "is_shadow": False}, actor=actor)
        return "AUTO_ENFORCED"
    if level == "MEDIUM":
        store.add_rule(ruleset_id, {**rule, "is_shadow": True}, actor=actor)
        return "SHADOW_MODE"
    candidate_store.create(ruleset_id, rule, risk, source_text=source_text)
    return "PENDING_REVIEW"


def maybe_trigger(
    request: dict,
    result: dict,
    tracker: Any,
    store: Any,
    candidate_store: Any,
    settings: Any,
    ruleset_id: str = "de.fraud",
    actor: str = "anomaly-trigger",
) -> Optional[dict]:
    """Record this decision's DECLINE against every tracked dimension; if any breaches
    threshold, generate -> classify -> route a new rule for it. Returns a summary dict
    for logging/tests, or None if nothing fired (including on internal failure)."""
    if result.get("composite_verdict") != "DECLINE":
        return None

    try:
        for label, fact_path, phrase in _TRACKED_DIMENSIONS:
            value = _dotted_get(request, fact_path)
            if value is None:
                continue

            tracker.record_event(label, value, "DECLINE", request.get("request_id"))
            count = tracker.count_recent(label, value, _WINDOW_SECONDS, outcome="DECLINE")
            if count <= _THRESHOLD:
                continue

            instruction = f"Decline orders where {phrase} {value}"

            # Give generation the target ruleset's own verdict vocabulary — without
            # this, "decline" always becomes the literal word "DECLINE", which isn't
            # valid for every ruleset (de.fraud's decline-class verdict is "BLOCK").
            # A KeyError here (unknown ruleset_id) just means no context is available;
            # generation still runs with its old ruleset-agnostic defaults rather than
            # aborting the whole breach.
            ruleset_context: Optional[dict] = None
            try:
                target = store.get_ruleset(ruleset_id)
                ruleset_context = {
                    "composite_class": target.get("composite_class", {}),
                    "verdict_severity": target.get("verdict_severity", []),
                }
            except KeyError:
                pass

            generated = generate_rule(instruction, settings, ruleset_context)
            if not generated["valid"]:
                logger.warning(
                    "anomaly.generated_rule_invalid",
                    extra={"event": "anomaly.generated_rule_invalid", "dimension": label, "value": value},
                )
                continue
            rule = generated["rule"]

            if _condition_leaf_count(rule.get("when")) == 0:
                logger.warning(
                    "anomaly.generated_rule_too_broad",
                    extra={
                        "event": "anomaly.generated_rule_too_broad",
                        "dimension": label,
                        "value": value,
                        "mode": generated["mode"],
                        "instruction": instruction,
                    },
                )
                continue

            if _references_unknown_facts(rule):
                logger.warning(
                    "anomaly.generated_rule_unknown_facts",
                    extra={
                        "event": "anomaly.generated_rule_unknown_facts",
                        "dimension": label,
                        "value": value,
                        "mode": generated["mode"],
                        "instruction": instruction,
                    },
                )
                continue

            # Dedup: the same breach fires on every DECLINE past threshold, not just the
            # first. A rule already live/shadow, or already sitting PENDING_REVIEW, must
            # not be re-added (churns ruleset versions) or re-queued (floods the queue)
            # on every subsequent decision until an admin acts or the spike passes.
            if _rule_already_present(rule["id"], ruleset_id, store) or candidate_store.exists_pending(ruleset_id, rule["id"]):
                continue

            risk = assess_risk(rule, settings)
            action = _route(rule, risk, ruleset_id, store, candidate_store, instruction, actor)
            summary = {
                "dimension": label,
                "value": value,
                "count": count,
                "risk_level": risk["risk_level"],
                "action": action,
                "rule_id": rule["id"],
            }
            logger.info("anomaly.triggered", extra={"event": "anomaly.triggered", **summary})
            return summary
    except Exception as exc:
        logger.warning("anomaly.trigger_failed", extra={"event": "anomaly.trigger_failed", "error": str(exc)})
    return None
