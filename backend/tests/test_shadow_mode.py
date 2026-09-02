"""risk_tiered_architecture.md §4 Tier 2 — a rule with `is_shadow: true` must evaluate
for real (so its trace/shadow_hits reflect actual traffic) but can never reach the real
decision: it can't terminate a GATE, can't move score_total, can't win the TERMS slot,
and can't tighten a live verdict/terms in OVERLAY.
"""
import datetime

from app.core.conditions import compile_condition
from app.core.evaluator import evaluate_ruleset

NOW = datetime.datetime.fromisoformat("2026-07-24T09:00:00+00:00")
_ALWAYS_TRUE = compile_condition({"all": []})


def _ruleset(rules: list, **overrides) -> dict:
    base = {
        "default_outcome": {"verdict": "APPROVE", "reason_codes": []},
        "verdict_severity": ["APPROVE", "REFER", "DECLINE"],
        "score_fact": "computed.risk_score",
        "rules": rules,
    }
    base.update(overrides)
    return base


def test_shadow_gate_never_terminates_falls_through_to_default():
    ruleset = _ruleset([
        {
            "id": "G-SHADOW", "phase": "GATE", "priority": 10, "enabled": True, "is_shadow": True,
            "when": _ALWAYS_TRUE,
            "then": {"verdict": "DECLINE", "terminal": True, "reason_codes": ["SHADOW_DECLINE"]},
        },
    ])
    decision, trace, shadow_hits = evaluate_ruleset({"computed": {}}, ruleset, NOW)

    assert decision["verdict"] == "APPROVE"  # default_outcome, not the shadow DECLINE
    assert decision["matched_rules"] == []
    assert shadow_hits == [
        {"rule_id": "G-SHADOW", "phase": "GATE", "would_have_yielded": "DECLINE", "reason_codes": ["SHADOW_DECLINE"]}
    ]
    gate_entry = next(e for e in trace if e["rule_id"] == "G-SHADOW")
    assert gate_entry["shadow"] is True


def test_shadow_gate_does_not_block_a_later_real_gate():
    ruleset = _ruleset([
        {
            "id": "G-SHADOW", "phase": "GATE", "priority": 10, "enabled": True, "is_shadow": True,
            "when": _ALWAYS_TRUE,
            "then": {"verdict": "DECLINE", "terminal": True, "reason_codes": ["SHADOW_DECLINE"]},
        },
        {
            "id": "G-REAL", "phase": "GATE", "priority": 20, "enabled": True,
            "when": _ALWAYS_TRUE,
            "then": {"verdict": "REFER", "terminal": True, "reason_codes": ["REAL_REFER"]},
        },
    ])
    decision, _, shadow_hits = evaluate_ruleset({"computed": {}}, ruleset, NOW)

    assert decision["verdict"] == "REFER"
    assert decision["matched_rules"] == ["G-REAL"]
    assert [h["rule_id"] for h in shadow_hits] == ["G-SHADOW"]


def test_shadow_scoring_delta_excluded_from_real_score_total():
    ruleset = _ruleset([
        {
            "id": "S-SHADOW", "phase": "SCORING", "priority": 100, "enabled": True, "is_shadow": True,
            "when": _ALWAYS_TRUE,
            "then": {"score_delta": 100, "reason_codes": ["SHADOW_SCORE"]},
        },
        {
            "id": "T-LOW", "phase": "TERMS", "priority": 200, "enabled": True,
            "when": compile_condition({"fact": "computed.risk_score", "op": "LESS_THAN", "value": 10}),
            "then": {"verdict": "APPROVE", "terminal": True},
        },
    ])
    decision, _, shadow_hits = evaluate_ruleset({"computed": {}}, ruleset, NOW)

    assert decision["score"] == 0  # the shadow's +100 never landed in score_total
    assert decision["verdict"] == "APPROVE"  # T-LOW still matched because risk_score stayed 0
    assert decision["matched_rules"] == ["T-LOW"]
    assert shadow_hits == [
        {"rule_id": "S-SHADOW", "phase": "SCORING", "would_have_score_delta": 100, "reason_codes": ["SHADOW_SCORE"]}
    ]


def test_shadow_terms_never_wins_the_slot():
    ruleset = _ruleset([
        {
            "id": "T-SHADOW", "phase": "TERMS", "priority": 100, "enabled": True, "is_shadow": True,
            "when": _ALWAYS_TRUE,
            "then": {"verdict": "DECLINE", "terminal": True, "reason_codes": ["SHADOW_TERMS"]},
        },
        {
            "id": "T-REAL", "phase": "TERMS", "priority": 200, "enabled": True,
            "when": _ALWAYS_TRUE,
            "then": {"verdict": "APPROVE", "terminal": True},
        },
    ])
    decision, _, shadow_hits = evaluate_ruleset({"computed": {}}, ruleset, NOW)

    assert decision["verdict"] == "APPROVE"
    assert decision["matched_rules"] == ["T-REAL"]
    assert [h["rule_id"] for h in shadow_hits] == ["T-SHADOW"]


def test_shadow_overlay_records_counterfactual_without_mutating_real_decision():
    ruleset = _ruleset([
        {
            "id": "T-BASE", "phase": "TERMS", "priority": 100, "enabled": True,
            "when": _ALWAYS_TRUE,
            "then": {"verdict": "APPROVE", "terminal": True, "terms": {"max_financed_amount": 1000}},
        },
        {
            "id": "OV-SHADOW", "phase": "OVERLAY", "priority": 400, "enabled": True, "is_shadow": True,
            "when": _ALWAYS_TRUE,
            "then": {"verdict": "DECLINE", "terms": {"max_financed_amount": 200}, "reason_codes": ["SHADOW_OVERLAY"]},
        },
    ])
    decision, _, shadow_hits = evaluate_ruleset({"computed": {}}, ruleset, NOW)

    assert decision["verdict"] == "APPROVE"  # untouched by the shadow overlay
    assert decision["terms"]["max_financed_amount"] == 1000  # untouched
    assert shadow_hits == [
        {
            "rule_id": "OV-SHADOW",
            "phase": "OVERLAY",
            "would_have_yielded": "DECLINE",
            "would_have_terms": {"max_financed_amount": 200},
            "reason_codes": ["SHADOW_OVERLAY"],
        }
    ]


def test_shadow_overlay_that_would_change_nothing_is_not_recorded_as_a_hit():
    """Mirrors the real OVERLAY branch's own rule: matching isn't enough, it has to
    actually tighten something (the trace still shows the match either way)."""
    ruleset = _ruleset([
        {
            "id": "T-BASE", "phase": "TERMS", "priority": 100, "enabled": True,
            "when": _ALWAYS_TRUE,
            "then": {"verdict": "APPROVE", "terminal": True, "terms": {"max_financed_amount": 1000}},
        },
        {
            "id": "OV-SHADOW-NOOP", "phase": "OVERLAY", "priority": 400, "enabled": True, "is_shadow": True,
            "when": _ALWAYS_TRUE,
            # 2000 is not a tightening cap against an existing 1000 -> no real effect
            "then": {"terms": {"max_financed_amount": 2000}, "reason_codes": ["WOULD_NOT_TIGHTEN"]},
        },
    ])
    decision, trace, shadow_hits = evaluate_ruleset({"computed": {}}, ruleset, NOW)

    assert decision["terms"]["max_financed_amount"] == 1000
    assert shadow_hits == []
    overlay_entry = next(e for e in trace if e["rule_id"] == "OV-SHADOW-NOOP")
    assert overlay_entry["outcome"] == "TRUE"
    assert overlay_entry["shadow"] is True
