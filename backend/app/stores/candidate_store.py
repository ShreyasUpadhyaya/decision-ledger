"""Tier 3 queue (risk_tiered_architecture.md §4): a HIGH-risk autonomously generated
rule never reaches ``RuleStore`` — it sits here as ``PENDING_REVIEW`` until an admin
approves it (spliced into the active ruleset via ``RuleStore.add_rule``, live) or
discards it (marked ``DISCARDED``, never touches evaluation at all).

Modeled directly on ``AsyncDecisionStore`` — a thin wrapper around one Mongo collection,
no shared interface with ``RulesetRepository``/``VectorRuleIndex`` because this data is
pre-authoritative: nothing here is ever read by the evaluator.
"""
import datetime
import uuid
from typing import Optional

from pymongo.database import Database

from ..logging_config import get_logger

logger = get_logger("decision_ledger.candidate_rules")


class CandidateRuleStore:
    def __init__(self, db: Database) -> None:
        self._collection = db["candidate_rules"]

    def create(self, ruleset_id: str, rule: dict, risk_assessment: dict, source_text: Optional[str] = None) -> dict:
        candidate_id = f"cand_{uuid.uuid4().hex[:12]}"
        now = datetime.datetime.now(datetime.timezone.utc)
        doc = {
            "candidate_id": candidate_id,
            "ruleset_id": ruleset_id,
            "rule": rule,
            "risk_level": risk_assessment.get("risk_level"),
            "reasoning": risk_assessment.get("reasoning"),
            "source_text": source_text,
            "status": "PENDING_REVIEW",
            "created_at": now,
            "updated_at": now,
        }
        self._collection.insert_one(doc)
        logger.info(
            "candidate_rule.created",
            extra={"event": "candidate_rule.created", "candidate_id": candidate_id, "ruleset_id": ruleset_id},
        )
        doc.pop("_id", None)
        return doc

    def get(self, candidate_id: str) -> Optional[dict]:
        return self._collection.find_one({"candidate_id": candidate_id}, {"_id": 0})

    def list(self, status: Optional[str] = None, limit: int = 50) -> list[dict]:
        query: dict = {"status": status} if status else {}
        cursor = self._collection.find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
        return list(cursor)

    def update_status(self, candidate_id: str, status: str) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        self._collection.update_one({"candidate_id": candidate_id}, {"$set": {"status": status, "updated_at": now}})

    def exists_pending(self, ruleset_id: str, rule_id: str) -> bool:
        """The anomaly trigger's dedup check: a HIGH-risk rule already queued for the
        same (ruleset, rule id) must not be re-queued on every subsequent breach while
        it's still awaiting review — see app/anomaly.py."""
        return (
            self._collection.find_one({"ruleset_id": ruleset_id, "rule.id": rule_id, "status": "PENDING_REVIEW"})
            is not None
        )
