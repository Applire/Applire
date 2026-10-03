# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Server-side browser sessions (ADR-091 cl. 11; RD-8; S-15).

Cookie ``applire_session`` = 32 random bytes (urlsafe base64); ``auth_sessions``
stores only the sha256. ``HttpOnly``, ``SameSite=Lax``, ``Path=/``, ``Secure``
iff ``COOKIE_SECURE=true`` (default false, ruling S-15). Sliding 14 days idle
(``last_seen_at`` refreshed at most hourly), 90 days absolute. A presented
cookie is never adopted: every sign-in mints a fresh token.

Resolution checks the user row on every request (disabled / tombstoned → no
user), so disabling acts at once (cl. 8).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from applire.config import settings
from applire.models.auth import AuthSession
from applire.models.user import User

SESSION_COOKIE = "applire_session"
IDLE_TIMEOUT = timedelta(days=14)
ABSOLUTE_TIMEOUT = timedelta(days=90)
REFRESH_INTERVAL = timedelta(hours=1)
ACTIVITY_INTERVAL = timedelta(hours=1)
TOKEN_BYTES = 32


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(ts: datetime) -> datetime:
    # SQLite hands back naive datetimes; Postgres aware ones.
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def user_is_live(user: User | None) -> bool:
    """Not disabled, not tombstoned (ADR-091 cl. 8)."""
    return user is not None and user.disabled_at is None and user.deleted_at is None


def _set_cookie(response: Response, raw: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        raw,
        max_age=int(ABSOLUTE_TIMEOUT.total_seconds()),
        path="/",
        httponly=True,
        samesite="lax",
        secure=bool(settings.cookie_secure),
    )


def clear_session_cookie(response: Response) -> None:
    """Expire the cookie (``Max-Age=0``) with the same attributes it was set with."""
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=bool(settings.cookie_secure),
    )


async def issue_session(db: AsyncSession, user: User, response: Response) -> None:
    """Create a fresh session for ``user``, set the cookie, stamp ``last_login_at``.

    The caller commits. Never reuses a presented token (session fixation).
    """
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    now = _now()
    session = AuthSession(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=hash_token(raw),
        created_at=now,
        last_seen_at=now,
    )
    db.add(session)
    user.last_login_at = now
    user.last_active_at = now
    await db.flush()
    _set_cookie(response, raw)


async def resolve_session(
    db: AsyncSession, raw: str | None
) -> tuple[AuthSession, User] | None:
    """The live session and its live user for cookie value ``raw``, else ``None``."""
    if not raw or len(raw) > 128:
        return None
    row = (
        await db.execute(
            select(AuthSession, User)
            .join(User, User.id == AuthSession.user_id)
            .where(AuthSession.token_hash == hash_token(raw))
        )
    ).first()
    if row is None:
        return None
    session, user = row
    now = _now()
    if session.revoked_at is not None:
        return None
    if now - _aware(session.created_at) > ABSOLUTE_TIMEOUT:
        return None
    if now - _aware(session.last_seen_at) > IDLE_TIMEOUT:
        return None
    if not user_is_live(user):
        return None
    dirty = False
    if now - _aware(session.last_seen_at) > REFRESH_INTERVAL:
        session.last_seen_at = now
        dirty = True
    if user.last_active_at is None or now - _aware(user.last_active_at) > ACTIVITY_INTERVAL:
        user.last_active_at = now
        dirty = True
    if dirty:
        await db.flush()
    return session, user


async def revoke_session(db: AsyncSession, session_id: uuid.UUID) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


async def revoke_user_sessions(
    db: AsyncSession, user_id: uuid.UUID, *, except_session_id: uuid.UUID | None = None
) -> int:
    """Revoke every live session of ``user_id`` (optionally keeping one); returns the count.

    Used on logout-everywhere, password change (others), reset, disable, delete.
    The caller commits.
    """
    stmt = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    if except_session_id is not None:
        stmt = stmt.where(AuthSession.id != except_session_id)
    result = await db.execute(stmt)
    return int(result.rowcount or 0)
