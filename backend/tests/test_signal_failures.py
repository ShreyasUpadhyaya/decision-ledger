"""Reproduces verdict-example-scenarios.md §3.1, §3.3 and §3.4 exactly. §3.2 (fraud's
forced-verdict fallback) lives in test_fraud.py, next to the ruleset it concerns."""
from tests.conftest import make_request, make_signals


def _financing(result: dict) -> dict:
    return next(d for d in result["decisions"] if d["type"] == "DEVICE_FINANCING")


def test_3_1_single_bureau_timeout_stays_below_overlay_threshold(evaluate, provider_state):
    provider_state.set_mode("bureau", "TIMEOUT")
    request = make_request(**{"customer.tenure_months": 51})
    signals = make_signals()
    del signals["bureau"]  # let the injected TIMEOUT mode govern bureau instead

    result = evaluate(request, signals)

    assert result["signal_health"]["bureau"] == "DEGRADED"
    assert result["signal_confidence"] == 0.65
    # bureau facts go INDETERMINATE; base+tenure+clean+ticket+sepa+identity still clears 70
    financing = _financing(result)
    assert financing["score"] == 90  # 45+15+15-5+10+10
    assert financing["verdict"] == "APPROVE"  # 0.65 is not below the 0.6 overlay threshold


def test_3_1_double_degradation_crosses_overlay_threshold(evaluate, provider_state):
    """Degrading account_history doesn't just cost confidence points — it also knocks
    out DF-105 and DF-130 (both read account.* facts), since those facts go
    INDETERMINATE too. For this ruleset's specific weights, losing both providers always
    drops the score into the 60-69 coverage gap on its own, so the terms phase already
    lands on REFER before the confidence overlay gets a chance to matter. Both routes to
    REFER are real; this test asserts what actually fires, not what would be needed to
    reach REFER purely through the overlay in isolation."""
    provider_state.set_mode("bureau", "TIMEOUT")
    provider_state.set_mode("account_history", "TIMEOUT")
    request = make_request(**{"customer.tenure_months": 51})
    signals = make_signals()
    del signals["bureau"]
    del signals["account_history"]

    result = evaluate(request, signals)

    assert result["signal_confidence"] == 0.40  # 1.0 - 0.35 - 0.25
    financing = _financing(result)
    assert financing["score"] == 65  # 45+15-5+10 — DF-105/DF-130 lost along with DF-107/108/110
    assert financing["verdict"] == "REFER"
    assert "NO_RULE_MATCHED" in financing["reason_codes"]  # the 60-69 gap, not the overlay
    assert result["composite_verdict"] == "REFER"


def test_3_3_device_catalog_stale_costs_nothing(evaluate, provider_state):
    provider_state.set_mode("device_catalog", "ERROR")
    signals = make_signals()
    del signals["device_catalog"]

    result = evaluate(make_request(**{"customer.tenure_months": 51}), signals)

    assert result["signal_health"]["device_catalog"] == "STALE"
    assert result["signal_confidence"] == 1.0
    assert _financing(result)["verdict"] == "APPROVE"


def test_3_4_total_provider_outage_routes_to_refer_not_5xx(evaluate, provider_state):
    provider_state.set_mode("bureau", "TIMEOUT")
    provider_state.set_mode("account_history", "TIMEOUT")
    provider_state.set_mode("fraud", "TIMEOUT")
    signals = make_signals()
    del signals["bureau"]
    del signals["account_history"]
    del signals["fraud"]

    result = evaluate(make_request(), signals)  # must not raise

    assert result["signal_health"]["bureau"] == "DEGRADED"
    assert result["signal_health"]["fraud"] == "DEGRADED"
    assert result["signal_health"]["account_history"] == "DEGRADED"
    assert result["composite_verdict"] == "REFER"
