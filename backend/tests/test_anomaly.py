"""The Anomaly Trigger (risk_tiered_architecture.md §2 & §5 step 6).

Uses lightweight fakes for the tracker/store/candidate_store rather than mongomock —
these tests are about the trigger's own decision logic (does it fire, does it dedup,
does it route by risk tier), not about Mongo. AnomalyTracker's actual query behavior is
covered separately below against mongomock, the same way test_async.py trusts
AsyncDecisionStore's shape without re-testing pymongo itself.
"""
import mongomock
import pytest

from app import anomaly
from app.config import Settings
from app.stores.anomaly_tracker import AnomalyTracker
from tests.conftest import make_request


@pytest.fixture()
def offline_settings() -> Settings:
    return Settings(openai_api_key="", enable_llm_explanations=True)


class FakeTracker:
    def __init__(self, counts: dict) -> None:
        self.events: list = []
        self._counts = counts

    def record_event(self, dimension, value, outcome, request_id=None) -> None:
        self.events.append((dimension, value, outcome, request_id))

    def count_recent(self, dimension, value, window_seconds, outcome=None) -> int:
        return self._counts.get((dimension, value), 0)


class FakeStore:
    def __init__(self, ruleset_id: str = "de.fraud", existing_rule_ids=(), composite_class=None, verdict_severity=None) -> None:
        self.ruleset_id = ruleset_id
        self.rules = {rid: {"id": rid} for rid in existing_rule_ids}
        self.added: list = []
        self.composite_class = composite_class or {}
        self.verdict_severity = verdict_severity or []

    def get_ruleset(self, ruleset_id: str) -> dict:
        if ruleset_id != self.ruleset_id:
            raise KeyError(ruleset_id)
        return {
            "rules": list(self.rules.values()),
            "composite_class": self.composite_class,
            "verdict_severity": self.verdict_severity,
        }

    def add_rule(self, ruleset_id: str, rule: dict, actor: str = "system") -> dict:
        self.rules[rule["id"]] = rule
        self.added.append((ruleset_id, rule, actor))
        return rule


class FakeCandidateStore:
    def __init__(self) -> None:
        self.created: list = []
        self._pending_ids: set = set()

    def create(self, ruleset_id, rule, risk, source_text=None) -> dict:
        self.created.append((ruleset_id, rule, risk, source_text))
        self._pending_ids.add(rule["id"])
        return {"candidate_id": "cand_fake"}

    def exists_pending(self, ruleset_id, rule_id) -> bool:
        return rule_id in self._pending_ids


def _decline_result(request_id: str = "req_1") -> dict:
    return {"composite_verdict": "DECLINE", "request_id": request_id}


# --- maybe_trigger: when it fires at all ---------------------------------------

def test_no_trigger_when_verdict_is_not_decline(offline_settings):
    tracker = FakeTracker({})
    request = make_request(**{"context.ip_country": "ZZ"})
    result = {"composite_verdict": "APPROVE", "request_id": "req_1"}

    out = anomaly.maybe_trigger(request, result, tracker, FakeStore(), FakeCandidateStore(), offline_settings)

    assert out is None
    assert tracker.events == []  # never even recorded — only DECLINEs count toward velocity


def test_no_trigger_when_tracked_dimension_missing_from_request(offline_settings):
    tracker = FakeTracker({})
    request = {"customer": {}, "order": {}}  # no "context" key at all
    out = anomaly.maybe_trigger(request, _decline_result(), tracker, FakeStore(), FakeCandidateStore(), offline_settings)

    assert out is None
    assert tracker.events == []


def test_records_every_decline_but_does_not_fire_below_threshold(offline_settings):
    tracker = FakeTracker({("ip_country", "ZZ"): 5})  # <= _THRESHOLD (10)
    request = make_request(**{"context.ip_country": "ZZ"})
    store, candidates = FakeStore(), FakeCandidateStore()

    out = anomaly.maybe_trigger(request, _decline_result(request["request_id"]), tracker, store, candidates, offline_settings)

    assert out is None
    assert tracker.events == [("ip_country", "ZZ", "DECLINE", request["request_id"])]  # still recorded
    assert store.added == []
    assert candidates.created == []


# --- Breach: heuristic path has no ip_country vocabulary -> must not auto-route ----

