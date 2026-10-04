# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Owned-row lookup for the letter/interview/gap services (ADR-092 cl. 6; Strawberry W2, 3d).

``owned_row`` is the by-id read the 3d services use where the existing
``LookupError`` message and the routers' 404 mapping must stay unchanged: it
returns ``None`` for a missing **and** a foreign id alike (S-10), and the caller
raises its own ``LookupError``. Owner resolution itself is the shared
``services.owner_resolution.resolve_user_id`` (ruling 3d-1); posting access is
``services.job.get_job_for_user`` (4a).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def owned_row(db: AsyncSession, Model: type, row_id: Any, user_id: uuid.UUID, *, live: bool = True):
    """``Model`` row ``row_id`` owned by ``user_id`` (live only by default), else ``None``."""
    stmt = select(Model).where(Model.id == row_id, Model.user_id == user_id)
    if live and hasattr(Model, "deleted_at"):
        stmt = stmt.where(Model.deleted_at.is_(None))
    return (await db.execute(stmt)).scalar_one_or_none()
