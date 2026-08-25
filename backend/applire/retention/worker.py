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
  applications     → soft-delete (deleted_at) after 730 days inactivity;
                     CANCELLED applications run on a short clock
                     (CANCELLED_APPLICATION_TTL_DAYS, default 7 — set by the
                     service on cancel) and, once tombstoned, get their
                     generated documents hard-deleted incl. submitted pins
                     (US222/issue #158, ADR-005 amendment 2026-07-13)
  master_profiles  → soft-delete after 730 days inactivity
  users            → soft-delete after 730 days inactivity
  generated_cvs (stale generation jobs) → mark failed after 10 minutes in
                     pending/generating (stale job reaper, arc42 §5.3.4)
  orphan files     → delete upload-volume files no DB row references any more
                     (issue #152, dFMEA SF-PROFILE.5 — e.g. a GDPR erasure whose
                     post-commit file delete failed), after a grace period

Technical debt note: Retention Worker is architecturally isolated but co-located.
  Extract to `applire-ops` when Cloud Edition requires singleton scheduling,
  tenant-scoped deletion, or independent audit SLA.
  Blocked by `applire-core` shared library extraction. | Cloud Edition scale-up |
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from applire.constants import (
    GENERATED_DOCUMENTS_TTL_DAYS as _GENERATED_DOCS_TTL_DAYS,
    INTERVIEW_SESSION_TTL_DAYS as _SESSION_TTL_DAYS,
    ORPHAN_FILE_GRACE_HOURS as _ORPHAN_GRACE_HOURS,
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


async def _scan_orphan_files(db: AsyncSession) -> int:
    """Delete upload-volume files that no DB row references any more.

    Issue #152 (dFMEA SF-PROFILE.5): GDPR erasure deletes rows first, commits,
    then deletes files best-effort — a failed file delete leaves PII on disk
    with nothing pointing at it. Photo replacement (services/photo.py) can
    orphan the old photo file the same way. This scan is the safety net.

    Referenced set = every uploads.file_path row ∪ every
    master_profiles.profile_json.personal_info.photo_url (ALL rows, including
    soft-deleted profiles — a tombstoned profile still owns its photo until
    hard erasure; photos have NO uploads row, so forgetting them here would
    delete every live profile photo).

    Safety rules:
      * storage backends without enumeration support (list_files() → None,
        e.g. Cloud's S3 provider) skip the scan entirely;
      * if the referenced set cannot be built (table absent / DB error) the
        scan deletes NOTHING — fail safe, never fail deletey;
      * files younger than ORPHAN_FILE_GRACE_HOURS are spared: the upload
        flow saves the file before committing its DB row, so a young
        unreferenced file may be an in-flight upload.
    """
    from pathlib import Path

    from applire.storage import get_storage

    storage = get_storage()
    listing = await storage.list_files()
    if listing is None:
        logger.info(
            "Retention: orphan scan skipped (storage backend does not support enumeration)"
        )
        return 0

    referenced: set[str] = set()
    try:
        rows = await db.execute(text("SELECT file_path FROM uploads"))
        referenced.update(row[0] for row in rows.fetchall())

        prof_rows = await db.execute(text("SELECT profile_json FROM master_profiles"))
        for (profile_json,) in prof_rows.fetchall():
            if isinstance(profile_json, str):  # SQLite test harness stores TEXT
                try:
                    profile_json = json.loads(profile_json)
                except ValueError:
                    continue
            if not isinstance(profile_json, dict):
                continue
            photo_url = (profile_json.get("personal_info") or {}).get("photo_url")
            if photo_url:
                referenced.add(photo_url)
    except (ProgrammingError, OperationalError):
        # Can't trust the referenced set → delete nothing this run.
        await db.rollback()
        return 0

    # Compare resolved absolute paths too, in case the configured upload dir
    # is expressed differently between save time and scan time.
    referenced_resolved = {str(Path(p).resolve()) for p in referenced}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=_ORPHAN_GRACE_HOURS)
    deleted = 0
    for path, mtime in listing:
        if path in referenced or str(Path(path).resolve()) in referenced_resolved:
            continue
        if mtime >= cutoff:
            continue  # grace period — possibly an in-flight upload
        try:
            await storage.delete(path)
        except Exception as exc:
            logger.warning("Retention: failed to delete orphan file %s: %s", path, exc)
            continue
        logger.info("Retention: deleted orphan upload file %s", path)
        deleted += 1
    return deleted


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


async def _purge_cancelled_documents(db: AsyncSession) -> tuple[int, int, int]:
    """Hard-delete generated documents of cancelled, tombstoned applications
    (US222/issue #158, ADR-005 amendment 2026-07-13).

    An explicit cancellation ends the processing purpose: once the shortened
    grace window has passed and the application is tombstoned, its documents
    are deleted REGARDLESS of GENERATED_DOCUMENTS_TTL_DAYS — including
    submitted pins (the UI announced the removal date throughout the window,
    so the US219 "never silently expired" principle holds). The linked flow
    session is tombstoned too.

    Guard: documents of a job with ANY live application are spared — protects
    the duplicate-JD reuse path today and, since generated documents carry no
    user column, the multi-user seam (job_analyses rows are shared) tomorrow.
    Returns (cvs_deleted, cover_letters_deleted, flow_sessions_tombstoned).
    """
    now = datetime.now(timezone.utc)
    cancelled = "cancelled"
    try:
        # Release pins pointing at rows this purge is about to delete (the FK
        # would otherwise block the DELETE on Postgres — same pattern as the
        # calendar-TTL purges above).
        await db.execute(
            text(
                "UPDATE applications SET submitted_cv_id = NULL "
                "WHERE deleted_at IS NOT NULL AND submitted_cv_id IS NOT NULL "
                "AND EXISTS ("
                "  SELECT 1 FROM generated_cvs c "
                "  WHERE c.id = applications.submitted_cv_id AND c.deleted_at IS NULL "
                "  AND EXISTS (SELECT 1 FROM applications a "
                "    WHERE a.deleted_at IS NOT NULL AND a.user_status = :st "
                "    AND a.job_analysis_id = c.job_analysis_id) "
                "  AND NOT EXISTS (SELECT 1 FROM applications b "
                "    WHERE b.deleted_at IS NULL "
                "    AND b.job_analysis_id = c.job_analysis_id)"
                ")"
            ),
            {"st": cancelled},
        )
        await db.execute(
            text(
                "UPDATE applications SET submitted_cover_letter_id = NULL "
                "WHERE deleted_at IS NOT NULL AND submitted_cover_letter_id IS NOT NULL "
                "AND EXISTS ("
                "  SELECT 1 FROM generated_cover_letters l "
                "  WHERE l.id = applications.submitted_cover_letter_id AND l.deleted_at IS NULL "
                "  AND EXISTS (SELECT 1 FROM applications a "
                "    WHERE a.deleted_at IS NOT NULL AND a.user_status = :st "
                "    AND a.job_analysis_id = l.job_analysis_id) "
                "  AND NOT EXISTS (SELECT 1 FROM applications b "
                "    WHERE b.deleted_at IS NULL "
                "    AND b.job_analysis_id = l.job_analysis_id)"
                ")"
            ),
            {"st": cancelled},
        )
        # Release flow-session artifact references to the doomed rows — the FKs
        # flow_sessions_generated_cv_id_fkey / _generated_cover_letter_id_fkey
        # otherwise abort the DELETE on Postgres (found on the live stack;
        # SQLite unit tests don't enforce FKs).
        await db.execute(
            text(
                "UPDATE flow_sessions SET generated_cv_id = NULL "
                "WHERE generated_cv_id IS NOT NULL "
                "AND EXISTS ("
                "  SELECT 1 FROM generated_cvs c "
                "  WHERE c.id = flow_sessions.generated_cv_id AND c.deleted_at IS NULL "
                "  AND EXISTS (SELECT 1 FROM applications a "
                "    WHERE a.deleted_at IS NOT NULL AND a.user_status = :st "
                "    AND a.job_analysis_id = c.job_analysis_id) "
                "  AND NOT EXISTS (SELECT 1 FROM applications b "
                "    WHERE b.deleted_at IS NULL "
                "    AND b.job_analysis_id = c.job_analysis_id)"
                ")"
            ),
            {"st": cancelled},
        )
        await db.execute(
            text(
                "UPDATE flow_sessions SET generated_cover_letter_id = NULL "
                "WHERE generated_cover_letter_id IS NOT NULL "
                "AND EXISTS ("
                "  SELECT 1 FROM generated_cover_letters l "
                "  WHERE l.id = flow_sessions.generated_cover_letter_id AND l.deleted_at IS NULL "
                "  AND EXISTS (SELECT 1 FROM applications a "
                "    WHERE a.deleted_at IS NOT NULL AND a.user_status = :st "
                "    AND a.job_analysis_id = l.job_analysis_id) "
                "  AND NOT EXISTS (SELECT 1 FROM applications b "
                "    WHERE b.deleted_at IS NULL "
                "    AND b.job_analysis_id = l.job_analysis_id)"
                ")"
            ),
            {"st": cancelled},
        )
        cvs_result = await db.execute(
            text(
                "DELETE FROM generated_cvs WHERE deleted_at IS NULL "
                "AND EXISTS (SELECT 1 FROM applications a "
                "  WHERE a.deleted_at IS NOT NULL AND a.user_status = :st "
                "  AND a.job_analysis_id = generated_cvs.job_analysis_id) "
                "AND NOT EXISTS (SELECT 1 FROM applications b "
                "  WHERE b.deleted_at IS NULL "
                "  AND b.job_analysis_id = generated_cvs.job_analysis_id)"
            ),
            {"st": cancelled},
        )
        cls_result = await db.execute(
            text(
                "DELETE FROM generated_cover_letters WHERE deleted_at IS NULL "
                "AND EXISTS (SELECT 1 FROM applications a "
                "  WHERE a.deleted_at IS NOT NULL AND a.user_status = :st "
                "  AND a.job_analysis_id = generated_cover_letters.job_analysis_id) "
                "AND NOT EXISTS (SELECT 1 FROM applications b "
                "  WHERE b.deleted_at IS NULL "
                "  AND b.job_analysis_id = generated_cover_letters.job_analysis_id)"
            ),
            {"st": cancelled},
        )
        flows_result = await db.execute(
            text(
                "UPDATE flow_sessions SET deleted_at = :now "
                "WHERE deleted_at IS NULL AND application_id IN ("
                "  SELECT id FROM applications "
                "  WHERE deleted_at IS NOT NULL AND user_status = :st"
                ")"
            ),
            {"now": now, "st": cancelled},
        )
        await db.commit()
        return (
            cvs_result.rowcount,   # type: ignore[return-value]
            cls_result.rowcount,   # type: ignore[return-value]
            flows_result.rowcount,  # type: ignore[return-value]
        )
    except (ProgrammingError, OperationalError, IntegrityError) as exc:
        # IntegrityError included so an unforeseen FK can never abort the whole
        # nightly run — the row survives to the next run, the report shows 0.
        logger.warning("_purge_cancelled_documents skipped: %s", exc)
        await db.rollback()
        return (0, 0, 0)


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



async def _release_fact_pins(db: AsyncSession) -> int:
    """Clear fact-pin quote copies on tombstoned applications (ADR-077 cl. 7).

    A fact pin's ``quote`` is a verbatim copy of the candidate's vault prose
    living on the applications row. While the application is live it serves
    the user's pin; once the row is tombstoned it is a purposeless copy of
    personal data — released in the same sweep that releases the
    submitted-document pins. Data minimisation, not an FK necessity (fact
    pins reference profile entries, not generated documents).
    """
    try:
        result = await db.execute(
            text(
                "UPDATE applications SET pinned_facts = NULL "
                "WHERE deleted_at IS NOT NULL AND pinned_facts IS NOT NULL"
            )
        )
        await db.commit()
        return result.rowcount  # type: ignore[return-value]
    except (ProgrammingError, OperationalError) as exc:
        logger.warning("_release_fact_pins skipped: %s", exc)
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
        # After the tombstone sweep: an application tombstoned TODAY releases
        # its fact-pin quotes in the same run (ADR-077 clause 7).
        fact_pins_released = await _release_fact_pins(db)
        # After the tombstone sweep so a cancelled application whose grace
        # window ended TODAY purges in the same run (US222).
        (
            cancelled_cvs_deleted,
            cancelled_cover_letters_deleted,
            cancelled_flows_tombstoned,
        ) = await _purge_cancelled_documents(db)
        stale_cv_jobs_failed = await _reap_stale_cv_jobs(db)
        cover_letters_deleted = await _purge_cover_letters(db)
        stale_cl_jobs_failed = await _reap_stale_cl_jobs(db)
        import_jobs_deleted = await _purge_import_jobs(db)
        gap_jobs_deleted = await _purge_gap_jobs(db)
        # After _purge_uploads so files whose rows were just TTL-purged are not
        # double-counted; anything its file pass failed to remove ages past the
        # grace period and is reclaimed here on a later run.
        orphan_files_deleted = await _scan_orphan_files(db)

    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "uploads_deleted": uploads_deleted,
        "interview_sessions_deleted": sessions_deleted,
        "generated_cvs_deleted": cvs_deleted,
        "master_profiles_tombstoned": profiles_tombstoned,
        "users_tombstoned": users_tombstoned,
        "applications_tombstoned": applications_tombstoned,
        "fact_pins_released": fact_pins_released,
        "cancelled_cvs_deleted": cancelled_cvs_deleted,
        "cancelled_cover_letters_deleted": cancelled_cover_letters_deleted,
        "cancelled_flows_tombstoned": cancelled_flows_tombstoned,
        "stale_cv_jobs_failed": stale_cv_jobs_failed,
        "generated_cover_letters_deleted": cover_letters_deleted,
        "stale_cl_jobs_failed": stale_cl_jobs_failed,
        "cv_import_jobs_deleted": import_jobs_deleted,
        "gap_analysis_jobs_deleted": gap_jobs_deleted,
        "submitted_exempt": submitted_exempt,
        "orphan_files_deleted": orphan_files_deleted,
    }
    print(json.dumps(report), flush=True)