def test_breach_with_heuristic_only_generation_is_refused_not_auto_enforced(offline_settings):
    """rule_generator's heuristic path has no fact vocabulary for context.ip_country —
    without an OpenAI key it would otherwise synthesize an UNCONDITIONAL decline gate.
    The trigger must detect that and refuse to route it anywhere, not silently auto-
    enforce (or even shadow) a rule that blocks every order."""
    tracker = FakeTracker({("ip_country", "ZZ"): 11})  # > _THRESHOLD
    request = make_request(**{"context.ip_country": "ZZ"})
    store, candidates = FakeStore(), FakeCandidateStore()

    out = anomaly.maybe_trigger(request, _decline_result(), tracker, store, candidates, offline_settings)

    assert out is None
    assert store.added == []
    assert candidates.created == []


def test_breach_with_a_specific_generated_condition_routes_by_risk_tier(offline_settings, monkeypatch):
    """With generation mocked to return a real (non-vacuous) ip_country condition —
    standing in for what the LLM path would produce with a key configured — the trigger
    must classify it and route it. ip_country is a population-wide fact, so the real
    (unmocked) risk_analyzer heuristic must land this on HIGH -> the candidate queue."""
    tracker = FakeTracker({("ip_country", "ZZ"): 11})
    request = make_request(**{"context.ip_country": "ZZ"})
    store, candidates = FakeStore(), FakeCandidateStore()

    specific_rule = {
        "id": "GEN-IP-ZZ",
        "phase": "GATE",
        "priority": 50,
        "enabled": True,
        "when": {"fact": "context.ip_country", "op": "EQUALS", "value": "ZZ"},
        "then": {"verdict": "DECLINE", "terminal": True, "reason_codes": ["GENERATED_ANOMALY_BLOCK"]},
    }
    monkeypatch.setattr(
        "app.anomaly.generate_rule",
        lambda text, settings, ruleset_context=None: {"rule": specific_rule, "mode": "heuristic", "valid": True, "warnings": []},
    )

    out = anomaly.maybe_trigger(request, _decline_result(), tracker, store, candidates, offline_settings)

    assert out["risk_level"] == "HIGH"
    assert out["action"] == "PENDING_REVIEW"
    assert out["rule_id"] == "GEN-IP-ZZ"
    assert store.added == []
    assert len(candidates.created) == 1
    assert candidates.created[0][0] == "de.fraud"
    assert candidates.created[0][1]["id"] == "GEN-IP-ZZ"


def test_breach_with_an_unprefixed_fact_path_is_refused_not_queued(offline_settings, monkeypatch):
    """A rule that has a real condition (passes the leaf-count guard) but references a
    fact path the engine doesn't recognise — e.g. "ip_country" instead of
    "context.ip_country", which is exactly what the LLM path was observed to produce —
    must be caught before it reaches ANY tier, not deferred to a 422 at approval time."""
    tracker = FakeTracker({("ip_country", "ZZ"): 11})
    request = make_request(**{"context.ip_country": "ZZ"})
    store, candidates = FakeStore(), FakeCandidateStore()

    monkeypatch.setattr(
        "app.anomaly.generate_rule",
        lambda text, settings, ruleset_context=None: {
            "rule": {
                "id": "GEN-BAD-FACT", "phase": "GATE", "priority": 50, "enabled": True,
                "when": {"fact": "ip_country", "op": "EQUALS", "value": "ZZ"},  # missing "context." prefix
                "then": {"verdict": "DECLINE", "terminal": True},
            },
            "mode": "llm", "valid": True, "warnings": [],
        },
    )

    out = anomaly.maybe_trigger(request, _decline_result(request["request_id"]), tracker, store, candidates, offline_settings)

    assert out is None
    assert store.added == []
    assert candidates.created == []


