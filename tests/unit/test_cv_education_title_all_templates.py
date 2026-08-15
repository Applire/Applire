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

"""#548 — "Industriemeister Metall, Metall" must not ship from ANY of the 7
CV templates, and a legitimately distinct field ("Bachelor of Science,
Informatik") must survive on all 7 too.

``education`` reaches the delivered document as a verbatim pass-through of
the vault's own array (``services/cv.py`` — "the education/languages section
call is retired", no LLM rewrites it during tailoring), so this test seeds
``tailored_data`` directly rather than mocking a writer draft; the join under
test lives entirely at render time. Mirrors
``test_cv_budget_unit_omission_all_templates.py``'s structure and drives the
same real ``get_cv_html`` entrypoint against every template in
``_TEMPLATE_FILES``, parametrized off the live dict so an eighth template
added later without the shared filter wired in fails this test rather than
silently shipping the duplication.
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.cv import _TEMPLATE_FILES  # noqa: E402

from tests.support.profile_factory import make_master_profile  # noqa: E402

# e1: the exact real-run shape (#548) -- field duplicates a word already in
#     degree. e2: a legitimately distinct field that must survive.
PROFILE_JSON = {
    "personal_info": {"name": "Stefan Brandt", "email": "stefan@example.com"},
    "professional_summary": {
        "de": "Erfahrener Fertigungsmeister.",
        "en": "Experienced production supervisor.",
    },
    "work_experience": [],
    "skills": [],
    "projects": [],
    "education": [
        {
            "id": "e1",
            "institution": "IHK",
            "degree": "Industriemeister Metall",
            "field": "Metall",
            "start_date": None,
            "end_date": "2010",
        },
        {
            "id": "e2",
            "institution": "TU Muenchen",
            "degree": "Bachelor of Science",
            "field": "Informatik",
            "start_date": "2012",
            "end_date": "2016",
        },
    ],
    "languages": [],
}


def _writer_draft():
    return {
        "contact": {
            "name": "Stefan Brandt", "email": "stefan@example.com",
            "phone": None, "location": None, "linkedin": None,
        },
        "summary": "Erfahrener Fertigungsmeister mit Fokus auf Blechumformung.",
        "work_history": [],
        "skills": [],
        "education": [],
        "languages": [],
    }


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.color_profile  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db, *, jd_language: str):
    from applire.models.cv import CVGenerationStatus, GeneratedCV
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.user import User

    user = User(id=uuid.uuid4(), email=f"education-title-{jd_language}@test.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=f"educationtitle{jd_language}",
        raw_text="Fertigungsleitung" if jd_language == "de" else "Production leadership",
        role_title="Fertigungsmeister" if jd_language == "de" else "Production Supervisor",
        company_name="IHK",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement=jd_language,
        jd_language=jd_language,
    )
    db.add(job)
    profile = make_master_profile(profile_json=PROFILE_JSON)
    db.add(profile)
    await db.flush()

    gap = GapAnalysis(job_analysis_id=job.id, profile_id=profile.id, keyword_ledger=[])
    db.add(gap)

    cv = GeneratedCV(
        job_analysis_id=job.id,
        profile_id=profile.id,
        template="classic_german",
        tailored_data={},
        status=CVGenerationStatus.pending.value,
        target_pages=2,
    )
    db.add(cv)
    await db.commit()
    await db.refresh(cv)
    return db, job, profile, cv


async def _run_render(db, job, profile, cv):
    from applire.services.cv import _render_cv_background

    mock_provider = MagicMock()

    async def _identity_review(*, draft, **_kwargs):
        return draft

    with (
        patch("applire.services.cv.AsyncSessionLocal") as mock_session_local,
        patch("applire.services.cv.get_provider", return_value=mock_provider),
        patch(
            "applire.services.cv._tailor_cv_with_fallback",
            AsyncMock(return_value=_writer_draft()),
        ),
        patch(
            "applire.services.cv.review_and_refine",
            AsyncMock(side_effect=_identity_review),
        ),
        patch(
            "applire.services.cv._html_to_pdf",
            AsyncMock(side_effect=RuntimeError("no browser in unit test")),
        ),
    ):
        mock_session_local.return_value.__aenter__.return_value = db
        await _render_cv_background(
            cv_id=cv.id, job_id=job.id, profile_id=profile.id, template="classic_german",
        )
    await db.refresh(cv)


def _assert_education_html(html: str) -> None:
    """Shared assertions, independent of which template produced ``html``."""
    # e1 (#548): the duplicated shape must never appear, in any spacing/case
    # a re-introduced join could produce.
    for bad_shape in (
        "Industriemeister Metall, Metall",
        "Industriemeister Metall,Metall",
        "Industriemeister Metall , Metall",
    ):
        assert bad_shape not in html, f"the doubled field leaked into the CV as {bad_shape!r}"
    # The correct, single-occurrence title must still be present.
    assert "Industriemeister Metall" in html
    # And "Metall" must not appear a second time anywhere near it as a
    # trailing fragment -- count occurrences directly rather than trusting
    # the substring check above alone.
    assert html.count("Industriemeister Metall") == 1

    # e2: a legitimately distinct field must NOT be a casualty of the fix.
    assert "Bachelor of Science, Informatik" in html


@pytest.mark.asyncio
@pytest.mark.parametrize("template_name", sorted(_TEMPLATE_FILES))
async def test_education_field_not_doubled_on_every_cv_template_de(db, template_name):
    from applire.services.cv import get_cv_html

    db_, job, profile, cv = await _seed(db, jd_language="de")
    await _run_render(db_, job, profile, cv)

    cv.template = template_name
    await db_.commit()

    html = await get_cv_html(cv.id, db_)
    _assert_education_html(html)


@pytest.mark.asyncio
@pytest.mark.parametrize("template_name", sorted(_TEMPLATE_FILES))
async def test_education_field_not_doubled_on_every_cv_template_en(db, template_name):
    from applire.services.cv import get_cv_html

    db_, job, profile, cv = await _seed(db, jd_language="en")
    await _run_render(db_, job, profile, cv)

    cv.template = template_name
    await db_.commit()

    html = await get_cv_html(cv.id, db_)
    _assert_education_html(html)
