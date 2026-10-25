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

"""My Documents service — list generated CVs (and CV-less letters) across all jobs for a user."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.application import Application
from applire.models.cover_letter import GeneratedCoverLetter
from applire.models.cv import GeneratedCV
from applire.models.job import JobAnalysis
from applire.schemas.documents import DocumentItem, DocumentListResponse, LetterRef
from applire.services.posting_labels import effective_posting_labels


async def list_documents(
    *,
    user_id: uuid.UUID,
    db: AsyncSession,
    page: int = 1,
    page_size: int = 10,
    status: str | None = None,
) -> DocumentListResponse:
    """Return all non-deleted CVs for *user_id*, newest first — plus a row for
    each letter whose posting has no live CV (789:187).

    Joins generated_cvs → applications to get role_title, company_name and
    flow_session_id without touching master_profiles.

    ADR-092 cl. 3 (US333/US334): the posting is shared, so the join on
    ``job_analysis_id`` alone would pair the caller's application with ANOTHER
    user's CV for the same posting — both sides are keyed on the owner.

    789:187 (Hazelnut build 4): a letter whose posting has no live CV of this
    user (the CV was deleted, or the letter was written on its own) had no row,
    and "Dokumente gesamt" counted CV rows only. Such a letter is now its own
    row (``kind="cover_letter"``, ``cv_id=None``), merged into the newest-first
    order; ``total`` counts rows (pagination), ``documents_total`` counts every
    document the list shows — a CV row with its letter is two. The page is a
    per-person list (tens of rows), so the merge and the page slice happen here
    rather than in one UNION query.
    """
    base = (
        select(GeneratedCV, Application, JobAnalysis)
        .join(Application, GeneratedCV.job_analysis_id == Application.job_analysis_id)
        .join(JobAnalysis, JobAnalysis.id == GeneratedCV.job_analysis_id)
        .where(
            GeneratedCV.user_id == user_id,
            Application.user_id == user_id,
            GeneratedCV.deleted_at.is_(None),
            Application.deleted_at.is_(None),
        )
    )
    if status:
        base = base.where(GeneratedCV.status == status)

    cv_rows = (await db.execute(base.order_by(GeneratedCV.created_at.desc()))).all()
    letters = await _letters_for_newest_cvs(
        user_id=user_id, db=db, job_ids={cv.job_analysis_id for cv, _a, _j in cv_rows}
    )

    items = [
        DocumentItem(
            cv_id=cv.id,
            flow_id=app.flow_session_id,
            role_title=labels[0],
            company_name=labels[1],
            template=cv.template,
            status=cv.status,
            created_at=cv.created_at,
            expires_at=cv.expires_at,
            origin=cv.origin,
            cover_letter=letters.get(cv.id),
        )
        for cv, app, job in cv_rows
        for labels in (effective_posting_labels(job, app),)
    ]
    items.extend(await _letters_without_a_cv(user_id=user_id, db=db, status=status))
    items.sort(key=lambda i: _utc(i.created_at), reverse=True)

    total = len(items)
    documents_total = total + sum(1 for i in items if i.kind == "cv" and i.cover_letter)
    offset = (page - 1) * page_size
    return DocumentListResponse(
        items=items[offset : offset + page_size],
        total=total,
        documents_total=documents_total,
    )


def _utc(when: datetime) -> datetime:
    """A sort key that orders naive (SQLite) and aware (Postgres) stamps alike."""
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


async def _letters_without_a_cv(
    *, user_id: uuid.UUID, db: AsyncSession, status: str | None
) -> list[DocumentItem]:
    """789:187: one row per posting whose newest live letter has no live CV of
    the same owner beside it (any CV status — a failed CV row still lists the
    posting, and its letter rides on that row). Owner-keyed on all three sides
    (ADR-092 cl. 3)."""
    has_cv = (
        select(GeneratedCV.id)
        .where(
            GeneratedCV.user_id == user_id,
            GeneratedCV.deleted_at.is_(None),
            GeneratedCV.job_analysis_id == GeneratedCoverLetter.job_analysis_id,
        )
        .exists()
    )
    q = (
        select(GeneratedCoverLetter, Application, JobAnalysis)
        .join(
            Application,
            (Application.job_analysis_id == GeneratedCoverLetter.job_analysis_id)
            & (Application.user_id == user_id)
            & Application.deleted_at.is_(None),
        )
        .join(JobAnalysis, JobAnalysis.id == GeneratedCoverLetter.job_analysis_id)
        .where(
            GeneratedCoverLetter.user_id == user_id,
            GeneratedCoverLetter.deleted_at.is_(None),
            ~has_cv,
        )
        .order_by(GeneratedCoverLetter.created_at.desc())
    )
    out: list[DocumentItem] = []
    seen: set[uuid.UUID] = set()
    for letter, app, job in (await db.execute(q)).all():
        if letter.job_analysis_id in seen:
            continue  # the newest letter of a posting only, as on a CV row
        seen.add(letter.job_analysis_id)
        letter_status = str(getattr(letter.status, "value", letter.status))
        if status and letter_status != status:
            continue
        role_title, company_name = effective_posting_labels(job, app)
        out.append(
            DocumentItem(
                kind="cover_letter",
                cv_id=None,
                flow_id=app.flow_session_id,
                role_title=role_title,
                company_name=company_name,
                template=letter.template,
                status=letter_status,
                created_at=letter.created_at,
                expires_at=letter.expires_at,
                origin=letter.origin,
                cover_letter=LetterRef(
                    cover_letter_id=letter.id,
                    status=letter_status,
                    created_at=letter.created_at,
                    expires_at=letter.expires_at,
                ),
            )
        )
    return out


async def _letters_for_newest_cvs(
    *, user_id: uuid.UUID, db: AsyncSession, job_ids: set[uuid.UUID]
) -> dict[uuid.UUID, LetterRef]:
    """#789: map the NEWEST live CV of each posting to that posting's newest
    live letter. Two owner-keyed queries over the page's postings (ADR-092
    cl. 3 — the posting is shared, the documents are not). A letter is shown
    once, on the row the user opens; older CV versions of the same posting
    carry none, so the page never lists one letter three times.
    """
    if not job_ids:
        return {}
    newest_cv: dict[uuid.UUID, uuid.UUID] = {}
    for cv_id, job_id in (
        await db.execute(
            select(GeneratedCV.id, GeneratedCV.job_analysis_id)
            .where(
                GeneratedCV.user_id == user_id,
                GeneratedCV.deleted_at.is_(None),
                GeneratedCV.job_analysis_id.in_(job_ids),
            )
            .order_by(GeneratedCV.created_at.desc())
        )
    ).all():
        newest_cv.setdefault(job_id, cv_id)
    out: dict[uuid.UUID, LetterRef] = {}
    for letter in (
        await db.execute(
            select(GeneratedCoverLetter)
            .where(
                GeneratedCoverLetter.user_id == user_id,
                GeneratedCoverLetter.deleted_at.is_(None),
                GeneratedCoverLetter.job_analysis_id.in_(job_ids),
            )
            .order_by(GeneratedCoverLetter.created_at.desc())
        )
    ).scalars():
        cv_id = newest_cv.get(letter.job_analysis_id)
        if cv_id is not None and cv_id not in out:
            out[cv_id] = LetterRef(
                cover_letter_id=letter.id,
                status=str(getattr(letter.status, "value", letter.status)),
                created_at=letter.created_at,
                expires_at=letter.expires_at,
            )
    return out
