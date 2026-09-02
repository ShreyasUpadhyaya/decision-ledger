"""Composite aggregator (spec §4.3): folds a decision set into one order-level verdict.

    any DECLINE_CLASS         -> order DECLINE
    else any REFER_CLASS      -> order REFER   (+ manual review task)
    else                      -> order APPROVE (+ terms from DEVICE_FINANCING)

Each ruleset declares its own verdict -> universal-bucket mapping via `composite_class`
in its envelope (e.g. `{"DECLINE": "DECLINE_CLASS", ...}`) — this function never
hardcodes a specific verdict string. Adding a new ruleset type with its own vocabulary
(e.g. `INELIGIBLE`) requires zero changes here, only an entry in that ruleset's own
`composite_class` map.
"""
from typing import Optional

# A verdict with no composite_class entry fails toward caution, never toward silent
# approval — this should be unreachable if the linter did its job (§6.7), but a runtime
# fallback still has to pick a direction, and REFER is the safe one.
_UNMAPPED_FALLBACK = "REFER_CLASS"


def aggregate(decision_set: list[dict], rulesets_by_type: dict[str, dict]) -> dict:
    def bucket_of(decision: dict) -> str:
        ruleset = rulesets_by_type.get(decision["type"], {})
        return ruleset.get("composite_class", {}).get(decision["verdict"], _UNMAPPED_FALLBACK)

    buckets = [bucket_of(d) for d in decision_set]

    if "DECLINE_CLASS" in buckets:
        return {"composite_verdict": "DECLINE", "terms": None}
    if "REFER_CLASS" in buckets:
        return {"composite_verdict": "REFER", "terms": None}

    financing: Optional[dict] = next((d for d in decision_set if d["type"] == "DEVICE_FINANCING"), None)
    if financing is not None:
        return {"composite_verdict": financing["verdict"], "terms": financing.get("terms")}
    return {"composite_verdict": "APPROVE", "terms": None}
