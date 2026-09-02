"""LLM & RAG layer. Every test here runs on the deterministic offline fallbacks — no API
key, no network, no ChromaDB — which is exactly the mode a live demo runs in. The
LangChain paths are covered by a mock so we prove the fallback wiring too.
"""
import pytest

from app.config import Settings
from app.core.conditions import compile_condition
from app.llm.explainer import build_template_explanation, explain_decision
from app.llm.risk_analyzer import assess_risk
from app.llm.rule_generator import generate_rule
from app.orchestrator import evaluate_decision
from app.providers import ProviderState
from tests.conftest import BASE_REQUEST, admin_headers, make_test_store


@pytest.fixture(scope="module")
def offline_settings() -> Settings:
    # No key -> llm_enabled is False -> deterministic paths only.
    return Settings(openai_api_key="", enable_llm_explanations=True)


# --- Explainer ---------------------------------------------------------------

def _approve_response() -> dict:
    return {
        "composite_verdict": "APPROVE",
        "terms": {"max_financed_amount": 899.0, "max_term_months": 24},
        "signal_confidence": 1.0,
        "signal_health": {"bureau": "OK", "fraud": "OK", "identity": "OK"},
        "decisions": [
            {"type": "DEVICE_FINANCING", "verdict": "APPROVE", "reason_codes": ["BASE_SCORE"]},
            {"type": "FRAUD", "verdict": "CLEAR", "reason_codes": []},
        ],
    }


def _refer_degraded_response() -> dict:
    return {
        "composite_verdict": "REFER",
        "terms": None,
        "signal_confidence": 0.65,
        "signal_health": {"bureau": "DEGRADED", "fraud": "DEGRADED", "identity": "OK"},
        "decisions": [
            {"type": "FRAUD", "verdict": "REVIEW", "reason_codes": ["FRAUD_PROVIDER_UNAVAILABLE"]},
        ],
    }


def test_template_explanation_leads_with_the_verdict():
    text = build_template_explanation(_approve_response())
    assert text.startswith("The order was approved.")
    assert "max financed amount = 899.0" in text


def test_template_explanation_omits_clean_passes():
    # A CLEAR fraud check and an APPROVE financing check should not be narrated.
    text = build_template_explanation(_approve_response())
    assert "Fraud" not in text and "Financing" not in text


def test_template_explanation_surfaces_degraded_signals_and_reason():
    text = build_template_explanation(_refer_degraded_response())
    assert "referred for manual review" in text
    assert "bureau" in text and "fraud" in text
    assert "0.65" in text
    assert "fraud provider was unavailable" in text


def test_explain_decision_returns_template_mode_without_a_key(offline_settings):
    out = explain_decision(_approve_response(), offline_settings)
    assert out["mode"] == "template"
    assert out["text"]


def test_explain_decision_never_raises_on_garbage_input(offline_settings):
    out = explain_decision({}, offline_settings)
    assert out["mode"] == "template"
    assert isinstance(out["text"], str)


def test_explain_decision_uses_llm_when_available_then_reports_mode(monkeypatch):
    settings = Settings(openai_api_key="sk-test", enable_llm_explanations=True)
    monkeypatch.setattr(
        "app.llm.explainer._explain_with_llm",
        lambda response, s: "A concise LLM paragraph.",
    )
    out = explain_decision(_approve_response(), settings)
    assert out["mode"] == "llm"
    assert out["text"] == "A concise LLM paragraph."


def test_explain_decision_falls_back_when_llm_raises(monkeypatch):
    settings = Settings(openai_api_key="sk-test", enable_llm_explanations=True)

    def _boom(response, s):
        raise RuntimeError("network down")

    monkeypatch.setattr("app.llm.explainer._explain_with_llm", _boom)
    out = explain_decision(_approve_response(), settings)
    assert out["mode"] == "template"
    assert out["text"].startswith("The order was approved.")


# --- Rule generator ----------------------------------------------------------