def test_breach_fetches_and_passes_the_target_rulesets_verdict_vocabulary(offline_settings, monkeypatch):
    """anomaly.py's own job in this fix: look up the target ruleset's composite_class
    and verdict_severity and hand them to generate_rule so "decline" resolves to a
    word this specific ruleset recognises (rule_generator.py's _pick_verdict is what
    actually does the picking — this test only proves anomaly.py wires the context
    through correctly)."""
    tracker = FakeTracker({("ip_country", "ZZ"): 11})
    request = make_request(**{"context.ip_country": "ZZ"})
    store = FakeStore(
        composite_class={"BLOCK": "DECLINE_CLASS", "CLEAR": "APPROVE_CLASS", "REVIEW": "REFER_CLASS"},
        verdict_severity=["CLEAR", "REVIEW", "BLOCK"],
    )
    candidates = FakeCandidateStore()

    captured: dict = {}

    def fake_generate_rule(text, settings, ruleset_context=None):
        captured["ruleset_context"] = ruleset_context
        return {
            "rule": {
                "id": "GEN-IP-ZZ", "phase": "GATE", "priority": 50, "enabled": True,
                "when": {"fact": "context.ip_country", "op": "EQUALS", "value": "ZZ"},
                "then": {"verdict": "BLOCK", "terminal": True},
            },
            "mode": "heuristic", "valid": True, "warnings": [],
        }

    monkeypatch.setattr("app.anomaly.generate_rule", fake_generate_rule)

    out = anomaly.maybe_trigger(request, _decline_result(request["request_id"]), tracker, store, candidates, offline_settings)

    assert captured["ruleset_context"] == {
        "composite_class": store.composite_class,
        "verdict_severity": store.verdict_severity,
    }
    assert out["action"] == "PENDING_REVIEW"  # ip_country is still HIGH risk regardless of verdict word


def test_breach_against_an_unknown_ruleset_id_still_runs_with_no_context(offline_settings, monkeypatch):
    """get_ruleset raising KeyError (bad/misconfigured ruleset_id) must degrade to
    ruleset_context=None, not abort the whole breach — generation still runs with the
    old ruleset-agnostic defaults."""
    tracker = FakeTracker({("ip_country", "ZZ"): 11})
    request = make_request(**{"context.ip_country": "ZZ"})
    store = FakeStore(ruleset_id="de.fraud")
    candidates = FakeCandidateStore()

    captured: dict = {}

    def fake_generate_rule(text, settings, ruleset_context=None):
        captured["ruleset_context"] = ruleset_context
        return {
            "rule": {
                "id": "GEN-IP-ZZ", "phase": "GATE", "priority": 50, "enabled": True,
                "when": {"fact": "context.ip_country", "op": "EQUALS", "value": "ZZ"},
                "then": {"verdict": "DECLINE", "terminal": True},
            },
            "mode": "heuristic", "valid": True, "warnings": [],
        }

    monkeypatch.setattr("app.anomaly.generate_rule", fake_generate_rule)

    out = anomaly.maybe_trigger(
        request, _decline_result(request["request_id"]), tracker, store, candidates, offline_settings,
        ruleset_id="de.unknown-ruleset",
    )

    assert captured["ruleset_context"] is None
    assert out is not None  # still ran to completion, just without vocabulary context


def test_dedup_skips_a_rule_already_pending_review(offline_settings, monkeypatch):
    tracker = FakeTracker({("ip_country", "ZZ"): 11})
    request = make_request(**{"context.ip_country": "ZZ"})
    store, candidates = FakeStore(), FakeCandidateStore()
    candidates._pending_ids.add("GEN-IP-ZZ")  # already queued from an earlier breach

    monkeypatch.setattr(
        "app.anomaly.generate_rule",
        lambda text, settings, ruleset_context=None: {
            "rule": {
                "id": "GEN-IP-ZZ", "phase": "GATE", "priority": 50, "enabled": True,
                "when": {"fact": "context.ip_country", "op": "EQUALS", "value": "ZZ"},
                "then": {"verdict": "DECLINE", "terminal": True},
            },
            "mode": "heuristic", "valid": True, "warnings": [],
        },
    )

    out = anomaly.maybe_trigger(request, _decline_result(), tracker, store, candidates, offline_settings)

    assert out is None  # nothing new to report
    assert len(candidates.created) == 0  # did not re-queue


