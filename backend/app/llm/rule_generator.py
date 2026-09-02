"""Natural-language -> JSON rule generator (spec §14.2).

``generate_rule(text, settings)`` turns a plain-English policy statement such as

    "Require a 50% deposit if the financed amount exceeds 2000 for new customers"

into a structured, *validated* rule object shaped exactly like the entries in the
``rulesets/*.json`` files, so it can be pasted straight into a ruleset.

As everywhere in this layer there are two paths behind one interface:

    * **LLM path** (``settings.llm_enabled``): a LangChain chain emits candidate JSON,
      which is then run through the very same validation as the fallback.
    * **Heuristic path** (always available): a deterministic phrase parser. It recognises
      a fact vocabulary, comparison keywords, an action, and optional qualifiers, and
      assembles the rule directly. No key, no network.

Whichever path produced it, the candidate is validated by actually compiling its
condition tree with the production compiler (``core.conditions.compile_condition``) — if
the engine can't compile it, it isn't a valid rule, full stop.
"""
import copy
import hashlib
import re
from typing import Any, Optional

from ..core.conditions import compile_condition
from ..logging_config import get_logger

logger = get_logger(__name__)

# --- Fact vocabulary ---------------------------------------------------------
# Ordered longest-phrase-first so "credit score" wins over a bare "score". Each entry:
#   phrase -> (fact_path, kind)   where kind is "numeric" | "bool_true" | "bool_false" | "string"
_FACT_VOCAB: list[tuple[str, str, str]] = [
    ("financed amount", "order.financed_amount", "numeric"),
    ("finance amount", "order.financed_amount", "numeric"),
    ("total amount", "order.total_amount", "numeric"),
    ("order amount", "order.total_amount", "numeric"),
    ("order value", "order.total_amount", "numeric"),
    ("credit score", "bureau.score", "numeric"),
    ("bureau score", "bureau.score", "numeric"),
    ("risk score", "computed.risk_score", "numeric"),
    ("days past due", "account.days_past_due", "numeric"),
    ("delinquency", "account.days_past_due", "numeric"),
    ("past due", "account.days_past_due", "numeric"),
    ("tenure", "customer.tenure_months", "numeric"),
    ("term months", "order.term_months", "numeric"),
    ("term length", "order.term_months", "numeric"),
    ("line count", "order.line_count", "numeric"),
    ("number of lines", "order.line_count", "numeric"),
    ("identity match score", "identity.match_score", "numeric"),
    ("match score", "identity.match_score", "numeric"),
    ("velocity", "fraud.velocity_orders_40m", "numeric"),
    ("session age", "context.session_age_seconds", "numeric"),
    ("age", "customer.date_of_birth", "age"),
]

# Standalone qualifier phrases that translate to a fixed condition leaf.
_QUALIFIER_VOCAB: list[tuple[str, dict]] = [
    ("new customer", {"fact": "customer.is_existing", "op": "IS_FALSE"}),
    ("new customers", {"fact": "customer.is_existing", "op": "IS_FALSE"}),
    ("existing customer", {"fact": "customer.is_existing", "op": "IS_TRUE"}),
    ("existing customers", {"fact": "customer.is_existing", "op": "IS_TRUE"}),
    ("verified sepa", {"fact": "account.sepa_mandate_verified", "op": "IS_TRUE"}),
    ("sepa mandate", {"fact": "account.sepa_mandate_verified", "op": "IS_TRUE"}),
    ("failed liveness", {"fact": "identity.liveness_check", "op": "IS_FALSE"}),
    ("passed liveness", {"fact": "identity.liveness_check", "op": "IS_TRUE"}),
    ("on the blocklist", {"fact": "fraud.device_on_global_blocklist", "op": "IS_TRUE"}),
    ("blocklisted device", {"fact": "fraud.device_on_global_blocklist", "op": "IS_TRUE"}),
]

# Comparison keywords -> numeric operator. Ordered so multi-word forms match first.
_COMPARATORS: list[tuple[str, str]] = [
    ("greater than or equal to", "GTE"),
    ("less than or equal to", "LTE"),
    ("at least", "GTE"),
    ("no less than", "GTE"),
    ("or more", "GTE"),
    ("or higher", "GTE"),
    ("at most", "LTE"),
    ("no more than", "LTE"),
    ("or less", "LTE"),
    ("or lower", "LTE"),
    ("greater than", "GREATER_THAN"),
    ("more than", "GREATER_THAN"),
    ("less than", "LESS_THAN"),
    ("fewer than", "LESS_THAN"),
    ("exceeds", "GREATER_THAN"),
    ("exceed", "GREATER_THAN"),
    ("above", "GREATER_THAN"),
    ("over", "GREATER_THAN"),
    ("below", "LESS_THAN"),
    ("under", "LESS_THAN"),
    ("equals", "EQUALS"),
    ("equal to", "EQUALS"),
    ("is exactly", "EQUALS"),
]

