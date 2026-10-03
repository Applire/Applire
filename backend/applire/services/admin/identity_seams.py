# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one place package 1b calls into 1a/1c/1d code (Strawberry W1 seam).

Names agreed through the lead developer (NEEDS-EDIT 1b-3, ruling 2026-10-03):

* 1a ``applire.auth.passwords``: ``async hash_password(raw) -> str``,
  ``check_password_policy(password, email) -> None`` (raises ``ValueError``),
  ``async verify_password(raw, stored | None) -> bool`` (dummy hash when ``None``).
* 1a ``applire.auth.sessions``: ``async issue_session(db, user, response)``,
  ``async revoke_user_sessions(db, user_id) -> int``, ``clear_session_cookie(response)``.
* 1c ``applire.auth.tokens``: ``async revoke_all_for_user(db, user_id) -> int``
  (agent + api tokens; bumps ``users.link_epoch``).
* 1a ``applire.auth.roles``: ``async lock_active_admins(db) -> list[UUID]``,
  ``async assert_not_last_admin(db, user_id) -> None`` (409 ``last_admin``) — the
  one last-admin predicate (ADR-091 cl. 7; ruling 2026-10-03).
* 1a ``applire.auth.csrf``: ``require_origin`` — the dependency every public
  unsafe route takes (cl. 12).
* 1d ``applire.auth.reauth``: ``async consume_grant(db, *, request, user, action,
  target_id) -> bool`` (ADR-091 cl. 23 atomic consume).

Each call resolves the partner function **at call time**; while the partner
module/function is absent (this branch alone), a minimal fallback keeps the
package working and testable. **At integration the main session deletes every
``_fallback_*`` and the ``getattr`` indirection** — the fallbacks are not a
second implementation to maintain.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import importlib
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Request, Response
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.auth import AuthSession, PersonalToken
from applire.models.user import User

logger = logging.getLogger(__name__)

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256
SESSION_COOKIE = "applire_session"


def _partner(module: str, name: str) -> Any | None:
    try:
        mod = importlib.import_module(module)
    except ImportError:
        return None
    return getattr(mod, name, None)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- passwords (1a) ------------------------------------------------------------

def check_password_policy(password: str, email: str) -> None:
    fn = _partner("applire.auth.passwords", "check_password_policy")
    if fn is not None:
        fn(password, email)
        return
    if not (PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH):
        raise ValueError("password length")
    if password.strip().lower() == (email or "").strip().lower():
        raise ValueError("password equals email")


def _fallback_scrypt(raw: str, salt: bytes) -> bytes:
    return hashlib.scrypt(raw.encode(), salt=salt, n=2**15, r=8, p=1, dklen=32,
                          maxmem=64 * 1024 * 1024)


async def hash_password(raw: str) -> str:
    fn = _partner("applire.auth.passwords", "hash_password")
    if fn is not None:
        return await fn(raw)
    salt = os.urandom(16)
    digest = await asyncio.to_thread(_fallback_scrypt, raw, salt)
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    return f"scrypt$15$8$1${b64(salt)}${b64(digest)}"


async def verify_password(raw: str, stored: str | None) -> bool:
    fn = _partner("applire.auth.passwords", "verify_password")
    if fn is not None:
        return await fn(raw, stored)
    if not stored or not stored.startswith("scrypt$15$8$1$"):
        await asyncio.to_thread(_fallback_scrypt, raw, b"\0" * 16)  # equal timing
        return False
    _, _, _, _, salt_b64, hash_b64 = stored.split("$")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    salt = base64.urlsafe_b64decode(pad(salt_b64))
    expected = base64.urlsafe_b64decode(pad(hash_b64))
    got = await asyncio.to_thread(_fallback_scrypt, raw, salt)
    return hmac.compare_digest(got, expected)


# --- sessions (1a) -------------------------------------------------------------

async def issue_session(db: AsyncSession, user: User, response: Response) -> None:
    """Sign ``user`` in on ``response`` (a fresh session; never adopts a presented one)."""
    fn = _partner("applire.auth.sessions", "issue_session")
    if fn is not None:
        await fn(db, user, response)
        return
    logger.debug("1b seam: applire.auth.sessions.issue_session absent — no cookie issued")


async def revoke_user_sessions(db: AsyncSession, user_id: uuid.UUID) -> int:
    fn = _partner("applire.auth.sessions", "revoke_user_sessions")
    if fn is not None:
        return await fn(db, user_id)
    res = await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    return res.rowcount or 0


def clear_session_cookie(response: Response) -> None:
    fn = _partner("applire.auth.sessions", "clear_session_cookie")
    if fn is not None:
        fn(response)
        return
    response.delete_cookie(SESSION_COOKIE, path="/")


# --- tokens (1c) ---------------------------------------------------------------

async def revoke_all_tokens(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Revoke every agent + api token of the person; bump ``link_epoch``."""
    fn = _partner("applire.auth.tokens", "revoke_all_for_user")
    if fn is not None:
        return await fn(db, user_id)
    res = await db.execute(
        update(PersonalToken)
        .where(
            PersonalToken.user_id == user_id,
            PersonalToken.revoked_at.is_(None),
            PersonalToken.scope.in_(("agent", "api")),
        )
        .values(revoked_at=_now())
    )
    await db.execute(
        update(User).where(User.id == user_id).values(link_epoch=User.link_epoch + 1)
    )
    return res.rowcount or 0


# --- re-authentication (1d, W3) ------------------------------------------------

async def consume_reauth_grant(
    db: AsyncSession, *, request: Request, user: User, action: str, target_id: uuid.UUID
) -> bool:
    """Consume a verified fresh-OIDC grant; ``False`` while 1d has not shipped
    (a password-less account then gets ``reauth_required`` — fail closed)."""
    fn = _partner("applire.auth.reauth", "consume_grant")
    if fn is None:
        return False
    return bool(await fn(db, request=request, user=user, action=action, target_id=target_id))


# --- last-admin predicate (1a) ---------------------------------------------------

LAST_ADMIN_DETAIL = {
    "error_code": "last_admin",
    "message": "This is the only active admin. Make someone else an admin first.",
}


async def assert_not_last_admin(db: AsyncSession, user_id: uuid.UUID) -> None:
    """409 ``last_admin`` if removing ``user_id`` would leave no active admin.

    Decided on the rows of the locking statement (cl. 7), never an earlier count."""
    fn = _partner("applire.auth.roles", "assert_not_last_admin")
    if fn is not None:
        await fn(db, user_id)
        return
    stmt = (
        select(User.id)
        .where(
            User.role == "admin",
            User.disabled_at.is_(None),
            User.deleted_at.is_(None),
            (User.password_hash.is_not(None)) | (User.oidc_subject.is_not(None)),
        )
        .with_for_update()
    )
    locked = list((await db.execute(stmt)).scalars())
    if user_id in locked and len(locked) <= 1:
        raise HTTPException(status_code=409, detail=LAST_ADMIN_DETAIL)


# --- CSRF / origin (1a) ----------------------------------------------------------

async def _no_origin_check() -> None:
    """Fallback while ``applire.auth.csrf`` is absent on this branch alone."""
    return None


def require_origin_dependency():
    """1a's ``require_origin`` if present (resolved at import of the router)."""
    return _partner("applire.auth.csrf", "require_origin") or _no_origin_check
