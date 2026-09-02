"""Natural-language decision explanation (spec §14.1).

``explain_decision(response, settings)`` translates a raw decision result — composite
verdict, per-decision verdicts, reason codes, degraded signals — into a clean paragraph
an operations analyst can read without knowing the rule IDs.

Two paths, one interface:

    * **LLM path** (``settings.llm_enabled``): a LangChain prompt+chain summarises the
      structured trace. Any import/config/network error is swallowed and we fall through.
    * **Fallback path** (always available): a deterministic, template-driven narrator
      built from a reason-code phrasebook. No key, no network, fully unit-testable.

The returned dict always carries ``mode`` (``"llm"`` or ``"template"``) so the UI and the
audit log can show exactly how the explanation was produced.
"""
from typing import Any

from ..logging_config import get_logger

logger = get_logger(__name__)

# A phrasebook mapping the reason codes emitted by the rulesets to plain-English clauses.
# Unknown codes are humanised generically (see ``_humanise``), so this only needs entries
# where the generic form would read badly.
_REASON_PHRASES: dict[str, str] = {
    "ACTIVE_DELINQUENCY": "the account is more than 60 days past due",
    "BASE_SCORE": "a baseline creditworthiness score was applied",
    "TENURE_BONUS": "the customer has a long tenure with us",
    "CLEAN_HISTORY": "the account has a clean payment history",
    "THIN_BUREAU_BAND": "the bureau score sits in a thin, marginal band",
    "VERY_LOW_BUREAU_SCORE": "the bureau score is very low",
    "STRONG_BUREAU_BAND": "the bureau score is strong",
    "HIGH_TICKET_TIER1": "the financed amount is high",
    "HIGH_TICKET_TIER2": "the financed amount is very high",
    "VERIFIED_SEPA_MANDATE": "a verified SEPA mandate is on file",
    "CLEAN_IDENTITY_AND_FRAUD": "identity and fraud checks are both clean",
    "HIGH_VALUE_MEDIUM_RISK": "this is a high-value order at medium risk",
    "MEDIUM_RISK_HIGH_TICKET": "this is a medium-risk order with a high ticket size",
    "VERY_HIGH_RISK": "the accumulated risk score is very high",
    "NO_RULE_MATCHED": "no terms rule matched, so the safe default was used",
    "SIGNAL_DEGRADED": "one or more upstream signals were degraded",
    "YOUNG_ADULT_CAP": "a young-adult financing cap applies",
    "DE_DEPOSIT_CAP": "the German-market deposit cap applies",
    "FRAUD_PROVIDER_UNAVAILABLE": "the fraud provider was unavailable and the order was held for review",
    "IDENTITY_PROVIDER_UNAVAILABLE": "the identity provider was unavailable and step-up KYC was required",
}

_VERDICT_GLOSS: dict[str, str] = {
    "APPROVE": "approved",
    "APPROVE_WITH_DEPOSIT": "approved subject to a deposit",
    "REFER": "referred for manual review",
    "REVIEW": "flagged for manual review",
    "DECLINE": "declined",
    "BLOCK": "blocked",
    "FAIL": "failed",
    "CLEAR": "cleared",
    "STEP_UP_KYC": "held for step-up identity verification",
    "PASS": "passed",
    "DOWNGRADE_OFFER": "offered on downgraded terms",
    "ANY": "unrestricted",
    "PREPAY_ONLY": "restricted to prepayment",
    "SEPA_ONLY": "restricted to SEPA direct debit",
}

# Verdicts that represent a clean, unremarkable pass — omitted from the per-decision
# narrative so the explanation stays focused on what actually drove the outcome.
_CLEAN_PASS = {"APPROVE", "CLEAR", "PASS", "ANY"}

_COMPOSITE_LEAD: dict[str, str] = {
    "APPROVE": "The order was approved.",
    "APPROVE_WITH_DEPOSIT": "The order was approved with a deposit requirement.",
    "REFER": "The order was referred for manual review.",
    "DECLINE": "The order was declined.",
}


def _humanise(code: str) -> str:
    """Fallback rendering for a reason code with no explicit phrasebook entry."""
    return code.replace("_", " ").lower()