def test_generate_the_canonical_deposit_rule(offline_settings):
    result = generate_rule(
        "Require a 50% deposit if the financed amount exceeds 2000 for new customers",
        offline_settings,
    )
    rule = result["rule"]
    assert result["valid"] is True
    assert result["mode"] == "heuristic"
    assert rule["phase"] == "OVERLAY"
    assert rule["then"]["terms"]["deposit_pct"] == 50
    conditions = rule["when"]["all"]
    assert {"fact": "order.financed_amount", "op": "GREATER_THAN", "value": 2000} in conditions
    assert {"fact": "customer.is_existing", "op": "IS_FALSE"} in conditions


def test_generate_hard_stop_becomes_a_gate(offline_settings):
    result = generate_rule("Decline the order if days past due is greater than 60", offline_settings)
    rule = result["rule"]
    assert rule["phase"] == "GATE"
    assert rule["then"]["verdict"] == "DECLINE"
    assert rule["then"].get("terminal") is True
    leaf = rule["when"] if "fact" in rule["when"] else rule["when"]["all"][0]
    assert leaf["fact"] == "account.days_past_due"
    assert leaf["op"] == "GREATER_THAN"
    assert leaf["value"] == 60


def test_generate_scoring_delta(offline_settings):
    result = generate_rule("Add 15 points to the score when tenure is at least 24 months", offline_settings)
    rule = result["rule"]
    assert rule["phase"] == "SCORING"
    assert rule["then"]["score_delta"] == 15
    assert rule["when"]["fact"] == "customer.tenure_months"
    assert rule["when"]["op"] == "GTE"


def test_generate_approve_terms(offline_settings):
    result = generate_rule("Approve when the risk score is at least 70", offline_settings)
    rule = result["rule"]
    assert rule["phase"] == "TERMS"
    assert rule["then"]["verdict"] == "APPROVE"
    assert rule["when"]["fact"] == "computed.risk_score"


def test_generated_rule_is_always_engine_compilable(offline_settings):
    for text in [
        "Require a 30% deposit if financed amount exceeds 1500",
        "Decline if bureau score is below 500",
        "Refer to manual review when velocity exceeds 3",
        "totally unparseable gibberish here",
    ]:
        result = generate_rule(text, offline_settings)
        # The production compiler must accept every generated condition tree.
        compile_condition(result["rule"]["when"])
        assert result["valid"] is True


def test_generate_flags_missing_conditions_with_a_warning(offline_settings):
    result = generate_rule("just approve everything", offline_settings)
    assert result["rule"]["when"] == {"all": []}
    assert any("no conditions" in w for w in result["warnings"])


# --- Rule generator: ruleset-aware verdict vocabulary -------------------------
# de.fraud's real vocabulary (verdict_severity + composite_class from
# rulesets/de.fraud.json) — its decline-class verdict is "BLOCK", not the generic
# "DECLINE" the heuristic/LLM defaults would otherwise always emit.
FRAUD_CONTEXT = {
    "composite_class": {"CLEAR": "APPROVE_CLASS", "REVIEW": "REFER_CLASS", "BLOCK": "DECLINE_CLASS"},
    "verdict_severity": ["CLEAR", "REVIEW", "BLOCK"],
}


def test_generate_hard_stop_uses_the_target_rulesets_decline_verdict(offline_settings):
    result = generate_rule("Decline the order if days past due is greater than 60", offline_settings, FRAUD_CONTEXT)
    assert result["rule"]["then"]["verdict"] == "BLOCK"
    assert result["valid"] is True


def test_generate_refer_uses_the_target_rulesets_refer_verdict(offline_settings):
    result = generate_rule("Refer to manual review when velocity exceeds 3", offline_settings, FRAUD_CONTEXT)
    assert result["rule"]["then"]["verdict"] == "REVIEW"
    assert result["valid"] is True


def test_generate_approve_uses_the_target_rulesets_approve_verdict(offline_settings):
    result = generate_rule("Approve when the risk score is at least 70", offline_settings, FRAUD_CONTEXT)
    assert result["rule"]["then"]["verdict"] == "CLEAR"
    assert result["valid"] is True


def test_generate_without_ruleset_context_keeps_the_old_generic_defaults(offline_settings):
    """Backward compatible: every existing caller that doesn't pass ruleset_context
    (e.g. the pinned tests above this section) must see unchanged behavior."""
    result = generate_rule("Decline the order if days past due is greater than 60", offline_settings)
    assert result["rule"]["then"]["verdict"] == "DECLINE"