_DEFAULT_PRIORITY = {"GATE": 50, "SCORING": 150, "TERMS": 250, "OVERLAY": 450}


def _number_after(text: str, anchor: str) -> Optional[float]:
    """First numeric literal appearing at/after ``anchor`` in ``text`` (commas & % stripped)."""
    idx = text.find(anchor)
    window = text[idx:] if idx >= 0 else text
    match = re.search(r"(\d[\d,]*\.?\d*)", window)
    if not match:
        return None
    raw = match.group(1).replace(",", "")
    value = float(raw)
    return int(value) if value.is_integer() else value


def _detect_comparator(text: str) -> Optional[str]:
    for phrase, op in _COMPARATORS:
        if phrase in text:
            return op
    return None


def _detect_facts(text: str) -> list[tuple[str, str, str]]:
    """Return matched (phrase, fact_path, kind) tuples, de-duplicated by fact_path,
    longest phrase first so specific facts win over generic ones."""
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for phrase, fact_path, kind in _FACT_VOCAB:
        if phrase in text and fact_path not in seen:
            found.append((phrase, fact_path, kind))
            seen.add(fact_path)
    return found


def _detect_qualifiers(text: str) -> list[dict]:
    leaves: list[dict] = []
    seen: set[str] = set()
    for phrase, leaf in _QUALIFIER_VOCAB:
        key = leaf["fact"] + leaf["op"]
        if phrase in text and key not in seen:
            leaves.append(dict(leaf))
            seen.add(key)
    return leaves


def _build_conditions(text: str, warnings: list[str]) -> list[dict]:
    conditions: list[dict] = []
    comparator = _detect_comparator(text)

    for phrase, fact_path, kind in _detect_facts(text):
        if kind == "age":
            value = _number_after(text, phrase) or 18
            conditions.append({"fact": fact_path, "op": "AGE_IN_YEARS_GTE", "value": int(value)})
            continue
        value = _number_after(text, phrase)
        if value is None:
            warnings.append(f"could not find a threshold number for '{phrase}'; skipped")
            continue
        op = comparator or "GTE"
        if comparator is None:
            warnings.append(f"no comparison keyword found for '{phrase}'; assumed GTE")
        conditions.append({"fact": fact_path, "op": op, "value": value})

    conditions.extend(_detect_qualifiers(text))
    return conditions


def _class_candidates(composite_class: dict, target_class: str) -> list[str]:
    return [verdict for verdict, cls in composite_class.items() if cls == target_class]


def _pick_verdict(ruleset_context: Optional[dict], target_class: str, prefer: str, fallback: str) -> str:
    """Pick a verdict word this specific ruleset actually recognises, instead of a
    generic literal like "DECLINE" that may not exist in its vocabulary (e.g.
    de.fraud's DECLINE_CLASS verdict is "BLOCK", not "DECLINE" — see
    risk_tiered_architecture.md's follow-up discussion on autonomous rule generation).

    ``target_class`` is one of the aggregator's universal buckets (APPROVE_CLASS /
    REFER_CLASS / DECLINE_CLASS, see core/aggregator.py) — every ruleset maps its own
    verdict vocabulary onto these via composite_class, so this is the one place that
    mapping is guaranteed to be authoritative for whichever ruleset is being targeted.

    ``prefer`` picks which candidate to use when a class has more than one verdict
    (e.g. device-financing's APPROVE_CLASS has both APPROVE and APPROVE_WITH_DEPOSIT):
    "least_severe" for a plain, unconditional effect (approve), "most_severe" for an
    escalation (decline/refer) — fail toward the stronger action within its own class,
    consistent with the rest of the platform's fail-toward-caution posture.

    Falls back to ``fallback`` (the old hardcoded literal) when no context is given or
    the ruleset's composite_class has no verdict in that bucket at all — never crashes,
    never returns nothing.
    """
    if not ruleset_context:
        return fallback
    candidates = _class_candidates(ruleset_context.get("composite_class") or {}, target_class)
    if not candidates:
        return fallback
    severity = ruleset_context.get("verdict_severity") or []
    candidates.sort(key=lambda v: severity.index(v) if v in severity else -1)
    return candidates[-1] if prefer == "most_severe" else candidates[0]


