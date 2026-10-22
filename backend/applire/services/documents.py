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

"""My Documents service — list generated CVs across all jobs for a user."""
import uuid

from sqlalchemy import func, select
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
    """Return all non-deleted CVs for *user_id*, newest first.

    Joins generated_cvs → applications to get role_title, company_name and
    flow_session_id without touching master_profiles.

    ADR-092 cl. 3 (US333/US334): the posting is shared, so the join on
    ``job_analysis_id`` alone would pair the caller's application with ANOTHER
    user's CV for the same posting — both sides are keyed on the owner.
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

    # Total count
    count_q = select(func.count()).select_from(base.subquery())
    total: int = (await db.execute(count_q)).scalar_one()

    # Paginated rows, newest first
    rows_q = (
        base.order_by(GeneratedCV.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(rows_q)).all()
    letters = await _letters_for_newest_cvs(
        user_id=user_id, db=db, job_ids={cv.job_analysis_id for cv, _a, _j in rows}
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
        for cv, app, job in rows
        for labels in (effective_posting_labels(job, app),)
    ]
    return DocumentListResponse(items=items, total=total)


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