def test_generate_picks_the_least_severe_verdict_within_the_approve_class():
    """device-financing's APPROVE_CLASS has two candidates (APPROVE and
    APPROVE_WITH_DEPOSIT) — a plain "approve" statement must resolve to the plain one,
    not the conditional one, even though composite_class alone can't tell them apart."""
    financing_context = {
        "composite_class": {
            "APPROVE": "APPROVE_CLASS", "APPROVE_WITH_DEPOSIT": "APPROVE_CLASS",
            "REFER": "REFER_CLASS", "DECLINE": "DECLINE_CLASS",
        },
        "verdict_severity": ["APPROVE", "APPROVE_WITH_DEPOSIT", "REFER", "DECLINE"],
    }
    settings = Settings(openai_api_key="")
    result = generate_rule("Approve when the risk score is at least 70", settings, financing_context)
    assert result["rule"]["then"]["verdict"] == "APPROVE"


def test_generate_falls_back_to_heuristic_when_llm_invents_a_verdict_outside_the_vocabulary(monkeypatch):
    """The LLM path is given the ruleset's vocabulary in its prompt, but nothing stops
    it ignoring instructions — if it emits a verdict outside that vocabulary anyway,
    _validate must reject the candidate so generate_rule falls back to the heuristic,
    which (also given ruleset_context) is guaranteed to pick a valid one."""
    settings = Settings(openai_api_key="sk-test", enable_llm_explanations=True)
    monkeypatch.setattr(
        "app.llm.rule_generator._generate_with_llm",
        lambda text, s, ruleset_context=None: {
            "rule": {
                "id": "X", "phase": "GATE", "priority": 50, "enabled": True,
                "when": {"fact": "order.financed_amount", "op": "GREATER_THAN", "value": 100},
                "then": {"verdict": "DECLINE", "terminal": True},  # not in FRAUD_CONTEXT's vocabulary
            },
            "mode": "llm", "confidence": 0.95, "warnings": [],
        },
    )
    result = generate_rule("Decline orders where the financed amount exceeds 100", settings, FRAUD_CONTEXT)
    assert result["mode"] == "heuristic"  # the LLM's invalid-vocabulary candidate was rejected
    assert result["rule"]["then"]["verdict"] == "BLOCK"
    assert result["valid"] is True


def test_verdict_vocab_string_uses_the_target_rulesets_composite_class():
    """What actually gets interpolated into the LLM prompt's RULESET VERDICTS line —
    tested directly rather than through the LangChain pipe chain, which isn't worth
    mocking just to observe a string that's built before the chain ever runs."""
    from app.llm.rule_generator import _verdict_vocab_string

    assert _verdict_vocab_string(FRAUD_CONTEXT) == '"BLOCK", "CLEAR", "REVIEW"'


def test_verdict_vocab_string_falls_back_to_the_generic_default_with_no_context():
    from app.llm.rule_generator import _verdict_vocab_string

    assert _verdict_vocab_string(None) == '"APPROVE", "APPROVE_WITH_DEPOSIT", "REFER", "DECLINE"'


# --- Risk analyzer ------------------------------------------------------------

def _rule(when: dict, phase: str = "GATE", verdict: str = "DECLINE") -> dict:
    return {
        "id": "GEN-RISK-TEST",
        "phase": phase,
        "priority": 50,
        "enabled": True,
        "when": when,
        "then": {"verdict": verdict, "terminal": True, "reason_codes": ["GENERATED"]},
    }


def test_single_customer_id_condition_is_low_risk(offline_settings):
    rule = _rule({"fact": "customer.customer_id", "op": "EQUALS", "value": "cus_123"})
    result = assess_risk(rule, offline_settings)
    assert result["risk_level"] == "LOW"
    assert result["mode"] == "heuristic"
    assert "customer.customer_id" in result["reasoning"]


def test_single_device_fingerprint_condition_is_low_risk(offline_settings):
    rule = _rule({"fact": "context.device_fingerprint", "op": "EQUALS", "value": "fp_abc"})
    assert assess_risk(rule, offline_settings)["risk_level"] == "LOW"


