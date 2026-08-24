# Copyright (C) 2026 Tobias Rosenbaum
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
"""
tests/unit/test_document_language_read_surface.py

E054/US289 — the status read surfaces carry the document's PINNED
``document_language`` (ADR-038 amendment 2026-08-23, clause 3b) so the UI can
badge each document's language (FMEA JF-F-G2.2: correct pinning reads as
inconsistency unless labelled). The stored value is surfaced as-is: a legacy
NULL row yields None (no badge) rather than a freshly-resolved claim — the
status endpoint must never disagree with what the generation run actually
stamped.

Also pins that GET /api/cv/{id}/status carries ``template``: the CV page seeds
its regenerate-same-template state from the status response after a reload
(US289 switch = existing regeneration path in the SAME template).

Run:
    PYTHONPATH=backend python3 -m pytest tests/unit/test_document_language_read_surface.py -v
"""
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Make the applire package importable
_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered."""
    from applire.db.session import Base
    import applire.models.user
    import applire.models.job
    import applire.models.profile
    import applire.models.gap
    import applire.models.cv
    import applire.models.session
    import applire.models.application
    import applire.models.cover_letter
    import applire.models.flow
    import applire.models.uploads

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


def _make_cv(job_id: uuid.UUID, document_language: str | None):
    from applire.models.cv import GeneratedCV
    return GeneratedCV(
        id=uuid.uuid4(),
        job_analysis_id=job_id,
        profile_id=uuid.uuid4(),
        tailored_data={},
        template="executive",
        status="ready",
        created_at=datetime.now(timezone.utc),
        document_language=document_language,
    )


def _make_cl(document_language: str | None):
    from applire.models.cover_letter import GeneratedCoverLetter, CoverLetterStatus
    return GeneratedCoverLetter(
        job_analysis_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        template="classic_german",
        letter_data={},
        pre_gen_inputs={},
        status=CoverLetterStatus.ready.value,
        document_language=document_language,
    )


@pytest.mark.asyncio
async def test_cv_status_carries_pinned_document_language(db):
    from applire.services.cv import get_cv_status, list_cvs_for_job

    job_id = uuid.uuid4()
    cv = _make_cv(job_id, document_language="en")
    db.add(cv)
    await db.commit()

    status_result = await get_cv_status(cv.id, db, "http://localhost:8001")
    assert status_result.document_language == "en"

    list_result = await list_cvs_for_job(job_id, db, "http://localhost:8001")
    assert list_result[0].document_language == "en"


@pytest.mark.asyncio
async def test_cv_status_legacy_null_language_stays_none(db):
    # A pre-migration row has no pinned language; the read surface must not
    # invent one (clause 3b: no fresh resolve on a read path's metadata).
    from applire.services.cv import get_cv_status

    cv = _make_cv(uuid.uuid4(), document_language=None)
    db.add(cv)
    await db.commit()

    status_result = await get_cv_status(cv.id, db, "http://localhost:8001")
    assert status_result.document_language is None


@pytest.mark.asyncio
async def test_cv_single_status_carries_template(db):
    # The list endpoint already carried template (E041/US232); the single
    # status endpoint must too, so the CV page can regenerate in the SAME
    # template after a reload (US289 language switch).
    from applire.services.cv import get_cv_status

    cv = _make_cv(uuid.uuid4(), document_language="de")
    db.add(cv)
    await db.commit()

    status_result = await get_cv_status(cv.id, db, "http://localhost:8001")
    assert status_result.template == "executive"


@pytest.mark.asyncio
async def test_cover_letter_status_carries_pinned_document_language(db):
    from applire.services.cover_letter import get_cover_letter_status

    cl = _make_cl(document_language="de")
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    result = await get_cover_letter_status(cl.id, db, "http://localhost:8001")
    assert result.document_language == "de"


@pytest.mark.asyncio
async def test_cover_letter_status_legacy_null_language_stays_none(db):
    from applire.services.cover_letter import get_cover_letter_status

    cl = _make_cl(document_language=None)
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    result = await get_cover_letter_status(cl.id, db, "http://localhost:8001")
    assert result.document_language is None
