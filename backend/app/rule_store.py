"""``RuleStore`` — the public facade the rest of the app talks to.

This is the "RuleStore abstraction" the platform has always had, but it now composes two
independent, swappable pieces (see ``app/stores/interfaces.py``) instead of owning
storage itself:

    * a ``RulesetRepository`` — versioned, Fernet-encrypted, audited MongoDB storage.
      **Authoritative.** The orchestrator reads only from here (`active_by_decision_type`).
    * a ``VectorRuleIndex`` — embeddings + metadata + the original rule JSON, used only
      for semantic search / the decision service's no-match fallback. Never authoritative.

Every write goes through this facade so both stores move together: `add_rule` (and the
ruleset-version endpoints, via `create_ruleset`) write the authoritative version first,
then index the same rule(s) into the vector store — one call, both stores updated.
"""
from typing import Any, Optional

from .core.linter import lint_ruleset
from .db import get_database
from .stores.ruleset_repository import MongoRulesetRepository
from .stores.vector_index import build_vector_index
from .logging_config import get_logger

logger = get_logger(__name__)

_BOOKKEEPING_FIELDS = {"ruleset_id", "version", "status", "content_hash", "published_at", "published_by"}


def _strip_bookkeeping(content: dict) -> dict:
    return {k: v for k, v in content.items() if k not in _BOOKKEEPING_FIELDS}


