"""Static analysis over a candidate ruleset (spec §6.7) — the safety net that lets
rulesets be added/updated dynamically without "dynamic" turning into "unvalidated."

Deliberately minimal for this increment: unknown facts, unknown operators, a missing
default outcome, and verdicts with no composite_class entry (which would otherwise hit
the aggregator's fail-toward-caution fallback silently). Unreachable-rule detection,
overlapping-condition detection, protected-attribute and proxy-risk checks are the
fuller spec §6.7 linter and are not built here.
"""
from .operators import KNOWN_OPERATORS

_KNOWN_FACT_PREFIXES = (
    "customer.", "order.", "context.", "bureau.", "account.", "fraud.", "identity.", "device.",
)
_KNOWN_TOP_LEVEL_FACTS = {"market"}
_ALWAYS_KNOWN_COMPUTED = {"computed.signal_confidence"}


def lint_ruleset(content: dict) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []

    if not content.get("default_outcome", {}).get("verdict"):
        errors.append({"check": "MISSING_DEFAULT_OUTCOME", "message": "ruleset has no default_outcome.verdict"})

    known_computed = set(_ALWAYS_KNOWN_COMPUTED)
    if content.get("score_fact"):
        known_computed.add(content["score_fact"])

    verdicts_seen: set = set()
    default_verdict = content.get("default_outcome", {}).get("verdict")
    if default_verdict:
        verdicts_seen.add(default_verdict)

    for rule in content.get("rules", []):
        # `or {}`, not `.get(key, {})` — a submitted rule can have `"when": null` or
        # `"then": null` explicitly (the key present, value None), which `.get`'s
        # default does not catch, and this must degrade to a lint error, never a crash.
        _walk_condition(rule.get("when") or {}, errors, rule.get("id", "?"), known_computed)
        verdict = (rule.get("then") or {}).get("verdict")
        if verdict:
            verdicts_seen.add(verdict)

    composite_class = content.get("composite_class", {})
    for verdict in sorted(verdicts_seen):
        if verdict not in composite_class:
            errors.append({
                "check": "MISSING_COMPOSITE_CLASS",
                "verdict": verdict,
                "message": f"verdict {verdict!r} has no composite_class entry",
            })

    return {"passed": len(errors) == 0, "errors": errors, "warnings": warnings}


def _walk_condition(node: dict, errors: list[dict], rule_id: str, known_computed: set) -> None:
    if "fact" in node:
        fact = node["fact"]
        op = node.get("op")
        if op not in KNOWN_OPERATORS:
            errors.append({"check": "UNKNOWN_OPERATOR", "rule_id": rule_id, "op": op})
        if not _is_known_fact(fact, known_computed):
            errors.append({"check": "UNKNOWN_FACT", "rule_id": rule_id, "fact": fact})
        return
    for combinator in ("all", "any", "none"):
        if combinator in node:
            for child in node[combinator]:
                _walk_condition(child, errors, rule_id, known_computed)


def _is_known_fact(fact: str, known_computed: set) -> bool:
    if fact in _KNOWN_TOP_LEVEL_FACTS or fact in known_computed:
        return True
    return any(fact.startswith(prefix) for prefix in _KNOWN_FACT_PREFIXES)
