# Copyright (C) 2024-2026 Tobias Rosenbaum
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

"""GDPR retention worker — runs TTL sweeps and emits a JSON report (ADR 005).

Rules (ADR 005 v2 — amended iter17; submitted-pin exemption 2026-07-06):
  uploads          → hard-delete after 7 days
  interview_sessions → hard-delete after 30 days
  generated_cvs    → hard-delete after expires_at, UNLESS pinned as submitted
                     on an active application (E039/US219 — same for cover
                     letters; report field: submitted_exempt)
  applications     → soft-delete (deleted_at) after 730 days inactivity
  master_profiles  → soft-delete after 730 days inactivity
  users            → soft-delete after 730 days inactivity
  generated_cvs (stale generation jobs) → mark failed after 10 minutes in
                     pending/generating (stale job reaper, arc42 §5.3.4)

Technical debt note: Retention Worker is architecturally isolated but co-located.
  Extract to `applire-ops` when Cloud Edition requires singleton scheduling,
  tenant-scoped deletion, or independent audit SLA.
  Blocked by `applire-core` shared library extraction. | Cloud Edition scale-up |
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text, update
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from applire.constants import (
    GENERATED_DOCUMENTS_TTL_DAYS as _GENERATED_DOCS_TTL_DAYS,
    INTERVIEW_SESSION_TTL_DAYS as _SESSION_TTL_DAYS,
    PROFILE_INACTIVITY_TTL_DAYS as _INACTIVITY_TTL_DAYS,
    UPLOAD_TTL_DAYS as _UPLOADS_TTL_DAYS,
)
from applire.db.session import AsyncSessionLocal
from applire.models.application import Application
from applire.models.cv import CVGenerationStatus, GeneratedCV
from applire.models.profile import MasterProfile
from applire.models.session import InterviewSession
from applire.models.user import User

logger = logging.getLogger(__name__)

_STALE_CV_JOB_MINUTES = 10        # pending/generating → failed after this long


async def _purge_uploads(db: AsyncSession) -> int:
    """Hard-delete uploads older than 7 days and remove their physical files.

    Collects file paths first, then deletes DB rows, then deletes files so that
    a storage I/O error cannot block the DB deletion (mirrors GDPR erasure).
    Catches ProgrammingError gracefully so the worker runs cleanly when the
    table is absent (anticipated-but-not-yet-created pattern).
    """
    from applire.storage import get_storage

    cutoff = datetime.now(timezone.utc) - timedelta(days=_UPLOADS_TTL_DAYS)
    try:
        rows = await db.execute(
            text("SELECT file_path FROM uploads WHERE created_at < :cutoff"),
            {"cutoff": cutoff},
        )
        file_paths: list[str] = [row[0] for row in rows.fetchall()]

        result = await db.execute(
            text("DELETE FROM uploads WHERE created_at < :cutoff"),
            {"cutoff": cutoff},
        )
        await db.commit()
    except (ProgrammingError, OperationalError):
        # ProgrammingError: PostgreSQL "table does not exist"
        # OperationalError: SQLite "no such table" (test environments)
        await db.rollback()
        return 0

    storage = get_storage()
    for path in file_paths:
        try:
            await storage.delete(path)
        except Exception as exc:
            logger.warning("Retention: failed to delete upload file %s: %s", path, exc)

    return result.rowcount  # type: ignore[return-value]


async def _purge_sessions(db: AsyncSession) -> int:
    """Hard-delete interview sessions inactive for more than 30 days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_SESSION_TTL_DAYS)
    try:
        result = await db.execute(
            text(
                "DELETE FROM interview_sessions WHERE updated_at < :cutoff"
            ),
            {"cutoff": cutoff},
        )
        await db.commit()
        return result.rowcount  # type: ignore[return-value]
    except (ProgrammingError, OperationalError):
        await db.rollback()
        return 0


