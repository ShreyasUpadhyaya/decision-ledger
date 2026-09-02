import datetime
import uuid
from typing import Optional

from pymongo.database import Database

from .logging_config import get_logger

logger = get_logger("decision_ledger.async")


class AsyncDecisionStore:
    def __init__(self, db: Database) -> None:
        self._collection = db["async_decisions"]

    def create_job(self, request: dict, webhook_url: Optional[str] = None) -> dict:
        job_id = f"async_{uuid.uuid4().hex[:12]}"
        now = datetime.datetime.now(datetime.timezone.utc)
        doc = {
            "job_id": job_id,
            "status": "PENDING",
            "request": request,
            "result": None,
            "webhook_url": webhook_url,
            "created_at": now,
            "updated_at": now,
        }
        self._collection.insert_one(doc)
        logger.info(
            "async.job_created",
            extra={"event": "async.job_created", "job_id": job_id, "webhook": bool(webhook_url)}
        )
        # Exclude internal MongoDB _id from returned dict
        doc.pop("_id", None)
        return doc

    def get_job(self, job_id: str) -> Optional[dict]:
        doc = self._collection.find_one({"job_id": job_id}, {"_id": 0})
        return doc

    def update_status(self, job_id: str, status: str) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        self._collection.update_one(
            {"job_id": job_id},
            {"$set": {"status": status, "updated_at": now}}
        )

    def update_result(self, job_id: str, result: dict, status: str) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        self._collection.update_one(
            {"job_id": job_id},
            {
                "$set": {
                    "status": status,
                    "result": result,
                    "completed_at": now,
                    "updated_at": now,
                }
            }
        )
        logger.info(
            "async.job_completed",
            extra={"event": "async.job_completed", "job_id": job_id, "status": status}
        )

    def list_jobs(self, status: Optional[str] = None, limit: int = 20) -> list[dict]:
        query = {}
        if status:
            query["status"] = status
        cursor = self._collection.find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
        return list(cursor)
