# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fresh-OIDC re-authentication grants for destructive actions (ADR-091 cl. 23; MD-7; R3-2iv).

A person **without** a password confirms a destructive action by a fresh IdP
login. One ``reauth_grants`` row per attempt, bound to (user, session, action,
target), living 5 minutes:

1. ``POST /api/me/reauth/start`` → :func:`create_grant`, then the IdP with
   ``prompt=login&max_age=0`` and the grant id in the signed state cookie.
2. The callback → :func:`verify_grant`: the current session must be the grant's,
   the IdP identity must be the account's own ``(issuer, sub)``, and
   ``auth_time >= started_at`` — an IdP that sends no ``auth_time`` is refused
   (it may have ignored ``prompt=login``; fail closed).
3. The destructive endpoint → :func:`consume_grant`: one atomic ``UPDATE …
   RETURNING`` bound to session, action and target, so a stolen session cannot
   reuse an earlier re-authentication.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.auth import REAUTH_ACTIONS, ReauthGrant
from applire.models.user import User

GRANT_TTL = timedelta(minutes=5)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def current_session_id(request: Request) -> uuid.UUID | None:
    """The id of the session that authenticated ``request`` (set by the local provider)."""
    if getattr(request.state, "auth_via", None) != "session":
        return None
    sid = getattr(request.state, "auth_session_id", None)
    return sid if isinstance(sid, uuid.UUID) else None


async def create_grant(
    db: AsyncSession, *, user: User, session_id: uuid.UUID, action: str, target_id: uuid.UUID
) -> ReauthGrant:
    """A new, unverified grant (flushed; the caller commits)."""
    if action not in REAUTH_ACTIONS:
        raise ValueError(f"unknown re-auth action {action!r}")
    now = _now()
    grant = ReauthGrant(
        id=uuid.uuid4(),
        user_id=user.id,
        session_id=session_id,
        action=action,
        target_id=target_id,
        started_at=now,
        expires_at=now + GRANT_TTL,
    )
    db.add(grant)
    await db.flush()
    return grant


async def verify_grant(
    db: AsyncSession,
    *,
    grant_id: uuid.UUID,
    user: User,
    session_id: uuid.UUID,
    issuer: str,
    subject: str,
    auth_time: int | None,
) -> bool:
    """Mark the grant verified after the IdP round-trip; ``False`` on any mismatch."""
    if auth_time is None:
        return False
    if user.oidc_issuer != issuer or user.oidc_subject != subject:
        return False
    grant = await db.get(ReauthGrant, grant_id)
    if grant is None or grant.user_id != user.id or grant.session_id != session_id:
        return False
    if auth_time < int(_aware(grant.started_at).timestamp()):
        return False
    now = _now()
    verified = (
        await db.execute(
            update(ReauthGrant)
            .where(
                ReauthGrant.id == grant_id,
                ReauthGrant.user_id == user.id,
                ReauthGrant.session_id == session_id,
                ReauthGrant.verified_at.is_(None),
                ReauthGrant.used_at.is_(None),
                ReauthGrant.expires_at > now,
            )
            .values(verified_at=now)
            .returning(ReauthGrant.id)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    return verified is not None


async def consume_grant(
    db: AsyncSession, *, request: Request, user: User, action: str, target_id: uuid.UUID
) -> bool:
    """Consume a verified grant for (user, current session, action, target) — once.

    Called by ``DELETE /api/me/account`` (1b) and ``DELETE /api/me/oidc`` for a
    password-less account. ``False`` → the endpoint answers 403 ``reauth_required``.
    The caller commits (with the destructive change).
    """
    session_id = current_session_id(request)
    if session_id is None or action not in REAUTH_ACTIONS:
        return False
    now = _now()
    used = (
        await db.execute(
            update(ReauthGrant)
            .where(
                ReauthGrant.user_id == user.id,
                ReauthGrant.session_id == session_id,
                ReauthGrant.action == action,
                ReauthGrant.target_id == target_id,
                ReauthGrant.verified_at.is_not(None),
                ReauthGrant.used_at.is_(None),
                ReauthGrant.expires_at > now,
            )
            .values(used_at=now)
            .returning(ReauthGrant.id)
            .execution_options(synchronize_session=False)
        )
    ).first()
    return used is not None
