"""Reproduces verdict-example-scenarios.md §1.8-1.10 and §3.2 exactly."""
from tests.conftest import make_request, make_signals


def _fraud(result: dict) -> dict:
    return next(d for d in result["decisions"] if d["type"] == "FRAUD")


def test_1_8_clear(evaluate):
    result = evaluate(make_request(), make_signals())
    fraud = _fraud(result)
    assert fraud["score"] == 0
    assert fraud["verdict"] == "CLEAR"
    assert fraud["reason_codes"] == []


def test_1_9_review(evaluate):
    signals = make_signals(**{"fraud.velocity_orders_40m": 3, "fraud.shipping_address_count_40m": 2})
    result = evaluate(make_request(), signals)
    fraud = _fraud(result)

    assert fraud["score"] == 60  # 40+20
    assert fraud["verdict"] == "REVIEW"
    assert fraud["review_task"] == {"sla_hours": 4}
    assert set(fraud["reason_codes"]) == {"VELOCITY_ANOMALY", "MULTI_ADDRESS_ANOMALY", "FRAUD_SCORE_ELEVATED"}
    assert result["composite_verdict"] == "REFER"


def test_1_10_block(evaluate):
    signals = make_signals(
        **{
            "fraud.velocity_orders_40m": 3,
            "fraud.shipping_address_count_40m": 2,
            "fraud.device_fingerprint_reuse_count": 5,
        }
    )
    result = evaluate(make_request(), signals)
    fraud = _fraud(result)

    assert fraud["score"] == 90  # 40+20+30
    assert fraud["verdict"] == "BLOCK"
    assert result["composite_verdict"] == "DECLINE"


def test_3_2_fraud_provider_error_forces_review_never_clear(evaluate, provider_state):
    provider_state.set_mode("fraud", "ERROR")
    signals = make_signals()
    del signals["fraud"]  # let the injected ERROR mode govern the fraud provider

    result = evaluate(make_request(), signals)
    fraud = _fraud(result)

    assert fraud["verdict"] == "REVIEW"
    assert fraud["reason_codes"] == ["FRAUD_PROVIDER_UNAVAILABLE"]
    assert result["signal_health"]["fraud"] == "DEGRADED"
    assert result["composite_verdict"] == "REFER"
