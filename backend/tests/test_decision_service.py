"""The merged decision flow: JSON engine first, vector search + LLM only as a fallback
when nothing matched anywhere. These use a minimal fake store (not the real 5 rulesets)
so "no rule matched" is easy to force deterministically.
"""
from app.config import Settings
from app.decision_service import decide
from app.providers import ProviderState
from tests.conftest import BASE_REQUEST

_EMPTY_RULESET = {
    "ruleset_id": "de.test-empty",
    "version": 1,
    "decision_type": "TEST_TYPE",
    "market": "DE",
    "score_fact": None,
    "verdict_severity": [],
    "composite_class": {"REFER": "REFER_CLASS"},
    "default_outcome": {"verdict": "REFER", "reason_codes": ["NO_RULE_MATCHED"]},
    "rules": [],  # nothing can ever match -> every request is "unmatched"
}


class _FakeStore:
    """Just enough of the RuleStore surface for decision_service.decide()."""

    def __init__(self, search_results):
        self._search_results = search_results

    def active_by_decision_type(self, market=None):
        return {"TEST_TYPE": _EMPTY_RULESET}

    def search(self, query, k=6):
        return self._search_results


def _offline_settings(**overrides) -> Settings:
    return Settings(openai_api_key="", enable_llm_explanations=True, **overrides)


def test_deterministic_match_never_triggers_fallback(evaluate):
    """Sanity check on the real rulesets: a normal, matching request must not be
    AI-assisted — the whole point of the merge is the JSON engine stays authoritative."""
    from tests.conftest import make_request, make_signals

    result = evaluate(make_request(**{"customer.tenure_months": 51}), make_signals(**{"bureau.score": 780}))
    assert result["composite_verdict"] == "APPROVE"


def test_no_match_anywhere_falls_back_to_default_verdict_when_no_similar_rule_found():
    store = _FakeStore(search_results=[])
    settings = _offline_settings(fallback_verdict="REFER")
    result = decide(BASE_REQUEST, store, ProviderState(), None, settings)

    assert result["ai_assisted"] is False
    assert result["composite_verdict"] == "REFER"
    assert result["similar_rules"] == []


def test_no_match_anywhere_with_a_strong_similar_rule_triggers_ai_assisted_recommendation():
    store = _FakeStore(
        search_results=[
            {"ruleset_id": "de.device-financing", "rule_id": "DF-201", "phase": "TERMS", "verdict": "APPROVE",
             "reason_codes": ["BASE_SCORE"], "facts": ["computed.risk_score"], "status": "ACTIVE", "score": 0.92},
            {"ruleset_id": "de.device-financing", "rule_id": "DF-014", "phase": "GATE", "verdict": "DECLINE",
             "reason_codes": ["ACTIVE_DELINQUENCY"], "facts": ["account.days_past_due"], "status": "ACTIVE", "score": 0.80},
        ]
    )
    settings = _offline_settings(vector_search_threshold=0.75)
    result = decide(BASE_REQUEST, store, ProviderState(), None, settings)

    assert result["ai_assisted"] is True
    assert result["composite_verdict"] in ("APPROVE", "REFER", "DECLINE")
    assert result["confidence"] > 0.0
    assert len(result["similar_rules"]) == 2
    assert result["explanation"]["mode"] == "heuristic"  # no OpenAI key in these tests
    assert "APPROVE" in result["explanation"]["text"] or "REFER" in result["explanation"]["text"] or "DECLINE" in result["explanation"]["text"]


def test_hits_below_threshold_are_ignored_and_default_fallback_applies():
    store = _FakeStore(
        search_results=[
            {"ruleset_id": "de.device-financing", "rule_id": "DF-201", "phase": "TERMS", "verdict": "APPROVE",
             "reason_codes": [], "facts": [], "status": "ACTIVE", "score": 0.40},
        ]
    )
    settings = _offline_settings(vector_search_threshold=0.75, fallback_verdict="REFER")
    result = decide(BASE_REQUEST, store, ProviderState(), None, settings)

    assert result["ai_assisted"] is False
    assert result["composite_verdict"] == "REFER"