async def _purge_cvs(db: AsyncSession) -> int:
    """Hard-delete generated CVs whose expires_at is in the past.

    Submitted-pin exemption (E039/US219, ADR-005 amendment 2026-07-06): a CV
    pinned as submitted on an ACTIVE application follows the application
    lifecycle, not the calendar TTL. Once the application is tombstoned the
    NOT EXISTS guard stops matching and the row purges on the next run.
    """
    now = datetime.now(timezone.utc)
    try:
        # A tombstoned application no longer protects its pin, but its FK still
        # POINTS at the row — Postgres rejects the DELETE unless the pin is
        # released first. Only pins on rows this purge is about to delete are
        # touched, so a reactivated application keeps its pin while the
        # document is alive.
        await db.execute(
            text(
                "UPDATE applications SET submitted_cv_id = NULL "
                "WHERE deleted_at IS NOT NULL AND submitted_cv_id IS NOT NULL "
                "AND EXISTS ("
                "  SELECT 1 FROM generated_cvs c "
                "  WHERE c.id = applications.submitted_cv_id "
                "  AND c.expires_at < :now AND c.deleted_at IS NULL"
                ")"
            ),
            {"now": now},
        )
        result = await db.execute(
            text(
                "DELETE FROM generated_cvs WHERE expires_at < :now AND deleted_at IS NULL "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM applications a "
                "  WHERE a.submitted_cv_id = generated_cvs.id AND a.deleted_at IS NULL"
                ")"
            ),
            {"now": now},
        )
        await db.commit()
        return result.rowcount  # type: ignore[return-value]
    except (ProgrammingError, OperationalError):
        await db.rollback()
        return 0


async def _purge_import_jobs(db: AsyncSession) -> int:
    """Hard-delete async CV-import jobs past their (short) TTL. These are ephemeral
    handles consumed by the polling UI within minutes; expires_at keeps the table from
    growing unbounded (E036 follow-up — async import)."""
    # Bind the datetime object, not an ISO string: asyncpg infers the bind type from
    # the timestamptz column and rejects a str ("expected a datetime … got str"),
    # crashing the worker on Postgres. SQLite (unit tests) accepted the string via a
    # lexical TEXT compare, which hid the bug — the other purges here bind a datetime.
    now = datetime.now(timezone.utc)
    try:
        result = await db.execute(
            text("DELETE FROM cv_import_jobs WHERE expires_at < :now AND deleted_at IS NULL"),
            {"now": now},
        )
        await db.commit()
        return result.rowcount  # type: ignore[return-value]
    except (ProgrammingError, OperationalError):
        await db.rollback()
        return 0


async def _purge_gap_jobs(db: AsyncSession) -> int:
    """Hard-delete async gap-analysis jobs past their (short) TTL. Ephemeral handles
    consumed by the polling UI within minutes; expires_at keeps the table from growing
    unbounded (E037 N2 — async gap analysis)."""
    # Bind the datetime object, not an ISO string: asyncpg infers the bind type from
    # the timestamptz column and rejects a str ("expected a datetime … got str"),
    # crashing the worker on Postgres. SQLite (unit tests) accepted the string via a
    # lexical TEXT compare, which hid the bug — the other purges here bind a datetime.
    now = datetime.now(timezone.utc)
    try:
        result = await db.execute(
            text("DELETE FROM gap_analysis_jobs WHERE expires_at < :now AND deleted_at IS NULL"),
            {"now": now},
        )
        await db.commit()
        return result.rowcount  # type: ignore[return-value]
    except (ProgrammingError, OperationalError):
        await db.rollback()
        return 0


