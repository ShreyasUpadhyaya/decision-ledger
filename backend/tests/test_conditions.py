import datetime

from app.core.conditions import LeafResult, compile_condition, evaluate_condition

NOW = datetime.datetime.fromisoformat("2026-07-24T09:00:00+00:00")


def _leaf(fact, op, value=None):
    node = {"fact": fact, "op": op}
    if value is not None:
        node["value"] = value
    return compile_condition(node)


def test_greater_than_true_and_false():
    node = _leaf("x", "GREATER_THAN", 5)
    assert evaluate_condition(node, {"x": 10}, NOW) == LeafResult.TRUE
    assert evaluate_condition(node, {"x": 3}, NOW) == LeafResult.FALSE


def test_between_inclusive_both_ends():
    node = _leaf("x", "BETWEEN", [40, 59])
    assert evaluate_condition(node, {"x": 40}, NOW) == LeafResult.TRUE
    assert evaluate_condition(node, {"x": 59}, NOW) == LeafResult.TRUE
    assert evaluate_condition(node, {"x": 39}, NOW) == LeafResult.FALSE
    assert evaluate_condition(node, {"x": 60}, NOW) == LeafResult.FALSE


def test_null_fact_is_indeterminate_not_false_for_ordered_ops():
    node = _leaf("x", "GREATER_THAN", 5)
    assert evaluate_condition(node, {"x": None}, NOW) == LeafResult.INDETERMINATE


def test_null_fact_is_indeterminate_not_false_for_equals():
    # rule_engine's == on None returns False rather than raising; conditions.py must
    # pre-empt that so nulls are uniformly INDETERMINATE regardless of operator (spec §6.6).
    node = _leaf("x", "EQUALS", "DE")
    assert evaluate_condition(node, {"x": None}, NOW) == LeafResult.INDETERMINATE


def test_is_null_and_is_not_null_see_the_null_directly():
    assert evaluate_condition(_leaf("x", "IS_NULL"), {"x": None}, NOW) == LeafResult.TRUE
    assert evaluate_condition(_leaf("x", "IS_NULL"), {"x": 5}, NOW) == LeafResult.FALSE
    assert evaluate_condition(_leaf("x", "IS_NOT_NULL"), {"x": None}, NOW) == LeafResult.FALSE
    assert evaluate_condition(_leaf("x", "IS_NOT_NULL"), {"x": 5}, NOW) == LeafResult.TRUE


def test_in_list_and_not_in_list():
    node = _leaf("payment_method", "IN_LIST", ["SEPA_DD", "CARD"])
    assert evaluate_condition(node, {"payment_method": "SEPA_DD"}, NOW) == LeafResult.TRUE
    assert evaluate_condition(node, {"payment_method": "CASH"}, NOW) == LeafResult.FALSE
    node2 = _leaf("payment_method", "NOT_IN_LIST", ["SEPA_DD", "CARD"])
    assert evaluate_condition(node2, {"payment_method": "CASH"}, NOW) == LeafResult.TRUE


def test_starts_with_and_ends_with():
    starts = _leaf("sku", "STARTS_WITH", "SM-")
    ends = _leaf("sku", "ENDS_WITH", "128")
    assert evaluate_condition(starts, {"sku": "SM-A556-128"}, NOW) == LeafResult.TRUE
    assert evaluate_condition(starts, {"sku": "IP-15-128"}, NOW) == LeafResult.FALSE
    assert evaluate_condition(ends, {"sku": "SM-A556-128"}, NOW) == LeafResult.TRUE
    assert evaluate_condition(ends, {"sku": "SM-A556-256"}, NOW) == LeafResult.FALSE


def test_contains():
    node = _leaf("sku", "CONTAINS", "A556")
    assert evaluate_condition(node, {"sku": "SM-A556-128"}, NOW) == LeafResult.TRUE
    assert evaluate_condition(node, {"sku": "SM-S931-256"}, NOW) == LeafResult.FALSE


def test_matches_regex():
    node = _leaf("email", "MATCHES_REGEX", r"^[^@]+@telekom\.de$")
    assert evaluate_condition(node, {"email": "risk.lead@telekom.de"}, NOW) == LeafResult.TRUE
    assert evaluate_condition(node, {"email": "someone@gmail.com"}, NOW) == LeafResult.FALSE


def test_age_in_years_gte():
    node = _leaf("dob", "AGE_IN_YEARS_GTE", 21)
    young = datetime.date(2007, 3, 1)  # 19 on 2026-07-24
    old = datetime.date(1990, 1, 1)  # 36 on 2026-07-24
    assert evaluate_condition(node, {"dob": young}, NOW) == LeafResult.FALSE
    assert evaluate_condition(node, {"dob": old}, NOW) == LeafResult.TRUE


def test_within_last_days():
    node = _leaf("opened_at", "WITHIN_LAST_DAYS", 30)
    recent = NOW - datetime.timedelta(days=10)
    old = NOW - datetime.timedelta(days=90)
    assert evaluate_condition(node, {"opened_at": recent}, NOW) == LeafResult.TRUE
    assert evaluate_condition(node, {"opened_at": old}, NOW) == LeafResult.FALSE


def test_all_combinator_three_valued_logic():
    node = compile_condition({"all": [
        {"fact": "a", "op": "GREATER_THAN", "value": 1},
        {"fact": "b", "op": "GREATER_THAN", "value": 1},
    ]})
    assert evaluate_condition(node, {"a": 2, "b": 2}, NOW) == LeafResult.TRUE
    assert evaluate_condition(node, {"a": 0, "b": 2}, NOW) == LeafResult.FALSE  # any FALSE -> FALSE
    assert evaluate_condition(node, {"a": None, "b": 2}, NOW) == LeafResult.INDETERMINATE
    assert evaluate_condition(node, {"a": None, "b": 0}, NOW) == LeafResult.FALSE  # FALSE beats INDETERMINATE


def test_any_combinator_three_valued_logic():
    node = compile_condition({"any": [
        {"fact": "a", "op": "GREATER_THAN", "value": 1},
        {"fact": "b", "op": "GREATER_THAN", "value": 1},
    ]})
    assert evaluate_condition(node, {"a": 0, "b": 0}, NOW) == LeafResult.FALSE
    assert evaluate_condition(node, {"a": 2, "b": 0}, NOW) == LeafResult.TRUE  # any TRUE -> TRUE
    assert evaluate_condition(node, {"a": None, "b": 0}, NOW) == LeafResult.INDETERMINATE
    assert evaluate_condition(node, {"a": None, "b": 2}, NOW) == LeafResult.TRUE  # TRUE beats INDETERMINATE


def test_none_combinator_is_nor():
    node = compile_condition({"none": [
        {"fact": "payment_method", "op": "IN_LIST", "value": ["SEPA_DD", "CARD"]},
    ]})
    assert evaluate_condition(node, {"payment_method": "CASH"}, NOW) == LeafResult.TRUE
    assert evaluate_condition(node, {"payment_method": "SEPA_DD"}, NOW) == LeafResult.FALSE
    assert evaluate_condition(node, {"payment_method": None}, NOW) == LeafResult.INDETERMINATE


def test_empty_all_is_vacuously_true():
    node = compile_condition({"all": []})
    assert evaluate_condition(node, {}, NOW) == LeafResult.TRUE