def test_ip_country_gate_is_high_risk_even_when_combined_with_other_conditions(offline_settings):
    rule = _rule({"all": [
        {"fact": "context.ip_country", "op": "EQUALS", "value": "XX"},
        {"fact": "order.total_amount", "op": "GREATER_THAN", "value": 50},
    ]})
    result = assess_risk(rule, offline_settings)
    assert result["risk_level"] == "HIGH"
    assert "context.ip_country" in result["reasoning"]


def test_unqualified_amount_hard_decline_is_high_risk(offline_settings):
    rule = _rule({"fact": "order.total_amount", "op": "GREATER_THAN", "value": 100}, verdict="DECLINE")
    result = assess_risk(rule, offline_settings)
    assert result["risk_level"] == "HIGH"


def test_tariff_code_condition_is_medium_risk(offline_settings):
    rule = _rule({"fact": "order.tariff_code", "op": "EQUALS", "value": "MAGENTA_M"})
    assert assess_risk(rule, offline_settings)["risk_level"] == "MEDIUM"


def test_narrowed_amount_condition_is_medium_not_high_risk(offline_settings):
    # Same amount fact as the high-risk case, but qualified by a second condition ->
    # a bounded slice, not a population-wide gate.
    rule = _rule({"all": [
        {"fact": "order.total_amount", "op": "GREATER_THAN", "value": 100},
        {"fact": "customer.is_existing", "op": "IS_FALSE"},
    ]})
    assert assess_risk(rule, offline_settings)["risk_level"] == "MEDIUM"


def test_unrecognised_shape_defaults_to_high_risk(offline_settings):
    rule = _rule({"fact": "computed.risk_score", "op": "LESS_THAN", "value": 5}, phase="TERMS", verdict="DECLINE")
    result = assess_risk(rule, offline_settings)
    assert result["risk_level"] == "HIGH"


def test_assess_risk_uses_llm_when_available_then_reports_mode(monkeypatch):
    settings = Settings(openai_api_key="sk-test", enable_llm_explanations=True)
    monkeypatch.setattr(
        "app.llm.risk_analyzer._generate_with_llm",
        lambda rule, s: {"risk_level": "MEDIUM", "reasoning": "LLM says medium."},
    )
    rule = _rule({"fact": "customer.customer_id", "op": "EQUALS", "value": "cus_123"})
    result = assess_risk(rule, settings)
    assert result["mode"] == "llm"
    assert result["risk_level"] == "MEDIUM"


def test_assess_risk_falls_back_when_llm_raises(monkeypatch):
    settings = Settings(openai_api_key="sk-test", enable_llm_explanations=True)

    def _boom(rule, s):
        raise RuntimeError("network down")

    monkeypatch.setattr("app.llm.risk_analyzer._generate_with_llm", _boom)
    rule = _rule({"fact": "customer.customer_id", "op": "EQUALS", "value": "cus_123"})
    result = assess_risk(rule, settings)
    assert result["mode"] == "heuristic"
    assert result["risk_level"] == "LOW"


def test_assess_risk_falls_back_when_llm_returns_an_unrecognised_risk_level(monkeypatch):
    settings = Settings(openai_api_key="sk-test", enable_llm_explanations=True)
    monkeypatch.setattr(
        "app.llm.risk_analyzer._generate_with_llm",
        lambda rule, s: {"risk_level": "SUPER_DANGEROUS", "reasoning": "not a real tier"},
    )
    rule = _rule({"fact": "context.ip_country", "op": "EQUALS", "value": "XX"})
    result = assess_risk(rule, settings)
    assert result["mode"] == "heuristic"
    assert result["risk_level"] == "HIGH"


# --- RuleStore: dual-write (authoritative store + vector index), one call -----

def test_store_seeds_both_the_authoritative_store_and_the_vector_index():
    store = make_test_store()
    stats = store.stats()
    assert stats["backend"] == "memory"
    assert stats["rules"] > 30
    assert "de.device-financing" in store.ruleset_ids()


def test_get_ruleset_assembles_header_config_and_rules():
    store = make_test_store()
    ruleset = store.get_ruleset("de.device-financing")
    assert ruleset["verdict_severity"][0] == "APPROVE"
    assert ruleset["score_fact"] == "computed.risk_score"
    assert any(r["id"] == "OV-410" for r in ruleset["rules"])


