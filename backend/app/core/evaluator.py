"""The pure four-phase evaluator (spec §5.1, §5.3).

`evaluate_ruleset(context, ruleset, requested_at) -> (decision, trace, shadow_hits)`
touches no database, no network, no live clock — `requested_at` is part of the frozen
context, not `datetime.now()`. Everything this needs is already resolved before it's
called.

A rule with `"is_shadow": true` (risk_tiered_architecture.md §4 Tier 2) still has its
condition evaluated for real, but its effect is never allowed to reach the real
decision: it can't terminate a GATE, can't move `score_total`, can't win the TERMS slot,
and can't tighten a live verdict/terms in OVERLAY. Every phase records what a firing
shadow rule *would* have done into `shadow_hits` instead — that's the whole mechanism
Shadow Mode needs; nothing above this function has to know a shadow rule exists.
"""
import datetime
from typing import Any, Optional

from .conditions import LeafResult, evaluate_condition, resolve_fact


def _outcome_entry(rule: dict, result: LeafResult, **extra: Any) -> dict:
    entry = {"rule_id": rule["id"], "phase": rule["phase"], "outcome": result.value}
    entry.update(extra)
    return entry


def _skip_entry(rule: dict, skipped_by: str) -> dict:
    return {"rule_id": rule["id"], "phase": rule["phase"], "outcome": "SKIPPED", "skipped_by": skipped_by}


def _not_evaluated_entry(rule: dict, matched_by: str) -> dict:
    return {
        "rule_id": rule["id"],
        "phase": rule["phase"],
        "outcome": "NOT_EVALUATED",
        "reason": f"{matched_by} matched first at a lower priority number; TERMS is first-match-wins",
    }


def _shadow_hit(rule: dict) -> dict:
    """A GATE/SCORING/TERMS shadow rule's `then` clause is used as-is — those phases
    apply a matching rule's effect directly, so "what it would have done" is just its
    own `then`. OVERLAY is different (see `_shadow_overlay_hit`): its effect depends on
    whatever the live rules already decided, so it needs its own counterfactual."""
    then = rule["then"]
    hit: dict = {"rule_id": rule["id"], "phase": rule["phase"]}
    if "verdict" in then:
        hit["would_have_yielded"] = then["verdict"]
    if "score_delta" in then:
        hit["would_have_score_delta"] = then["score_delta"]
    if "terms" in then:
        hit["would_have_terms"] = then["terms"]
    if then.get("reason_codes"):
        hit["reason_codes"] = list(then["reason_codes"])
    return hit


def _shadow_overlay_hit(rule: dict, verdict: str, terms: dict, severity: list) -> Optional[dict]:
    """Same tighten-only math the real OVERLAY branch applies, run against the current
    (real) verdict/terms without mutating them — the counterfactual for "if this rule
    had been live, given everything decided above it so far". Mirrors the real branch's
    own rule: only counts as a hit if it would actually have tightened something, not
    merely matched (see the OVERLAY loop's `changed` flag below)."""
    then = rule["then"]
    hit: dict = {"rule_id": rule["id"], "phase": "OVERLAY"}
    changed = False
    if "verdict" in then and severity:
        tightened = _tighten_verdict(verdict, then["verdict"], severity)
        if tightened != verdict:
            hit["would_have_yielded"] = tightened
            changed = True
    if "terms" in then:
        would_cap = {}
        for key, cap in then["terms"].items():
            if key in terms and isinstance(cap, (int, float)) and isinstance(terms[key], (int, float)) and cap < terms[key]:
                would_cap[key] = cap
        if would_cap:
            hit["would_have_terms"] = would_cap
            changed = True
    if not changed:
        return None
    if then.get("reason_codes"):
        hit["reason_codes"] = list(then["reason_codes"])
    return hit


def _set_fact(context: dict, dotted_path: str, value: Any) -> None:
    parts = dotted_path.split(".")
    node = context
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _dedupe(items: list) -> list:
    seen: set = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _tighten_verdict(current: str, candidate: str, severity: list) -> str:
    if candidate not in severity:
        return current
    cur_idx = severity.index(current) if current in severity else -1
    cand_idx = severity.index(candidate)
    return candidate if cand_idx > cur_idx else current