def _build_action(text: str, warnings: list[str], ruleset_context: Optional[dict] = None) -> tuple[str, dict]:
    """Return (phase, then-clause) from the action verbs in the statement."""
    if any(w in text for w in ("decline", "reject", "deny", "block", "hard stop", "hard-stop")):
        verdict = _pick_verdict(ruleset_context, "DECLINE_CLASS", "most_severe", "DECLINE")
        return "GATE", {"verdict": verdict, "terminal": True, "reason_codes": ["GENERATED_HARD_STOP"]}

    if any(w in text for w in ("refer", "manual review", "flag", "review")):
        verdict = _pick_verdict(ruleset_context, "REFER_CLASS", "most_severe", "REFER")
        return "GATE", {"verdict": verdict, "terminal": True, "reason_codes": ["GENERATED_REFER"]}

    deposit_match = re.search(r"(\d+)\s*%?\s*deposit", text) or re.search(r"deposit\s*of\s*(\d+)", text)
    if "deposit" in text and deposit_match:
        pct = int(deposit_match.group(1))
        # Deposit terms (deposit_pct) are device-financing-specific semantics, not a
        # generic concept every ruleset has an equivalent for — no vocabulary lookup
        # here, unlike the three branches above.
        return "OVERLAY", {
            "verdict": "APPROVE_WITH_DEPOSIT",
            "terms": {"deposit_pct": pct},
            "reason_codes": ["GENERATED_DEPOSIT_REQUIREMENT"],
        }

    if any(w in text for w in ("add", "points", "score delta", "subtract", "penal", "bonus")):
        # The delta sits right next to "points"/"add" — e.g. "add 15 points" (before) or
        # "add 15" (after) — so anchor on those tokens rather than the first number seen.
        delta_match = (
            re.search(r"(\d+)\s*points?", text)
            or re.search(r"(?:add|subtract|reduce|bonus|penal\w*|delta|by)\D*?(\d+)", text)
        )
        delta = int(delta_match.group(1)) if delta_match else 10
        if any(w in text for w in ("subtract", "penal", "reduce", "minus")):
            delta = -abs(delta)
        return "SCORING", {"score_delta": delta, "reason_codes": ["GENERATED_SCORE_DELTA"]}

    if "approve" in text:
        verdict = _pick_verdict(ruleset_context, "APPROVE_CLASS", "least_severe", "APPROVE")
        return "TERMS", {"verdict": verdict, "terminal": True, "reason_codes": ["GENERATED_APPROVE"]}

    warnings.append("no explicit action verb detected; defaulted to a REFER gate")
    verdict = _pick_verdict(ruleset_context, "REFER_CLASS", "most_severe", "REFER")
    return "GATE", {"verdict": verdict, "terminal": True, "reason_codes": ["GENERATED_REFER"]}


def _rule_id(text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:6].upper()
    return f"GEN-{digest}"


def _assemble(text: str, ruleset_context: Optional[dict] = None) -> dict:
    """Deterministic heuristic assembly -> {rule, mode, confidence, warnings}."""
    lowered = text.lower().strip()
    warnings: list[str] = []

    conditions = _build_conditions(lowered, warnings)
    phase, then = _build_action(lowered, warnings, ruleset_context)

    if conditions:
        when = conditions[0] if len(conditions) == 1 else {"all": conditions}
    else:
        when = {"all": []}
        warnings.append("no conditions were detected; rule matches unconditionally")

    rule = {
        "id": _rule_id(lowered),
        "phase": phase,
        "priority": _DEFAULT_PRIORITY[phase],
        "enabled": True,
        "when": when,
        "then": then,
    }

    # Confidence: a rough, honest signal for the UI — full marks only when we found both
    # a real condition and a comparator, no warnings raised.
    confidence = 0.9
    if warnings:
        confidence -= 0.2 * len(warnings)
    confidence = max(0.2, round(confidence, 2))

    return {"rule": rule, "mode": "heuristic", "confidence": confidence, "warnings": warnings}


