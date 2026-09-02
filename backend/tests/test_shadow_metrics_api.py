"""End-to-end shadow-mode lifecycle (risk_tiered_architecture.md §4 Tier 2): a shadow
rule fires silently, its hit is aggregated over the shadow-metrics API, and publishing
it flips it live so the exact same traffic now gets the real verdict.
"""
from app.main import app as fastapi_app
from tests.conftest import BASE_REQUEST, admin_headers

SHADOW_RULE = {
    "id": "FD-SHADOW-TEST",
    "phase": "GATE",
    "priority": 5,
    "enabled": True,
    "is_shadow": True,
    "when": {"fact": "fraud.velocity_orders_40m", "op": "GTE", "value": 1},
    "then": {"verdict": "BLOCK", "terminal": True, "reason_codes": ["SHADOW_TEST_BLOCK"]},
}

FRAUD_SIGNALS = {
    "verdict": "CLEAR",
    "velocity_orders_40m": 5,
    "shipping_address_count_40m": 0,
    "device_fingerprint_reuse_count": 0,
    "device_on_global_blocklist": False,
}


def _decision(body: dict, decision_type: str) -> dict:
    return next(d for d in body["decisions"] if d["type"] == decision_type)


def test_shadow_rule_fires_silently_then_publish_makes_it_live(app_client):
    fastapi_app.state.store.add_rule("de.fraud", SHADOW_RULE)

    request = {**BASE_REQUEST, "test_signals": {"fraud": FRAUD_SIGNALS}}

    silent = app_client.post(
        "/v1/decisions",
        json=request,
        headers={"Idempotency-Key": "shadow-silent-1", **admin_headers()},
    )
    assert silent.status_code == 200
    body = silent.json()

    # Real fraud ruleset still ran normally underneath the shadow rule: velocity>=3 is a
    # real SCORING hit (FD-110) that pushes fraud_score to REVIEW via FD-220, not BLOCK.
    assert _decision(body, "FRAUD")["verdict"] == "REVIEW"
    assert body["composite_verdict"] == "REFER"

    shadow_hits = [h for h in body["shadow_metrics"] if h["rule_id"] == "FD-SHADOW-TEST"]
    assert shadow_hits == [
        {
            "ruleset_id": "de.fraud",
            "decision_type": "FRAUD",
            "rule_id": "FD-SHADOW-TEST",
            "phase": "GATE",
            "would_have_yielded": "BLOCK",
            "reason_codes": ["SHADOW_TEST_BLOCK"],
        }
    ]

    metrics = app_client.get("/v1/rulesets/de.fraud/shadow-metrics", headers=admin_headers())
    assert metrics.status_code == 200
    rule_metrics = next(r for r in metrics.json()["rules"] if r["rule_id"] == "FD-SHADOW-TEST")
    assert rule_metrics["total_hits"] == 1
    assert rule_metrics["by_verdict"] == {"BLOCK": 1}

    publish = app_client.post("/v1/rulesets/de.fraud/rules/FD-SHADOW-TEST/publish", headers=admin_headers())
    assert publish.status_code == 200
    assert publish.json()["is_shadow"] is False

    live = app_client.post(
        "/v1/decisions",
        json=request,
        headers={"Idempotency-Key": "shadow-silent-2", **admin_headers()},
    )
    assert live.status_code == 200
    live_body = live.json()
    assert _decision(live_body, "FRAUD")["verdict"] == "BLOCK"
    assert live_body["composite_verdict"] == "DECLINE"
    assert live_body["shadow_metrics"] == []  # the rule is real now, no longer a shadow hit


def test_shadow_metrics_404_for_unknown_ruleset(app_client):
    resp = app_client.get("/v1/rulesets/does.not.exist/shadow-metrics", headers=admin_headers())
    assert resp.status_code == 404


def test_publish_404_for_unknown_rule(app_client):
    resp = app_client.post("/v1/rulesets/de.fraud/rules/NOPE/publish", headers=admin_headers())
    assert resp.status_code == 404
