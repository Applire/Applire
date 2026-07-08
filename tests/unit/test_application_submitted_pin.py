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

"""E039 / US219 — Mark as submitted: pin the sent artifact on the application.

Covers (ADR-005 amendment 2026-07-06):
  - Application carries submitted_cv_id / submitted_cover_letter_id (nullable FKs)
  - patch_application sets the pin after validating the artifact exists, is not
    deleted, and belongs to the same job as the application
  - an explicit null clears the pin (same clear semantics as the dossier fields)
  - pinning resets the GDPR inactivity timer (the pin is a purpose signal)
  - ApplicationResponse exposes both pin fields

No Docker, no LLM.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.schemas.application import (
    CreateApplicationRequest,
    PatchApplicationRequest,
)
from applire.services.application import create_application, patch_application

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered."""
    from applire.db.session import Base  # noqa: F401
    import applire.models.user        # noqa: F401
    import applire.models.job         # noqa: F401
    import applire.models.profile     # noqa: F401
    import applire.models.gap         # noqa: F401
    import applire.models.cv          # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session     # noqa: F401
    import applire.models.flow        # noqa: F401
    import applire.models.uploads     # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


def _make_job(**overrides):
    from applire.models.job import JobAnalysis

    defaults = dict(
        id=uuid.uuid4(),
        raw_text_hash=f"hash-{uuid.uuid4()}",
        raw_text="Sample JD",
        role_title="Data Analyst",
        company_name="DataCraft GmbH",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="German",
    )
    defaults.update(overrides)
    return JobAnalysis(**defaults)


def _make_cv(job_id: uuid.UUID, **overrides):
    from applire.models.cv import GeneratedCV

    defaults = dict(
        id=uuid.uuid4(),
        job_analysis_id=job_id,
        profile_id=uuid.uuid4(),
        tailored_data={},
        template="classic_german",
        status="ready",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=90),
    )
    defaults.update(overrides)
    return GeneratedCV(**defaults)


def _make_cover_letter(job_id: uuid.UUID, **overrides):
    from applire.models.cover_letter import GeneratedCoverLetter

    defaults = dict(
        id=uuid.uuid4(),
        job_analysis_id=job_id,
        profile_id=uuid.uuid4(),
        letter_data={},
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=90),
    )
    defaults.update(overrides)
    return GeneratedCoverLetter(**defaults)


@pytest_asyncio.fixture
async def app_with_cv(db):
    """Stub user + job + application + a ready generated CV for that job."""
    from applire.models.user import User

    user = User(id=_STUB_USER_ID, email="stub@applire.local")
    job = _make_job()
    cv = _make_cv(job.id)
    db.add_all([user, job, cv])
    await db.commit()

    app = await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db
    )
    return app, job, cv


# ---------------------------------------------------------------------------
# Pin: set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_sets_submitted_cv_id(db, app_with_cv):
    app, _job, cv = app_with_cv

    result = await patch_application(
        app.id, PatchApplicationRequest(submitted_cv_id=cv.id), db
    )
    assert result.submitted_cv_id == cv.id


@pytest.mark.asyncio
async def test_patch_sets_submitted_cover_letter_id(db, app_with_cv):
    app, job, _cv = app_with_cv
    cl = _make_cover_letter(job.id)
    db.add(cl)
    await db.commit()

    result = await patch_application(
        app.id, PatchApplicationRequest(submitted_cover_letter_id=cl.id), db
    )
    assert result.submitted_cover_letter_id == cl.id


@pytest.mark.asyncio
async def test_pin_resets_inactivity_timer(db, app_with_cv):
    """Pinning is user activity — the GDPR inactivity clock restarts (ADR-005)."""
    app, _job, cv = app_with_cv

    before = datetime.now(timezone.utc) + timedelta(days=700)
    result = await patch_application(
        app.id, PatchApplicationRequest(submitted_cv_id=cv.id), db
    )
    expires = result.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    assert expires > before


# ---------------------------------------------------------------------------
# Pin: clear (explicit null)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_clears_submitted_cv_id_with_explicit_null(db, app_with_cv):
    app, _job, cv = app_with_cv
    await patch_application(app.id, PatchApplicationRequest(submitted_cv_id=cv.id), db)

    result = await patch_application(
        app.id, PatchApplicationRequest(submitted_cv_id=None), db
    )
    assert result.submitted_cv_id is None


@pytest.mark.asyncio
async def test_patch_omitting_pin_leaves_it_unchanged(db, app_with_cv):
    app, _job, cv = app_with_cv
    await patch_application(app.id, PatchApplicationRequest(submitted_cv_id=cv.id), db)

    result = await patch_application(
        app.id, PatchApplicationRequest(notes="called the recruiter"), db
    )
    assert result.submitted_cv_id == cv.id


# ---------------------------------------------------------------------------
# Pin: validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_rejects_nonexistent_cv(db, app_with_cv):
    app, _job, _cv = app_with_cv

    with pytest.raises(ValueError, match="submitted_cv_id.*reference"):
        await patch_application(
            app.id, PatchApplicationRequest(submitted_cv_id=uuid.uuid4()), db
        )


@pytest.mark.asyncio
async def test_patch_rejects_cv_of_different_job(db, app_with_cv):
    """A pin must reference an artifact generated for THIS application's job."""
    app, _job, _cv = app_with_cv
    other_job = _make_job()
    other_cv = _make_cv(other_job.id)
    db.add_all([other_job, other_cv])
    await db.commit()

    with pytest.raises(ValueError, match="submitted_cv_id.*reference"):
        await patch_application(
            app.id, PatchApplicationRequest(submitted_cv_id=other_cv.id), db
        )


@pytest.mark.asyncio
async def test_patch_rejects_soft_deleted_cv(db, app_with_cv):
    app, job, _cv = app_with_cv
    dead_cv = _make_cv(job.id, deleted_at=datetime.now(timezone.utc))
    db.add(dead_cv)
    await db.commit()

    with pytest.raises(ValueError, match="submitted_cv_id.*reference"):
        await patch_application(
            app.id, PatchApplicationRequest(submitted_cv_id=dead_cv.id), db
        )


@pytest.mark.asyncio
async def test_patch_rejects_nonexistent_cover_letter(db, app_with_cv):
    app, _job, _cv = app_with_cv

    with pytest.raises(ValueError, match="submitted_cover_letter_id.*reference"):
        await patch_application(
            app.id,
            PatchApplicationRequest(submitted_cover_letter_id=uuid.uuid4()),
            db,
        )


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_exposes_pin_fields_as_null_by_default(db, app_with_cv):
    app, _job, _cv = app_with_cv
    assert app.submitted_cv_id is None
    assert app.submitted_cover_letter_id is None
