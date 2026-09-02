"""Reproduces verdict-example-scenarios.md §1.1-1.4 and §2.2-2.3 exactly."""
import datetime

import pytest

from app.core.conditions import compile_condition
from app.core.evaluator import evaluate_ruleset
from tests.conftest import make_request, make_signals


def _financing(result: dict) -> dict:
    return next(d for d in result["decisions"] if d["type"] == "DEVICE_FINANCING")


def test_1_1_clean_approve(evaluate):
    request = make_request(**{"customer.tenure_months": 51})
    request["order"]["financed_amount"] = 899.00
    request["order"]["total_amount"] = 899.00
    signals = make_signals(**{"bureau.score": 780})

    result = evaluate(request, signals)
    financing = _financing(result)

    assert financing["score"] == 105  # 45+15+15+15-5+10+10, see §0.2 rule table
    assert financing["verdict"] == "APPROVE"
    assert financing["terms"] == {"max_financed_amount": 899.00, "max_term_months": 24}
    assert result["composite_verdict"] == "APPROVE"


def test_1_2_approve_with_deposit(evaluate):
    request = make_request(
        **{
            "customer.is_existing": False,
            "customer.tenure_months": 0,
            "order.financed_amount": 1099.00,
            "order.total_amount": 1099.00,
        }
    )
    signals = make_signals(**{"bureau.score": 618, "account_history.days_past_due": None})

    result = evaluate(request, signals)
    financing = _financing(result)

    assert financing["score"] == 40  # 45-15-5-5+10+10, see §0.2 rule table
    assert financing["verdict"] == "APPROVE_WITH_DEPOSIT"
    assert financing["terms"]["deposit_pct"] == 20
    assert financing["terms"]["deposit_amount"] == 219.80  # 20% of 1099.00, precise (not the doc's rounded "~€220")
    assert financing["terms"]["max_financed_amount"] == 900.00
    assert "MEDIUM_RISK_HIGH_TICKET" in financing["reason_codes"]


def test_1_3_decline_via_gate(evaluate):
    request = make_request(**{"order.financed_amount": 650.00})
    signals = make_signals(**{"account_history.days_past_due": 78})

    result = evaluate(request, signals)
    financing = _financing(result)

    assert financing["verdict"] == "DECLINE"
    assert financing["reason_codes"] == ["ACTIVE_DELINQUENCY"]
    assert financing["matched_rules"] == ["DF-014"]

    trace = result["traces"]["DEVICE_FINANCING"]
    skipped = [e for e in trace if e["outcome"] == "SKIPPED"]
    assert len(skipped) > 0
    assert all(e["skipped_by"] == "DF-014" for e in skipped)


def test_1_4_decline_via_accumulated_score(evaluate):
    request = make_request(
        **{
            "customer.is_existing": False,
            "customer.tenure_months": 0,
            "order.financed_amount": 1099.00,
        }
    )
    signals = make_signals(**{"bureau.score": 480, "account_history.sepa_mandate_verified": False})

    result = evaluate(request, signals)
    financing = _financing(result)

    assert financing["score"] == 15  # 45-30-5-5+10, see §0.2 rule table
    assert financing["verdict"] == "DECLINE"
    assert "VERY_HIGH_RISK" in financing["reason_codes"]  # plus the scoring rules' own codes
    assert financing["matched_rules"][-1] == "DF-240"
    # this is a terms-phase decline, not a gate — no rule was skipped
    trace = result["traces"]["DEVICE_FINANCING"]
    assert not any(e["outcome"] == "SKIPPED" for e in trace)


@pytest.mark.parametrize(
    "score,expected_verdict",
    [
        (19, "DECLINE"),
        (20, "REFER"),
        (39, "REFER"),
        (40, "APPROVE_WITH_DEPOSIT"),
        (59, "APPROVE_WITH_DEPOSIT"),
        (60, "REFER"),  # the deliberate 60-69 coverage gap, see §0.2's note
        (69, "REFER"),
        (70, "APPROVE"),
    ],
)
def test_2_2_between_and_gte_boundaries_are_inclusive(rulesets, score, expected_verdict):
    real = rulesets["de.device-financing"]
    injected = {
        **real,
        "rules": [
            {
                "id": "SCORE-INJECT",
                "phase": "SCORING",
                "priority": 1,
                "enabled": True,
                "when": compile_condition({"all": []}),
                "then": {"score_delta": score},
            }
        ]
        + [r for r in real["rules"] if r["phase"] == "TERMS"],
    }
    context = {"order": {"financed_amount": 700}, "computed": {}}
    decision, _, _ = evaluate_ruleset(context, injected, datetime.datetime.fromisoformat("2026-07-24T09:00:00+00:00"))
    assert decision["verdict"] == expected_verdict


def test_2_3_terms_tie_break_by_priority(evaluate):
    """DF-215's condition set is a strict subset of DF-220's; DF-215 must win because
    215 < 220, and the trace must record DF-220 as NOT_EVALUATED, not silently absent."""
    request = make_request(
        **{
            "customer.is_existing": False,
            "customer.tenure_months": 24,
            "order.financed_amount": 1600.00,
        }
    )
    signals = make_signals(**{"bureau.score": 618})

    result = evaluate(request, signals)
    financing = _financing(result)

    assert financing["score"] == 55  # 45+15-15-10+10+10, lands in the DF-215/DF-220 overlap
    assert financing["verdict"] == "APPROVE_WITH_DEPOSIT"
    assert "DF-215" in financing["matched_rules"]
    assert "DF-220" not in financing["matched_rules"]

    trace = result["traces"]["DEVICE_FINANCING"]
    df220_entry = next(e for e in trace if e["rule_id"] == "DF-220")
    assert df220_entry["outcome"] == "NOT_EVALUATED"
