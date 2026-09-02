"""The Anomaly Tracker (risk_tiered_architecture.md §2): a durable sliding-window
velocity counter the autonomous trigger loop watches for spikes (e.g. > 10 declines
from one ip_country in 5 minutes).

Mongo-backed rather than an in-process counter deliberately: `app.state.decisions_store`
(see main.py) already shows what an in-process dict costs here — it's invisible across
worker processes and wiped on restart, both of which would silently under-count a real
spike in any deployment with more than one process. A TTL index on `at` (see app/db.py)
self-prunes so this collection never grows unbounded; nothing here deletes explicitly.
"""
import datetime
from typing import Any, Optional

from pymongo.database import Database


class AnomalyTracker:
    def __init__(self, db: Database) -> None:
        self._collection = db["anomaly_events"]

    def record_event(self, dimension: str, value: Any, outcome: str, request_id: Optional[str] = None) -> None:
        """One event per observation, e.g. dimension="ip_country", value="XX",
        outcome="DECLINE"."""
        self._collection.insert_one(
            {
                "dimension": dimension,
                "value": value,
                "outcome": outcome,
                "request_id": request_id,
                "at": datetime.datetime.now(datetime.timezone.utc),
            }
        )

    def count_recent(self, dimension: str, value: Any, window_seconds: int, outcome: Optional[str] = None) -> int:
        since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=window_seconds)
        query: dict = {"dimension": dimension, "value": value, "at": {"$gte": since}}
        if outcome:
            query["outcome"] = outcome
        return self._collection.count_documents(query)
