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

"""Per-user erasure (ADR-092 cl. 11, RD-3 / D-3, US336) — the one implementation.

``erase(db, user_id, scope)`` is the contract both doors call:
``DELETE /api/profile`` → ``"vault"`` (the router is an adapter) and
``DELETE /api/me/account`` / admin delete → ``"account"`` (1b's
``services/admin/users.delete_account``, which runs it in its three-step order:
disable + revoke committed → **erase** → tombstone + audit).

**What goes (both scopes), leaf → root, every statement keyed on the owner:**
uploads → (break the applications ↔ flow_sessions cycle and the submitted pins)
→ flow_sessions → generated_cvs → generated_cover_letters → interview_sessions →
gap_analyses → gap_analysis_jobs → cv_import_jobs → applications →
master_profiles (``profile_snapshots`` cascade). ``user_settings``: the
signature path is cleared (vault) or the row deleted (account). ``"account"``
additionally deletes the person's identity rows (``auth_sessions``,
``reauth_grants``, ``personal_tokens``, ``auth_links``) and detaches their
``llm_usage`` history (``user_id`` → NULL, the row's own ``ON DELETE SET NULL``
intent — the user row is tombstoned, not deleted, so the FK action never fires).
The user row itself is **not** touched here except ``photo_consent`` (the photo
is gone); 1b tombstones it afterwards.

**Shared postings (S-17) — lock first, check second.** The postings the user
referenced are candidates. ``SELECT id FROM job_analyses WHERE id IN (…)
ORDER BY id FOR UPDATE`` waits for any concurrent transaction that is creating
a link to one of them (the FK check of its INSERT holds ``KEY SHARE`` on the
posting row); then a **new statement** ``DELETE … WHERE id IN (:locked) AND NOT
EXISTS (…)`` over all seven referencing tables reads a fresh snapshot and keeps
every posting another user still references. A link creator that comes second
fails its FK check on a deleted posting and retries its dedup in a fresh
session (4a, ``analyze``). Two simultaneous erasures may each keep a shared
posting; the retention worker's orphan purge (``purge_unreferenced_postings``,
the same predicate) collects it. One retry on ``IntegrityError``.

Files (uploads, photos, signature) are deleted after the commit; a failure is
logged at ERROR and reclaimed by the retention orphan scan (#152).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import delete, exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from applire import ownership

__all__ = [
    "ErasureFailed",
    "ErasureScope",
    "POSTING_REFERENCES",
    "erase",
    "purge_unreferenced_postings",
]

logger = logging.getLogger(__name__)

ErasureScope = Literal["vault", "account"]

#: How many times the transaction is attempted (cl. 11: "retries once").
_ATTEMPTS = 2


class ErasureFailed(Exception):
    """The erasure transaction rolled back — nothing was deleted."""


def _posting_reference_models() -> tuple[Any, ...]:
    """Every table with a ``job_analysis_id`` FK into ``job_analyses`` (seven)."""
    from applire.models.application import Application
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.cv import GeneratedCV
    from applire.models.flow import FlowSession
    from applire.models.gap import GapAnalysis
    from applire.models.gap_job import GapAnalysisJob
    from applire.models.session import InterviewSession

    return (
        Application,
        GeneratedCV,
        GeneratedCoverLetter,
        GapAnalysis,
        GapAnalysisJob,
        InterviewSession,
        FlowSession,
    )


def _posting_fk(model: Any) -> Any:
    """The model's column that references ``job_analyses.id`` (``job_analysis_id``,
    ``job_id`` on ``flow_sessions``) — read from the FK, never assumed by name."""
    for col in model.__table__.columns:
        if any(fk.column.table.name == "job_analyses" for fk in col.foreign_keys):
            return getattr(model, col.key)
    raise TypeError(f"{model.__tablename__} has no FK into job_analyses")


#: Table names of the referencing set — a test asserts it equals the FK catalogue.
POSTING_REFERENCES: tuple[str, ...] = (
    "applications",
    "generated_cvs",
    "generated_cover_letters",
    "gap_analyses",
    "gap_analysis_jobs",
    "interview_sessions",
    "flow_sessions",
)


async def purge_unreferenced_postings(
    db: AsyncSession,
    candidates: Iterable[uuid.UUID] | None = None,
    *,
    created_before: datetime | None = None,
) -> int:
    """Delete shared postings no row of any user references — lock, then check.

    ``candidates`` limits the purge to those ids (erasure); ``None`` takes every
    posting (retention), ``created_before`` an age floor. Runs under
    ``unscoped("job-refcount")`` — the NOT EXISTS reads every user's references
    by design (it decides *whether* anyone still needs the posting, never *who*).
    Flushes nothing itself; the caller owns the transaction.
    """
    from applire.models.job import JobAnalysis

    with ownership.unscoped("job-refcount"):
        lock = select(JobAnalysis.id)
        if candidates is not None:
            ids = sorted(set(candidates), key=str)
            if not ids:
                return 0
            lock = lock.where(JobAnalysis.id.in_(ids))
        if created_before is not None:
            lock = lock.where(JobAnalysis.created_at < created_before)
        # Step 1 — lock first (Postgres: waits for in-flight link creators).
        locked = list(
            (await db.execute(lock.order_by(JobAnalysis.id).with_for_update())).scalars()
        )
        if not locked:
            return 0
        # Step 2 — a NEW statement: a fresh snapshot sees links committed while
        # we waited for the lock.
        stmt = delete(JobAnalysis).where(JobAnalysis.id.in_(locked))
        for model in _posting_reference_models():
            stmt = stmt.where(~exists().where(_posting_fk(model) == JobAnalysis.id))
        result = await db.execute(stmt.execution_options(synchronize_session=False))
        return int(result.rowcount or 0)


async def _collect_files(db: AsyncSession, uid: uuid.UUID) -> list[str]:
    from applire.models.profile import MasterProfile
    from applire.models.uploads import UploadRecord
    from applire.models.user_settings import UserSettings

    paths: list[str] = [
        p for p in (await db.execute(select(UploadRecord.file_path).where(UploadRecord.user_id == uid))).scalars()
        if p
    ]
    # The photo lives in the vault JSON (personal_info.photo_url) — every profile
    # row of the owner, soft-deleted included (they are deleted too).
    for blob in (await db.execute(select(MasterProfile.profile_json).where(MasterProfile.user_id == uid))).scalars():
        photo = ((blob or {}).get("personal_info") or {}).get("photo_url")
        if photo:
            paths.append(photo)
    # #359: the signature image lives on user_settings (ADR-088).
    sig = (
        await db.execute(select(UserSettings.signature_path).where(UserSettings.user_id == uid))
    ).scalar_one_or_none()
    if sig:
        paths.append(sig)
    return paths


async def _candidate_postings(db: AsyncSession, uid: uuid.UUID) -> set[uuid.UUID]:
    out: set[uuid.UUID] = set()
    for model in _posting_reference_models():
        out.update(
            v
            for v in (
                await db.execute(select(_posting_fk(model)).where(model.user_id == uid))
            ).scalars()
            if v is not None
        )
    return out


async def _delete_owned(db: AsyncSession, uid: uuid.UUID, scope: ErasureScope) -> dict[str, int]:
    from applire.models.application import Application
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.cv import GeneratedCV
    from applire.models.flow import FlowSession
    from applire.models.gap import GapAnalysis
    from applire.models.gap_job import GapAnalysisJob
    from applire.models.import_job import CVImportJob
    from applire.models.profile import MasterProfile, ProfileSnapshot
    from applire.models.session import InterviewSession
    from applire.models.uploads import UploadRecord
    from applire.models.user import User
    from applire.models.user_settings import UserSettings

    counts: dict[str, int] = {}

    async def _run(name: str, stmt: Any) -> None:
        r = await db.execute(stmt.execution_options(synchronize_session=False))
        counts[name] = counts.get(name, 0) + int(r.rowcount or 0)

    await _run("uploads", delete(UploadRecord).where(UploadRecord.user_id == uid))
    # Break the applications ↔ flow_sessions cycle and let go of the submitted
    # pins (E039/US219): Art. 17 beats the pin.
    await db.execute(
        update(Application)
        .where(Application.user_id == uid)
        .values(flow_session_id=None, submitted_cv_id=None, submitted_cover_letter_id=None)
        .execution_options(synchronize_session=False)
    )
    await _run("flow_sessions", delete(FlowSession).where(FlowSession.user_id == uid))
    await _run("generated_cvs", delete(GeneratedCV).where(GeneratedCV.user_id == uid))
    await _run(
        "generated_cover_letters",
        delete(GeneratedCoverLetter).where(GeneratedCoverLetter.user_id == uid),
    )
    # interview_sessions.gap_analysis_id → gap_analyses: sessions first.
    await _run("interview_sessions", delete(InterviewSession).where(InterviewSession.user_id == uid))
    await _run("gap_analyses", delete(GapAnalysis).where(GapAnalysis.user_id == uid))
    await _run("gap_analysis_jobs", delete(GapAnalysisJob).where(GapAnalysisJob.user_id == uid))
    await _run("cv_import_jobs", delete(CVImportJob).where(CVImportJob.user_id == uid))
    await _run("applications", delete(Application).where(Application.user_id == uid))
    # profile_snapshots: ON DELETE CASCADE on Postgres; SQLite (no FK enforcement
    # in the unit tier) needs the explicit delete — owner via the chain.
    own_profiles = select(MasterProfile.id).where(MasterProfile.user_id == uid)
    await _run(
        "profile_snapshots",
        delete(ProfileSnapshot).where(ProfileSnapshot.profile_id.in_(own_profiles)),
    )
    await _run("master_profiles", delete(MasterProfile).where(MasterProfile.user_id == uid))

    if scope == "account":
        await _run("user_settings", delete(UserSettings).where(UserSettings.user_id == uid))
    else:
        await db.execute(
            update(UserSettings)
            .where(UserSettings.user_id == uid)
            .values(signature_path=None)
            .execution_options(synchronize_session=False)
        )
        counts["user_settings"] = 0

    # The photo file is gone with the vault — so is the consent to process it.
    await db.execute(
        update(User)
        .where(User.id == uid)
        .values(photo_consent=False, photo_consent_at=None)
        .execution_options(synchronize_session=False)
    )
    counts["users"] = 0  # tombstoning is the account door's step 3 (1b)
    return counts


async def _delete_identity(db: AsyncSession, uid: uuid.UUID) -> dict[str, int]:
    """Account scope only: the identity rows (not ``__owned__``, ADR-092 cl. 3)."""
    from applire.models.auth import AuthLink, AuthSession, PersonalToken, ReauthGrant
    from applire.models.llm_usage import LlmUsage

    counts: dict[str, int] = {}
    for name, model in (
        ("reauth_grants", ReauthGrant),
        ("auth_sessions", AuthSession),
        ("personal_tokens", PersonalToken),
        ("auth_links", AuthLink),
    ):
        r = await db.execute(
            delete(model).where(model.user_id == uid).execution_options(synchronize_session=False)
        )
        counts[name] = int(r.rowcount or 0)
    r = await db.execute(
        update(LlmUsage)
        .where(LlmUsage.user_id == uid)
        .values(user_id=None)
        .execution_options(synchronize_session=False)
    )
    counts["llm_usage_detached"] = int(r.rowcount or 0)
    return counts


async def erase(
    db: AsyncSession,
    user_id: uuid.UUID,
    scope: ErasureScope,
    *,
    storage: Any | None = None,
) -> dict[str, int]:
    """Erase ``user_id``'s data for ``scope``, commit, then delete the files.

    Returns per-table deleted-row counts (``job_analyses`` = shared postings
    purged because nobody else referenced them). Raises :class:`ErasureFailed`
    when the transaction rolled back (nothing deleted).
    """
    if scope not in ("vault", "account"):
        raise ValueError(f"unknown erasure scope: {scope!r}")
    if not isinstance(user_id, uuid.UUID):
        user_id = uuid.UUID(str(user_id))

    last_exc: Exception | None = None
    counts: dict[str, int] = {}
    files: list[str] = []
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            with ownership.owner_context(user_id):
                files = await _collect_files(db, user_id)
                candidates = await _candidate_postings(db, user_id)
                counts = await _delete_owned(db, user_id, scope)
                if scope == "account":
                    counts.update(await _delete_identity(db, user_id))
            counts["job_analyses"] = await purge_unreferenced_postings(db, candidates)
            await db.commit()
            break
        except IntegrityError as exc:
            # A link to a candidate posting was committed between our lock and
            # our DELETE's own FK checks, or a concurrent writer raced a leaf
            # row in — the whole transaction rolled back; once more.
            await db.rollback()
            last_exc = exc
            logger.warning(
                "GDPR erasure attempt %d for user %s hit an integrity error; %s",
                attempt, user_id, "retrying" if attempt < _ATTEMPTS else "giving up",
            )
        except Exception as exc:
            await db.rollback()
            logger.exception("GDPR erasure failed for user %s", user_id)
            raise ErasureFailed("Erasure failed — no data was deleted. Please retry.") from exc
    else:
        raise ErasureFailed("Erasure failed — no data was deleted. Please retry.") from last_exc

    if storage is None:
        from applire.storage import get_storage

        storage = get_storage()
    for path in files:
        try:
            await storage.delete(path)
        except Exception as exc:  # noqa: BLE001 — never undo a committed erasure
            # ERROR, not warning: a leftover file is PII outliving an Art. 17
            # request until the retention orphan scan reclaims it (#152).
            logger.error(
                "Failed to delete file %s after GDPR erasure: %s "
                "(retention orphan scan reclaims it within 24h)",
                path,
                exc,
            )

    logger.info(
        "GDPR erasure completed",
        extra={
            "event": "user_erasure_completed",
            "scope": scope,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "records": counts,
        },
    )
    return counts
