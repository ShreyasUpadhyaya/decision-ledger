"""Reproduces verdict-example-scenarios.md §4.1-4.4 exactly."""
from app.core.aggregator import aggregate
from tests.conftest import make_request, make_signals


def _decision(result: dict, decision_type: str) -> dict:
    return next(d for d in result["decisions"] if d["type"] == decision_type)


def test_4_1_everything_clean_composite_matches_financing(evaluate):
    request = make_request(**{"customer.tenure_months": 51})
    signals = make_signals(**{"bureau.score": 780})

    result = evaluate(request, signals)

    assert _decision(result, "IDENTITY")["verdict"] == "PASS"
    assert _decision(result, "FRAUD")["verdict"] == "CLEAR"
    assert _decision(result, "DEVICE_FINANCING")["verdict"] == "APPROVE"
    assert result["composite_verdict"] == "APPROVE"
    assert result["terms"] == {"max_financed_amount": 899.00, "max_term_months": 24}


def test_4_2_one_review_forces_composite_refer(evaluate):
    request = make_request(**{"customer.tenure_months": 51})
    signals = make_signals(
        **{
            "bureau.score": 780,
            "fraud.velocity_orders_40m": 3,
            "fraud.shipping_address_count_40m": 2,
        }
    )

    result = evaluate(request, signals)

    assert _decision(result, "DEVICE_FINANCING")["verdict"] == "APPROVE"
    assert _decision(result, "FRAUD")["verdict"] == "REVIEW"
    assert result["composite_verdict"] == "REFER"


def test_4_3_one_block_forces_composite_decline(evaluate):
    request = make_request(**{"customer.tenure_months": 51})
    signals = make_signals(
        **{
            "bureau.score": 780,
            "fraud.velocity_orders_40m": 3,
            "fraud.shipping_address_count_40m": 2,
            "fraud.device_fingerprint_reuse_count": 5,
        }
    )

    result = evaluate(request, signals)

    assert _decision(result, "DEVICE_FINANCING")["verdict"] == "APPROVE"
    assert _decision(result, "FRAUD")["verdict"] == "BLOCK"
    assert result["composite_verdict"] == "DECLINE"


def test_4_4_overlays_stack_per_field(evaluate):
    """A 19-year-old triggers OV-410 (cap max_financed<=500); DE market always triggers
    OV-420 (cap deposit_pct<=30). Both must apply, each tightening its own field."""
    request = make_request(
        **{
            "customer.is_existing": False,
            "customer.tenure_months": 24,
            "customer.date_of_birth": "2007-03-01",  # age 19 on 2026-07-24
            "order.financed_amount": 1600.00,
        }
    )
    signals = make_signals(**{"bureau.score": 618})

    result = evaluate(request, signals)
    financing = _decision(result, "DEVICE_FINANCING")

    assert financing["score"] == 55
    assert "DF-215" in financing["matched_rules"]  # pre-overlay: deposit_pct 35, max 1200
    assert "DF-220" not in financing["matched_rules"]  # shadowed by DF-215's lower priority number
    assert financing["terms"]["deposit_pct"] == 30  # min(35, 30) via OV-420
    assert financing["terms"]["max_financed_amount"] == 500  # min(1200, 500) via OV-410
    assert set(financing["reason_codes"]) >= {"YOUNG_ADULT_CAP", "DE_DEPOSIT_CAP"}


def test_aggregator_has_zero_hardcoded_awareness_of_any_verdict_string():
    """A brand-new ruleset type can invent its own verdict vocabulary — the aggregator
    must classify it correctly purely from that ruleset's own composite_class map,
    never from a verdict string the aggregator module itself recognizes."""
    rulesets_by_type = {
        "MADE_UP_TYPE": {
            "composite_class": {
                "SUPER_FINE": "APPROVE_CLASS",
                "HOLD_ON": "REFER_CLASS",
                "ABSOLUTELY_NOT": "DECLINE_CLASS",
            }
        }
    }

    clean = [{"type": "MADE_UP_TYPE", "verdict": "SUPER_FINE"}]
    assert aggregate(clean, rulesets_by_type)["composite_verdict"] == "APPROVE"

    referred = [{"type": "MADE_UP_TYPE", "verdict": "HOLD_ON"}]
    assert aggregate(referred, rulesets_by_type)["composite_verdict"] == "REFER"

    declined = [{"type": "MADE_UP_TYPE", "verdict": "ABSOLUTELY_NOT"}]
    assert aggregate(declined, rulesets_by_type)["composite_verdict"] == "DECLINE"


def test_aggregator_fails_toward_refer_for_an_unmapped_verdict():
    """Defensive fallback only — the linter's MISSING_COMPOSITE_CLASS check should make
    this unreachable in practice, but if it ever happens, fail toward caution, never
    toward silent approval."""
    rulesets_by_type = {"MADE_UP_TYPE": {"composite_class": {}}}
    result = aggregate([{"type": "MADE_UP_TYPE", "verdict": "SOMETHING_NO_ONE_MAPPED"}], rulesets_by_type)
    assert result["composite_verdict"] == "REFER"