def _validate(rule: dict, ruleset_context: Optional[dict] = None) -> list[str]:
    """Compile the candidate's condition tree with the production compiler. Returns a list
    of validation errors (empty == valid). We copy first so ``_compiled`` scratch keys
    never leak back into the returned rule."""
    errors: list[str] = []
    required = {"id", "phase", "when", "then"}
    missing = required - rule.keys()
    if missing:
        errors.append(f"missing required keys: {sorted(missing)}")
    if rule.get("phase") not in {"GATE", "SCORING", "TERMS", "OVERLAY"}:
        errors.append(f"invalid phase: {rule.get('phase')!r}")
    try:
        compile_condition(copy.deepcopy(rule["when"]))
    except Exception as exc:
        errors.append(f"condition does not compile: {exc}")

    # The `then` clause must carry an effect the evaluator actually reads — a `verdict`
    # (GATE/TERMS/OVERLAY) or a numeric `score_delta` (SCORING). Without this check an LLM
    # is free to invent its own action vocabulary (e.g. {"action": "decline"}), which
    # compiles and stores but silently does nothing at evaluation time.
    then = rule.get("then")
    if not isinstance(then, dict):
        errors.append("`then` must be an object")
    else:
        has_verdict = isinstance(then.get("verdict"), str) and bool(then["verdict"].strip())
        has_delta = isinstance(then.get("score_delta"), (int, float)) and not isinstance(
            then.get("score_delta"), bool
        )
        if not (has_verdict or has_delta):
            errors.append(
                "`then` has no engine-recognised effect: expected a 'verdict' "
                "(APPROVE / APPROVE_WITH_DEPOSIT / REFER / DECLINE) or a numeric 'score_delta'"
            )
        elif rule.get("phase") == "SCORING" and not has_delta:
            errors.append("a SCORING rule needs a numeric 'score_delta'")
        elif rule.get("phase") in {"GATE", "TERMS"} and not has_verdict:
            errors.append("a GATE/TERMS rule needs a 'verdict'")

        # A verdict that compiles and has an effect is still wrong if it's not a word
        # THIS ruleset recognises — every ruleset has its own vocabulary (de.fraud uses
        # BLOCK, not DECLINE) and RuleStore.add_rule's own lint would reject a mismatch
        # anyway (MISSING_COMPOSITE_CLASS); catching it here, when the target ruleset is
        # known, means an autonomously generated candidate with the wrong verdict word
        # gets rejected and re-attempted (via the heuristic fallback) at generation
        # time, not discovered later as a confusing 422 at publish/approval time.
        if has_verdict and ruleset_context and ruleset_context.get("composite_class"):
            valid_verdicts = set(ruleset_context["composite_class"].keys())
            if then["verdict"] not in valid_verdicts:
                errors.append(
                    f"verdict {then['verdict']!r} is not in this ruleset's vocabulary "
                    f"(expected one of {sorted(valid_verdicts)})"
                )
    return errors


_LLM_PROMPT = (
    "Convert the business policy below into a SINGLE JSON rule for a decision engine. "
    "Output ONLY JSON, no prose. Schema: "
    '{{"id": str, "phase": "GATE"|"SCORING"|"TERMS"|"OVERLAY", "priority": int, '
    '"enabled": true, "when": <condition>, "then": <action>}}. A <condition> is either '
    '{{"fact": str, "op": str, "value": any}} or {{"all"|"any"|"none": [<condition>...]}}. '
    "Valid ops: GREATER_THAN, GTE, LESS_THAN, LTE, EQUALS, NOT_EQUALS, BETWEEN, IS_TRUE, "
    "IS_FALSE, CONTAINS, STARTS_WITH, ENDS_WITH, MATCHES_REGEX, IS_NULL, IS_NOT_NULL, "
    "AGE_IN_YEARS_GTE. Available facts include order.financed_amount, order.total_amount, "
    "order.term_months, order.line_count, bureau.score, computed.risk_score, "
    "customer.tenure_months, customer.is_existing, account.days_past_due, "
    "customer.date_of_birth, identity.match_score.\n"
    # The <action> schema MUST be spelled out, or the model invents its own keys
    # (e.g. {{\"action\": \"decline\"}}) which the engine silently ignores.
    "An <action> (\"then\") is EXACTLY ONE of these two shapes — never invent other keys:\n"
    '  1. A verdict (use for phase GATE, TERMS or OVERLAY): '
    '{{"verdict": <one of the ruleset verdicts listed below>, "terminal": true, '
    '"reason_codes": [str], "terms": {{...}}}}  (terms is optional; for a deposit use '
    '"terms": {{"deposit_pct": 30}} only if a deposit-style verdict is in that list).\n'
    '  2. A score adjustment (use for phase SCORING): '
    '{{"score_delta": int, "reason_codes": [str]}}  (negative to penalise).\n'
    "Phase guidance: a hard decline/refer stop -> GATE; a points adjustment -> SCORING; "
    "an approval -> TERMS; a deposit requirement -> OVERLAY.\n"
    "RULESET VERDICTS — the verdict you output MUST be exactly one of these, verbatim; "
    "never use a generic word like \"DECLINE\" unless it is literally in this list: "
    "{verdict_vocab}\n"
    'Example — "Decline when the term months is over 36" (assume DECLINE is in this '
    'ruleset\'s verdict list): {{"id": "gen-decline-term", "phase": "GATE", "priority": 50, '
    '"enabled": true, "when": {{"fact": "order.term_months", "op": "GREATER_THAN", "value": 36}}, '
    '"then": {{"verdict": "DECLINE", "terminal": true, "reason_codes": ["LONG_TERM"]}}}}\n'
    "\nPOLICY: {text}\n\nJSON:"
)

