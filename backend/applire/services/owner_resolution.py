# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Owner resolution and owned reads for the service layer — ONE module (ADR-092
cl. 6, cl. 8; MD-21; MD-24 (4)).

W2 grew three helper modules for the same job (4a ``owner_resolution``, 3d
``owner_scope``, 3c ``cv_owner``); W3 consolidates them here. The two former
modules stay as re-export shims so existing imports keep working.

* :func:`resolve_user_id` — the MD-21 convention. Public service functions keep
  ``user_id: uuid.UUID | None = None``; ``None`` falls back to the USER owner
  context (``ownership.current_owner()``); an unscoped context or no context at
  all raises ``OwnerContextMissing`` — a per-user operation never runs without
  naming whose rows it touches. Every fallback hit is counted per call site in
  ``OWNER_FALLBACK_STATS``; the unit suites hold production doors at **zero**
  hits (MD-24 (2), ``tests/support/owner_ratchet.py``) — the fallback is never
  wrong, only implicit.
* :func:`resolve_owner` — the same, with the call site as a keyword (3c's form).
* :func:`owned_row` — one row by id for its owner (live only by default), else
  ``None`` for a missing **and** a foreign id alike (S-10); the caller raises its
  own ``LookupError`` so existing 404 mappings stay unchanged (3d's form).
* :func:`owned_cv` — one generated CV by id for its owner, else ``LookupError``
  with the identical message for missing and foreign (3c's form).
* :func:`job_for_user` — ``services.job.get_job_for_user`` (the posting access
  rule, 4a) with ``OwnedNotFound`` mapped to ``LookupError``.

The vault side keeps its own site-deriving wrapper (``services/profile/owner.py``),
which delegates to :func:`resolve_user_id`.
"""

from __future__ import annotations

import collections
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire import ownership

__all__ = [
    "OWNER_FALLBACK_STATS",
    "job_for_user",
    "owned_cv",
    "owned_row",
    "resolve_owner",
    "resolve_user_id",
]

_log = logging.getLogger(__name__)

OWNER_FALLBACK_STATS: "collections.Counter[str]" = collections.Counter()


def resolve_user_id(user_id: uuid.UUID | None, site: str) -> uuid.UUID:
    """``user_id`` if given, else the USER owner context; raise when neither exists."""
    if user_id is not None:
        return user_id
    ctx = ownership.current_owner()
    if ctx is None or ctx.is_unscoped or ctx.user_id is None:
        raise ownership.OwnerContextMissing(
            f"{site}: no user_id and no user owner context (ADR-092 cl. 8, ruling 3d-1)"
        )
    OWNER_FALLBACK_STATS[site] += 1
    _log.debug("owner fallback from context at %s", site)
    return ctx.user_id


def resolve_owner(user_id: uuid.UUID | None, *, site: str) -> uuid.UUID:
    """The acting user: explicit ``user_id``, else the USER owner context (counted)."""
    return resolve_user_id(user_id, site)


async def owned_row(
    db: AsyncSession, Model: type, row_id: Any, user_id: uuid.UUID, *, live: bool = True
):
    """``Model`` row ``row_id`` owned by ``user_id`` (live only by default), else ``None``."""
    stmt = select(Model).where(Model.id == row_id, Model.user_id == user_id)
    if live and hasattr(Model, "deleted_at"):
        stmt = stmt.where(Model.deleted_at.is_(None))
    return (await db.execute(stmt)).scalar_one_or_none()


async def owned_cv(
    db: AsyncSession,
    cv_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    include_deleted: bool = False,
) -> Any:
    """The ``GeneratedCV`` ``cv_id`` owned by ``user_id`` — else ``LookupError``.

    Missing and foreign ids raise the identical message (S-10).
    """
    from applire.models.cv import GeneratedCV

    stmt = select(GeneratedCV).where(
        GeneratedCV.id == cv_id, GeneratedCV.user_id == user_id
    )
    if not include_deleted:
        stmt = stmt.where(GeneratedCV.deleted_at.is_(None))
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None:
        raise LookupError(f"Generated CV {cv_id} not found")
    return record


async def job_for_user(db: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID) -> Any:
    """The posting for this user, else ``LookupError`` (same text as a missing job)."""
    from applire.services.job import get_job_for_user

    try:
        return await get_job_for_user(db, job_id, user_id)
    except ownership.OwnedNotFound as exc:
        raise LookupError(f"Job analysis {job_id} not found") from exc
