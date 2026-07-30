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

"""#328 — quantified role facts (team_size / budget_managed / industry_context)
reach the rendered CV as deterministic document furniture (ADR-062 clause 1),
independent of whether the writer LLM's prose narrates the figure.

#328 records that three consecutive fixes in this area passed their tests and
changed NOTHING on the page. So this drives the real service entrypoint
(``_render_cv_background``) end to end against a real SQLite DB, mirroring
``tests/unit/test_cv_narrative_coverage_integration.py`` — only
``review_and_refine`` and the initial writer call are mocked — then asserts on
the PERSISTED ``tailored_data`` AND on the rendered HTML from the real
``get_cv_html`` path (the only proof the furniture reaches a reader), in both
German and English.

The writer's draft deliberately narrates the budget-managed role's bullet as
the BARE word "Budgetverantwortung" (no figure) -- the exact #328 ground-truth
shape -- to prove the furniture line does not depend on writer/reconciler
compliance at all.
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

_BUDGET_BULLET_BARE = "Budgetverantwortung"

PROFILE_JSON = {
    "personal_info": {"name": "Petra Wolff", "email": "petra@example.com"},
    "professional_summary": {"de": "Erfahrene Werksleiterin.", "en": "Experienced plant manager."},
    "work_experience": [
        {
            "id": "w1",
            "company": "Nordstahl AG",
            "role": "Werksleiterin",
            "start_date": "2016-01",
            "end_date": None,
            "is_current": True,
            "responsibilities": [
                "Budgetverantwortung ca. 6 Mio. EUR (Personal, Instandhaltung, "
                "Material-Gemeinkosten)."
            ],
            "achievements": [],
            "team_size": 38,
            "budget_managed": "ca. 6 Mio. EUR",
            "industry_context": "Anlagenbau",
        },
        {
            # A second role with NO quantified facts at all -- proves the
            # furniture line is suppressed entirely rather than rendering an
            # empty/dangling separator.
            "id": "w2",
            "company": "Beta GmbH",
            "role": "Junior Consultant",
            "start_date": "2013-01",
            "end_date": "2015-12",
            "is_current": False,
            "responsibilities": ["Unterstützte das Beraterteam bei Kundenprojekten."],
            "achievements": [],
            "team_size": None,
            "budget_managed": None,
            "industry_context": None,
        },
    ],
    "skills": [],
    "projects": [],
    "education": [],
    "languages": [],
}


def _writer_draft():
    """The writer's draft: the budget-managed role's bullet is reduced to the
    bare label with NO figure -- the exact #328 ground-truth defect shape.
    Nothing downstream should need to fix this bullet for the figure to reach
    the page; the furniture line is a wholly separate deterministic surface.
    """
    return {
        "contact": {
            "name": "Petra Wolff", "email": "petra@example.com",
            "phone": None, "location": None, "linkedin": None,
        },
        "summary": "Erfahrene Werksleiterin mit Fokus auf Anlagenbau.",
        "work_history": [
            {
                "id": "w1", "company": "Nordstahl AG", "role": "Werksleiterin",
                "start_date": "2016-01", "end_date": None,
                "bullets": [_BUDGET_BULLET_BARE],
            },
            {
                "id": "w2", "company": "Beta GmbH", "role": "Junior Consultant",
                "start_date": "2013-01", "end_date": "2015-12",
                "bullets": ["Unterstützte das Beraterteam bei Kundenprojekten."],
            },
        ],
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

    user = User(id=uuid.uuid4(), email=f"role-facts-it-{jd_language}@test.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=f"rolefacts{jd_language}",
        raw_text="Werksleitung Anlagenbau" if jd_language == "de" else "Plant management",
        role_title="Werksleiterin" if jd_language == "de" else "Plant Manager",
        company_name="Nordstahl AG",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement=jd_language,
        jd_language=jd_language,
    )
    db.add(job)
    profile = MasterProfile(profile_json=PROFILE_JSON)
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


@pytest.mark.asyncio
async def test_role_facts_persisted_on_tailored_work_entry(db):
    """The vault's team_size/budget_managed/industry_context reach the
    PERSISTED tailored_data on the matching work entry -- keyed by the same
    WorkEntry id _backfill_work_ids establishes, not by company-name string --
    even though the writer's own bullet dropped the figure entirely."""
    db, job, profile, cv = await _seed(db, jd_language="de")
    await _run_render(db, job, profile, cv)

    from applire.models.cv import CVGenerationStatus
    assert cv.status == CVGenerationStatus.ready.value

    work = cv.tailored_data["work_history"]
    w1 = next(w for w in work if w["id"] == "w1")
    assert w1["team_size"] == 38
    assert w1["budget_managed"] == "ca. 6 Mio. EUR"
    assert w1["industry_context"] == "Anlagenbau"
    # The writer's under-specified bullet is untouched by this pass -- the
    # furniture line is a wholly separate deterministic surface, not a bullet
    # rewrite.
    assert w1["bullets"] == [_BUDGET_BULLET_BARE]

    w2 = next(w for w in work if w["id"] == "w2")
    assert w2["team_size"] is None
    assert w2["budget_managed"] is None
    assert w2["industry_context"] is None


@pytest.mark.asyncio
async def test_role_facts_rendered_as_furniture_in_german_html(db):
    """The ONLY proof the furniture reaches a reader: the real get_cv_html
    path, in German."""
    db, job, profile, cv = await _seed(db, jd_language="de")
    await _run_render(db, job, profile, cv)

    from applire.services.cv import get_cv_html
    html = await get_cv_html(cv.id, db)

    assert "38" in html
    assert "ca. 6 Mio. EUR" in html
    assert "Anlagenbau" in html
    # DE labels (labels[lang] chrome mechanism, ADR-038) -- not hardcoded EN.
    assert "Teamgröße" in html
    assert "Branche" in html

    # The no-facts role must NOT grow a dangling furniture line (no empty
    # separators, no empty label pair).
    import re
    junior_block = html[html.index("Junior Consultant"):html.index("Junior Consultant") + 400]
    assert "Teamgröße" not in junior_block
    assert "Budget" not in junior_block
    assert "Branche" not in junior_block


@pytest.mark.asyncio
async def test_role_facts_rendered_as_furniture_in_english_html(db):
    """Same document, English-language job -- the labels must follow the
    output language via labels[lang], never hardcoded German."""
    db, job, profile, cv = await _seed(db, jd_language="en")
    await _run_render(db, job, profile, cv)

    from applire.services.cv import get_cv_html
    html = await get_cv_html(cv.id, db)

    assert "38" in html
    assert "ca. 6 Mio. EUR" in html
    assert "Anlagenbau" in html
    assert "Team size" in html
    assert "Industry" in html
    # The German labels must NOT leak into the English render.
    assert "Teamgröße" not in html
    assert "Branche" not in html