async def _tombstone_inactive_profiles(db: AsyncSession) -> int:
    """Soft-delete master profiles inactive for ≥ 24 months."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_INACTIVITY_TTL_DAYS)
    now = datetime.now(timezone.utc)
    try:
        result = await db.execute(
            update(MasterProfile)
            .where(MasterProfile.updated_at < cutoff)
            .where(MasterProfile.deleted_at.is_(None))
            .values(deleted_at=now)
        )
        await db.commit()
        return result.rowcount  # type: ignore[return-value]
    except (ProgrammingError, OperationalError) as exc:
        logger.warning("_tombstone_inactive_profiles skipped: %s", exc)
        await db.rollback()
        return 0


async def _tombstone_inactive_users(db: AsyncSession) -> int:
    """Soft-delete users inactive for ≥ 24 months (based on profile activity)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_INACTIVITY_TTL_DAYS)
    now = datetime.now(timezone.utc)
    try:
        result = await db.execute(
            update(User)
            .where(User.created_at < cutoff)
            .where(User.deleted_at.is_(None))
            .values(deleted_at=now)
        )
        await db.commit()
        return result.rowcount  # type: ignore[return-value]
    except (ProgrammingError, OperationalError) as exc:
        logger.warning("_tombstone_inactive_users skipped: %s", exc)
        await db.rollback()
        return 0


async def _tombstone_inactive_applications(db: AsyncSession) -> int:
    """Soft-delete applications whose inactivity timer has expired (730 days).

    The expires_at column is reset on every update (status change, notes, workflow
    advancement). This is an inactivity timer, not a creation timer (ADR 005 v2).
    """
    now = datetime.now(timezone.utc)
    try:
        result = await db.execute(
            update(Application)
            .where(Application.expires_at < now)
            .where(Application.deleted_at.is_(None))
            .values(deleted_at=now)
        )
        await db.commit()
        return result.rowcount  # type: ignore[return-value]
    except (ProgrammingError, OperationalError) as exc:
        logger.warning("_tombstone_inactive_applications skipped: %s", exc)
        await db.rollback()
        return 0


