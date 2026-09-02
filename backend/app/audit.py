"""Audit trail for all ruleset write operations."""
import datetime
from typing import Optional
from pymongo.database import Database


def log_audit(
    db: Database,
    ruleset_id: str,
    action: str,
    actor: str,
    version: Optional[int],
    details: Optional[dict] = None,
) -> None:
    """
    Log an audit event to the database.
    Actions tracked: CREATE_VERSION, ROLLBACK, DEPRECATE, REACTIVATE
    """
    event = {
        "ruleset_id": ruleset_id,
        "action": action,
        "actor": actor,
        "version": version,
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "details": details or {}
    }
    db.audit_log.insert_one(event)
