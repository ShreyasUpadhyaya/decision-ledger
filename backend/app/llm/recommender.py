"""Vector-search fallback recommendation (merged-architecture addition).

Used only when the JSON rule engine has no matching rule anywhere for a request
(``app.decision_service``). ``recommend_from_similar_rules(request, similar_rules,
settings)`` turns a handful of semantically similar historical rules into a single
APPROVE / REFER / DECLINE recommendation with a confidence score and an explanation.

Two paths behind one interface, same pattern as ``explainer.py`` / ``rule_generator.py``:

    * **LLM path** (``settings.llm_enabled``): a LangChain chain reasons over the
      request and the similar rules' verdicts/reason codes, and returns a small JSON
      recommendation.
    * **Heuristic path** (always available): similarity-weighted majority vote over the
      similar rules' verdicts. No key, no network.

Never raises: an LLM failure or an invalid LLM response both fall back to the heuristic,
which always produces a structurally valid recommendation.
"""
from collections import defaultdict
from typing import Any

from ..logging_config import get_logger

logger = get_logger(__name__)

_VALID_VERDICTS = {"APPROVE", "REFER", "DECLINE"}

# A rule's own verdict vocabulary (APPROVE_WITH_DEPOSIT, STEP_UP_KYC, ...) is collapsed
# to the universal APPROVE/REFER/DECLINE bucket the fallback path returns, mirroring the
# aggregator's own DECLINE_CLASS/REFER_CLASS fail-toward-caution posture.
_DECLINE_LIKE = {"DECLINE", "BLOCK", "FAIL"}
_REFER_LIKE = {"REFER", "REVIEW", "STEP_UP_KYC", "DOWNGRADE_OFFER"}


def _bucket(verdict: str) -> str:
    if verdict in _DECLINE_LIKE:
        return "DECLINE"
    if verdict in _REFER_LIKE:
        return "REFER"
    return "APPROVE"


def _heuristic(similar_rules: list[dict]) -> dict:
    weights: dict = defaultdict(float)
    for hit in similar_rules:
        verdict = hit.get("verdict")
        if not verdict:
            continue
        weights[_bucket(verdict)] += max(hit.get("score") or 0.0, 0.0)

    if not weights:
        return {
            "verdict": "REFER",
            "confidence": 0.0,
            "explanation": "No similar rule carried a usable verdict; referred for manual review.",
            "mode": "heuristic",
        }

    verdict, top_weight = max(weights.items(), key=lambda kv: kv[1])
    total = sum(weights.values())
    confidence = round(top_weight / total, 2) if total else 0.0

    cited = ", ".join(
        f"{h['ruleset_id']}/{h['rule_id']} ({h.get('verdict')})"
        for h in similar_rules[:3]
        if h.get("rule_id")
    )
    explanation = (
        f"No deterministic rule matched this request. Based on the most similar existing "
        f"rules ({cited or 'none found'}), the recommended outcome is {verdict}."
    )
    return {"verdict": verdict, "confidence": confidence, "explanation": explanation, "mode": "heuristic"}


_LLM_PROMPT = (
    "You are a decisioning analyst for a telecom e-commerce checkout. No deterministic "
    "rule matched the order below. Using ONLY the similar historical rules provided, "
    "recommend one outcome.\n\n"
    "ORDER REQUEST:\n{request_json}\n\n"
    "SIMILAR RULES (each: ruleset, rule_id, verdict, reason_codes, similarity score):\n"
    "{similar_rules_json}\n\n"
    "Respond with ONLY a JSON object of this exact shape, no prose: "
    '{{"verdict": "APPROVE"|"REFER"|"DECLINE", "confidence": <float 0-1>, '
    '"explanation": "<one paragraph, plain business English, do not invent facts>"}}'
)


def _recommend_with_llm(request: dict, similar_rules: list[dict], settings: Any) -> dict:
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
    raw = (prompt | model | StrOutputParser()).invoke(
        {
            "request_json": json.dumps(request, default=str),
            "similar_rules_json": json.dumps(similar_rules, default=str),
        }
    ).strip()
    raw = raw.strip("` \n")
    if raw.lower().startswith("json"):
        raw = raw[4:].strip()
    parsed = json.loads(raw)

    raw_verdict = parsed.get("verdict")
    # The prompt shows the model the similar rules' own domain verdicts (e.g.
    # APPROVE_WITH_DEPOSIT, STEP_UP_KYC), so a reasonable response often echoes one of
    # those back verbatim rather than the bucketed APPROVE/REFER/DECLINE. Collapse it the
    # same way the heuristic path already does before rejecting it as unrecognised.
    verdict = raw_verdict if raw_verdict in _VALID_VERDICTS else _bucket(str(raw_verdict))
    if verdict not in _VALID_VERDICTS:
        raise ValueError(f"LLM returned an unrecognised verdict: {raw_verdict!r}")
    confidence = float(parsed.get("confidence", 0.5))
    explanation = str(parsed.get("explanation", "")).strip()
    if not explanation:
        raise ValueError("LLM returned an empty explanation")
    return {
        "verdict": verdict,
        "confidence": max(0.0, min(1.0, confidence)),
        "explanation": explanation,
        "mode": "llm",
    }


def recommend_from_similar_rules(request: dict, similar_rules: list[dict], settings: Any) -> dict:
    """Return ``{"verdict", "confidence", "explanation", "mode"}``. Never raises."""
    if getattr(settings, "llm_enabled", False):
        try:
            return _recommend_with_llm(request, similar_rules, settings)
        except Exception as exc:
            logger.warning(
                "llm.recommend_failed_fallback",
                extra={"event": "llm.recommend_failed", "error": str(exc)},
            )

    return _heuristic(similar_rules)
