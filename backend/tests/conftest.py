import copy
import os
from pathlib import Path
from unittest.mock import patch

# Keep the whole suite hermetic and fast: no live MongoDB, no OpenAI key, no ChromaDB.
# These must be set before `app.config` (and anything importing it) is first imported.
os.environ.setdefault("VECTOR_BACKEND", "memory")
os.environ.setdefault("RULES_STORE_PATH", "")
os.environ.setdefault("OPENAI_API_KEY", "")
os.environ.setdefault("AUTH_SECRET", "test-secret-do-not-use-in-prod")
os.environ.setdefault("DECISION_LEDGER_ENCRYPTION_KEY", "Zle6mPbk-f2bJdNym2gB1fnW8TcvVU5ZWzzRM_Mc8kc=")

import mongomock
import pytest
from fastapi.testclient import TestClient

from app.auth import create_access_token
from app.main import app
from app.orchestrator import evaluate_decision
from app.providers import ProviderState
from app.rule_store import RuleStore
from app.stores.ruleset_repository import MongoRulesetRepository
from app.stores.vector_index import InMemoryVectorIndex

RULESETS_DIR = Path(__file__).resolve().parent.parent / "rulesets"

BASE_REQUEST = {
    "request_id": "req_XXXX",
    "market": "DE",
    "channel": "ONESHOP_WEB",
    "requested_at": "2026-07-24T09:00:00Z",
    "customer": {
        "customer_id": "cus_XXXX",
        "date_of_birth": "1990-01-01",
        "is_existing": True,
        "tenure_months": 24,
        "segment": "CONSUMER",
    },
    "order": {
        "total_amount": 899.00,
        "financed_amount": 899.00,
        "term_months": 24,
        "device_sku": "SM-A556-128",
        "line_count": 1,
        "tariff_code": "MAGENTA_M",
        "payment_method": "SEPA_DD",
    },
    "context": {
        "ip_country": "DE",
        "shipping_country": "DE",
        "device_fingerprint": "fp_default",
        "session_age_seconds": 300,
    },
}

HEALTHY_SIGNALS = {
    "bureau": {"score": 700},
    "account_history": {"days_past_due": 0, "sepa_mandate_verified": True},
    "fraud": {
        "verdict": "CLEAR",
        "velocity_orders_40m": 0,
        "shipping_address_count_40m": 0,
        "device_fingerprint_reuse_count": 0,
        "device_on_global_blocklist": False,
    },
    "identity": {"match_score": 0.95, "liveness_check": True, "document_type": "PASSPORT"},
    "device_catalog": {"price": 899.00, "in_stock": True},
}


def make_request(**overrides) -> dict:
    request = copy.deepcopy(BASE_REQUEST)
    for dotted_path, value in overrides.items():
        parts = dotted_path.split(".")
        node = request
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    return request


def make_signals(**overrides) -> dict:
    signals = copy.deepcopy(HEALTHY_SIGNALS)
    for dotted_path, value in overrides.items():
        provider, field = dotted_path.split(".", 1)
        signals[provider][field] = value
    return signals


def admin_headers(username: str = "tester") -> dict:
    return {"Authorization": f"Bearer {create_access_token(username, 'admin')}"}


def user_headers(username: str = "u") -> dict:
    return {"Authorization": f"Bearer {create_access_token(username, 'user')}"}


def make_test_store() -> RuleStore:
    """A fresh, fully isolated RuleStore — a mongomock-backed authoritative repository
    plus an in-memory vector index, seeded from rulesets/*.json. Exercises the exact
    dual-write path production uses (RuleStore.create_ruleset / seed_if_empty), just
    against fakes instead of real MongoDB / Atlas."""
    db = mongomock.MongoClient().decision_ledger_test
    store = RuleStore(
        settings=None,  # unused: both dependencies are injected explicitly below
        repository=MongoRulesetRepository(db),
        vector_index=InMemoryVectorIndex(),
    )
    store.seed_if_empty(RULESETS_DIR)
    return store


@pytest.fixture(scope="session", autouse=True)
def mock_user_store_mongo():
    """UserStore builds its own MongoClient and eagerly creates a unique index on
    construction — route it at a mongomock client for the whole session so app startup
    never attempts (and times out waiting on) a real MongoDB connection."""
    fake_client = mongomock.MongoClient()
    with patch("app.user_store.MongoClient", return_value=fake_client):
        yield


@pytest.fixture(scope="session")
def rule_store() -> RuleStore:
    """A session-scoped store for read-only, cross-test-safe fixtures (the domain
    evaluate() fixture below never mutates rules)."""
    return make_test_store()


@pytest.fixture(scope="session")
def rulesets_by_type(rule_store) -> dict:
    return rule_store.active_by_decision_type()


@pytest.fixture(scope="session")
def rulesets(rulesets_by_type) -> dict:
    """Same active rulesets, keyed by ruleset_id instead of decision_type, with
    conditions already compiled — for tests that inject a modified copy of one specific
    ruleset directly into evaluate_ruleset. (RuleStore.get_ruleset() is deliberately
    NOT used here: it returns uncompiled, JSON-safe content for the /v1/rules API, and a
    compiled `_compiled` operator on a condition node isn't JSON-serializable.)"""
    return {rs["ruleset_id"]: rs for rs in rulesets_by_type.values()}


@pytest.fixture()
def provider_state() -> ProviderState:
    return ProviderState()


@pytest.fixture()
def evaluate(rulesets_by_type, provider_state):
    """Evaluate a request with explicit signal overrides, bypassing HTTP entirely."""

    def _evaluate(request: dict, signals: dict) -> dict:
        return evaluate_decision(request, rulesets_by_type, provider_state, signal_overrides=signals)

    return _evaluate


@pytest.fixture()
def app_client():
    """A fresh TestClient per test: its own mongomock-backed authoritative store and
    in-memory vector index (via app.main's real startup path), auth working (UserStore
    is mongomock-routed by the autouse fixture above)."""
    app.state.provider_state.reset()
    app.state.decisions_store.clear()
    app.state.idempotency_index.clear()

    test_db = mongomock.MongoClient().decision_ledger_test
    with patch("app.rule_store.get_database", return_value=test_db), patch(
        "app.main.get_database", return_value=test_db
    ):
        with TestClient(app) as client:
            yield client
