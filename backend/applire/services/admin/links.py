# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Single-use invite and reset links (ADR-091 cl. 22–23, S-6, S-7, RD-8).

* A link is 32 random bytes (``secrets.token_urlsafe``); only its sha256 is stored.
* The token travels in the page **fragment** (``<origin>/invite#<token>``,
  ``<origin>/reset#<token>``) and in POST bodies — never in a request path or query,
  so no access log can print it.
* Lifetimes (RD-8): invite 7 days, reset 1 hour.
* A new link of the same purpose **supersedes** the person's older unused ones
  (``used_at`` set — they answer ``link_used``).
* Redemption is one statement — ``UPDATE auth_links SET used_at=now() WHERE
  token_hash=:h AND used_at IS NULL AND expires_at>now() RETURNING …`` — so two
  concurrent redeems cannot both succeed (adversarial-security §S "links").
* **Where a link points (MD-32, adv-id-1).** A link that LEAVES the browser — every
  mail — is built from ``APPLIRE_BASE_URL`` only (:func:`mail_origin`); with the
  shipped default no mail is sent at all, because a request's ``Host``/``Origin``
  is chosen by whoever sends it (an unauthenticated ``/forgot`` with ``Host:
  evil.example`` would otherwise mail the victim a genuine link to the attacker).
  A link SHOWN to a signed-in admin (:func:`request_origin`) may use the origin of
  that admin's own browser session — the CSRF check already tied it to ``Host``.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from applire.config import settings
from applire.models.auth import AuthLink
from applire.models.user import User

__all__ = [
    "LINK_TTL",
    "LinkPurpose",
    "LinkState",
    "build_link_url",
    "consume_link",
    "hash_link_token",
    "inspect_link",
    "issue_link",
    "mail_origin",
    "mail_without_base_url_warning",
    "request_origin",
    "revoke_open_links",
]

LinkPurpose = Literal["invite", "reset"]
LinkState = Literal["valid", "expired", "used"]

LINK_TTL: dict[str, timedelta] = {"invite": timedelta(days=7), "reset": timedelta(hours=1)}
SHIPPED_DEFAULT_BASE_URL = "http://localhost:8001"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(ts: datetime) -> datetime:
    # SQLite hands timestamps back naive; they were written as UTC.
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def hash_link_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mail_origin() -> str | None:
    """The origin for a link that leaves the browser (MD-32): ``APPLIRE_BASE_URL``
    when the operator set it, else ``None`` — the caller then sends no mail."""
    base = (getattr(settings, "applire_base_url", "") or "").strip().rstrip("/")
    if base and base != SHIPPED_DEFAULT_BASE_URL:
        return base
    return None


MAIL_WITHOUT_BASE_URL_WARNING = (
    "SMTP is configured but APPLIRE_BASE_URL is not set: Applire sends NO invitation "
    "or password-reset mail (a mailed link must point at your real address, never at "
    "one taken from a request). Set APPLIRE_BASE_URL in .env to the address people "
    "open Applire on — SELF-HOSTING §15."
)


def mail_without_base_url_warning() -> str | None:
    """The startup WARNING of MD-32, or ``None`` when mail and base URL agree."""
    from applire.services import mail

    if mail.smtp_enabled() and mail_origin() is None:
        return MAIL_WITHOUT_BASE_URL_WARNING
    return None


def request_origin(request: Request, *, signed_in: bool = False) -> str:
    """Origin for a link SHOWN to the signed-in admin (no trailing slash).

    ``APPLIRE_BASE_URL`` when set. Otherwise, only when the caller vouches that
    the request is a signed-in admin's (``signed_in=True``, after
    ``require_admin_session``) and it is not a bearer request: that browser's own
    origin (Origin == Host, the rule the CSRF check enforces), else its ``Host``.
    Anyone else — an unauthenticated or bearer request — never gets a
    header-derived origin (MD-32): the answer is ``""`` and the link is relative.
    """
    configured = mail_origin()
    if configured is not None:
        return configured
    if not signed_in or getattr(request.state, "auth_via", None) == "bearer":
        return ""
    origin = (request.headers.get("origin") or "").strip()
    host = (request.headers.get("host") or "").strip()
    if origin and origin != "null":
        parts = urlsplit(origin)
        if parts.scheme in ("http", "https") and parts.netloc and parts.netloc == host:
            return f"{parts.scheme}://{parts.netloc}"
    return str(request.base_url).rstrip("/")


def build_link_url(origin: str, purpose: LinkPurpose, raw: str) -> str:
    return f"{origin.rstrip('/')}/{purpose}#{raw}"


async def revoke_open_links(
    db: AsyncSession, user_id: uuid.UUID, purpose: LinkPurpose | None = None
) -> int:
    stmt = update(AuthLink).where(AuthLink.user_id == user_id, AuthLink.used_at.is_(None))
    if purpose is not None:
        stmt = stmt.where(AuthLink.purpose == purpose)
    res = await db.execute(stmt.values(used_at=_now()))
    return res.rowcount or 0


async def issue_link(db: AsyncSession, user: User, purpose: LinkPurpose) -> tuple[AuthLink, str]:
    """Create a link (flushed, not committed); older unused links of ``purpose`` die."""
    await revoke_open_links(db, user.id, purpose)
    raw = secrets.token_urlsafe(32)
    link = AuthLink(
        user_id=user.id,
        purpose=purpose,
        token_hash=hash_link_token(raw),
        expires_at=_now() + LINK_TTL[purpose],
    )
    db.add(link)
    await db.flush()
    return link, raw


@dataclass(frozen=True)
class InspectedLink:
    link: AuthLink
    user: User
    state: LinkState


async def inspect_link(db: AsyncSession, raw: str) -> InspectedLink | None:
    """The link and its person, or ``None`` (unknown token, or a closed account)."""
    row = (
        await db.execute(
            select(AuthLink, User)
            .join(User, User.id == AuthLink.user_id)
            .where(AuthLink.token_hash == hash_link_token(raw))
        )
    ).first()
    if row is None:
        return None
    link, user = row
    if user.deleted_at is not None or user.disabled_at is not None:
        return None
    if link.used_at is not None:
        state: LinkState = "used"
    elif _aware(link.expires_at) <= _now():
        state = "expired"
    else:
        state = "valid"
    return InspectedLink(link=link, user=user, state=state)


async def consume_link(db: AsyncSession, raw: str) -> tuple[uuid.UUID, uuid.UUID, str] | None:
    """Atomically mark the link used; ``(link_id, user_id, purpose)`` or ``None``.

    The single ``UPDATE … RETURNING`` is the whole decision: whoever's statement
    matches first wins, every other caller gets ``None``.
    """
    now = _now()
    res = await db.execute(
        update(AuthLink)
        .where(
            AuthLink.token_hash == hash_link_token(raw),
            AuthLink.used_at.is_(None),
            AuthLink.expires_at > now,
        )
        .values(used_at=now)
        .returning(AuthLink.id, AuthLink.user_id, AuthLink.purpose)
        .execution_options(synchronize_session=False)
    )
    row = res.first()
    return (row[0], row[1], row[2]) if row else None


async def newest_open_invite_expiry(db: AsyncSession, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, datetime]:
    """For the users list: expiry of each person's newest unused invite."""
    if not user_ids:
        return {}
    rows = (
        await db.execute(
            select(AuthLink.user_id, func.max(AuthLink.expires_at))
            .where(
                AuthLink.user_id.in_(user_ids),
                AuthLink.purpose == "invite",
                AuthLink.used_at.is_(None),
            )
            .group_by(AuthLink.user_id)
        )
    ).all()
    return {uid: _aware(exp) for uid, exp in rows}


async def recent_reset_count(db: AsyncSession, user_id: uuid.UUID, window: timedelta) -> int:
    return (
        await db.execute(
            select(func.count(AuthLink.id)).where(
                AuthLink.user_id == user_id,
                AuthLink.purpose == "reset",
                AuthLink.created_at > _now() - window,
            )
        )
    ).scalar_one()