def _rules_by_phase(ruleset: dict) -> dict:
    by_phase: dict = {"GATE": [], "SCORING": [], "TERMS": [], "OVERLAY": []}
    for rule in ruleset["rules"]:
        if rule.get("enabled", True):
            by_phase[rule["phase"]].append(rule)
    for phase_rules in by_phase.values():
        phase_rules.sort(key=lambda r: r["priority"])
    return by_phase


def evaluate_ruleset(context: dict, ruleset: dict, requested_at: datetime.datetime) -> tuple[dict, list, list]:
    by_phase = _rules_by_phase(ruleset)
    trace: list = []
    shadow_hits: list = []

    # Phase 1 — GATES: first match wins, terminal, short-circuits everything below.
    # A shadow gate that fires never sets terminal_gate, so it can neither short-circuit
    # this ruleset nor block a later (real) gate from still winning normally.
    terminal_gate: Optional[dict] = None
    for rule in by_phase["GATE"]:
        if terminal_gate is not None:
            trace.append(_skip_entry(rule, terminal_gate["id"]))
            continue
        result = evaluate_condition(rule["when"], context, requested_at)
        is_shadow = bool(rule.get("is_shadow"))
        trace.append(_outcome_entry(rule, result, **({"shadow": True} if is_shadow else {})))
        # A gate can only short-circuit if it actually carries a verdict. A malformed rule
        # missing one is treated as a no-op rather than crashing later on then["verdict"].
        if result == LeafResult.TRUE and "verdict" in rule["then"]:
            if is_shadow:
                shadow_hits.append(_shadow_hit(rule))
            else:
                terminal_gate = rule

    if terminal_gate is not None:
        for phase in ("SCORING", "TERMS", "OVERLAY"):
            for rule in by_phase[phase]:
                trace.append(_skip_entry(rule, terminal_gate["id"]))
        then = terminal_gate["then"]
        decision = {
            "verdict": then["verdict"],
            "pre_overlay_verdict": None,
            "terms": then.get("terms"),
            "reason_codes": list(then.get("reason_codes", [])),
            "matched_rules": [terminal_gate["id"]],
            "score": None,
        }
        return decision, trace, shadow_hits

    # Phase 2 — SCORING: every matching rule fires, deltas accumulate. A shadow rule's
    # delta is recorded but never added to score_total — it must not move which TERMS
    # rule wins below, since that's the real effect this phase exists to produce.
    score_total = 0
    scoring_reason_codes: list = []
    scoring_matched: list = []
    for rule in by_phase["SCORING"]:
        result = evaluate_condition(rule["when"], context, requested_at)
        then = rule["then"]
        is_shadow = bool(rule.get("is_shadow"))
        extra = {"shadow": True} if is_shadow else {}
        trace.append(_outcome_entry(rule, result, score_delta=then.get("score_delta", 0) if result == LeafResult.TRUE else None, **extra))
        if result == LeafResult.TRUE:
            if is_shadow:
                shadow_hits.append(_shadow_hit(rule))
            else:
                score_total += then.get("score_delta", 0)
                scoring_reason_codes += then.get("reason_codes", [])
                scoring_matched.append(rule["id"])

    score_fact = ruleset.get("score_fact")
    if score_fact:
        _set_fact(context, score_fact, score_total)

    # Phase 3 — TERMS: first match wins on the accumulated score. A shadow rule that
    # matches never takes the slot — evaluation just keeps going as if it weren't
    # there, exactly like a shadow GATE not blocking a later real gate.
    matched_terms_rule: Optional[dict] = None
    for rule in by_phase["TERMS"]:
        if matched_terms_rule is not None:
            trace.append(_not_evaluated_entry(rule, matched_terms_rule["id"]))
            continue
        result = evaluate_condition(rule["when"], context, requested_at)
        is_shadow = bool(rule.get("is_shadow"))
        trace.append(_outcome_entry(rule, result, **({"shadow": True} if is_shadow else {})))
        # Same guard as the gate phase: only a verdict-bearing rule can win the TERMS slot.
        if result == LeafResult.TRUE and "verdict" in rule["then"]:
            if is_shadow:
                shadow_hits.append(_shadow_hit(rule))
            else:
                matched_terms_rule = rule

    if matched_terms_rule is not None:
        then = matched_terms_rule["then"]
        verdict = then["verdict"]
        terms = dict(then.get("terms") or {})
        reason_codes = scoring_reason_codes + list(then.get("reason_codes", []))
        matched_rules = scoring_matched + [matched_terms_rule["id"]]
    else:
        default = ruleset["default_outcome"]
        verdict = default["verdict"]
        terms = {}
        reason_codes = scoring_reason_codes + list(default.get("reason_codes", []))
        matched_rules = list(scoring_matched)

    # A verdict's "default terms" (e.g. a plain APPROVE means "as requested") must be
    # resolved before overlays run, or a cap has nothing concrete to tighten (spec §5.3).
    # This is data-driven per ruleset, not a hardcoded engine assumption.
    # `.get(key, {})` alone would not catch a key that's *present* with value None —
    # which is exactly what a client omitting this optional field produces via the
    # ruleset-versions API (Pydantic fills the unset field with its None default,
    # rather than leaving the key absent).
    defaults_by_verdict = ruleset.get("default_terms_by_verdict") or {}
    if not terms and verdict in defaults_by_verdict:
        for key, fact_path in defaults_by_verdict[verdict].items():
            value = resolve_fact(context, fact_path)
            if value is not None:
                terms[key] = value

    pre_overlay_verdict = verdict
    severity = ruleset.get("verdict_severity", [])

    # Phase 4 — OVERLAYS: every matching rule applies, in priority order, tighten-only.
    # An overlay only counts as having "matched" (contributes a reason code and appears
    # in matched_rules) if it actually tightened something — the trace still records
    # every TRUE condition regardless, so "matched but had no effect" stays visible
    # there, but reason_codes/matched_rules would otherwise fill up with e.g. a deposit
    # cap on a decision that was never going to carry a deposit.
    for rule in by_phase["OVERLAY"]:
        result = evaluate_condition(rule["when"], context, requested_at)
        is_shadow = bool(rule.get("is_shadow"))
        trace.append(_outcome_entry(rule, result, **({"shadow": True} if is_shadow else {})))
        if result != LeafResult.TRUE:
            continue
        if is_shadow:
            # Counterfactual against the real verdict/terms as decided so far — never
            # mutates them. See _shadow_overlay_hit's docstring for why this can't just
            # reuse _shadow_hit like the other three phases.
            hit = _shadow_overlay_hit(rule, verdict, terms, severity)
            if hit is not None:
                shadow_hits.append(hit)
            continue
        then = rule["then"]
        changed = False
        if "verdict" in then and severity:
            tightened = _tighten_verdict(verdict, then["verdict"], severity)
            if tightened != verdict:
                verdict = tightened
                changed = True
        if "terms" in then:
            for key, cap in then["terms"].items():
                # tighten-only: a cap can only shrink a field that already exists.
                # Injecting a field from nowhere (e.g. a deposit cap onto a decision
                # that never had a deposit) would fabricate a term, not tighten one.
                if key in terms and isinstance(cap, (int, float)) and isinstance(terms[key], (int, float)) and cap < terms[key]:
                    terms[key] = cap
                    changed = True
        if changed:
            reason_codes += list(then.get("reason_codes", []))
            matched_rules.append(rule["id"])

    decision = {
        "verdict": verdict,
        "pre_overlay_verdict": pre_overlay_verdict if pre_overlay_verdict != verdict else None,
        "terms": terms or None,
        "reason_codes": _dedupe(reason_codes),
        "matched_rules": matched_rules,
        "score": score_total if score_fact else None,
    }
    return decision, trace, shadow_hits
