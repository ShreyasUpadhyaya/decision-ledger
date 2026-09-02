"""The two seams the merged architecture is built around.

``RuleStore`` (``app/rule_store.py``) is a facade over exactly one
``RulesetRepository`` (the authoritative, versioned JSON store the evaluator reads from)
and exactly one ``VectorRuleIndex`` (a fallback-only semantic index). Both are
``typing.Protocol``s rather than ABCs — the Mongo-backed implementations already satisfy
these shapes structurally, and a test double only has to match the shape, not inherit
from anything.
"""
from typing import Optional, Protocol


class RulesetRepository(Protocol):
    """Authoritative, versioned, audited storage for ruleset envelopes. This is the
    single source of truth the JSON rule engine evaluates against."""

    def create_version(self, ruleset_id: str, content: dict, published_by: str = "system") -> dict:
        ...

    def get_version(self, ruleset_id: str, version: int) -> dict:
        ...

    def get_active(self, ruleset_id: str) -> Optional[dict]:
        ...

    def list_versions(self, ruleset_id: str) -> list[dict]:
        ...

    def list_ruleset_ids(self) -> list[str]:
        ...

    def is_deprecated(self, ruleset_id: str) -> bool:
        ...

    def deprecated_at(self, ruleset_id: str) -> Optional[str]:
        ...

    def active_by_decision_type(self, market: Optional[str] = None) -> dict[str, dict]:
        ...

    def deprecated_ruleset_ids(self, market: Optional[str] = None) -> list[str]:
        ...

    def rollback(self, ruleset_id: str) -> dict:
        ...

    def deprecate(self, ruleset_id: str) -> None:
        ...

    def reactivate(self, ruleset_id: str) -> None:
        ...


class VectorRuleIndex(Protocol):
    """A secondary, fallback-only semantic index over rules. Never authoritative: every
    write it receives originates from a ``RulesetRepository`` write, never the reverse."""

    kind: str

    def put_rule(
        self,
        ruleset_id: str,
        rule: dict,
        decision_type: str,
        ruleset_version: int,
        status: str = "ACTIVE",
    ) -> None:
        ...

    def delete_rule(self, ruleset_id: str, rule_id: str) -> bool:
        ...

    def search(self, query: str, k: int) -> list[dict]:
        ...

    def rule_count(self) -> int:
        ...
