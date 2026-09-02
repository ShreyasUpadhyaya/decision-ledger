"""Tier 3 lifecycle (risk_tiered_architecture.md §4): a HIGH-risk candidate rule sits
PENDING_REVIEW until an admin approves (spliced into the ruleset, live) or discards it
(never touches evaluation). The anomaly trigger that populates this queue automatically
is covered in test_anomaly.py; here the queue and its two terminal actions are tested
directly against the store, the same way test_async.py exercises AsyncDecisionStore.
"""
from app.main import app as fastapi_app
from tests.conftest import admin_headers

CANDIDATE_RULE = {
    "id": "GEN-CANDIDATE-1",
    "phase": "GATE",
    "priority": 5,
    "enabled": True,
    "when": {"fact": "context.ip_country", "op": "EQUALS", "value": "ZZ"},
    "then": {"verdict": "BLOCK", "terminal": True, "reason_codes": ["GENERATED_ANOMALY_BLOCK"]},
}
RISK_ASSESSMENT = {"risk_level": "HIGH", "reasoning": "blocks an entire ip_country."}


def _make_candidate(ruleset_id: str = "de.fraud") -> dict:
    return fastapi_app.state.candidate_store.create(ruleset_id, CANDIDATE_RULE, RISK_ASSESSMENT, source_text="test")


def test_pending_candidate_is_listed_by_default(app_client):
    candidate = _make_candidate()
    resp = app_client.get("/v1/candidate-rules", headers=admin_headers())
    assert resp.status_code == 200
    ids = [c["candidate_id"] for c in resp.json()["candidates"]]
    assert candidate["candidate_id"] in ids


def test_get_candidate_rule_by_id(app_client):
    candidate = _make_candidate()
    resp = app_client.get(f"/v1/candidate-rules/{candidate['candidate_id']}", headers=admin_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["risk_level"] == "HIGH"
    assert body["status"] == "PENDING_REVIEW"
    assert body["rule"]["id"] == "GEN-CANDIDATE-1"


def test_get_unknown_candidate_404s(app_client):
    assert app_client.get("/v1/candidate-rules/cand_nope", headers=admin_headers()).status_code == 404


def test_approve_splices_the_rule_live_and_marks_approved(app_client):
    candidate = _make_candidate("de.fraud")

    resp = app_client.post(f"/v1/candidate-rules/{candidate['candidate_id']}/approve", headers=admin_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "APPROVED"
    assert body["ruleset_id"] == "de.fraud"

    listed = app_client.get("/v1/rules", params={"ruleset_id": "de.fraud"}).json()
    rule_ids = [r["id"] for r in listed["rulesets"]["de.fraud"]]
    assert "GEN-CANDIDATE-1" in rule_ids

    fetched = app_client.get(f"/v1/candidate-rules/{candidate['candidate_id']}", headers=admin_headers()).json()
    assert fetched["status"] == "APPROVED"

    # approving twice is rejected, not silently re-applied
    again = app_client.post(f"/v1/candidate-rules/{candidate['candidate_id']}/approve", headers=admin_headers())
    assert again.status_code == 409


def test_approved_candidate_is_never_left_in_shadow_mode(app_client):
    """Tier 3 approval means "enforce this now" — even if a candidate somehow carried
    is_shadow=True, approval must force it live, not quietly leave it muted."""
    shadow_leaning_rule = {**CANDIDATE_RULE, "is_shadow": True}
    candidate = fastapi_app.state.candidate_store.create("de.fraud", shadow_leaning_rule, RISK_ASSESSMENT)

    app_client.post(f"/v1/candidate-rules/{candidate['candidate_id']}/approve", headers=admin_headers())

    active = fastapi_app.state.store.get_ruleset("de.fraud")
    published = next(r for r in active["rules"] if r["id"] == "GEN-CANDIDATE-1")
    assert published.get("is_shadow", False) is False


def test_discard_marks_discarded_and_never_touches_the_ruleset(app_client):
    candidate = _make_candidate("de.fraud")

    resp = app_client.post(f"/v1/candidate-rules/{candidate['candidate_id']}/discard", headers=admin_headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "DISCARDED"

    listed = app_client.get("/v1/rules", params={"ruleset_id": "de.fraud"}).json()
    rule_ids = [r["id"] for r in listed["rulesets"]["de.fraud"]]
    assert "GEN-CANDIDATE-1" not in rule_ids

    # discarding twice is rejected
    again = app_client.post(f"/v1/candidate-rules/{candidate['candidate_id']}/discard", headers=admin_headers())
    assert again.status_code == 409


def test_approve_and_discard_404_for_unknown_candidate(app_client):
    assert app_client.post("/v1/candidate-rules/cand_nope/approve", headers=admin_headers()).status_code == 404
    assert app_client.post("/v1/candidate-rules/cand_nope/discard", headers=admin_headers()).status_code == 404