class RuleStore:
    """Source of truth for rules: authoritative storage + a fallback semantic index."""

    def __init__(self, settings: Any, repository: Optional[Any] = None, vector_index: Optional[Any] = None) -> None:
        self._repo = repository if repository is not None else MongoRulesetRepository(
            get_database(settings.mongodb_uri, settings.mongodb_database)
        )
        self._vector = vector_index if vector_index is not None else build_vector_index(settings)

    @property
    def backend(self) -> str:
        return self._vector.kind

    # --- seeding / whole-ruleset writes --------------------------------------
    def create_ruleset(self, ruleset_id: str, content: dict, published_by: str = "system") -> dict:
        """Publish a new ruleset version (first version for a brand-new ruleset_id, or a
        new version of an existing one) and index every one of its rules into the vector
        store. Used by ``seed.py`` and the ``POST /v1/rulesets/{id}/versions`` route."""
        lint_result = lint_ruleset({**content, "ruleset_id": ruleset_id})
        if not lint_result["passed"]:
            raise ValueError(lint_result["errors"])

        stored = self._repo.create_version(ruleset_id, content, published_by=published_by)
        for rule in stored["rules"]:
            self._vector.put_rule(ruleset_id, rule, stored["decision_type"], stored["version"])
        logger.info(
            "rulestore.ruleset_published",
            extra={
                "event": "rulestore.ruleset_published",
                "ruleset_id": ruleset_id,
                "version": stored["version"],
                "rules": len(stored["rules"]),
            },
        )
        return stored

    def seed_if_empty(self, directory) -> dict:
        from .seed import seed_from_directory

        return seed_from_directory(self, directory)

    # --- reads (for evaluation + listing) ------------------------------------
    def ruleset_ids(self) -> list[str]:
        return self._repo.list_ruleset_ids()

    def active_by_decision_type(self, market: Optional[str] = None) -> dict[str, dict]:
        return self._repo.active_by_decision_type(market)

    def all_rulesets(self) -> dict[str, dict]:
        """Active content for every known ruleset, keyed by ruleset_id."""
        result = {}
        for rid in self.ruleset_ids():
            active = self._repo.get_active(rid)
            if active is not None:
                result[rid] = active
        return result

    def get_ruleset(self, ruleset_id: str) -> dict:
        active = self._repo.get_active(ruleset_id)
        if active is None:
            raise KeyError(f"unknown ruleset: {ruleset_id}")
        return active

    def list_rules(self, ruleset_id: str) -> list[dict]:
        return self.get_ruleset(ruleset_id)["rules"]

    def list_versions(self, ruleset_id: str) -> list[dict]:
        return self._repo.list_versions(ruleset_id)

    def get_version(self, ruleset_id: str, version: int) -> dict:
        return self._repo.get_version(ruleset_id, version)

    def deprecated_ruleset_ids(self, market: Optional[str] = None) -> list[str]:
        return self._repo.deprecated_ruleset_ids(market)

    def deprecated_at(self, ruleset_id: str) -> Optional[str]:
        return self._repo.deprecated_at(ruleset_id)

    # --- rule-level writes (dual-write: authoritative store, then vector index) ----
    def add_rule(self, ruleset_id: str, rule: dict, actor: str = "system") -> dict:
        """Splice one rule into the ruleset's current content, publish it as a new
        (audited) version, and index it into the vector store. One call, both stores
        updated — this is an upsert by rule id, so it doubles as add and update."""
        content = self._repo.get_active(ruleset_id)
        if content is None:
            raise KeyError(f"unknown ruleset: {ruleset_id}")

        rules = [r for r in content["rules"] if r["id"] != rule["id"]]
        rules.append(rule)
        new_content = {**_strip_bookkeeping(content), "rules": rules}

        lint_result = lint_ruleset({**new_content, "ruleset_id": ruleset_id})
        if not lint_result["passed"]:
            raise ValueError(lint_result["errors"])

        stored = self._repo.create_version(ruleset_id, new_content, published_by=actor)
        self._vector.put_rule(ruleset_id, rule, stored["decision_type"], stored["version"])
        logger.info(
            "rulestore.rule_added",
            extra={"event": "rulestore.rule_added", "ruleset_id": ruleset_id, "rule_id": rule.get("id"), "version": stored["version"]},
        )
        return rule

    update_rule = add_rule

    def publish_shadow_rule(self, ruleset_id: str, rule_id: str, actor: str = "system") -> dict:
        """Tier 2 -> live (risk_tiered_architecture.md §4): flip ``is_shadow`` off and
        re-publish through the same upsert-by-id path ``add_rule`` already uses. The
        rule's condition/effect are untouched — only the flag moves, minting a new
        audited version like any other rule write."""
        content = self._repo.get_active(ruleset_id)
        if content is None:
            raise KeyError(f"unknown ruleset: {ruleset_id}")
        rule = next((r for r in content["rules"] if r["id"] == rule_id), None)
        if rule is None:
            raise KeyError(f"unknown rule: {rule_id}")
        published = {**rule, "is_shadow": False}
        return self.add_rule(ruleset_id, published, actor=actor)

    def delete_rule(self, ruleset_id: str, rule_id: str, actor: str = "system") -> bool:
        content = self._repo.get_active(ruleset_id)
        if content is None:
            raise KeyError(f"unknown ruleset: {ruleset_id}")

        rules = [r for r in content["rules"] if r["id"] != rule_id]
        if len(rules) == len(content["rules"]):
            return False

        new_content = {**_strip_bookkeeping(content), "rules": rules}
        self._repo.create_version(ruleset_id, new_content, published_by=actor)
        self._vector.delete_rule(ruleset_id, rule_id)
        logger.info("rulestore.rule_deleted", extra={"event": "rulestore.rule_deleted", "ruleset_id": ruleset_id, "rule_id": rule_id})
        return True

    # --- ruleset lifecycle (authoritative store only; vector docs are left in
    # place — search is a fallback aid, not a decision authority, so a deprecated
    # ruleset's rules remaining searchable is harmless) ----------------------------
    def rollback(self, ruleset_id: str) -> dict:
        # Re-indexes every rule in the version being rolled back to. Known gap: a rule
        # id present in the version being rolled *away from* but absent from the
        # rolled-back-to version stays in the vector index until it's next
        # added/deleted explicitly — acceptable since the vector index is a search
        # convenience, never a decision authority.
        stored = self._repo.rollback(ruleset_id)
        for rule in stored["rules"]:
            self._vector.put_rule(ruleset_id, rule, stored["decision_type"], stored["version"])
        return stored

    def deprecate(self, ruleset_id: str) -> None:
        self._repo.deprecate(ruleset_id)

    def reactivate(self, ruleset_id: str) -> None:
        self._repo.reactivate(ruleset_id)

    # --- search --------------------------------------------------------------
    def search(self, query: str, k: int = 6) -> list[dict]:
        return self._vector.search(query, k)

    def stats(self) -> dict:
        return {"backend": self.backend, "rulesets": len(self.ruleset_ids()), "rules": self._vector.rule_count()}
