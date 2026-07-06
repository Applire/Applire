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

"""E039 / US216 — Application source link (dossier, step P1).

Covers:
  - create_application denormalizes source_url from JobAnalysis (URL-tab auto-persist)
  - explicit request.source_url overrides the JobAnalysis value (text-tab manual field)
  - patch_application updates source_url and resets the GDPR inactivity timer
  - source_url is null when neither the job nor the request carries one

No Docker, no LLM.
"""

import uuid
from datetime import datetime, timezone

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
    """In-memory SQLite session with all models registered (pattern of test_application_service.py)."""
    from applire.db.session import Base  # noqa: F401
    import applire.models.user        # noqa: F401
    import applire.models.job         # noqa: F401
    import applire.models.profile     # noqa: F401
    import applire.models.gap         # noqa: F401
    import applire.models.cv          # noqa: F401
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


@pytest_asyncio.fixture
async def user_and_job(db):
    """Insert a stub user and job analysis; return (user, job)."""
    from applire.models.user import User
    from applire.models.job import JobAnalysis

    user = User(
        id=_STUB_USER_ID,
        email="local@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="abc123",
        raw_text="Head of Department at Example AG",
        role_title="Head of Department",
        company_name="Example AG",
        required_skills=["Leadership"],
        nice_to_have_skills=[],
        keywords=["Leadership"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="DE",
    )
    db.add_all([user, job])
    await db.commit()
    return user, job

_JOB_URL = "https://jobs.example.com/head-of-department-123"
_MANUAL_URL = "https://www.linkedin.com/jobs/view/456"


@pytest.mark.asyncio
async def test_create_denormalizes_source_url_from_job(db, user_and_job):
    """URL-tab path: the JD was scraped from a URL — it becomes the application's source."""
    user, job = user_and_job
    job.source_url = _JOB_URL
    await db.commit()

    resp = await create_application(
        user.id, CreateApplicationRequest(job_analysis_id=job.id), db
    )
    assert resp.source_url == _JOB_URL


@pytest.mark.asyncio
async def test_create_with_explicit_source_url_overrides_job(db, user_and_job):
    """Text-tab path: user supplies the source link manually; it wins over the job value."""
    user, job = user_and_job
    job.source_url = _JOB_URL
    await db.commit()

    resp = await create_application(
        user.id,
        CreateApplicationRequest(job_analysis_id=job.id, source_url=_MANUAL_URL),
        db,
    )
    assert resp.source_url == _MANUAL_URL


@pytest.mark.asyncio
async def test_create_source_url_null_when_absent(db, user_and_job):
    """Pasted JD, no manual link: source stays null — never invented."""
    user, job = user_and_job
    resp = await create_application(
        user.id, CreateApplicationRequest(job_analysis_id=job.id), db
    )
    assert resp.source_url is None


@pytest.mark.asyncio
async def test_patch_source_url_updates_and_resets_inactivity_timer(db, user_and_job):
    """Adding the link later counts as activity (ADR-005 applications rule)."""
    user, job = user_and_job
    created = await create_application(
        user.id, CreateApplicationRequest(job_analysis_id=job.id), db
    )
    expires_before = created.expires_at

    patched = await patch_application(
        created.id, PatchApplicationRequest(source_url=_MANUAL_URL), db
    )
    assert patched.source_url == _MANUAL_URL
    assert patched.expires_at >= expires_before
    assert patched.updated_at >= created.updated_at