async def _reap_stale_cv_jobs(db: AsyncSession) -> int:
    """Mark CV generation jobs stuck in pending/generating for > 10 minutes as failed.

    Prevents ghost jobs when the BackgroundTasks process crashes mid-render.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_STALE_CV_JOB_MINUTES)
    now = datetime.now(timezone.utc)
    try:
        result = await db.execute(
            update(GeneratedCV)
            .where(
                GeneratedCV.status.in_(
                    [CVGenerationStatus.pending.value, CVGenerationStatus.generating.value]
                )
            )
            .where(GeneratedCV.created_at < cutoff)
            .where(GeneratedCV.deleted_at.is_(None))
            .values(
                status=CVGenerationStatus.failed.value,
                error_message="Generation timed out (stale job reaper)",
            )
        )
        await db.commit()
        return result.rowcount  # type: ignore[return-value]
    except (ProgrammingError, OperationalError):
        await db.rollback()
        return 0


async def _purge_cover_letters(db: AsyncSession) -> int:
    """Hard-delete generated cover letters whose expires_at is in the past.

    Same submitted-pin exemption as _purge_cvs (E039/US219, ADR-005 amendment),
    including the release of tombstoned applications' pins before the DELETE
    (the FK would otherwise block the purge on Postgres).
    """
    now = datetime.now(timezone.utc)
    try:
        await db.execute(
            text(
                "UPDATE applications SET submitted_cover_letter_id = NULL "
                "WHERE deleted_at IS NOT NULL AND submitted_cover_letter_id IS NOT NULL "
                "AND EXISTS ("
                "  SELECT 1 FROM generated_cover_letters l "
                "  WHERE l.id = applications.submitted_cover_letter_id "
                "  AND l.expires_at < :now AND l.deleted_at IS NULL"
                ")"
            ),
            {"now": now},
        )
        result = await db.execute(
            text(
                "DELETE FROM generated_cover_letters WHERE expires_at < :now AND deleted_at IS NULL "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM applications a "
                "  WHERE a.submitted_cover_letter_id = generated_cover_letters.id "
                "  AND a.deleted_at IS NULL"
                ")"
            ),
            {"now": now},
        )
        await db.commit()
        return result.rowcount  # type: ignore[return-value]
    except (ProgrammingError, OperationalError):
        await db.rollback()
        return 0


async def _count_submitted_exempt(db: AsyncSession) -> int:
    """Count expired rows spared this run by the submitted-pin exemption — CVs
    plus cover letters pinned on an active application (ADR-005 auditability:
    the JSON report must show what was deliberately NOT purged)."""
    now = datetime.now(timezone.utc)
    try:
        result = await db.execute(
            text(
                "SELECT "
                "(SELECT COUNT(*) FROM generated_cvs c "
                " WHERE c.expires_at < :now AND c.deleted_at IS NULL "
                " AND EXISTS (SELECT 1 FROM applications a "
                "   WHERE a.submitted_cv_id = c.id AND a.deleted_at IS NULL)) "
                "+ "
                "(SELECT COUNT(*) FROM generated_cover_letters l "
                " WHERE l.expires_at < :now AND l.deleted_at IS NULL "
                " AND EXISTS (SELECT 1 FROM applications a "
                "   WHERE a.submitted_cover_letter_id = l.id AND a.deleted_at IS NULL))"
            ),
            {"now": now},
        )
        return result.scalar_one()
    except (ProgrammingError, OperationalError):
        await db.rollback()
        return 0


async def _reap_stale_cl_jobs(db: AsyncSession) -> int:
    """Mark cover letter generation jobs stuck > 10 minutes in pending/generating as failed."""
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_STALE_CV_JOB_MINUTES)
    try:
        result = await db.execute(
            update(GeneratedCoverLetter)
            .where(
                GeneratedCoverLetter.status.in_(
                    [CoverLetterStatus.pending.value, CoverLetterStatus.generating.value]
                )
            )
            .where(GeneratedCoverLetter.created_at < cutoff)
            .where(GeneratedCoverLetter.deleted_at.is_(None))
            .values(
                status=CoverLetterStatus.failed.value,
                error_message="Generation timed out (stale job reaper)",
            )
        )
        await db.commit()
        return result.rowcount  # type: ignore[return-value]
    except (ProgrammingError, OperationalError):
        await db.rollback()
        return 0


async def run() -> None:
    """Execute all TTL rules and emit a structured JSON report to stdout."""
    async with AsyncSessionLocal() as db:
        uploads_deleted = await _purge_uploads(db)
        sessions_deleted = await _purge_sessions(db)
        # Counted before the purges: the exempt rows are exactly the ones the
        # guarded DELETEs skip, so ordering doesn't change the number — but
        # counting first keeps the report honest if a later purge errors.
        submitted_exempt = await _count_submitted_exempt(db)
        cvs_deleted = await _purge_cvs(db)
        profiles_tombstoned = await _tombstone_inactive_profiles(db)
        users_tombstoned = await _tombstone_inactive_users(db)
        applications_tombstoned = await _tombstone_inactive_applications(db)
        stale_cv_jobs_failed = await _reap_stale_cv_jobs(db)
        cover_letters_deleted = await _purge_cover_letters(db)
        stale_cl_jobs_failed = await _reap_stale_cl_jobs(db)
        import_jobs_deleted = await _purge_import_jobs(db)
        gap_jobs_deleted = await _purge_gap_jobs(db)

    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "uploads_deleted": uploads_deleted,
        "interview_sessions_deleted": sessions_deleted,
        "generated_cvs_deleted": cvs_deleted,
        "master_profiles_tombstoned": profiles_tombstoned,
        "users_tombstoned": users_tombstoned,
        "applications_tombstoned": applications_tombstoned,
        "stale_cv_jobs_failed": stale_cv_jobs_failed,
        "generated_cover_letters_deleted": cover_letters_deleted,
        "stale_cl_jobs_failed": stale_cl_jobs_failed,
        "cv_import_jobs_deleted": import_jobs_deleted,
        "gap_analysis_jobs_deleted": gap_jobs_deleted,
        "submitted_exempt": submitted_exempt,
    }
    print(json.dumps(report), flush=True)
