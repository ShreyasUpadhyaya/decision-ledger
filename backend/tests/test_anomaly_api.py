"""HTTP-level proof of the full loop (risk_tiered_architecture.md §2, §5 step 6): a
burst of real DECLINEs from one ip_country through the actual /v1/decisions endpoint
trips the anomaly trigger as a background task, with no test reaching into app.state
directly. Runs under the hermetic test suite's no-API-key settings (conftest.py sets
OPENAI_API_KEY=""), so this also proves the safety guards in app/anomaly.py hold at the
real API layer: the heuristic generator's missing context.* vocabulary must NOT result
in an unconditional auto-block despite a genuine threshold breach.
"""
from tests.conftest import BASE_REQUEST, admin_headers

BURST_SIGNALS = {
    "verdict": "CLEAR",
    "velocity_orders_40m": 0,
    "shipping_address_count_40m": 0,
    "device_fingerprint_reuse_count": 0,
    "device_on_global_blocklist": True,  # real FD-010 GATE -> BLOCK -> composite DECLINE
}


def _decline_request(i: int) -> dict:
    return {
        **BASE_REQUEST,
        "request_id": f"burst-{i}",
        "context": {**BASE_REQUEST["context"], "ip_country": "ZZ"},
        "test_signals": {"fraud": BURST_SIGNALS},
    }


def test_decline_burst_from_one_ip_country_does_not_auto_block_without_llm_vocabulary(app_client):
    """11 real DECLINEs (> the 10-event threshold) from ip_country=ZZ. Under the
    hermetic no-key settings this suite runs with, rule_generator's heuristic path
    can't parse "the ip_country is ZZ" into anything but an unconditional decline gate
    — the anomaly trigger's own guard must refuse that outright rather than let a
    risk-tiering safety mechanism become the thing that blocks every order."""
    for i in range(11):
        resp = app_client.post(
            "/v1/decisions",
            json=_decline_request(i),
            headers={"Idempotency-Key": f"burst-key-{i}", **admin_headers()},
        )
        assert resp.status_code == 200
        assert resp.json()["composite_verdict"] == "DECLINE"

    # Nothing got auto-enforced, shadowed, or even queued for review — the vocabulary
    # gap was refused, not silently escalated.
    candidates = app_client.get("/v1/candidate-rules", headers=admin_headers()).json()
    assert candidates["candidates"] == []

    fraud_rules = app_client.get("/v1/rules", params={"ruleset_id": "de.fraud"}).json()
    rule_ids = [r["id"] for r in fraud_rules["rulesets"]["de.fraud"]]
    assert all(not rid.startswith("GEN-") for rid in rule_ids)

    # And a fresh, unrelated order is completely unaffected.
    clean = app_client.post(
        "/v1/decisions",
        json={**BASE_REQUEST, "request_id": "clean-1"},
        headers={"Idempotency-Key": "clean-key-1", **admin_headers()},
    )
    assert clean.status_code == 200
    assert clean.json()["composite_verdict"] != "DECLINE"


def test_decline_burst_below_threshold_never_fires(app_client):
    for i in range(5):  # well under the 10-event threshold
        app_client.post(
            "/v1/decisions",
            json=_decline_request(i),
            headers={"Idempotency-Key": f"small-burst-{i}", **admin_headers()},
        )

    candidates = app_client.get("/v1/candidate-rules", headers=admin_headers()).json()
    assert candidates["candidates"] == []
