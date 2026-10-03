# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""Owned reads for the CV side (ADR-092 cl. 5c, cl. 6; Strawberry W2 / 3c).

* :func:`resolve_owner` — thin alias of the shared W2 convention
  ``services.owner_resolution.resolve_user_id`` (ruling 3d-1; one shared
  ``OWNER_FALLBACK_STATS``).
* :func:`owned_cv` — one generated CV by id for its owner. A missing **or
  foreign** id raises ``LookupError`` with the same message (S-10), which every
  CV door already maps to 404 / ``not_found``.
* :func:`job_for_user` — 4a's ``services.job.get_job_for_user`` (the posting
  access rule), with ``OwnedNotFound`` mapped to ``LookupError`` so the CV
  doors keep their existing 404 mapping.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire import ownership
from applire.services.owner_resolution import OWNER_FALLBACK_STATS, resolve_user_id

__all__ = [
    "OWNER_FALLBACK_STATS",
    "resolve_owner",
    "owned_cv",
    "job_for_user",
]


def resolve_owner(user_id: uuid.UUID | None, *, site: str) -> uuid.UUID:
    """The acting user: explicit ``user_id``, else the USER owner context (counted)."""
    return resolve_user_id(user_id, site)


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
