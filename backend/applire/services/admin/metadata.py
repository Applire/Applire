# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-person metadata for the admin's users list — counts and sizes, never content
(ADR-091 cl. 27, S-4, W0B-1).

Everything the admin sees about a person's *data* comes from
:func:`metadata_statements`: aggregate SELECTs whose only columns are ids, owner
keys, timestamps and numeric sizes. ``tests/unit/test_admin_metadata_boundary.py``
compiles each statement and fails on any other column — the S-4 boundary is a
property of the compiled SQL, not of reviewer attention.

Owner keys arrive with package 3a (migrations 0074/0075). This module codes
against them and degrades while one is absent on a branch: a count without its
owner column is ``0``, the AI-usage sum ``None`` (rendered "—").

The statements run under ``unscoped("admin-metadata")`` (ADR-092 cl. 7): they
read owned tables for **every** person, which the owner guard otherwise refuses.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.application import Application
from applire.models.cover_letter import GeneratedCoverLetter
from applire.models.cv import GeneratedCV
from applire.models.llm_usage import LlmUsage
from applire.models.uploads import UploadRecord as Upload
from applire.ownership import unscoped

__all__ = ["AI_USAGE_WINDOW_DAYS", "UserMetadata", "collect", "metadata_statements"]

AI_USAGE_WINDOW_DAYS = 30

#: The only columns a metadata statement may touch (tested on the compiled SQL).
ALLOWED_COLUMNS = frozenset({
    "id", "user_id", "created_at", "deleted_at", "byte_size", "total_tokens",
})


@dataclass
class UserMetadata:
    application_count: int = 0
    document_count: int = 0
    storage_bytes: int | None = 0
    ai_tokens_30d: int | None = None


def _count_by_owner(model, user_ids) -> Select:
    """``(user_id, count)`` per owner — every owned table carries ``user_id``
    since 0074/0075 (the W1 profile-join fallback for chain tables was dead
    code once they did; removed in W3, MD-24 (6))."""
    return (
        select(model.user_id, func.count(model.id))
        .where(model.user_id.in_(user_ids))
        .group_by(model.user_id)
    )


def metadata_statements(user_ids: list[uuid.UUID], *, now: datetime | None = None) -> dict[str, Select]:
    """Aggregate statements keyed by metric; each returns ``(user_id, value)`` rows."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=AI_USAGE_WINDOW_DAYS)
    return {
        "application_count": _count_by_owner(Application, user_ids),
        "cv_count": _count_by_owner(GeneratedCV, user_ids),
        "letter_count": _count_by_owner(GeneratedCoverLetter, user_ids),
        "storage_bytes": (
            select(Upload.user_id, func.coalesce(func.sum(Upload.byte_size), 0))
            .where(Upload.user_id.in_(user_ids))
            .group_by(Upload.user_id)
        ),
        "ai_tokens_30d": (
            select(LlmUsage.user_id, func.coalesce(func.sum(LlmUsage.total_tokens), 0))
            .where(LlmUsage.user_id.in_(user_ids), LlmUsage.created_at >= since)
            .group_by(LlmUsage.user_id)
        ),
    }


async def collect(db: AsyncSession, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, UserMetadata]:
    """Metadata for each id (one query per metric, not per person)."""
    out = {uid: UserMetadata() for uid in user_ids}
    if not user_ids:
        return out
    stmts = metadata_statements(user_ids)
    for meta in out.values():
        meta.ai_tokens_30d = 0  # llm_usage carries user_id: a person without rows used 0
    with unscoped("admin-metadata"):
        for key, stmt in stmts.items():
            for uid, value in (await db.execute(stmt)).all():
                meta = out.get(uid)
                if meta is None:
                    continue
                value = int(value or 0)
                if key in ("cv_count", "letter_count"):
                    meta.document_count += value
                else:
                    setattr(meta, key, value)
    return out
