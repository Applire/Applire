# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Failed background jobs for the admin dashboard (#694, ADR-086 amended 2026-10-07).

Metadata only (S-4, RD-6): kind, ids, time and a stable ``error_code``. **Never**
``error_message`` — it is free text and can quote document content (FMEA N-16).
The statements are column-allowlisted exactly like ``services/admin/metadata.py``
and run under ``unscoped("admin-metadata")`` (ADR-092 cl. 7).

The tables keep no failure timestamp; ``failed_at`` is the job's ``created_at``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.cover_letter import GeneratedCoverLetter
from applire.models.cv import GeneratedCV
from applire.models.gap_job import GapAnalysisJob
from applire.models.import_job import CVImportJob
from applire.ownership import unscoped

WINDOW_DAYS = 7
MAX_ITEMS = 20

#: Every column the statements may read. No text column besides the stable code.
ALLOWED_COLUMNS = frozenset({"id", "user_id", "status", "created_at", "deleted_at", "error_code"})


@dataclass(frozen=True)
class FailedJob:
    kind: str
    id: uuid.UUID
    user_id: uuid.UUID | None
    failed_at: datetime | None
    error_code: str | None


def _one(kind: str, model, since: datetime, *, has_code: bool) -> Select:
    code = model.error_code if has_code else literal(None).label("error_code")
    return select(
        literal(kind).label("kind"),
        model.id.label("id"),
        model.user_id.label("user_id"),
        model.created_at.label("failed_at"),
        code.label("error_code") if has_code else code,
    ).where(model.status == "failed", model.created_at >= since, model.deleted_at.is_(None))


def failed_job_statements(*, now: datetime | None = None) -> dict[str, Select]:
    since = (now or datetime.now(timezone.utc)) - timedelta(days=WINDOW_DAYS)
    return {
        "cv": _one("cv", GeneratedCV, since, has_code=True),
        "cover_letter": _one("cover_letter", GeneratedCoverLetter, since, has_code=False),
        "import": _one("import", CVImportJob, since, has_code=True),
        "gap": _one("gap", GapAnalysisJob, since, has_code=True),
    }


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def collect(db: AsyncSession, *, now: datetime | None = None) -> tuple[int, list[FailedJob]]:
    """``(count, newest MAX_ITEMS items)`` of failed jobs in the window."""
    stmts = failed_job_statements(now=now)
    with unscoped("admin-metadata"):
        rows = (await db.execute(union_all(*stmts.values()))).all()
    jobs = [FailedJob(r.kind, r.id, r.user_id, _aware(r.failed_at), r.error_code) for r in rows]
    jobs.sort(key=lambda j: j.failed_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return len(jobs), jobs[:MAX_ITEMS]
