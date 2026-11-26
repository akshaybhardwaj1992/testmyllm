import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ApiError
from app.core.security import decode_token
from app.models import ApiToken, User

COOKIE = "testlab_session"
TOKEN_PREFIX = "tml_"  # TestMyLLM API tokens
LEGACY_TOKEN_PREFIXES = ("tl_",)  # tokens issued before the rename keep working


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Cookie session for the SPA; `Authorization: Bearer` with a JWT or a project API token (tml_…).

    API tokens act as the user who created them, restricted to one project and capped at the token's
    role (see services.access.require_project). The scope is attached to this request's User instance.
    """
    token = request.cookies.get(COOKIE)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if token and token.startswith((TOKEN_PREFIX, *LEGACY_TOKEN_PREFIXES)):
        api = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token), ApiToken.revoked_at.is_(None)))
        user = db.get(User, api.created_by) if api else None
        if user is None:
            raise ApiError(401, "unauthorized", "Invalid or revoked API token")
        now = datetime.now(UTC)
        if api.last_used_at is None or now - api.last_used_at > timedelta(minutes=1):
            api.last_used_at = now
            db.commit()
        user._token_scope = (api.project_id, api.role)  # type: ignore[attr-defined]
        return user
    user_id = decode_token(token) if token else None
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise ApiError(401, "unauthorized", "Not signed in or session expired")
    return user


def token_scope(user: User) -> tuple[int, str] | None:
    return getattr(user, "_token_scope", None)
