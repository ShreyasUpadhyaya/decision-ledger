"""Blast-radius classifier for autonomously generated rules (risk_tiered_architecture.md
§3). ``assess_risk(rule, settings)`` looks at a candidate rule's condition tree — never
its intent, never anything outside the rule itself — and assigns LOW/MEDIUM/HIGH so the
anomaly trigger (``app/anomaly.py``) knows which of the three lifecycles (auto-enforce,
shadow, human review) a newly generated rule is allowed into.

Same two-path shape as ``rule_generator.py``:

    * **LLM path** (``settings.llm_enabled``): a LangChain chain classifies the rule.
    * **Heuristic path** (always available): a deterministic scope check over the
      condition tree's fact set. No key, no network.

Whichever path answers, an unrecognised or missing risk_level is never trusted at face
value — it degrades to the heuristic, and the heuristic itself defaults to HIGH for
anything it doesn't recognise. Failing toward caution here is deliberate and mirrors
``core/aggregator.py``'s own ``_UNMAPPED_FALLBACK``: an autonomously generated rule this
module can't confidently place gets the most restrictive lifecycle, never the most
permissive one.
"""
import re
from typing import Any, Optional

from ..logging_config import get_logger

logger = get_logger(__name__)

_RISK_LEVELS = ("LOW", "MEDIUM", "HIGH")

# Facts whose value partitions the *entire* customer base along one axis — a rule keyed
# on one of these can affect anyone, regardless of what else it checks.
_BROAD_POPULATION_FACTS = {"context.ip_country", "context.shipping_country", "market"}

# Facts that pin a rule to exactly one entity — the narrowest possible blast radius.
_SINGLE_ENTITY_FACTS = {"customer.customer_id", "context.device_fingerprint"}

# Facts that scope a rule to a meaningful but bounded slice (one tariff, one SKU) rather
# than a single customer or the whole population.
_BOUNDED_SLICE_FACTS = {"order.tariff_code", "order.device_sku"}

# A single leaf on one of these, with no other narrowing condition, covers a huge and
# growing share of orders — "over $X" alone is a population-wide gate, not a slice.
_UNBOUNDED_NUMERIC_FACTS = {"order.total_amount", "order.financed_amount"}

_NARROW_OP_SUFFIXES = {"EQUALS", "IN_LIST", "CONTAINS"}


def _collect_leaves(node: dict, leaves: list) -> None:
    if not isinstance(node, dict):
        return
    if "fact" in node:
        leaves.append(node)
        return
    for combinator in ("all", "any", "none"):
        for child in node.get(combinator) or []:
            _collect_leaves(child, leaves)


def _heuristic_assess(rule: dict) -> dict:
    leaves: list = []
    _collect_leaves(rule.get("when") or {}, leaves)
    facts = [leaf.get("fact") for leaf in leaves]
    fact_set = set(facts)

    broad = fact_set & _BROAD_POPULATION_FACTS
    if broad:
        return {
            "risk_level": "HIGH",
            "reasoning": f"condition keys on population-wide fact(s) {sorted(broad)} — matches an unbounded slice of traffic.",
        }

    if len(leaves) == 1 and facts[0] in _SINGLE_ENTITY_FACTS and leaves[0].get("op") in _NARROW_OP_SUFFIXES:
        return {
            "risk_level": "LOW",
            "reasoning": f"single-entity condition on {facts[0]!r} via {leaves[0].get('op')} — affects at most one customer/device.",
        }

    then = rule.get("then") or {}
    is_hard_decline = rule.get("phase") in ("GATE", "TERMS") and then.get("verdict") == "DECLINE"
    if len(leaves) == 1 and facts[0] in _UNBOUNDED_NUMERIC_FACTS and is_hard_decline:
        return {
            "risk_level": "HIGH",
            "reasoning": f"unqualified {facts[0]!r} threshold with no other narrowing condition, hard-declining — "
            "equivalent to a blanket cap on all orders above the threshold.",
        }

    if fact_set & _BOUNDED_SLICE_FACTS or len(leaves) >= 2:
        return {
            "risk_level": "MEDIUM",
            "reasoning": f"condition is scoped to a bounded slice (facts: {sorted(fact_set)}), "
            "narrower than a population-wide gate but broader than a single entity.",
        }

    return {
        "risk_level": "HIGH",
        "reasoning": f"condition shape (facts: {sorted(fact_set)}) didn't match a recognised low/medium pattern; "
        "defaulting to the most restrictive tier.",
    }


_LLM_PROMPT = (
    "You are a risk classifier for an autonomous rule-generation system in a decision "
    "engine. Classify the BLAST RADIUS of the JSON rule below into exactly one of three "
    "tiers:\n"
    "  LOW RISK: highly specific rules affecting a single entity, e.g. blocking one "
    "customer_id or one device_fingerprint.\n"
    "  MEDIUM RISK: moderate-impact rules, e.g. blocking a specific tariff_code or "
    "device_sku, or adding a deposit requirement scoped by more than one condition.\n"
    "  HIGH RISK: broad-impact rules, e.g. blocking an entire ip_country or "
    "shipping_country, or a hard decline on an amount threshold with no other "
    "narrowing condition.\n"
    "Output ONLY JSON, no prose: {{\"risk_level\": \"LOW\"|\"MEDIUM\"|\"HIGH\", "
    "\"reasoning\": str}}\n"
    "\nRULE: {rule_json}\n\nJSON:"
)


def _generate_with_llm(rule: dict, settings: Any) -> dict:
    import json

    from langchain_openai import ChatOpenAI  # lazy: optional dependency
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser

    prompt = ChatPromptTemplate.from_template(_LLM_PROMPT)
    model = ChatOpenAI(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        api_key=settings.openai_api_key,
        timeout=settings.llm_timeout_seconds,
    )
    raw = (prompt | model | StrOutputParser()).invoke({"rule_json": json.dumps(rule)}).strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    result = json.loads(raw)
    return {"risk_level": result.get("risk_level"), "reasoning": result.get("reasoning", "")}


def _valid(result: Optional[dict]) -> bool:
    return (
        isinstance(result, dict)
        and result.get("risk_level") in _RISK_LEVELS
        and isinstance(result.get("reasoning"), str)
        and bool(result["reasoning"].strip())
    )


def assess_risk(rule: dict, settings: Any) -> dict:
    """Return ``{risk_level, reasoning, mode}``. Never raises: an LLM failure or an
    LLM response this module doesn't trust both fall back to the heuristic, which is
    always structurally valid and never returns anything outside LOW/MEDIUM/HIGH."""
    result: Optional[dict] = None

    if getattr(settings, "llm_enabled", False):
        try:
            candidate = _generate_with_llm(rule, settings)
            if _valid(candidate):
                result = {**candidate, "mode": "llm"}
            else:
                logger.warning(
                    "llm.risk_assessment_invalid_fallback",
                    extra={"event": "llm.risk_assessment_invalid", "rule_id": rule.get("id")},
                )
        except Exception as exc:
            logger.warning(
                "llm.risk_assessment_failed_fallback",
                extra={"event": "llm.risk_assessment_failed", "error": str(exc)},
            )

    if result is None:
        result = {**_heuristic_assess(rule), "mode": "heuristic"}

    return result
