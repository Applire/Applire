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

"""E039 / US218 — Application status pipeline (step P2).

Covers:
  - UserStatus enum carries the full pipeline: tracking, applied, interviewing,
    offer, rejected, hired
  - patch_application accepts user_status=interviewing
  - list_applications filters by the new value

No Docker, no LLM.
"""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.application import UserStatus
from applire.schemas.application import (
    CreateApplicationRequest,
    PatchApplicationRequest,
)
from applire.services.application import (
    create_application,
    list_applications,
    patch_application,
)

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Enum contract
# ---------------------------------------------------------------------------


def test_userstatus_has_interviewing():
    assert UserStatus.interviewing.value == "interviewing"


def test_userstatus_member_set_is_full_pipeline():
    assert {m.value for m in UserStatus} == {
        "tracking", "applied", "interviewing", "offer", "rejected", "hired"
    }


def test_patch_request_accepts_interviewing():
    req = PatchApplicationRequest(user_status=UserStatus.interviewing)
    assert req.user_status == UserStatus.interviewing


# ---------------------------------------------------------------------------
# Service round-trip
# ---------------------------------------------------------------------------


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
    import applire.models.cover_letter  # noqa: F401
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


def _make_job():
    from applire.models.job import JobAnalysis

    return JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=uuid.uuid4().hex,
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


@pytest_asyncio.fixture
async def user_and_jobs(db):
    """Insert a stub user and two job analyses; return (user, [job1, job2])."""
    from applire.models.user import User

    user = User(
        id=_STUB_USER_ID,
        email="local@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    jobs = [_make_job(), _make_job()]
    db.add_all([user, *jobs])
    await db.commit()
    return user, jobs


@pytest.mark.asyncio
async def test_patch_sets_interviewing(db, user_and_jobs):
    _, jobs = user_and_jobs
    created = await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=jobs[0].id), db
    )
    patched = await patch_application(
        created.id, _STUB_USER_ID, PatchApplicationRequest(user_status=UserStatus.interviewing), db
    )
    assert patched.user_status == UserStatus.interviewing


@pytest.mark.asyncio
async def test_list_filters_by_interviewing(db, user_and_jobs):
    _, jobs = user_and_jobs
    first = await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=jobs[0].id), db
    )
    await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=jobs[1].id), db
    )
    await patch_application(
        first.id, _STUB_USER_ID, PatchApplicationRequest(user_status=UserStatus.interviewing), db
    )

    result = await list_applications(
        _STUB_USER_ID, db, user_status=UserStatus.interviewing
    )
    assert len(result.items) == 1
    assert result.items[0].id == first.id
    assert result.items[0].user_status == UserStatus.interviewing
