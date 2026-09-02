"""Three-valued condition evaluation over the all/any/none grammar (spec §6.3),
with null handling per spec §6.6: a null fact makes a leaf INDETERMINATE, never FALSE.
"""
import datetime
import enum
from typing import Any, Optional

from .operators import compile_operator


class LeafResult(enum.Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    INDETERMINATE = "INDETERMINATE"


_NEGATE = {
    LeafResult.TRUE: LeafResult.FALSE,
    LeafResult.FALSE: LeafResult.TRUE,
    LeafResult.INDETERMINATE: LeafResult.INDETERMINATE,
}


def resolve_fact(context: dict, dotted_path: str) -> Any:
    node = context
    for part in dotted_path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def compile_condition(node: dict) -> dict:
    """Attach a compiled operator to every leaf, in place, once per ruleset load."""
    if "fact" in node:
        node["_compiled"] = compile_operator(node["op"], node.get("value"))
        return node
    for combinator in ("all", "any", "none"):
        if combinator in node:
            for child in node[combinator]:
                compile_condition(child)
    return node


def evaluate_condition(node: dict, context: dict, requested_at: datetime.datetime) -> LeafResult:
    if "fact" in node:
        return _evaluate_leaf(node, context, requested_at)
    if "all" in node:
        return _combine_all(node["all"], context, requested_at)
    if "any" in node:
        return _combine_any(node["any"], context, requested_at)
    if "none" in node:
        return _NEGATE[_combine_any(node["none"], context, requested_at)]
    raise ValueError(f"malformed condition node: {node!r}")


def _evaluate_leaf(node: dict, context: dict, requested_at: datetime.datetime) -> LeafResult:
    fact_value = resolve_fact(context, node["fact"])
    compiled = node["_compiled"]
    if fact_value is None and not compiled.allow_null:
        return LeafResult.INDETERMINATE
    try:
        return LeafResult.TRUE if compiled.evaluate(fact_value, requested_at) else LeafResult.FALSE
    except Exception:
        # a type mismatch here means the ruleset should never have passed lint;
        # degrade to INDETERMINATE rather than take the whole request down
        return LeafResult.INDETERMINATE


def _combine_all(children: list, context: dict, requested_at: datetime.datetime) -> LeafResult:
    saw_indeterminate = False
    for child in children:
        result = evaluate_condition(child, context, requested_at)
        if result == LeafResult.FALSE:
            return LeafResult.FALSE
        if result == LeafResult.INDETERMINATE:
            saw_indeterminate = True
    return LeafResult.INDETERMINATE if saw_indeterminate else LeafResult.TRUE


def _combine_any(children: list, context: dict, requested_at: datetime.datetime) -> LeafResult:
    saw_indeterminate = False
    for child in children:
        result = evaluate_condition(child, context, requested_at)
        if result == LeafResult.TRUE:
            return LeafResult.TRUE
        if result == LeafResult.INDETERMINATE:
            saw_indeterminate = True
    return LeafResult.INDETERMINATE if saw_indeterminate else LeafResult.FALSE
