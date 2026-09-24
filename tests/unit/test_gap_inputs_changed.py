# Copyright (C) 2026 Tobias Rosenbaum
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

"""ADR-090 clause 8, gap half — a gap analysis says it is stale instead of
re-running by itself.

The gap analysis is a model judgement; ADR-090 rules that it never re-runs
because of a change made outside its own view. The read route therefore
returns the stored row with ``inputs_changed``, derived from a fingerprint
over the part of the profile the analysis reads (no contact data, photo or
bookkeeping), and the gap view offers the re-check. A row from before
Alembic 0069 falls back to the whole-profile fingerprint.
"""

import copy
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.models.profile import authorized_profile_write
from applire.models.user import User
from applire.providers.llm.mock import MockLLMProvider
from applire.routers.job import get_latest_gap_analysis
from applire.services.gap import (
    _gap_inputs_fingerprint,
    _input_fingerprint,
    analysis_inputs_changed,
    analyze_gaps,
    gap_relevant_profile,
)
from tests.support.profile_factory import make_master_profile

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")


def _profile_json() -> dict:
    return {
        "personal_info": {
            "name": "Max Muster",
            "email": "max@test.de",
            "phone": "+49 1",
            "photo_url": "/uploads/a.png",
            "location": "Köln",
        },
        "professional_summary": {"de": "Entwickler", "en": "Developer"},
        "work_experience": [{"company": "Acme", "role": "Dev", "start_date": "2018-01"}],
        "education": [{"institution": "TU Berlin", "degree": "BSc", "field": "CS"}],
        "skills": [{"name": "Python", "category": "technical", "proficiency": "expert"}],
        "languages": [{"language": "German", "level": "native"}],
        "certifications": [],
        "publications": [],
        "volunteer_activities": [],
        "metadata": {
            "completeness_score": 0.7,
            "last_updated": "2026-09-01T00:00:00+00:00",
            "application_count": 1,
            "enrichment_history": [],
            "denied_concepts": [],
        },
        "_meta": {"na_fields": []},
    }


def _job() -> JobAnalysis:
    return JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="hash-inputs-changed",
        raw_text="Senior Python Engineer",
        role_title="Senior Python Engineer",
        required_skills=["Python", "FastAPI"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="DE",
    )


def _with(profile_json: dict, mutate) -> dict:
    out = copy.deepcopy(profile_json)
    mutate(out)
    return out


# ---------------------------------------------------------------------------
# The fingerprint's scope
# ---------------------------------------------------------------------------


def test_gap_relevant_profile_drops_contact_photo_and_bookkeeping():
    view = gap_relevant_profile(_profile_json())
    assert view["personal_info"] == {"location": "Köln"}
    assert "_meta" not in view
    assert view["metadata"] == {"denied_concepts": []}
    assert view["skills"] == _profile_json()["skills"]


NOISE = {
    "email": lambda p: p["personal_info"].update(email="new@test.de"),
    "phone": lambda p: p["personal_info"].update(phone="+49 2"),
    "photo": lambda p: p["personal_info"].update(photo_url="/uploads/b.png"),
    "name": lambda p: p["personal_info"].update(name="Max M."),
    "last_updated": lambda p: p["metadata"].update(last_updated="2026-09-24T00:00:00+00:00"),
    "application_count": lambda p: p["metadata"].update(application_count=5),
    "enrichment_history": lambda p: p["metadata"]["enrichment_history"].append({"id": "x"}),
    "completeness": lambda p: p["metadata"].update(completeness_score=0.9),
    "na_fields": lambda p: p["_meta"]["na_fields"].append("publications"),
}

RELEVANT = {
    "skill": lambda p: p["skills"].append({"name": "Kubernetes"}),
    "role": lambda p: p["work_experience"].append({"company": "Beta", "role": "Lead"}),
    "denial": lambda p: p["metadata"]["denied_concepts"].append({"concept": "Kubernetes"}),
    "location": lambda p: p["personal_info"].update(location="Berlin"),
    "summary": lambda p: p["professional_summary"].update(en="Platform developer"),
}


@pytest.mark.parametrize("name", sorted(NOISE))
def test_a_change_the_analysis_does_not_read_keeps_the_fingerprint(name):
    job = _job()
    before = make_master_profile(id=uuid.uuid4(), profile_json=_profile_json())
    after = make_master_profile(id=uuid.uuid4(), profile_json=_with(_profile_json(), NOISE[name]))
    assert _gap_inputs_fingerprint(job, before) == _gap_inputs_fingerprint(job, after)
    # The whole-profile idempotency key DOES move — that is the noise the
    # gap-relevant fingerprint exists to remove.
    assert _input_fingerprint(job, before) != _input_fingerprint(job, after)


@pytest.mark.parametrize("name", sorted(RELEVANT))
def test_a_change_the_analysis_reads_moves_the_fingerprint(name):
    job = _job()
    before = make_master_profile(id=uuid.uuid4(), profile_json=_profile_json())
    after = make_master_profile(id=uuid.uuid4(), profile_json=_with(_profile_json(), RELEVANT[name]))
    assert _gap_inputs_fingerprint(job, before) != _gap_inputs_fingerprint(job, after)


# ---------------------------------------------------------------------------
# analysis_inputs_changed — stored fingerprint, legacy fallback
# ---------------------------------------------------------------------------


