"""MongoDB connection lifecycle and index management for the ruleset repository."""
from typing import Optional
from pymongo import MongoClient, ASCENDING
from pymongo.database import Database

from .config import settings

_client: Optional[MongoClient] = None


def get_database(uri: Optional[str] = None, db_name: Optional[str] = None) -> Database:
    """Get MongoDB database instance, initializing indexes if needed."""
    global _client
    actual_uri = uri or settings.mongodb_uri
    actual_db_name = db_name or settings.mongodb_database

    if _client is None:
        # A short server-selection timeout keeps an unreachable Mongo from blocking
        # startup for pymongo's 30s default — fail fast and clearly instead.
        if uri is None:
            _client = MongoClient(actual_uri, serverSelectionTimeoutMS=5000)
            db = _client[actual_db_name]
        else:
            client = MongoClient(actual_uri, serverSelectionTimeoutMS=5000)
            db = client[actual_db_name]
    else:
        if uri is not None:
            client = MongoClient(actual_uri, serverSelectionTimeoutMS=5000)
            db = client[actual_db_name]
        else:
            db = _client[actual_db_name]

    _ensure_indexes(db)
    return db


def _ensure_indexes(db: Database) -> None:
    # ruleset_versions indexes
    # 1. Unique constraint on (ruleset_id, version)
    db.ruleset_versions.create_index(
        [("ruleset_id", ASCENDING), ("version", ASCENDING)],
        unique=True
    )
    # 2. Index for active_by_decision_type lookups
    db.ruleset_versions.create_index(
        [("market", ASCENDING), ("decision_type", ASCENDING), ("deprecated_at", ASCENDING)]
    )

    # audit_log index
    db.audit_log.create_index(
        [("ruleset_id", ASCENDING), ("timestamp", ASCENDING)]
    )

    # shadow_metrics indexes (risk_tiered_architecture.md §4 Tier 2)
    db.shadow_metrics.create_index([("ruleset_id", ASCENDING), ("rule_id", ASCENDING)])
    db.shadow_metrics.create_index([("recorded_at", ASCENDING)])

    # candidate_rules indexes (risk_tiered_architecture.md §4 Tier 3)
    db.candidate_rules.create_index([("candidate_id", ASCENDING)], unique=True)
    db.candidate_rules.create_index([("status", ASCENDING), ("created_at", ASCENDING)])

    # anomaly_events: sliding-window lookup index + a TTL so the collection self-prunes
    # (risk_tiered_architecture.md §2). 1 hour comfortably covers the 5-minute window
    # the tracker actually queries, with headroom for clock skew between app instances.
    db.anomaly_events.create_index([("dimension", ASCENDING), ("value", ASCENDING), ("at", ASCENDING)])
    db.anomaly_events.create_index([("at", ASCENDING)], expireAfterSeconds=3600)


def close_database() -> None:
    """Close MongoDB connection on shutdown."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
