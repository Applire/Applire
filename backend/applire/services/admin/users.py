# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Account lifecycle for the admin and for self-deletion (ADR-091 cl. 7, 22, 25, 26).

Every mutation writes its audit row in the same transaction (``services.audit``).

**Last-admin protection (cl. 7)** is 1a's single predicate
(``auth/roles.assert_not_last_admin``, called through the seam module) for role
change, disable, admin delete and self delete — decided on the rows of a
``SELECT … FOR UPDATE``, never on an earlier count.

**Deletion (cl. 25, RD-3)** runs in three steps so the last-admin decision cannot
be undone by the erasure's own commits: (1) lock + decide + mark the account
disabled + revoke its sessions/tokens/links, commit — from here on it cannot sign
in and does not count as an admin; (2) ``erasure.erase(db, user_id, "account")``;
(3) tombstone + audit, commit. If step 2 fails the account stays *disabled* and
listed, and the delete can simply be retried.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.user import ROLE_ADMIN, ROLE_USER, User
from applire.services import audit, erasure
from applire.services.admin import identity_seams as seams
from applire.services.admin.links import issue_link, revoke_open_links

__all__ = [
    "AccountError",
    "UserStatus",
    "assert_not_last_admin",
    "create_user",
    "delete_account",
    "find_by_email",
    "get_live_user",
    "is_active_admin",
    "list_users",
    "patch_user",
    "reissue_invite",
    "issue_reset_link",
    "revoke_tokens",
    "user_status",
]

UserStatus = Literal["pending", "active", "disabled"]