def _row(job, profile, *, gap_fp: bool, whole_fp: bool) -> GapAnalysis:
    return GapAnalysis(
        job_analysis_id=job.id,
        profile_id=profile.id,
        input_fingerprint=_input_fingerprint(job, profile) if whole_fp else None,
        gap_inputs_fingerprint=_gap_inputs_fingerprint(job, profile) if gap_fp else None,
    )


def test_a_new_row_ignores_a_contact_edit_and_flags_a_skill():
    job = _job()
    at_analysis = make_master_profile(id=uuid.uuid4(), profile_json=_profile_json())
    row = _row(job, at_analysis, gap_fp=True, whole_fp=True)
    assert analysis_inputs_changed(row, job, at_analysis) is False
    contact = make_master_profile(id=uuid.uuid4(), profile_json=_with(_profile_json(), NOISE["email"]))
    assert analysis_inputs_changed(row, job, contact) is False
    skill = make_master_profile(id=uuid.uuid4(), profile_json=_with(_profile_json(), RELEVANT["skill"]))
    assert analysis_inputs_changed(row, job, skill) is True


def test_a_row_from_before_0069_falls_back_to_the_whole_profile_fingerprint():
    """Noisy, never silent: without its own fingerprint a legacy row also
    flags a contact edit — but it does flag the skill."""
    job = _job()
    at_analysis = make_master_profile(id=uuid.uuid4(), profile_json=_profile_json())
    row = _row(job, at_analysis, gap_fp=False, whole_fp=True)
    assert analysis_inputs_changed(row, job, at_analysis) is False
    skill = make_master_profile(id=uuid.uuid4(), profile_json=_with(_profile_json(), RELEVANT["skill"]))
    assert analysis_inputs_changed(row, job, skill) is True
    contact = make_master_profile(id=uuid.uuid4(), profile_json=_with(_profile_json(), NOISE["email"]))
    assert analysis_inputs_changed(row, job, contact) is True


def test_a_row_with_no_fingerprint_at_all_reads_unchanged():
    job = _job()
    profile = make_master_profile(id=uuid.uuid4(), profile_json=_profile_json())
    row = _row(job, profile, gap_fp=False, whole_fp=False)
    assert analysis_inputs_changed(row, job, profile) is False


# ---------------------------------------------------------------------------
# Seams: the row write, the legacy backfill, the read route
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def factory():
    from applire.db.session import Base
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.gap_job  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.user_settings  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, expire_on_commit=False)
    yield f
    await engine.dispose()


@pytest_asyncio.fixture
async def db(factory):
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def seeded(db):
    user = User(
        id=_STUB_USER_ID,
        email="local@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = _job()
    profile = make_master_profile(id=uuid.uuid4(), profile_json=_profile_json())
    db.add_all([user, job, profile])
    await db.commit()
    return job, profile


async def _row_count(db, job_id) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(GapAnalysis).where(
                GapAnalysis.job_analysis_id == job_id, GapAnalysis.deleted_at.is_(None)
            )
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_a_new_analysis_row_stores_its_gap_relevant_fingerprint(db, seeded):
    job, profile = seeded
    result = await analyze_gaps(job.id, db, MockLLMProvider())
    row = await db.get(GapAnalysis, result.id)
    assert row.gap_inputs_fingerprint == _gap_inputs_fingerprint(job, profile)
    assert result.inputs_changed is False, "a just-computed analysis is the check"


@pytest.mark.asyncio
async def test_an_unchanged_reuse_backfills_a_row_from_before_0069(db, seeded):
    job, profile = seeded
    result = await analyze_gaps(job.id, db, MockLLMProvider())
    row = await db.get(GapAnalysis, result.id)
    row.gap_inputs_fingerprint = None
    await db.commit()

    again = await analyze_gaps(job.id, db, MockLLMProvider())
    assert again.id == result.id, "unchanged inputs reuse the row"
    await db.refresh(row)
    assert row.gap_inputs_fingerprint == _gap_inputs_fingerprint(job, profile)


@pytest.mark.asyncio
async def test_the_read_route_says_stale_and_never_re_runs(db, seeded):
    """ADR-090 clause 8: GET /gaps after a gap-relevant profile change returns
    the SAME stored row with inputs_changed, and writes no new analysis."""
    job, profile = seeded
    first = await analyze_gaps(job.id, db, MockLLMProvider())
    await db.commit()

    fresh = await get_latest_gap_analysis(job_id=job.id, db=db, _auth=None)
    assert fresh.id == first.id
    assert fresh.inputs_changed is False

    # A profile edit through any door (the test stands in for commit_ops).
    with authorized_profile_write():
        profile.profile_json = _with(profile.profile_json, NOISE["email"])
    await db.commit()
    contact = await get_latest_gap_analysis(job_id=job.id, db=db, _auth=None)
    assert contact.inputs_changed is False, "a contact edit is not a reason to re-check"

    with authorized_profile_write():
        profile.profile_json = _with(profile.profile_json, RELEVANT["skill"])
    await db.commit()
    stale = await get_latest_gap_analysis(job_id=job.id, db=db, _auth=None)
    assert stale.id == first.id
    assert stale.inputs_changed is True
    assert await _row_count(db, job.id) == 1, "the read never re-ran the analysis"


def test_migration_0069_chains_onto_0068():
    import importlib.util
    from pathlib import Path

    import applire

    root = Path(applire.__path__[0]).parent
    path = root / "alembic" / "versions" / "0069_gap_inputs_fingerprint.py"
    spec = importlib.util.spec_from_file_location("m0069", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0069"
    assert module.down_revision == "0068"
