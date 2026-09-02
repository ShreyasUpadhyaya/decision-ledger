"""MongoDB-backed user store for authentication (spec §11/§12 patterns).

Users live in MongoDB and are accessed through the synchronous ``pymongo`` driver — the
routes here are ``def`` (not ``async``), so a sync driver is the right fit; motor would be
the wrong tool.

Two safety properties mirror the rest of the app's startup model:

* **Never crash import.** ``MongoClient`` connects lazily, so constructing the store never
  touches the network. The unique index on ``username`` *does* force a connection, so it is
  attempted best-effort: if Mongo is unreachable we log a clear error and carry on. The
  auth routes then surface a 5xx when they actually hit the database.
* **Never leak secrets.** Only ``{username, role}`` is ever returned to callers — the bcrypt
  ``password_hash`` and Mongo ``_id`` never cross the store boundary.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from passlib.context import CryptContext
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from .logging_config import get_logger

logger = get_logger("decision_ledger.users")

# bcrypt for password hashing (spec §3).
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class UserStore:
    def __init__(self, settings: Any) -> None:
        # A short server-selection timeout keeps an unreachable Mongo from blocking import
        # (and every auth request) for the 30s pymongo default.
        self._client: MongoClient = MongoClient(
            settings.mongodb_uri, serverSelectionTimeoutMS=3000
        )
        self._collection = self._client[settings.mongodb_database][
            settings.mongodb_users_collection
        ]

        # Best-effort unique index. This is the first call that actually connects, so it
        # fails when Mongo is down — we log clearly but do NOT raise, so app import still
        # succeeds and the auth routes report the outage with a 5xx instead.
        try:
            self._collection.create_index([("username", ASCENDING)], unique=True)
        except PyMongoError as exc:
            logger.error(
                "users.index_unavailable",
                extra={
                    "event": "users.index_unavailable",
                    "uri": settings.mongodb_uri,
                    "error": str(exc),
                },
            )

    @staticmethod
    def _public(doc: dict) -> dict:
        """Project a stored document down to the client-safe shape."""
        return {"username": doc["username"], "role": doc["role"]}

    def exists(self, username: str) -> bool:
        return self._collection.count_documents({"username": username}, limit=1) > 0

    def get(self, username: str) -> Optional[dict]:
        doc = self._collection.find_one({"username": username})
        return self._public(doc) if doc else None

    def create(self, username: str, password: str, role: str) -> dict:
        """Insert a new user, storing only the bcrypt hash. Raises ``ValueError`` if the
        username is already taken (enforced by the unique index)."""
        doc = {
            "username": username,
            "role": role,
            "password_hash": _pwd_context.hash(password),
            "created_at": datetime.now(timezone.utc),
        }
        try:
            self._collection.insert_one(doc)
        except DuplicateKeyError as exc:
            raise ValueError(f"username already exists: {username}") from exc
        return self._public(doc)

    def verify(self, username: str, password: str) -> bool:
        doc = self._collection.find_one({"username": username})
        if not doc:
            return False
        return _pwd_context.verify(password, doc["password_hash"])