def test_dedup_skips_a_rule_already_live_in_the_ruleset(offline_settings, monkeypatch):
    tracker = FakeTracker({("ip_country", "ZZ"): 11})
    request = make_request(**{"context.ip_country": "ZZ"})
    store = FakeStore(existing_rule_ids=["GEN-IP-ZZ"])  # already live (e.g. auto-enforced earlier)
    candidates = FakeCandidateStore()

    monkeypatch.setattr(
        "app.anomaly.generate_rule",
        lambda text, settings, ruleset_context=None: {
            "rule": {
                "id": "GEN-IP-ZZ", "phase": "GATE", "priority": 50, "enabled": True,
                "when": {"fact": "context.ip_country", "op": "EQUALS", "value": "ZZ"},
                "then": {"verdict": "DECLINE", "terminal": True},
            },
            "mode": "heuristic", "valid": True, "warnings": [],
        },
    )

    out = anomaly.maybe_trigger(request, _decline_result(), tracker, store, candidates, offline_settings)

    assert out is None
    assert store.added == []  # did not re-add / churn a new version
    assert candidates.created == []


# --- _route: tier dispatch, independent of how the rule/risk were produced ----

def test_route_low_risk_auto_enforces_live():
    store = FakeStore()
    rule = {"id": "R1"}
    action = anomaly._route(rule, {"risk_level": "LOW"}, "de.fraud", store, FakeCandidateStore(), "text", "anomaly-trigger")
    assert action == "AUTO_ENFORCED"
    assert store.added == [("de.fraud", {"id": "R1", "is_shadow": False}, "anomaly-trigger")]


def test_route_medium_risk_goes_to_shadow_mode():
    store = FakeStore()
    rule = {"id": "R2"}
    action = anomaly._route(rule, {"risk_level": "MEDIUM"}, "de.fraud", store, FakeCandidateStore(), "text", "anomaly-trigger")
    assert action == "SHADOW_MODE"
    assert store.added == [("de.fraud", {"id": "R2", "is_shadow": True}, "anomaly-trigger")]


def test_route_high_risk_goes_to_candidate_queue():
    store = FakeStore()
    candidates = FakeCandidateStore()
    rule = {"id": "R3"}
    risk = {"risk_level": "HIGH", "reasoning": "broad"}
    action = anomaly._route(rule, risk, "de.fraud", store, candidates, "text", "anomaly-trigger")
    assert action == "PENDING_REVIEW"
    assert store.added == []
    assert candidates.created == [("de.fraud", rule, risk, "text")]


def test_maybe_trigger_never_raises_on_a_broken_tracker(offline_settings):
    class ExplodingTracker:
        def record_event(self, *a, **k):
            raise RuntimeError("mongo is down")

    request = make_request(**{"context.ip_country": "ZZ"})
    out = anomaly.maybe_trigger(request, _decline_result(), ExplodingTracker(), FakeStore(), FakeCandidateStore(), offline_settings)
    assert out is None


# --- AnomalyTracker against mongomock: the actual sliding-window query --------

@pytest.fixture()
def tracker() -> AnomalyTracker:
    db = mongomock.MongoClient().decision_ledger_test
    return AnomalyTracker(db)


def test_count_recent_only_counts_matching_dimension_value_and_outcome(tracker):
    tracker.record_event("ip_country", "ZZ", "DECLINE")
    tracker.record_event("ip_country", "ZZ", "DECLINE")
    tracker.record_event("ip_country", "DE", "DECLINE")  # different value
    tracker.record_event("ip_country", "ZZ", "APPROVE")  # different outcome

    assert tracker.count_recent("ip_country", "ZZ", window_seconds=300, outcome="DECLINE") == 2
    assert tracker.count_recent("ip_country", "DE", window_seconds=300, outcome="DECLINE") == 1
    assert tracker.count_recent("ip_country", "ZZ", window_seconds=300) == 3  # no outcome filter


def test_count_recent_excludes_events_outside_the_window(tracker):
    import datetime

    tracker.record_event("ip_country", "ZZ", "DECLINE")
    # Backdate the just-inserted event 10 minutes, bypassing record_event's own clock —
    # simpler and more reliable than monkeypatching datetime.now() across the module.
    ten_min_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)
    tracker._collection.update_one({"dimension": "ip_country", "value": "ZZ"}, {"$set": {"at": ten_min_ago}})

    assert tracker.count_recent("ip_country", "ZZ", window_seconds=300, outcome="DECLINE") == 0  # 5-min window
    assert tracker.count_recent("ip_country", "ZZ", window_seconds=900, outcome="DECLINE") == 1  # 15-min window still sees it
