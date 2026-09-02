"""Reproduces verdict-example-scenarios.md §1.5-1.7 exactly."""
from tests.conftest import make_request, make_signals


def _identity(result: dict) -> dict:
    return next(d for d in result["decisions"] if d["type"] == "IDENTITY")


def test_1_5_pass(evaluate):
    result = evaluate(make_request(), make_signals())
    identity = _identity(result)
    assert identity["score"] == 0
    assert identity["verdict"] == "PASS"


def test_1_6_step_up_kyc(evaluate):
    signals = make_signals(**{"identity.match_score": 0.55})
    result = evaluate(make_request(), signals)
    identity = _identity(result)

    assert identity["score"] == 40
    assert identity["verdict"] == "STEP_UP_KYC"
    assert identity["terms"] == {"required_document": "SELFIE_WITH_ID"}
    assert set(identity["reason_codes"]) == {"LOW_FACE_MATCH", "IDENTITY_MEDIUM_RISK"}


def test_1_7_fail_via_accumulated_risk(evaluate):
    signals = make_signals(**{"identity.match_score": 0.30, "identity.liveness_check": False})
    result = evaluate(make_request(), signals)
    identity = _identity(result)

    assert identity["score"] == 70  # 40+30
    assert identity["verdict"] == "FAIL"
    assert set(identity["reason_codes"]) == {"LOW_FACE_MATCH", "LIVENESS_FAILED", "IDENTITY_HIGH_RISK"}
    assert result["composite_verdict"] == "DECLINE"


def test_gate_fail_missing_document_on_financed_order(evaluate):
    signals = make_signals(**{"identity.document_type": None})
    result = evaluate(make_request(), signals)
    identity = _identity(result)

    assert identity["verdict"] == "FAIL"
    assert identity["reason_codes"] == ["NO_ID_ON_FINANCED_ORDER"]
    assert identity["matched_rules"] == ["ID-010"]