def test_search_finds_the_young_adult_cap():
    store = make_test_store()
    results = store.search("cap financing for young customers", k=5)
    assert store.backend == "memory"
    assert "OV-410" in [r["rule_id"] for r in results]  # YOUNG_ADULT_CAP


def test_search_finds_fraud_rules():
    store = make_test_store()
    results = store.search("fraud velocity blocklist device", k=5)
    assert results
    assert all("score" in r for r in results)
    assert any(r["ruleset_id"] == "de.fraud" for r in results)


def test_search_respects_k():
    store = make_test_store()
    assert len(store.search("score", k=3)) <= 3


def test_search_empty_query_returns_nothing():
    store = make_test_store()
    assert store.search("", k=5) == []


def test_search_results_are_ranked_descending():
    store = make_test_store()
    scores = [r["score"] for r in store.search("deposit financing risk", k=6)]
    assert scores == sorted(scores, reverse=True)


def test_add_rule_writes_the_authoritative_store_and_indexes_it_in_one_call():
    """`add_rule` is the one-API-call dual-write: a new (audited) ruleset version in the
    authoritative repository, AND an updated vector-index entry, together."""
    store = make_test_store()
    before_version = store.get_ruleset("de.device-financing")["version"]

    store.add_rule(
        "de.device-financing",
        {
            "id": "GEN-TEST1",
            "phase": "OVERLAY",
            "priority": 460,
            "enabled": True,
            "when": {"fact": "order.financed_amount", "op": "GREATER_THAN", "value": 5000},
            "then": {"verdict": "APPROVE_WITH_DEPOSIT", "terms": {"deposit_pct": 90}, "reason_codes": ["JUMBO_TICKET_DEPOSIT"]},
        },
    )

    # authoritative store: new version, rule present
    after = store.get_ruleset("de.device-financing")
    assert after["version"] == before_version + 1
    assert any(r["id"] == "GEN-TEST1" for r in after["rules"])

    # vector index: searchable immediately
    hits = store.search("jumbo ticket deposit", k=5)
    assert "GEN-TEST1" in [r["rule_id"] for r in hits]


def test_added_gate_rule_goes_live_in_evaluation():
    """A rule added through RuleStore must affect the very next decision — proving the
    authoritative repository (not the static JSON) is the evaluator's source of truth."""
    store = make_test_store()
    request = {**BASE_REQUEST, "request_id": "req_live"}
    signals = {
        "bureau": {"score": 780},
        "account_history": {"days_past_due": 0, "sepa_mandate_verified": True},
        "fraud": {"verdict": "CLEAR", "velocity_orders_40m": 0, "shipping_address_count_40m": 0, "device_fingerprint_reuse_count": 0, "device_on_global_blocklist": False},
        "identity": {"match_score": 0.95, "liveness_check": True, "document_type": "PASSPORT"},
        "device_catalog": {"price": 899.0, "in_stock": True},
    }
    before = evaluate_decision(request, store.active_by_decision_type(), ProviderState(), signals)
    financing_before = next(d for d in before["decisions"] if d["type"] == "DEVICE_FINANCING")
    assert financing_before["verdict"] == "APPROVE"

    store.add_rule(
        "de.device-financing",
        {
            "id": "GEN-GATE1", "phase": "GATE", "priority": 5, "enabled": True,
            "when": {"fact": "order.financed_amount", "op": "GREATER_THAN", "value": 100},
            "then": {"verdict": "DECLINE", "terminal": True, "reason_codes": ["GENERATED_HARD_STOP"]},
        },
    )
    after = evaluate_decision(request, store.active_by_decision_type(), ProviderState(), signals)
    financing_after = next(d for d in after["decisions"] if d["type"] == "DEVICE_FINANCING")
    assert financing_after["verdict"] == "DECLINE"
    assert "GEN-GATE1" in financing_after["matched_rules"]


def test_add_rule_to_unknown_ruleset_raises():
    store = make_test_store()
    with pytest.raises(KeyError):
        store.add_rule("de.does-not-exist", {"id": "X", "phase": "GATE", "when": {"all": []}, "then": {}})


