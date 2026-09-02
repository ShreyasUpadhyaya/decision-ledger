"""Authentication utilities: JWT minting and FastAPI dependencies (spec §5).

Sessions are stateless HS256 JWTs signed with ``settings.auth_secret``. Nothing here
touches the database — token verification is pure crypto, so the ``get_current_user`` /
``require_role`` guards keep working (and correctly return 401/403) even if MongoDB is
down. Only the signup/login routes read or write users.
"""
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import jwt
from fastapi import Depends, Header, HTTPException

from .config import settings
from .schemas import PublicUser

_ALGORITHM = "HS256"
_BEARER_PREFIX = "Bearer "


def create_access_token(username: str, role: str) -> str:
    """Mint an HS256 JWT with ``sub`` / ``role`` claims and an expiry driven by config."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.auth_token_ttl_minutes),
    }
    return jwt.encode(payload, settings.auth_secret, algorithm=_ALGORITHM)


def get_current_user(authorization: Optional[str] = Header(default=None)) -> PublicUser:
    """Resolve the caller from a ``Bearer <token>`` Authorization header.

    Raises 401 on a missing, malformed, invalid or expired token.
    """
    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")

    token = authorization[len(_BEARER_PREFIX):].strip()
    try:
        claims = jwt.decode(token, settings.auth_secret, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    username = claims.get("sub")
    role = claims.get("role")
    if not username or not role:
        raise HTTPException(status_code=401, detail="Invalid token claims")
    return PublicUser(username=username, role=role)


def require_role(role: str) -> Callable[[PublicUser], PublicUser]:
    """Build a dependency that requires the caller to hold a specific role (else 403)."""

    def _dependency(user: PublicUser = Depends(get_current_user)) -> PublicUser:
        if user.role != role:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user

    return _dependency
