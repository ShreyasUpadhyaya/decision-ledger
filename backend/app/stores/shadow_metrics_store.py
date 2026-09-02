"""Durable store for shadow-mode rule hits (risk_tiered_architecture.md §4 Tier 2).

`app.state.decisions_store` (see main.py) is an in-process dict, wiped on restart and
invisible across worker processes — it can't back a "this shadow rule would have
declined N transactions today" dashboard view. This collection is the thing that view
actually reads from: every firing shadow rule, across every decision, lands here.
"""
import datetime
from typing import Optional

from pymongo.database import Database


class ShadowMetricsStore:
    def __init__(self, db: Database) -> None:
        self._collection = db["shadow_metrics"]

    def record_hits(self, request_id: str, hits: list[dict]) -> None:
        """``hits`` is ``result["shadow_metrics"]`` straight off a decision — one doc
        per firing shadow rule, already carrying ruleset_id/decision_type/rule_id/
        would_have_* (see app/orchestrator.py)."""
        if not hits:
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        docs = [{**hit, "request_id": request_id, "recorded_at": now} for hit in hits]
        self._collection.insert_many(docs)

    def aggregate_by_rule(self, ruleset_id: str) -> list[dict]:
        """Per-rule hit counts plus a would_have_yielded breakdown, for one ruleset —
        what the admin dashboard's shadow-rule review card reads."""
        pipeline = [
            {"$match": {"ruleset_id": ruleset_id}},
            {
                "$group": {
                    "_id": {"rule_id": "$rule_id", "would_have_yielded": "$would_have_yielded"},
                    "count": {"$sum": 1},
                }
            },
        ]
        by_rule: dict[str, dict] = {}
        for row in self._collection.aggregate(pipeline):
            rule_id = row["_id"]["rule_id"]
            verdict = row["_id"]["would_have_yielded"]
            entry = by_rule.setdefault(rule_id, {"rule_id": rule_id, "total_hits": 0, "by_verdict": {}})
            entry["total_hits"] += row["count"]
            if verdict is not None:
                entry["by_verdict"][verdict] = row["count"]
        return list(by_rule.values())

    def list_recent(self, ruleset_id: str, rule_id: Optional[str] = None, limit: int = 50) -> list[dict]:
        query: dict = {"ruleset_id": ruleset_id}
        if rule_id:
            query["rule_id"] = rule_id
        cursor = self._collection.find(query, {"_id": 0}).sort("recorded_at", -1).limit(limit)
        return list(cursor)