class AccountError(Exception):
    """A refused account action; ``status``/``code`` map onto the F1 error table."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def to_http(self):
        """The F1 error body: plain ``"<kind> not found"`` for 404 (S-10) and the
        empty-patch 422; ``{"error_code", "message"}`` otherwise."""
        from fastapi import HTTPException

        if self.status == 404 or self.code == "empty_patch":
            return HTTPException(status_code=self.status, detail=self.message)
        return HTTPException(status_code=self.status,
                             detail={"error_code": self.code, "message": self.message})


def _last_admin() -> AccountError:
    return AccountError(409, "last_admin",
                        "This is the only active admin. Make someone else an admin first.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def has_credential(user: User) -> bool:
    return user.password_hash is not None or user.oidc_subject is not None


def user_status(user: User) -> UserStatus:
    if user.disabled_at is not None:
        return "disabled"
    if not has_credential(user) and user.last_login_at is None:
        return "pending"
    return "active"


def is_active_admin(user: User) -> bool:
    return (
        user.role == ROLE_ADMIN
        and user.disabled_at is None
        and user.deleted_at is None
        and has_credential(user)
    )


# --- lookups --------------------------------------------------------------------

async def get_live_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    """A not-tombstoned user, or 404 ``user not found`` (S-10: no oracle)."""
    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise AccountError(404, "not_found", "user not found")
    return user


async def find_by_email(db: AsyncSession, email: str) -> User | None:
    """Case-insensitive, with the index's own expression (never ``casefold()``)."""
    return (
        await db.execute(
            select(User).where(func.lower(User.email) == func.lower(email), User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()


async def list_users(db: AsyncSession) -> list[User]:
    return list(
        (
            await db.execute(
                select(User).where(User.deleted_at.is_(None)).order_by(User.created_at, User.id)
            )
        ).scalars()
    )


async def ui_language_of(db: AsyncSession, user_id: uuid.UUID) -> str | None:
    """The person's chosen UI language, if any (mail language, cl. 24)."""
    from applire.models.user_settings import UserSettings
    from applire.ownership import owner_context

    with owner_context(user_id):  # user_settings is an owned table (ADR-092)
        return (
            await db.execute(
                select(UserSettings.ui_language).where(UserSettings.user_id == user_id).limit(1)
            )
        ).scalar_one_or_none()


# --- last-admin predicate -------------------------------------------------------

async def assert_not_last_admin(db: AsyncSession, user_id: uuid.UUID) -> None:
    """1a's single predicate (``auth/roles.py``) via the seam; its 409 is an
    ``HTTPException`` and is turned into :class:`AccountError` here."""
    from fastapi import HTTPException

    try:
        await seams.assert_not_last_admin(db, user_id)
    except HTTPException as exc:
        if exc.status_code == 409:
            raise _last_admin() from exc
        raise


# --- create / invite / reset ----------------------------------------------------

@dataclass
class IssuedLinkResult:
    user: User
    purpose: Literal["invite", "reset"]
    raw_token: str
    link_id: uuid.UUID
    expires_at: datetime


async def create_user(
    db: AsyncSession, *, actor: User, email: str, role: str, mailed: bool
) -> IssuedLinkResult:
    """A *pending* account and its invite link (committed). ``mailed`` is whether the
    caller will mail it (recorded in the audit row)."""
    if await find_by_email(db, email) is not None:
        raise AccountError(409, "email_taken", "An account with this email exists.")
    user = User(email=email, role=role if role in (ROLE_ADMIN, ROLE_USER) else ROLE_USER)
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:  # concurrent create with the same email
        await db.rollback()
        raise AccountError(409, "email_taken", "An account with this email exists.") from exc
    link, raw = await issue_link(db, user, "invite")
    await audit.record(db, actor_id=actor.id, action="user.created", target_type="user",
                       target_id=user.id, details={"role": user.role, "mailed": mailed})
    await db.commit()
    return IssuedLinkResult(user, "invite", raw, link.id, link.expires_at)


async def reissue_invite(db: AsyncSession, *, actor: User, user_id: uuid.UUID, mailed: bool) -> IssuedLinkResult:
    user = await get_live_user(db, user_id)
    if user_status(user) != "pending":
        raise AccountError(409, "user_not_pending",
                           "This person already has a credential — create a reset link instead.")
    link, raw = await issue_link(db, user, "invite")
    await audit.record(db, actor_id=actor.id, action="user.reinvited", target_type="user",
                       target_id=user.id, details={"link_id": link.id, "mailed": mailed})
    await db.commit()
    return IssuedLinkResult(user, "invite", raw, link.id, link.expires_at)


async def issue_reset_link(
    db: AsyncSession, *, actor: User | None, user_id: uuid.UUID, via: str, mailed: bool
) -> IssuedLinkResult:
    user = await get_live_user(db, user_id)
    if user_status(user) != "active":
        raise AccountError(409, "user_not_active",
                           "Pending (send a new invitation) or disabled accounts get no reset link.")
    link, raw = await issue_link(db, user, "reset")
    await audit.record(db, actor_id=actor.id if actor else None, action="reset_link.issued",
                       target_type="user", target_id=user.id,
                       details={"link_id": link.id, "via": via, "mailed": mailed})
    await db.commit()
    return IssuedLinkResult(user, "reset", raw, link.id, link.expires_at)


# --- role / disable / tokens ----------------------------------------------------

async def patch_user(
    db: AsyncSession, *, actor: User, user_id: uuid.UUID, role: str | None, disabled: bool | None
) -> User:
    if role is None and disabled is None:
        raise AccountError(422, "empty_patch", "Nothing to change.")
    user = await get_live_user(db, user_id)

    leaves_admins = (
        (role == ROLE_USER and user.role == ROLE_ADMIN)
        or (disabled is True and user.disabled_at is None and user.role == ROLE_ADMIN)
    )
    if leaves_admins:
        await assert_not_last_admin(db, user.id)

    if role is not None and role != user.role:
        old = user.role
        user.role = role
        await audit.record(db, actor_id=actor.id, action="user.role_changed", target_type="user",
                           target_id=user.id, details={"from_role": old, "to_role": role})
    if disabled is True and user.disabled_at is None:
        user.disabled_at = _now()
        await seams.revoke_user_sessions(db, user.id)
        await seams.revoke_all_tokens(db, user.id)  # bumps link_epoch (cl. 18)
        await audit.record(db, actor_id=actor.id, action="user.disabled", target_type="user",
                           target_id=user.id, details={})
    elif disabled is False and user.disabled_at is not None:
        user.disabled_at = None
        await audit.record(db, actor_id=actor.id, action="user.enabled", target_type="user",
                           target_id=user.id, details={})
    await db.commit()
    await db.refresh(user)
    return user


async def revoke_tokens(db: AsyncSession, *, actor: User, user_id: uuid.UUID) -> int:
    user = await get_live_user(db, user_id)
    count = await seams.revoke_all_tokens(db, user.id)
    await audit.record(db, actor_id=actor.id, action="tokens.revoked_all", target_type="user",
                       target_id=user.id, details={"count": count})
    await db.commit()
    return count


# --- deletion -------------------------------------------------------------------

async def delete_account(
    db: AsyncSession, *, actor: User, user_id: uuid.UUID, by: Literal["admin", "self"]
) -> None:
    """Erase and tombstone (cl. 25); refused for the last active admin."""
    user = await get_live_user(db, user_id)

    # Step 1 — decide on the locked rows, then take the account out of play.
    if user.role == ROLE_ADMIN:
        await assert_not_last_admin(db, user.id)
    if user.disabled_at is None:
        user.disabled_at = _now()
    await seams.revoke_user_sessions(db, user.id)
    await seams.revoke_all_tokens(db, user.id)
    await revoke_open_links(db, user.id)
    await db.commit()

    # Step 2 — the one erasure implementation (ADR-092 cl. 11, filled by 3b).
    counts = await erasure.erase(db, user.id, "account")

    # Step 3 — tombstone (idempotent if erase already did it) + audit.
    user = await db.get(User, user_id, populate_existing=True)
    if user is not None:
        user.email = f"deleted+{user.id}@invalid"
        user.password_hash = None
        user.oidc_issuer = None
        user.oidc_subject = None
        user.link_epoch = (user.link_epoch or 0) + 1
        if user.deleted_at is None:
            user.deleted_at = _now()
    erased_rows = sum(int(v) for v in (counts or {}).values())
    await audit.record(db, actor_id=actor.id, action="user.deleted", target_type="user",
                       target_id=user_id, details={"by": by, "erased_rows": erased_rows})
    await db.commit()
