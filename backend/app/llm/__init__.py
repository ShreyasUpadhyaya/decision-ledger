"""LLM enhancement layer (spec §14).

Capabilities, each with a deterministic offline fallback so the platform is fully
functional with no API key and no network:

    * ``explainer``       — turn a raw decision trace into a human-readable paragraph
    * ``rule_generator``  — turn plain-English policy into a validated JSON rule
    * ``recommender``     — turn similar-rule vector search hits into an
      APPROVE/REFER/DECLINE recommendation when the JSON engine finds no match
    * ``risk_analyzer``   — classify a generated rule's blast radius (LOW/MEDIUM/HIGH)
      for the autonomous anomaly-trigger loop (risk_tiered_architecture.md §3)

Rule *storage* (authoritative JSON + vector index) lives in :mod:`app.rule_store`, not
here; that store's vector index is used only for fallback search, never the source of
truth for evaluation.

Heavy optional dependencies (``langchain``) are imported lazily inside the functions that
need them, so ``import app.llm`` never fails on a machine with only the core stack.
"""
from .explainer import explain_decision
from .rule_generator import generate_rule
from .recommender import recommend_from_similar_rules
from .risk_analyzer import assess_risk

__all__ = ["explain_decision", "generate_rule", "recommend_from_similar_rules", "assess_risk"]
