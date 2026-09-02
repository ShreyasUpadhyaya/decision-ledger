"""Auth guard behaviour. These checks are token-only and never touch MongoDB (beyond the
mongomock-backed store every app_client fixture spins up), so they stay hermetic:
signup/login against a live DB are a manual/integration concern, but the JWT + role
enforcement is fully covered here.
"""
from app.auth import create_access_token
from tests.conftest import BASE_REQUEST


def _auth(role: str, username: str = "u") -> dict:
    return {"Authorization": f"Bearer {create_access_token(username, role)}"}


def test_health_and_ready_are_public(app_client):
    assert app_client.get("/health").status_code == 200
    assert app_client.get("/ready").status_code == 200


def test_decision_requires_authentication(app_client):
    resp = app_client.post("/v1/decisions", json=BASE_REQUEST, headers={"Idempotency-Key": "noauth"})
    assert resp.status_code == 401


def test_malformed_bearer_is_rejected(app_client):
    resp = app_client.get("/v1/auth/me", headers={"Authorization": "Token abc"})
    assert resp.status_code == 401


def test_me_returns_the_token_identity(app_client):
    resp = app_client.get("/v1/auth/me", headers=_auth("user", "ada"))
    assert resp.status_code == 200
    assert resp.json() == {"username": "ada", "role": "user"}


def test_any_authenticated_user_can_post_a_decision(app_client):
    resp = app_client.post(
        "/v1/decisions",
        json=BASE_REQUEST,
        headers={"Idempotency-Key": "user-1", **_auth("user")},
    )
    assert resp.status_code == 200


def test_admin_only_route_forbids_plain_user(app_client):
    resp = app_client.get("/v1/rules/search", params={"q": "deposit"}, headers=_auth("user"))
    assert resp.status_code == 403


def test_admin_only_route_allows_admin(app_client):
    resp = app_client.get("/v1/rules/search", params={"q": "deposit"}, headers=_auth("admin"))
    assert resp.status_code == 200


def test_provider_injection_is_admin_only(app_client):
    assert app_client.post("/v1/_test/providers/bureau", json={"mode": "TIMEOUT"}, headers=_auth("user")).status_code == 403
    assert app_client.post("/v1/_test/providers/reset", headers=_auth("admin")).status_code == 200


def test_ruleset_mutation_routes_are_admin_only(app_client):
    body = {
        "decision_type": "T", "market": "DE", "score_fact": None, "verdict_severity": ["OK"],
        "composite_class": {"OK": "APPROVE_CLASS"}, "default_outcome": {"verdict": "OK", "reason_codes": []},
        "rules": [],
    }
    assert app_client.post("/v1/rulesets/de.auth-test/versions", json=body, headers=_auth("user")).status_code == 403
    assert app_client.post("/v1/rulesets/de.auth-test/versions", json=body, headers=_auth("admin")).status_code == 200