def _phrase(code: str) -> str:
    return _REASON_PHRASES.get(code, _humanise(code))


def _gloss(verdict: str) -> str:
    return _VERDICT_GLOSS.get(verdict, verdict.replace("_", " ").lower())


def _degraded_signals(response: dict) -> list[str]:
    health = response.get("signal_health", {}) or {}
    return [name for name, status in health.items() if status != "OK"]


def build_template_explanation(response: dict) -> str:
    """Deterministic narrator — the always-available fallback. Pure and side-effect free."""
    composite = response.get("composite_verdict", "UNKNOWN")
    parts: list[str] = [_COMPOSITE_LEAD.get(composite, f"The order verdict is {_gloss(composite)}.")]

    # Per-decision summary — only mention decisions that carry signal (i.e. not a plain
    # clean pass), so the paragraph stays focused on what actually drove the outcome.
    notable: list[str] = []
    for decision in response.get("decisions", []):
        verdict = decision.get("verdict")
        dtype = decision.get("type", "").replace("_", " ").title()
        if verdict in _CLEAN_PASS:
            continue
        top_codes = decision.get("reason_codes", [])[:2]
        if top_codes:
            reasons = " and ".join(_phrase(c) for c in top_codes)
            notable.append(f"the {dtype} check was {_gloss(verdict)} because {reasons}")
        else:
            notable.append(f"the {dtype} check was {_gloss(verdict)}")

    if notable:
        parts.append("Specifically, " + "; ".join(notable) + ".")

    degraded = _degraded_signals(response)
    if degraded:
        pretty = ", ".join(s.replace("_", " ") for s in degraded)
        confidence = response.get("signal_confidence")
        conf_str = f" (overall signal confidence {confidence})" if confidence is not None else ""
        parts.append(
            f"Note that the following upstream signals were degraded: {pretty}{conf_str}; "
            "the platform applied its safe-fallback policy for these."
        )

    terms = response.get("terms")
    if terms:
        rendered = ", ".join(f"{k.replace('_', ' ')} = {v}" for k, v in terms.items())
        parts.append(f"The resulting commercial terms are: {rendered}.")

    return " ".join(parts)


_LLM_PROMPT = (
    "You are a decisioning analyst for a telecom e-commerce checkout. In one clear "
    "paragraph, plain business English, explain WHY the following order decision was "
    "reached. Reference the composite verdict, the decisions that drove it, any degraded "
    "signals, and the final terms. Do not invent facts beyond the JSON. Do not mention "
    "rule IDs.\n\nDECISION JSON:\n{decision_json}\n\nExplanation:"
)


def _explain_with_llm(response: dict, settings: Any) -> str:
    """LangChain path. Raises on any problem so the caller can fall back cleanly."""
    import json

    from langchain_openai import ChatOpenAI  # lazy: optional dependency
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser

    slim = {
        "composite_verdict": response.get("composite_verdict"),
        "terms": response.get("terms"),
        "signal_confidence": response.get("signal_confidence"),
        "signal_health": response.get("signal_health"),
        "decisions": [
            {"type": d.get("type"), "verdict": d.get("verdict"), "reason_codes": d.get("reason_codes")}
            for d in response.get("decisions", [])
        ],
    }
    prompt = ChatPromptTemplate.from_template(_LLM_PROMPT)
    model = ChatOpenAI(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        api_key=settings.openai_api_key,
        timeout=settings.llm_timeout_seconds,
    )
    chain = prompt | model | StrOutputParser()
    return chain.invoke({"decision_json": json.dumps(slim, default=str)}).strip()


def explain_decision(response: dict, settings: Any) -> dict:
    """Return ``{"text": str, "mode": "llm"|"template", "degraded": bool}``.

    Never raises: an LLM failure always degrades to the deterministic template.
    """
    if getattr(settings, "llm_enabled", False):
        try:
            text = _explain_with_llm(response, settings)
            if text:
                return {"text": text, "mode": "llm", "degraded": False}
        except Exception as exc:  # any import/config/network failure -> fall back
            logger.warning(
                "llm.explain_failed_fallback",
                extra={"event": "llm.explain_failed", "error": str(exc)},
            )

    return {"text": build_template_explanation(response), "mode": "template", "degraded": False}
