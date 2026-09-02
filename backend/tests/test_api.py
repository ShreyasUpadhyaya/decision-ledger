"""End-to-end HTTP-level checks: decisions (idempotency, batch isolation, auth) and the
authoritative ruleset-version lifecycle (publish, rollback, deprecate, reactivate).
"""
from tests.conftest import BASE_REQUEST, admin_headers

NEW_RULESET_BODY = {
    "decision_type": "LOYALTY_ELIGIBILITY",
    "market": "DE",
    "score_fact": None,
    "verdict_severity": ["ELIGIBLE", "INELIGIBLE"],
    "composite_class": {"ELIGIBLE": "APPROVE_CLASS", "INELIGIBLE": "REFER_CLASS"},
    "default_outcome": {"verdict": "ELIGIBLE", "reason_codes": []},
    "rules": [
        {
            "id": "LY-100",
            "phase": "TERMS",
            "priority": 100,
            "enabled": True,
            "when": {"fact": "customer.tenure_months", "op": "GTE", "value": 999},
            "then": {"verdict": "INELIGIBLE", "terminal": True, "reason_codes": ["TOO_NEW"]},
        }
    ],
}


def test_post_decision_then_get_it_back(app_client):
    response = app_client.post(
        "/v1/decisions",
        json=BASE_REQUEST,
        headers={"Idempotency-Key": "test-key-1", **admin_headers()},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["replayed"] is False
    decision_id = body["decision_id"]

    fetched = app_client.get(f"/v1/decisions/{decision_id}")
    assert fetched.status_code == 200
    assert fetched.json()["decision_id"] == decision_id


def test_list_decisions_most_recent_first(app_client):
    created_ids = []
    for i in range(3):
        response = app_client.post(
            "/v1/decisions", json=BASE_REQUEST, headers={"Idempotency-Key": f"list-key-{i}", **admin_headers()}
        )
        created_ids.append(response.json()["decision_id"])

    listed_ids = [
        d["decision_id"] for d in app_client.get("/v1/decisions", headers=admin_headers()).json()["decisions"]
    ]
    assert listed_ids == list(reversed(created_ids))


def test_6_1_malformed_request_never_reaches_the_evaluator(app_client):
    bad_request = {**BASE_REQUEST, "customer": {**BASE_REQUEST["customer"]}}
    del bad_request["customer"]["date_of_birth"]  # required, non-nullable fact

    response = app_client.post(
        "/v1/decisions",
        json=bad_request,
        headers={"Idempotency-Key": "test-key-bad", **admin_headers()},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == 422
    assert "date_of_birth" in body["detail"]


def test_6_2_idempotent_retry_returns_stored_decision(app_client):
    key = "retry-key-1"
    first = app_client.post("/v1/decisions", json=BASE_REQUEST, headers={"Idempotency-Key": key, **admin_headers()})
    second = app_client.post("/v1/decisions", json=BASE_REQUEST, headers={"Idempotency-Key": key, **admin_headers()})

    assert first.json()["decision_id"] == second.json()["decision_id"]
    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True


def test_6_3_batch_isolates_one_bad_item_from_the_rest(app_client):
    good = BASE_REQUEST
    bad = {**BASE_REQUEST, "order": {**BASE_REQUEST["order"], "financed_amount": "not-a-number"}}

    response = app_client.post("/v1/decisions/batch", json={"requests": [good, bad, good]})
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 3
    assert "decision_id" in results[0]
    assert "error" in results[1]
    assert "decision_id" in results[2]


def test_failure_injection_endpoint_changes_signal_health(app_client):
    app_client.post("/v1/_test/providers/bureau", json={"mode": "TIMEOUT"}, headers=admin_headers())
    response = app_client.post(
        "/v1/decisions",
        json=BASE_REQUEST,
        headers={"Idempotency-Key": "test-key-injection", **admin_headers()},
    )
    assert response.status_code == 200
    assert response.json()["signal_health"]["bureau"] == "DEGRADED"

    app_client.post("/v1/_test/providers/reset", headers=admin_headers())
    response2 = app_client.post(
        "/v1/decisions",
        json=BASE_REQUEST,
        headers={"Idempotency-Key": "test-key-injection-2", **admin_headers()},
    )
    assert response2.json()["signal_health"]["bureau"] == "OK"


def test_publish_brand_new_decision_type_appears_in_decisions_automatically(app_client):
    created = app_client.post("/v1/rulesets/de.test-loyalty/versions", json=NEW_RULESET_BODY, headers=admin_headers())
    assert created.status_code == 200
    assert created.json()["version"] == 1

    response = app_client.post(
        "/v1/decisions",
        json=BASE_REQUEST,
        headers={"Idempotency-Key": "new-type-key-1", **admin_headers()},
    )
    assert response.status_code == 200
    types = [d["type"] for d in response.json()["decisions"]]
    assert "LOYALTY_ELIGIBILITY" in types
    loyalty = next(d for d in response.json()["decisions"] if d["type"] == "LOYALTY_ELIGIBILITY")
    assert loyalty["verdict"] == "ELIGIBLE"  # BASE_REQUEST's tenure_months (24) is well under 999


def test_lint_failure_returns_422_and_does_not_store(app_client):
    bad_body = {**NEW_RULESET_BODY, "rules": [
        {"id": "LY-BAD", "phase": "TERMS", "priority": 100,
         "when": {"fact": "nonsense.made_up", "op": "EQUALS", "value": 1},
         "then": {"verdict": "INELIGIBLE", "terminal": True}},
    ]}
    response = app_client.post("/v1/rulesets/de.test-bad/versions", json=bad_body, headers=admin_headers())
    assert response.status_code == 422
    assert response.json()["detail"]["type"] == "https://verdict.example/errors/ruleset-lint-failed"

    not_found = app_client.get("/v1/rulesets/de.test-bad/versions", headers=admin_headers())
    assert not_found.status_code == 404


def test_publish_update_creates_version_2_and_rollback_returns_to_v1(app_client):
    app_client.post("/v1/rulesets/de.test-loyalty/versions", json=NEW_RULESET_BODY, headers=admin_headers())
    updated_body = {**NEW_RULESET_BODY, "default_outcome": {"verdict": "INELIGIBLE", "reason_codes": []}}
    v2 = app_client.post("/v1/rulesets/de.test-loyalty/versions", json=updated_body, headers=admin_headers())
    assert v2.json()["version"] == 2

    versions = app_client.get("/v1/rulesets/de.test-loyalty/versions", headers=admin_headers()).json()["versions"]
    assert [v["version"] for v in versions] == [1, 2]

    v1_content = app_client.get("/v1/rulesets/de.test-loyalty/versions/1", headers=admin_headers()).json()
    assert v1_content["default_outcome"]["verdict"] == "ELIGIBLE"  # untouched by v2's publish

    rolled_back = app_client.post("/v1/rulesets/de.test-loyalty/rollback", headers=admin_headers())
    assert rolled_back.json()["version"] == 1


def test_deprecate_then_reactivate_via_api(app_client):
    app_client.post("/v1/rulesets/de.test-loyalty/versions", json=NEW_RULESET_BODY, headers=admin_headers())

    deprecated = app_client.post("/v1/rulesets/de.test-loyalty/deprecate", headers=admin_headers())
    assert deprecated.json()["deprecated_at"] is not None

    listed = app_client.get("/v1/rulesets", headers=admin_headers()).json()["rulesets"]
    entry = next(r for r in listed if r["ruleset_id"] == "de.test-loyalty")
    assert entry["deprecated_at"] is not None

    reactivated = app_client.post("/v1/rulesets/de.test-loyalty/reactivate", headers=admin_headers())
    assert reactivated.json()["deprecated_at"] is None


def test_deprecated_ruleset_causes_next_decision_call_to_422(app_client):
    app_client.post("/v1/rulesets/de.device-financing/deprecate", headers=admin_headers())

    response = app_client.post(
        "/v1/decisions",
        json=BASE_REQUEST,
        headers={"Idempotency-Key": "deprecated-key-1", **admin_headers()},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["type"] == "https://verdict.example/errors/ruleset-deprecated"

    # deprecation never blocks reads/audit
    still_readable = app_client.get("/v1/rulesets/de.device-financing/versions", headers=admin_headers())
    assert still_readable.status_code == 200


def test_ruleset_publish_indexes_every_rule_into_the_vector_store_too(app_client):
    """Publishing a whole new ruleset version is also a dual-write: every rule in it
    must be searchable immediately, not just stored in the authoritative repository."""
    app_client.post("/v1/rulesets/de.test-loyalty/versions", json=NEW_RULESET_BODY, headers=admin_headers())
    hits = app_client.get(
        "/v1/rules/search", params={"q": "loyalty eligibility too new"}, headers=admin_headers()
    ).json()
    assert any(r["rule_id"] == "LY-100" for r in hits["results"])
