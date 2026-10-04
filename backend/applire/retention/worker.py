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
  users            → soft-delete after 730 days INACTIVITY — keyed on
                     coalesce(last_active_at, created_at), never on sign-up
                     date alone; an admin is never tombstoned (D-4, ADR-005
                     amended 2026-10-03 / ADR-092 cl. 12a)
  job_analyses     → shared postings no row of any user references, older
                     than INTERVIEW_SESSION_TTL_DAYS, hard-deleted with the
                     erasure's lock-then-check predicate (ADR-092 cl. 11/12c)
  auth_sessions    → revoked / idle-expired / absolute-expired rows purged;
  auth_links, reauth_grants → used or expired rows purged (ADR-005 amended)
  audit_events     → older than AUDIT_LOG_RETENTION_DAYS (0 = keep) deleted —
                     the only DELETE the audit table permits (ADR-091 cl. 26)

Ownership (ADR-092 cl. 7/8): the sweep is a declared cross-user entry point —
it runs under ``unscoped("retention")``; the per-row TTLs stay global (cl. 12d).
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
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from applire.constants import (
    GENERATED_DOCUMENTS_TTL_DAYS as _GENERATED_DOCS_TTL_DAYS,
    INTERVIEW_SESSION_TTL_DAYS as _SESSION_TTL_DAYS,
    ORPHAN_FILE_GRACE_HOURS as _ORPHAN_GRACE_HOURS,
    PROFILE_INACTIVITY_TTL_DAYS as _INACTIVITY_TTL_DAYS,
    UPLOAD_TTL_DAYS as _UPLOADS_TTL_DAYS,
)
from applire import ownership
from applire.db.session import AsyncSessionLocal
from applire.models.application import Application
from applire.models.cv import CVGenerationStatus, GeneratedCV
from applire.models.profile import MasterProfile
from applire.models.session import InterviewSession
from applire.models.user import ROLE_ADMIN, User

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
    hard erasure) ∪ every user_settings.signature_path (#359, ADR-088).

    Every binary reference is enumerated EXPLICITLY, and that is the reason this
    docstring is long: no stored image has an `uploads` row, so a binary
    reference missing from this set is not merely unprotected — its files are
    actively deleted once past the grace period. The signature lives on
    `user_settings` rather than in the profile JSONB (ADR-088), which is
    precisely why it needs its own SELECT here instead of riding the profile
    scan. Any future stored binary is added in the same breath as its service
    module, or it is a data-loss bug with a 24-hour fuse.

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

        # #359 / ADR-088: the signature image's path lives on user_settings, not
        # in the profile JSONB, so it needs its own read. Same failure mode as
        # the photo if omitted — the file is deleted, not merely unprotected.
        sig_rows = await db.execute(
            text("SELECT signature_path FROM user_settings WHERE signature_path IS NOT NULL")
        )
        referenced.update(row[0] for row in sig_rows.fetchall() if row[0])
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
        # flow_sessions_interview_session_id_fkey: release the flow's pointer to
        # a session this purge deletes, or Postgres aborts the DELETE (and the
        # run). Found on the W3 real boot (Postgres); SQLite enforces no FK.
        await db.execute(
            text(
                "UPDATE flow_sessions SET interview_session_id = NULL "
                "WHERE interview_session_id IN ("
                "  SELECT id FROM interview_sessions WHERE updated_at < :cutoff)"
            ),
            {"cutoff": cutoff},
        )
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
        # flow_sessions_generated_cv_id_fkey: same release for the flow's
        # pointer, limited to exactly the rows the DELETE below removes.
        await db.execute(
            text(
                "UPDATE flow_sessions SET generated_cv_id = NULL "
                "WHERE generated_cv_id IN ("
                "  SELECT c.id FROM generated_cvs c "
                "  WHERE c.expires_at < :now AND c.deleted_at IS NULL "
                "  AND NOT EXISTS (SELECT 1 FROM applications a "
                "    WHERE a.submitted_cv_id = c.id AND a.deleted_at IS NULL))"
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
    """Soft-delete accounts INACTIVE for ≥ 24 months (D-4, ADR-092 cl. 12a).

    Inactivity = ``coalesce(last_active_at, created_at)`` — the last sign-in /
    authenticated write, falling back to sign-up for an account that never
    acted. The pre-Strawberry query keyed on ``created_at`` alone and would
    have tombstoned every real account 24 months after it was created, however
    active. An **admin is never tombstoned** (last-admin protection, ADR-091
    cl. 7): an instance whose only admin went quiet must stay claimable by them.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=_INACTIVITY_TTL_DAYS)
    now = datetime.now(timezone.utc)
    try:
        result = await db.execute(
            update(User)
            .where(
                func.coalesce(User.last_active_at, User.created_at, type_=User.created_at.type)
                < cutoff
            )
            .where(User.role != ROLE_ADMIN)
            .where(User.deleted_at.is_(None))
            .values(deleted_at=now)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        return result.rowcount  # type: ignore[return-value]
    except (ProgrammingError, OperationalError) as exc:
        logger.warning("_tombstone_inactive_users skipped: %s", exc)
        await db.rollback()
        return 0


async def _purge_orphan_postings(db: AsyncSession) -> int:
    """Hard-delete shared postings nobody references any more (ADR-092 cl. 12c).

    ``job_analyses`` is an instance-wide cache without an owner (S-17); before
    Strawberry such rows lived forever. The predicate is the erasure's
    (``services.erasure.purge_unreferenced_postings``: lock the candidates,
    then a fresh ``DELETE … NOT EXISTS`` over all seven referencing tables), so
    a posting a user links to concurrently survives. Age floor: older than
    ``INTERVIEW_SESSION_TTL_DAYS`` — a posting analysed a minute ago whose
    link is still being created is not an orphan.
    """
    from applire.services.erasure import purge_unreferenced_postings

    cutoff = datetime.now(timezone.utc) - timedelta(days=_SESSION_TTL_DAYS)
    try:
        n = await purge_unreferenced_postings(db, None, created_before=cutoff)
        await db.commit()
        return n
    except IntegrityError as exc:
        # A link landed between the lock and the delete on a backend without
        # row locks; the next run collects whatever is still orphaned.
        logger.warning("_purge_orphan_postings deferred to the next run: %s", exc)
        await db.rollback()
        return 0
    except (ProgrammingError, OperationalError) as exc:
        logger.warning("_purge_orphan_postings skipped: %s", exc)
        await db.rollback()
        return 0


async def _purge_auth_housekeeping(db: AsyncSession) -> dict[str, int]:
    """Purge dead credentials (ADR-005 amended 2026-10-03, item 4).

    ``auth_sessions``: revoked, idle past ``IDLE_TIMEOUT`` or older than
    ``ABSOLUTE_TIMEOUT`` — none of them can authenticate any more (RD-8).
    ``auth_links`` / ``reauth_grants``: used or expired. Personal tokens are kept
    (a revoked token stays listed with its revocation date).
    """
    from applire.auth.sessions import ABSOLUTE_TIMEOUT, IDLE_TIMEOUT
    from applire.models.auth import AuthLink, AuthSession, ReauthGrant

    now = datetime.now(timezone.utc)
    out = {"auth_sessions_deleted": 0, "auth_links_deleted": 0, "reauth_grants_deleted": 0}
    try:
        dead_sessions = or_(
            AuthSession.revoked_at.is_not(None),
            AuthSession.last_seen_at < now - IDLE_TIMEOUT,
            AuthSession.created_at < now - ABSOLUTE_TIMEOUT,
        )
        # Grants first: reauth_grants.session_id → auth_sessions (CASCADE on
        # Postgres; explicit here so the order holds on every backend).
        r = await db.execute(
            delete(ReauthGrant).where(
                or_(
                    ReauthGrant.used_at.is_not(None),
                    ReauthGrant.expires_at < now,
                    ReauthGrant.session_id.in_(select(AuthSession.id).where(dead_sessions)),
                )
            )
        )
        out["reauth_grants_deleted"] = r.rowcount or 0
        r = await db.execute(delete(AuthSession).where(dead_sessions))
        out["auth_sessions_deleted"] = r.rowcount or 0
        r = await db.execute(
            delete(AuthLink).where(or_(AuthLink.used_at.is_not(None), AuthLink.expires_at < now))
        )
        out["auth_links_deleted"] = r.rowcount or 0
        await db.commit()
    except (ProgrammingError, OperationalError) as exc:
        logger.warning("_purge_auth_housekeeping skipped: %s", exc)
        await db.rollback()
    return out


async def _purge_audit_events(db: AsyncSession) -> int:
    """Delete audit rows older than ``AUDIT_LOG_RETENTION_DAYS`` (default 730;
    ``0`` = keep forever) — the only DELETE the append-only audit table permits
    (ADR-091 cl. 26; the Postgres trigger refuses UPDATE, allows DELETE)."""
    from applire.config import settings as _settings
    from applire.models.audit import AuditEvent

    days = int(getattr(_settings, "audit_log_retention_days", 730) or 0)
    if days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        r = await db.execute(delete(AuditEvent).where(AuditEvent.at < cutoff))
        await db.commit()
        return r.rowcount or 0
    except (ProgrammingError, OperationalError) as exc:
        logger.warning("_purge_audit_events skipped: %s", exc)
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
        # fk_flow_sessions_cover_letter: release the flow's pointer to exactly
        # the letters the DELETE below removes (W3 real boot: the run aborted).
        await db.execute(
            text(
                "UPDATE flow_sessions SET generated_cover_letter_id = NULL "
                "WHERE generated_cover_letter_id IN ("
                "  SELECT l.id FROM generated_cover_letters l "
                "  WHERE l.expires_at < :now AND l.deleted_at IS NULL "
                "  AND NOT EXISTS (SELECT 1 FROM applications a "
                "    WHERE a.submitted_cover_letter_id = l.id AND a.deleted_at IS NULL))"
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


async def _purge_llm_usage(db: AsyncSession) -> int:
    """Delete token-usage rows past LLM_USAGE_RETENTION_DAYS (ADR-086 cl. 10).

    Not a GDPR clock — `llm_usage` carries counters and opaque ids, never prompt
    or completion text (the table has no text column at all, SF-OPS.9). This is
    a growth bound on a table the ops layer's own disk probe watches.
    """
    try:
        from applire.services.ops.config import LLM_USAGE_RETENTION_DAYS
        from applire.services.ops.usage_report import purge_old_usage

        return await purge_old_usage(db, LLM_USAGE_RETENTION_DAYS)
    except (ProgrammingError, OperationalError) as exc:
        logger.warning("_purge_llm_usage skipped: %s", exc)
        await db.rollback()
        return 0


async def _trim_retention_runs(db: AsyncSession) -> int:
    """Keep only the newest OPS_RETENTION_RUNS_KEEP run records."""
    try:
        from applire.services.ops.config import OPS_RETENTION_RUNS_KEEP

        if OPS_RETENTION_RUNS_KEEP <= 0:
            return 0
        result = await db.execute(
            text(
                "DELETE FROM retention_runs WHERE id NOT IN ("
                " SELECT id FROM retention_runs ORDER BY run_at DESC LIMIT :keep)"
            ),
            {"keep": OPS_RETENTION_RUNS_KEEP},
        )
        await db.commit()
        return result.rowcount or 0  # type: ignore[return-value]
    except (ProgrammingError, OperationalError) as exc:
        logger.warning("_trim_retention_runs skipped: %s", exc)
        await db.rollback()
        return 0


async def record_run(report: dict, *, duration_ms: int, ok: bool, error: str | None) -> None:
    """Persist one run record — the stdout report's first consumer (ADR-086 cl. 5).

    Since this worker was built its JSON report has gone to stdout and nothing
    has read it; `SF-RET.1`/`SF-RET.2` say so in their control cells, and the
    2026-07-12 founder ruling settled that *an unread signal is not a detection
    mechanism*. This is the consumer.

    Its own session, and every failure swallowed: the worker's job is deleting
    data on a legal clock, and a monitoring insert may never be the reason a
    GDPR sweep aborts.
    """
    try:
        from applire.models.retention_run import RetentionRun

        async with AsyncSessionLocal() as db:
            db.add(
                RetentionRun(
                    report=report,
                    duration_ms=duration_ms,
                    ok=ok,
                    error=error,
                )
            )
            await db.commit()
            await _trim_retention_runs(db)
    except Exception as exc:
        logger.warning(
            "retention run not recorded (%s: %s) — the sweep itself is unaffected",
            type(exc).__name__,
            exc,
        )


async def run() -> None:
    """Execute all TTL rules, emit a JSON report to stdout AND persist it."""
    started = time.monotonic()
    try:
        report = await _sweep()
    except Exception as exc:
        # A crashed sweep still leaves a record, so the ops layer can say "the
        # last run FAILED" rather than only "no run for N hours" (SF-RET.1).
        # The exception type and message only — never a value from a row.
        await record_run(
            {},
            duration_ms=int((time.monotonic() - started) * 1000),
            ok=False,
            error=f"{type(exc).__name__}: {exc}"[:500],
        )
        raise
    # The stdout line is unchanged and stays: every existing log-reading habit
    # keeps working. The row beside it is what the ops layer reads (ADR-086 cl. 5).
    print(json.dumps(report), flush=True)
    await record_run(
        report,
        duration_ms=int((time.monotonic() - started) * 1000),
        ok=True,
        error=None,
    )


async def _sweep() -> dict:
    """Run every TTL rule and build the report. Raises on an unhandled failure.

    A declared cross-user entry point (ADR-092 cl. 8): ``unscoped("retention")``.
    """
    with ownership.unscoped("retention"):
        return await _sweep_unscoped()


async def _sweep_unscoped() -> dict:
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
        # ADR-086 clause 10 — a growth bound, not a PII clock.
        llm_usage_deleted = await _purge_llm_usage(db)
        # After every owned-row purge above: a posting whose last referencing
        # session/document went today is an orphan in the same run.
        orphan_postings_deleted = await _purge_orphan_postings(db)
        auth_housekeeping = await _purge_auth_housekeeping(db)
        audit_events_deleted = await _purge_audit_events(db)

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
        "llm_usage_deleted": llm_usage_deleted,
        "orphan_postings_deleted": orphan_postings_deleted,
        **auth_housekeeping,
        "audit_events_deleted": audit_events_deleted,
    }
    return report