_GENERIC_VERDICT_VOCAB = ["APPROVE", "APPROVE_WITH_DEPOSIT", "REFER", "DECLINE"]


def _verdict_vocab_string(ruleset_context: Optional[dict]) -> str:
    """The exact verdict list the LLM prompt is told it must use, verbatim, for
    whichever ruleset is being targeted — falls back to the old generic vocabulary
    when no ruleset context is known."""
    verdicts = sorted((ruleset_context or {}).get("composite_class") or {}) or _GENERIC_VERDICT_VOCAB
    return ", ".join(f'"{v}"' for v in verdicts)


def _generate_with_llm(text: str, settings: Any, ruleset_context: Optional[dict] = None) -> dict:
    import json

    from langchain_openai import ChatOpenAI  # lazy: optional dependency
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser

    verdict_vocab = _verdict_vocab_string(ruleset_context)

    prompt = ChatPromptTemplate.from_template(_LLM_PROMPT)
    model = ChatOpenAI(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        api_key=settings.openai_api_key,
        timeout=settings.llm_timeout_seconds,
    )
    raw = (prompt | model | StrOutputParser()).invoke({"text": text, "verdict_vocab": verdict_vocab}).strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    rule = json.loads(raw)
    rule.setdefault("id", _rule_id(text.lower()))
    rule.setdefault("enabled", True)
    rule.setdefault("priority", _DEFAULT_PRIORITY.get(rule.get("phase", "TERMS"), 250))
    return {"rule": rule, "mode": "llm", "confidence": 0.95, "warnings": []}


def generate_rule(text: str, settings: Any, ruleset_context: Optional[dict] = None) -> dict:
    """Return ``{rule, mode, confidence, warnings, valid, validation_errors, source_text}``.

    ``ruleset_context`` is ``{"composite_class": {...}, "verdict_severity": [...]}`` for
    the ruleset this rule is destined for — pass it whenever the target ruleset is known
    (the caller can get it from ``RuleStore.get_ruleset(ruleset_id)``) so the verdict
    word chosen is one this specific ruleset actually recognises, not a generic
    default. Omitting it preserves the old ruleset-agnostic behavior.

    Never raises: an LLM failure or an invalid LLM candidate both fall back to the
    deterministic heuristic, which is always structurally valid — and, given
    ``ruleset_context``, always picks a verdict from that ruleset's own vocabulary too.
    """
    result: Optional[dict] = None

    if getattr(settings, "llm_enabled", False):
        try:
            candidate = _generate_with_llm(text, settings, ruleset_context)
            if not _validate(candidate["rule"], ruleset_context):
                result = candidate
            else:
                logger.warning(
                    "llm.rule_invalid_fallback",
                    extra={"event": "llm.rule_invalid", "text": text},
                )
        except Exception as exc:
            logger.warning(
                "llm.rule_gen_failed_fallback",
                extra={"event": "llm.rule_gen_failed", "error": str(exc)},
            )

    if result is None:
        result = _assemble(text, ruleset_context)

    errors = _validate(result["rule"], ruleset_context)
    result["valid"] = not errors
    result["validation_errors"] = errors
    result["source_text"] = text
    return result