def test_delete_rule_removes_it_from_both_stores():
    store = make_test_store()
    store.add_rule(
        "de.device-financing",
        {"id": "GEN-DEL1", "phase": "OVERLAY", "priority": 470, "enabled": True,
         "when": {"fact": "order.financed_amount", "op": "GREATER_THAN", "value": 9000},
         "then": {"terms": {"deposit_pct": 95}, "reason_codes": ["TEMP"]}},
    )
    assert store.delete_rule("de.device-financing", "GEN-DEL1") is True
    assert not any(r["id"] == "GEN-DEL1" for r in store.get_ruleset("de.device-financing")["rules"])
    assert store.delete_rule("de.device-financing", "GEN-DEL1") is False  # already gone


# --- API integration (auth + full HTTP round trip) ---------------------------

def test_decision_response_carries_an_explanation(app_client):
    body = app_client.post(
        "/v1/decisions", json=BASE_REQUEST, headers={"Idempotency-Key": "llm-1", **admin_headers()}
    ).json()
    assert "explanation" in body
    assert body["explanation"]["mode"] in ("template", "llm")
    assert body["explanation"]["text"]


def test_rules_generate_endpoint_previews_without_storing(app_client):
    resp = app_client.post(
        "/v1/rules/generate",
        json={"text": "Require a 50% deposit if financed amount exceeds 2000 for new customers", "persist": False},
        headers=admin_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["persisted"] is False
    assert body["rule"]["then"]["terms"]["deposit_pct"] == 50


def test_rules_generate_uses_the_target_rulesets_verdict_vocabulary(app_client):
    """The exact bug from the conversation, exercised through the real HTTP API against
    the real seeded de.fraud ruleset: "decline" must resolve to de.fraud's own
    DECLINE_CLASS verdict ("BLOCK"), not the generic "DECLINE" literal — which would
    otherwise fail the ruleset's own composite_class vocabulary if ever persisted."""
    resp = app_client.post(
        "/v1/rules/generate",
        json={"text": "Decline the order if days past due is greater than 60", "ruleset_id": "de.fraud", "persist": False},
        headers=admin_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["rule"]["then"]["verdict"] == "BLOCK"


def test_generated_rule_persists_to_store_and_is_searchable(app_client):
    # Unreachable condition so this never perturbs other decision tests, but it is stored.
    resp = app_client.post(
        "/v1/rules/generate",
        json={
            "text": "Require a 77% deposit if financed amount exceeds 999999 for new customers",
            "ruleset_id": "de.device-financing",
        },
        headers=admin_headers(),
    )
    body = resp.json()
    assert body["persisted"] is True
    assert body["ruleset_id"] == "de.device-financing"
    rule_id = body["rule"]["id"]

    # It is now searchable via the vector index...
    hits = app_client.get(
        "/v1/rules/search", params={"q": "77% deposit requirement", "k": 8}, headers=admin_headers()
    ).json()
    assert rule_id in [r["rule_id"] for r in hits["results"]]

    # ...and listed among the ruleset's active rules (the authoritative store).
    listed = app_client.get("/v1/rules", params={"ruleset_id": "de.device-financing"}).json()
    assert rule_id in [r["id"] for r in listed["rulesets"]["de.device-financing"]]


def test_generate_to_unknown_ruleset_is_rejected(app_client):
    resp = app_client.post(
        "/v1/rules/generate",
        json={"text": "Approve when risk score is at least 70", "ruleset_id": "de.nope"},
        headers=admin_headers(),
    )
    assert resp.status_code == 422


def test_rules_generate_rejects_too_short_text(app_client):
    assert app_client.post("/v1/rules/generate", json={"text": "hi"}, headers=admin_headers()).status_code == 422


def test_rules_search_endpoint(app_client):
    resp = app_client.get(
        "/v1/rules/search", params={"q": "young adult deposit cap", "k": 3}, headers=admin_headers()
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend"] in ("memory", "mongodb")
    assert body["count"] >= 1
    assert body["results"][0]["rule_id"]


def test_rules_search_requires_query(app_client):
    assert app_client.get("/v1/rules/search", headers=admin_headers()).status_code == 422


def test_ready_reports_store_stats(app_client):
    body = app_client.get("/ready").json()
    assert body["status"] == "READY"
    assert body["rules_loaded"] > 30
    assert body["rag_backend"] in ("memory", "mongodb")
