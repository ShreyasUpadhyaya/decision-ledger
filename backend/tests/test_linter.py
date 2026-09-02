"""Tests for the minimal ruleset linter — the safety net for dynamic add/update."""
import json
from pathlib import Path

from app.core.linter import lint_ruleset

RULESETS_DIR = Path(__file__).resolve().parent.parent / "rulesets"


def _valid_ruleset(**overrides) -> dict:
    content = {
        "decision_type": "TEST_TYPE",
        "market": "DE",
        "score_fact": "computed.test_score",
        "composite_class": {"OK": "APPROVE_CLASS", "NOT_OK": "DECLINE_CLASS"},
        "default_outcome": {"verdict": "OK", "reason_codes": []},
        "rules": [
            {
                "id": "T-100",
                "phase": "SCORING",
                "priority": 100,
                "when": {"all": []},
                "then": {"score_delta": 10},
            },
            {
                "id": "T-200",
                "phase": "TERMS",
                "priority": 200,
                "when": {"fact": "computed.test_score", "op": "GTE", "value": 5},
                "then": {"verdict": "NOT_OK", "terminal": True},
            },
        ],
    }
    content.update(overrides)
    return content


def test_all_five_real_rulesets_pass_the_linter():
    for path in sorted(RULESETS_DIR.glob("*.json")):
        content = json.loads(path.read_text(encoding="utf-8"))
        result = lint_ruleset(content)
        assert result["passed"], f"{path.name} failed lint: {result['errors']}"


def test_valid_synthetic_ruleset_passes():
    result = lint_ruleset(_valid_ruleset())
    assert result["passed"]
    assert result["errors"] == []


def test_unknown_fact_is_an_error():
    bad = _valid_ruleset(rules=[
        {"id": "T-1", "phase": "TERMS", "priority": 100,
         "when": {"fact": "nonsense.made_up_field", "op": "EQUALS", "value": 1},
         "then": {"verdict": "OK", "terminal": True}},
    ])
    result = lint_ruleset(bad)
    assert not result["passed"]
    assert any(e["check"] == "UNKNOWN_FACT" for e in result["errors"])


def test_unknown_operator_is_an_error():
    bad = _valid_ruleset(rules=[
        {"id": "T-1", "phase": "TERMS", "priority": 100,
         "when": {"fact": "order.line_count", "op": "FUZZY_MATCHES", "value": 1},
         "then": {"verdict": "OK", "terminal": True}},
    ])
    result = lint_ruleset(bad)
    assert not result["passed"]
    assert any(e["check"] == "UNKNOWN_OPERATOR" for e in result["errors"])


def test_missing_composite_class_entry_is_an_error():
    bad = _valid_ruleset(composite_class={"OK": "APPROVE_CLASS"})  # NOT_OK is missing
    result = lint_ruleset(bad)
    assert not result["passed"]
    assert any(e["check"] == "MISSING_COMPOSITE_CLASS" and e["verdict"] == "NOT_OK" for e in result["errors"])


def test_missing_default_outcome_is_an_error():
    bad = _valid_ruleset(default_outcome={})
    result = lint_ruleset(bad)
    assert not result["passed"]
    assert any(e["check"] == "MISSING_DEFAULT_OUTCOME" for e in result["errors"])


def test_scoring_rules_and_terms_only_overlays_do_not_crash_the_linter():
    """SCORING rules and terms-only overlays have no `then.verdict` at all — the linter
    must not assume every rule produces a verdict."""
    ruleset = _valid_ruleset(rules=[
        {"id": "T-SCORE", "phase": "SCORING", "priority": 100,
         "when": {"all": []}, "then": {"score_delta": 5}},
        {"id": "T-OVERLAY", "phase": "OVERLAY", "priority": 400,
         "when": {"fact": "market", "op": "EQUALS", "value": "DE"},
         "then": {"terms": {"cap": 100}}},
    ])
    result = lint_ruleset(ruleset)  # must not raise
    assert result["passed"]
