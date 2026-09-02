"""Unit tests for the version-history repository behind dynamic ruleset add/update/archive."""
from pathlib import Path

import mongomock
import pytest

from app.seed import seed_from_directory
from app.stores.ruleset_repository import MongoRulesetRepository
from app.stores.vector_index import InMemoryVectorIndex
from app.rule_store import RuleStore

RULESETS_DIR = Path(__file__).resolve().parent.parent / "rulesets"


def _tiny_ruleset(**overrides) -> dict:
    content = {
        "decision_type": "TEST_TYPE",
        "market": "DE",
        "score_fact": None,
        "verdict_severity": ["OK", "NOT_OK"],
        "composite_class": {"OK": "APPROVE_CLASS", "NOT_OK": "DECLINE_CLASS"},
        "default_outcome": {"verdict": "OK", "reason_codes": []},
        "rules": [
            {
                "id": "T-100",
                "phase": "TERMS",
                "priority": 100,
                "enabled": True,
                "when": {"fact": "order.line_count", "op": "GREATER_THAN", "value": 10},
                "then": {"verdict": "NOT_OK", "terminal": True},
            }
        ],
    }
    content.update(overrides)
    return content


@pytest.fixture()
def db():
    client = mongomock.MongoClient()
    return client.decision_ledger_test


def test_create_new_ruleset_starts_at_version_1(db):
    store = MongoRulesetRepository(db)
    stored = store.create_version("de.test-1", _tiny_ruleset())
    assert stored["version"] == 1
    assert stored["status"] == "PUBLISHED"
    assert store.get_active("de.test-1")["version"] == 1


def test_publishing_again_creates_version_2_and_it_becomes_active(db):
    store = MongoRulesetRepository(db)
    store.create_version("de.test-1", _tiny_ruleset())
    v2 = store.create_version("de.test-1", _tiny_ruleset())
    assert v2["version"] == 2
    assert store.get_active("de.test-1")["version"] == 2


def test_get_version_returns_exact_old_content_after_a_new_version_is_published(db):
    store = MongoRulesetRepository(db)
    store.create_version("de.test-1", _tiny_ruleset())
    store.create_version("de.test-1", _tiny_ruleset(verdict_severity=["OK", "NOT_OK", "EXTRA"]))
    v1 = store.get_version("de.test-1", 1)
    assert v1["verdict_severity"] == ["OK", "NOT_OK"]  # unaffected by v2's publish


def test_content_hash_stable_across_identical_republish_and_changes_with_content(db):
    store = MongoRulesetRepository(db)
    v1 = store.create_version("de.test-1", _tiny_ruleset())
    v2 = store.create_version("de.test-1", _tiny_ruleset())  # textually identical
    assert v1["content_hash"] == v2["content_hash"]

    v3 = store.create_version("de.test-1", _tiny_ruleset(verdict_severity=["OK", "NOT_OK", "EXTRA"]))
    assert v3["content_hash"] != v2["content_hash"]


def test_rollback_reactivates_previous_version(db):
    store = MongoRulesetRepository(db)
    store.create_version("de.test-1", _tiny_ruleset())
    store.create_version("de.test-1", _tiny_ruleset(verdict_severity=["OK", "NOT_OK", "V2"]))
    assert store.get_active("de.test-1")["version"] == 2

    rolled_back = store.rollback("de.test-1")
    assert rolled_back["version"] == 1
    assert store.get_active("de.test-1")["version"] == 1


def test_rollback_with_no_earlier_version_raises(db):
    store = MongoRulesetRepository(db)
    store.create_version("de.test-1", _tiny_ruleset())
    with pytest.raises(ValueError):
        store.rollback("de.test-1")


def test_deprecate_and_reactivate(db):
    store = MongoRulesetRepository(db)
    store.create_version("de.test-1", _tiny_ruleset())
    assert not store.is_deprecated("de.test-1")

    store.deprecate("de.test-1")
    assert store.is_deprecated("de.test-1")
    assert store.deprecated_at("de.test-1") is not None

    store.reactivate("de.test-1")
    assert not store.is_deprecated("de.test-1")


def test_active_by_decision_type_excludes_deprecated(db):
    store = MongoRulesetRepository(db)
    store.create_version("de.test-1", _tiny_ruleset())
    assert "TEST_TYPE" in store.active_by_decision_type()

    store.deprecate("de.test-1")
    assert "TEST_TYPE" not in store.active_by_decision_type()


def test_deprecated_ruleset_ids_reports_it_separately_from_active_lookup(db):
    store = MongoRulesetRepository(db)
    store.create_version("de.test-1", _tiny_ruleset())
    store.deprecate("de.test-1")
    assert store.deprecated_ruleset_ids() == ["de.test-1"]
    assert store.deprecated_ruleset_ids(market="NL") == []  # different market, filtered out


def test_reads_still_work_on_a_deprecated_ruleset(db):
    store = MongoRulesetRepository(db)
    store.create_version("de.test-1", _tiny_ruleset())
    store.deprecate("de.test-1")
    assert store.get_active("de.test-1") is not None
    assert len(store.list_versions("de.test-1")) == 1


def test_decision_type_mismatch_rejected_on_update(db):
    store = MongoRulesetRepository(db)
    store.create_version("de.test-1", _tiny_ruleset())
    with pytest.raises(ValueError):
        store.create_version("de.test-1", _tiny_ruleset(decision_type="DIFFERENT_TYPE"))


def test_market_mismatch_rejected_on_update(db):
    store = MongoRulesetRepository(db)
    store.create_version("de.test-1", _tiny_ruleset())
    with pytest.raises(ValueError):
        store.create_version("de.test-1", _tiny_ruleset(market="NL"))


def test_collision_rejected_for_two_ruleset_ids_sharing_market_and_decision_type(db):
    store = MongoRulesetRepository(db)
    store.create_version("de.test-1", _tiny_ruleset())
    with pytest.raises(ValueError):
        store.create_version("de.test-2", _tiny_ruleset())


def test_bootstrap_from_db_loads_all_five_real_rulesets_through_rule_store(db):
    """Seeding goes through the RuleStore facade (seed.py calls store.create_ruleset),
    which writes the authoritative repository AND the vector index together."""
    store = RuleStore(settings=None, repository=MongoRulesetRepository(db), vector_index=InMemoryVectorIndex())
    seed_from_directory(store, RULESETS_DIR)

    assert "de.device-financing" in store.ruleset_ids()
    assert store.get_ruleset("de.device-financing")["version"] == 1

    by_type = store.active_by_decision_type()
    assert set(by_type.keys()) == {
        "DEVICE_FINANCING",
        "FRAUD",
        "IDENTITY",
        "TARIFF_ELIGIBILITY",
        "PAYMENT_POLICY",
    }
    assert store.stats()["rules"] > 30
