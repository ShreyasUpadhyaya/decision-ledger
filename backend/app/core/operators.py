"""Operator registry (spec §6.4): compiles a rule's ``{fact, op, value}`` leaf into a
callable ``(fact_value, requested_at) -> bool``.

Numeric, string, collection and absolute-date operators compile to a `rule_engine`
expression string and are evaluated by the `rule_engine` library (github: zeroSteiner/
rule-engine) — this gives real parsing and type-checking instead of a hand-rolled
comparison per operator. The two operators that need "the current instant"
(WITHIN_LAST_DAYS, AGE_IN_YEARS_GTE) are plain Python callables instead: the evaluator
is a pure function (spec §5.1) and must never read a live clock, so "now" is always
`requested_at` from the frozen context, which `rule_engine` has no notion of.

Null handling (spec §6.6) lives one layer up in `conditions.py` — every operator here
assumes its `fact_value` is not None, except the three explicitly about nullness.
"""
import datetime
import json
import re as _re
from dataclasses import dataclass
from typing import Any, Callable

import rule_engine

Evaluate = Callable[[Any, datetime.datetime], bool]


@dataclass
class CompiledOperator:
    allow_null: bool
    evaluate: Evaluate


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    raise TypeError(f"unsupported literal type for rule value: {type(value)!r}")


def _array_literal(values: list) -> str:
    return "[" + ", ".join(_literal(v) for v in values) + "]"


def _date_literal(iso_value: str) -> str:
    return f"d{json.dumps(iso_value)}"


def _rule_predicate(expr: str) -> Callable[[Any], bool]:
    compiled = rule_engine.Rule(expr)
    return lambda fact_value: bool(compiled.matches({"fact": fact_value}))


def _op_between(value: Any) -> Callable[[Any], bool]:
    lo, hi = value
    return _rule_predicate(f"fact >= {_literal(lo)} and fact <= {_literal(hi)}")


def _op_between_dates(value: Any) -> Callable[[Any], bool]:
    lo, hi = value
    return _rule_predicate(f"fact >= {_date_literal(lo)} and fact <= {_date_literal(hi)}")


def _op_starts_with(value: str) -> Callable[[Any], bool]:
    # json.dumps (not manual quoting) so a backslash from re.escape survives
    # rule_engine's own unicode-escape decoding of string literals intact.
    return _rule_predicate(f"fact =~ {json.dumps('^' + _re.escape(value))}")


def _op_ends_with(value: str) -> Callable[[Any], bool]:
    return _rule_predicate(f"fact =~~ {json.dumps(_re.escape(value) + '$')}")


def _op_contains(value: str) -> Callable[[Any], bool]:
    return _rule_predicate(f"{_literal(value)} in fact")


def _within_last_days(days: Any) -> Evaluate:
    span = datetime.timedelta(days=days)

    def run(fact_value: datetime.datetime, requested_at: datetime.datetime) -> bool:
        delta = requested_at - fact_value
        return datetime.timedelta(0) <= delta <= span

    return run


def _age_in_years_gte(years: Any) -> Evaluate:
    def run(fact_value: datetime.date, requested_at: datetime.datetime) -> bool:
        had_birthday = (requested_at.month, requested_at.day) >= (fact_value.month, fact_value.day)
        age = requested_at.year - fact_value.year - (0 if had_birthday else 1)
        return age >= years

    return run


_SIMPLE: dict[str, Callable[[Any], Callable[[Any], bool]]] = {
    "EQUALS": lambda v: _rule_predicate(f"fact == {_literal(v)}"),
    "NOT_EQUALS": lambda v: _rule_predicate(f"fact != {_literal(v)}"),
    "GREATER_THAN": lambda v: _rule_predicate(f"fact > {_literal(v)}"),
    "GTE": lambda v: _rule_predicate(f"fact >= {_literal(v)}"),
    "LESS_THAN": lambda v: _rule_predicate(f"fact < {_literal(v)}"),
    "LTE": lambda v: _rule_predicate(f"fact <= {_literal(v)}"),
    "BETWEEN": _op_between,
    "IS_TRUE": lambda v: _rule_predicate("fact == true"),
    "IS_FALSE": lambda v: _rule_predicate("fact == false"),
    "CONTAINS": _op_contains,
    "STARTS_WITH": _op_starts_with,
    "ENDS_WITH": _op_ends_with,
    "IN_LIST": lambda v: _rule_predicate(f"fact in {_array_literal(v)}"),
    "NOT_IN_LIST": lambda v: _rule_predicate(f"fact not in {_array_literal(v)}"),
    "MATCHES_REGEX": lambda v: _rule_predicate(f"fact =~~ {json.dumps(v)}"),
    "BEFORE": lambda v: _rule_predicate(f"fact < {_date_literal(v)}"),
    "AFTER": lambda v: _rule_predicate(f"fact > {_date_literal(v)}"),
    "BETWEEN_DATES": _op_between_dates,
}

_NULL_SAFE: dict[str, Callable[[Any], Evaluate]] = {
    "IS_NULL": lambda v: (lambda fv, now: fv is None),
    "IS_NOT_NULL": lambda v: (lambda fv, now: fv is not None),
    "IS_EMPTY": lambda v: (lambda fv, now: fv is None or len(fv) == 0),
}

_TIME_RELATIVE: dict[str, Callable[[Any], Evaluate]] = {
    "WITHIN_LAST_DAYS": _within_last_days,
    "AGE_IN_YEARS_GTE": _age_in_years_gte,
}


KNOWN_OPERATORS = frozenset({**_SIMPLE, **_NULL_SAFE, **_TIME_RELATIVE}.keys())


def compile_operator(op: str, value: Any) -> CompiledOperator:
    if op in _NULL_SAFE:
        return CompiledOperator(allow_null=True, evaluate=_NULL_SAFE[op](value))
    if op in _TIME_RELATIVE:
        return CompiledOperator(allow_null=False, evaluate=_TIME_RELATIVE[op](value))
    if op not in _SIMPLE:
        raise ValueError(f"unknown operator: {op!r}")
    predicate = _SIMPLE[op](value)
    return CompiledOperator(allow_null=False, evaluate=lambda fv, now: predicate(fv))
