"""MongoDB-backed ruleset version store with Fernet encryption and audit logging.

This is the authoritative rule source: the JSON rule engine (``app/orchestrator.py``)
reads exclusively from ``active_by_decision_type``. Nothing here knows about vector
search or embeddings — that lives one layer up, in the ``RuleStore`` facade
(``app/rule_store.py``), which writes to this repository first and only then indexes
the same rule into a ``VectorRuleIndex``.
"""
import copy
import datetime
import hashlib
import json
from typing import Optional
from pymongo.database import Database

from ..core.conditions import compile_condition
from ..encryption import encrypt_content, decrypt_content
from ..audit import log_audit

_BOOKKEEPING_FIELDS = {"version", "status", "content_hash", "published_at", "published_by"}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _content_hash(content: dict) -> str:
    hashable = {k: v for k, v in content.items() if k not in _BOOKKEEPING_FIELDS}
    payload = json.dumps(hashable, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()[:16]


class MongoRulesetRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        # A parallel, condition-compiled copy of the active rulesets, used only for
        # evaluation — compiled once at publish time (or load time), never touched again, so a request
        # never pays expression-parsing cost.
        self._compiled: dict[str, dict[int, dict]] = {}

    def _compile_and_cache(self, stored_content: dict) -> None:
        """Deep copy, compile conditions, and cache for evaluation."""
        compiled = copy.deepcopy(stored_content)
        for rule in compiled["rules"]:
            compile_condition(rule["when"])

        ruleset_id = compiled["ruleset_id"]
        version = compiled["version"]
        self._compiled.setdefault(ruleset_id, {})[version] = compiled

    # -- writes -----------------------------------------------------------------

    def create_version(self, ruleset_id: str, content: dict, published_by: str = "system") -> dict:
        decision_type = content["decision_type"]
        market = content.get("market")

        prior = self.get_active(ruleset_id)
        if prior is None:
            latest_meta = self.db.ruleset_versions.find_one(
                {"ruleset_id": ruleset_id},
                sort=[("version", -1)]
            )
            if latest_meta:
                prior = self.get_version(ruleset_id, latest_meta["version"])

        if prior:
            if content["decision_type"] != prior["decision_type"]:
                raise ValueError(
                    f"decision_type mismatch: {ruleset_id} is {prior['decision_type']!r}, "
                    f"got {content['decision_type']!r}"
                )
            if market != prior.get("market"):
                raise ValueError(f"market mismatch: {ruleset_id} is {prior.get('market')!r}, got {market!r}")
        else:
            self._check_no_collision(ruleset_id, market, decision_type)

        content_hash = _content_hash(content)

        latest = self.db.ruleset_versions.find_one(
            {"ruleset_id": ruleset_id},
            sort=[("version", -1)]
        )
        next_version = (latest["version"] + 1) if latest else 1

        self.db.ruleset_versions.update_many(
            {"ruleset_id": ruleset_id, "is_active": True},
            {"$set": {"is_active": False}}
        )

        stored = {
            **content,
            "ruleset_id": ruleset_id,
            "version": next_version,
            "status": "PUBLISHED",
            "content_hash": content_hash,
            "published_at": _now_iso(),
            "published_by": published_by,
        }

        doc = {
            "ruleset_id": ruleset_id,
            "version": next_version,
            "status": "PUBLISHED",
            "decision_type": decision_type,
            "market": market,
            "is_active": True,
            "deprecated_at": self.deprecated_at(ruleset_id),
            "content_encrypted": encrypt_content(stored),
            "content_hash": content_hash,
            "published_at": _now_iso(),
            "published_by": published_by,
            "created_at": datetime.datetime.now(datetime.timezone.utc)
        }

        self.db.ruleset_versions.insert_one(doc)

        log_audit(
            db=self.db,
            ruleset_id=ruleset_id,
            action="CREATE_VERSION",
            actor=published_by,
            version=next_version,
            details={"content_hash": content_hash, "previous_active_version": prior["version"] if prior else None}
        )

        self._compile_and_cache(stored)
        return stored

    def _check_no_collision(self, ruleset_id: str, market: Optional[str], decision_type: str) -> None:
        query = {"is_active": True, "market": market, "decision_type": decision_type}
        conflicts = list(self.db.ruleset_versions.find(query))
        for conflict in conflicts:
            if conflict["ruleset_id"] != ruleset_id:
                raise ValueError(
                    f"another ruleset ({conflict['ruleset_id']!r}) is already active for "
                    f"market={market!r}, decision_type={decision_type!r}"
                )

    def rollback(self, ruleset_id: str) -> dict:
        active = self.get_active(ruleset_id)
        if not active:
            raise ValueError(f"unknown ruleset_id: {ruleset_id!r} or no active version")

        current_version = active["version"]

        earlier = self.db.ruleset_versions.find_one(
            {"ruleset_id": ruleset_id, "version": {"$lt": current_version}},
            sort=[("version", -1)]
        )

        if not earlier:
            raise ValueError(f"no earlier version to roll back to for {ruleset_id!r}")

        target_version = earlier["version"]

        self.db.ruleset_versions.update_many(
            {"ruleset_id": ruleset_id},
            {"$set": {"is_active": False}}
        )

        self.db.ruleset_versions.update_one(
            {"ruleset_id": ruleset_id, "version": target_version},
            {"$set": {"is_active": True}}
        )

        log_audit(
            db=self.db,
            ruleset_id=ruleset_id,
            action="ROLLBACK",
            actor="system",
            version=target_version,
            details={"previous_active_version": current_version}
        )

        rolled_back = self.get_version(ruleset_id, target_version)
        self._compile_and_cache(rolled_back)
        return rolled_back

    def deprecate(self, ruleset_id: str) -> None:
        if not self.db.ruleset_versions.find_one({"ruleset_id": ruleset_id}):
            raise ValueError(f"unknown ruleset_id: {ruleset_id!r}")

        ts = _now_iso()
        self.db.ruleset_versions.update_many(
            {"ruleset_id": ruleset_id},
            {"$set": {"deprecated_at": ts}}
        )

        log_audit(self.db, ruleset_id, "DEPRECATE", "system", None)

    def reactivate(self, ruleset_id: str) -> None:
        if not self.db.ruleset_versions.find_one({"ruleset_id": ruleset_id}):
            raise ValueError(f"unknown ruleset_id: {ruleset_id!r}")

        self.db.ruleset_versions.update_many(
            {"ruleset_id": ruleset_id},
            {"$set": {"deprecated_at": None}}
        )

        log_audit(self.db, ruleset_id, "REACTIVATE", "system", None)

    # -- reads ------------------------------------------------------------------

    def get_version(self, ruleset_id: str, version: int) -> dict:
        doc = self.db.ruleset_versions.find_one({"ruleset_id": ruleset_id, "version": version})
        if not doc:
            raise ValueError(f"no such version: {ruleset_id!r} v{version}")
        return decrypt_content(doc["content_encrypted"])

    def get_active(self, ruleset_id: str) -> Optional[dict]:
        doc = self.db.ruleset_versions.find_one({"ruleset_id": ruleset_id, "is_active": True})
        if not doc:
            return None
        return decrypt_content(doc["content_encrypted"])

    def list_versions(self, ruleset_id: str) -> list[dict]:
        cursor = self.db.ruleset_versions.find(
            {"ruleset_id": ruleset_id},
            sort=[("version", 1)]
        )
        versions = []
        for doc in cursor:
            versions.append(decrypt_content(doc["content_encrypted"]))
        return versions

    def list_ruleset_ids(self) -> list[str]:
        return sorted(self.db.ruleset_versions.distinct("ruleset_id"))

    def is_deprecated(self, ruleset_id: str) -> bool:
        doc = self.db.ruleset_versions.find_one({"ruleset_id": ruleset_id})
        if not doc:
            return False
        return doc.get("deprecated_at") is not None

    def deprecated_at(self, ruleset_id: str) -> Optional[str]:
        doc = self.db.ruleset_versions.find_one({"ruleset_id": ruleset_id})
        if not doc:
            return None
        return doc.get("deprecated_at")

    # -- what the orchestrator and the API actually consume ----------------------

    def active_by_decision_type(self, market: Optional[str] = None) -> dict[str, dict]:
        result: dict[str, dict] = {}
        query = {"is_active": True, "deprecated_at": None}
        if market is not None:
            query["market"] = {"$in": [market, None]}

        docs = self.db.ruleset_versions.find(query)
        for doc in docs:
            ruleset_id = doc["ruleset_id"]
            version = doc["version"]

            if ruleset_id not in self._compiled or version not in self._compiled[ruleset_id]:
                stored = decrypt_content(doc["content_encrypted"])
                self._compile_and_cache(stored)

            compiled = self._compiled[ruleset_id][version]
            result[compiled["decision_type"]] = compiled

        return result

    def deprecated_ruleset_ids(self, market: Optional[str] = None) -> list[str]:
        query = {"is_active": True, "deprecated_at": {"$ne": None}}
        if market is not None:
            query["market"] = {"$in": [market, None]}

        docs = self.db.ruleset_versions.find(query)
        return sorted([doc["ruleset_id"] for doc in docs])

    # -- bootstrap ----------------------------------------------------------------

    @classmethod
    def bootstrap_from_db(cls, db: Database) -> "MongoRulesetRepository":
        store = cls(db)
        store.active_by_decision_type()
        return store
