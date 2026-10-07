# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Roles and the last-admin predicate (ADR-091 cl. 7; SF-IAM.7).

One predicate guards role change, disable, admin delete, self delete and the
retention worker: refused if it would leave zero active admins. In ONE
transaction the caller runs ``lock_active_admins`` (``SELECT … FOR UPDATE`` on
the active admin rows) and the decision is taken **on the rows that statement
returned**, never on an earlier count: under READ COMMITTED a second concurrent
demotion waits on the lock, re-reads, and sees only the surviving admin.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.user import ROLE_ADMIN, User

LAST_ADMIN = {
    "error_code": "last_admin",
    "message": "This would leave the instance without an active administrator.",
}


def active_admin_predicate():
    """An admin who can actually administer (ruling MD-18 tightens cl. 7).

    role ``admin``, not disabled, not tombstoned **and holding a credential**
    (password or OIDC binding): a pending invited admin cannot sign in, so it
    must not count — otherwise the only real admin could leave the instance to
    an unredeemed, expiring invite.
    """
    return (
        (User.role == ROLE_ADMIN)
        & User.disabled_at.is_(None)
        & User.deleted_at.is_(None)
        & (User.password_hash.is_not(None) | User.oidc_subject.is_not(None))
    )


async def lock_active_admins(db: AsyncSession) -> list[uuid.UUID]:
    """Lock and return the ids of every active admin (``FOR UPDATE``; no-op lock on SQLite)."""
    rows = await db.execute(
        select(User.id).where(active_admin_predicate()).order_by(User.id).with_for_update()
    )
    return [r[0] for r in rows]


async def assert_not_last_admin(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Raise 409 ``last_admin`` if removing ``user_id``'s admin standing leaves none.

    Call inside the transaction that then demotes/disables/deletes ``user_id``.
    A ``user_id`` that is not an active admin never trips it.
    """
    admins = await lock_active_admins(db)
    if user_id in admins and len(admins) <= 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LAST_ADMIN)


def is_admin(user: User, request: Request | None = None) -> bool:
    """Role ``admin`` — or the harness stub, which acts as admin (cl. 3)."""
    if request is not None and getattr(request.state, "auth_via", None) == "harness":
        return True
    return getattr(user, "role", None) == ROLE_ADMIN


def effective_role(user: User, request: Request | None = None) -> str:
    return ROLE_ADMIN if is_admin(user, request) else "user"
