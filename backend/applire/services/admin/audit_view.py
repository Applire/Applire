# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The admin's read of the audit log (S-11 "admin view in C"; RD-6, RD-11).

Newest first, filtered, keyset-paged on ``(at, id)`` — stable while rows are
appended. Metadata only: the stored ``detail`` scalars (ids, roles, reasons,
setting keys and non-secret values — ``services/audit.py`` closes what a row may
hold) plus the actor's / target's CURRENT email, resolved live from ``users``
(``None`` for the system or an erased account). No IP exists to show.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.audit import AuditEvent
from applire.models.user import User
from applire.services.audit import ACTIONS

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


class InvalidCursor(ValueError):
    """A cursor that does not decode to ``(at, id)``."""


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def encode_cursor(at: datetime, id_: uuid.UUID) -> str:
    raw = f"{_aware(at).isoformat()}|{id_}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        at_s, id_s = base64.urlsafe_b64decode(padded.encode()).decode().split("|", 1)
        return _aware(datetime.fromisoformat(at_s)), uuid.UUID(id_s)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursor("invalid cursor") from exc


async def page(
    db: AsyncSession,
    *,
    actions: Sequence[str] = (),
    actor_id: uuid.UUID | None = None,
    target_user_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    """The ``AuditPageResponse`` payload."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    stmt = select(AuditEvent)
    if actions:
        stmt = stmt.where(AuditEvent.action.in_(list(actions)))
    if actor_id is not None:
        stmt = stmt.where(AuditEvent.actor_user_id == actor_id)
    if target_user_id is not None:
        stmt = stmt.where(AuditEvent.target_user_id == target_user_id)
    if since is not None:
        stmt = stmt.where(AuditEvent.at >= since)
    if until is not None:
        stmt = stmt.where(AuditEvent.at < until)
    if cursor:
        c_at, c_id = decode_cursor(cursor)
        stmt = stmt.where(
            or_(AuditEvent.at < c_at, and_(AuditEvent.at == c_at, AuditEvent.id < c_id))
        )
    stmt = stmt.order_by(AuditEvent.at.desc(), AuditEvent.id.desc()).limit(limit + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    more = len(rows) > limit
    rows = rows[:limit]

    ids = {r.actor_user_id for r in rows} | {r.target_user_id for r in rows}
    ids.discard(None)
    emails: dict[uuid.UUID, str] = {}
    if ids:
        emails = {
            uid: email
            for uid, email in (
                await db.execute(
                    select(User.id, User.email).where(User.id.in_(ids), User.deleted_at.is_(None))
                )
            ).all()
        }
    items = [
        {
            "id": r.id,
            "at": _aware(r.at),
            "action": r.action,
            "actor_user_id": r.actor_user_id,
            "actor_email": emails.get(r.actor_user_id) if r.actor_user_id else None,
            "target_user_id": r.target_user_id,
            "target_email": emails.get(r.target_user_id) if r.target_user_id else None,
            "detail": dict(r.detail or {}),
        }
        for r in rows
    ]
    next_cursor = encode_cursor(rows[-1].at, rows[-1].id) if more and rows else None
    return {"items": items, "next_cursor": next_cursor, "actions": sorted(ACTIONS)}
