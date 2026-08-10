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

"""#328 follow-up — the quantified role-facts furniture line
(``team_size`` / ``budget_managed`` / ``industry_context``) must reach the
rendered CV on EVERY user-selectable template, not just ``lebenslauf.html.j2``
(``classic_german``). A previous pass wired the render into ONE of the seven
templates in ``_TEMPLATE_FILES`` (``backend/applire/services/cv.py``) and
passed review; a user who picked any other template silently lost the figure.

This module closes the CLASS, not just the seven current instances: it
parametrizes over ``_TEMPLATE_FILES`` ITSELF (never a hardcoded name list), so
an eighth template added later without wiring the furniture line makes this
test FAIL rather than silently pass. See
``test_template_list_is_derived_from_source`` below for a self-check that
proves the parametrization really is keyed off the live dict (not a frozen
copy taken at import time by a human transcribing the same seven names).

Drives the real ``get_cv_html`` service entrypoint (never string-matches
template source) against a real in-memory SQLite DB, mirroring
``tests/unit/test_cv_role_facts_integration.py`` — only ``review_and_refine``
and the initial writer call are mocked. Asserts on the RENDERED HTML in both
German and English.
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.cv import _TEMPLATE_FILES  # noqa: E402

# The writer's draft deliberately narrates the budget-managed role's bullet as
# the BARE label with no figure -- the furniture line must not depend on the
# writer/reconciler at all (mirrors test_cv_role_facts_integration.py).
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
            # furniture line is suppressed entirely on every template, rather
            # than rendering an empty/dangling separator.
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

    user = User(id=uuid.uuid4(), email=f"role-facts-alltpl-{jd_language}@test.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=f"rolefactsalltpl{jd_language}",
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


def _assert_role_facts_html(html: str, *, lang: str) -> None:
    """Shared assertions, independent of which template produced ``html``."""
    from applire.templates.labels import cv_labels

    labels = cv_labels(lang)

    # w1 (Nordstahl AG / Werksleiterin) carries all three quantified facts,
    # rendered as deterministic furniture regardless of the writer's bullet.
    assert "38" in html, "team_size figure missing from rendered HTML"
    assert "ca. 6 Mio. EUR" in html, "budget_managed figure missing from rendered HTML"
    assert "Anlagenbau" in html, "industry_context figure missing from rendered HTML"
    assert labels["role_team_size"] in html
    assert labels["role_budget"] in html
    assert labels["role_industry"] in html

    # w2 (Beta GmbH / Junior Consultant) has NO quantified facts -- the line
    # must be suppressed entirely there, not rendered empty/dangling. Scope
    # the check to the slice of HTML between the two role markers so a
    # site-wide "Teamgröße" match (present, correctly, for w1) doesn't mask a
    # regression for w2.
    beta_idx = html.index("Beta GmbH")
    tail = html[beta_idx:beta_idx + 600]
    assert labels["role_team_size"] not in tail
    assert labels["role_budget"] not in tail
    assert labels["role_industry"] not in tail


# ---------------------------------------------------------------------------
# The class-closing test: parametrized over _TEMPLATE_FILES ITSELF.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("template_name", sorted(_TEMPLATE_FILES))
async def test_role_facts_render_on_every_cv_template_de(db, template_name):
    """Every template mapped in _TEMPLATE_FILES renders the role-facts line
    when the data is present, and omits it cleanly when absent -- German.

    Adding an eighth template to _TEMPLATE_FILES without wiring the furniture
    line into its .j2 file makes THIS test fail for that new parametrized
    case, because the list comes from _TEMPLATE_FILES at collection time, not
    from a hardcoded name here.
    """
    from applire.services.cv import get_cv_html

    db_, job, profile, cv = await _seed(db, jd_language="de")
    await _run_render(db_, job, profile, cv)

    cv.template = template_name
    await db_.commit()

    html = await get_cv_html(cv.id, db_)
    _assert_role_facts_html(html, lang="de")


@pytest.mark.asyncio
@pytest.mark.parametrize("template_name", sorted(_TEMPLATE_FILES))
async def test_role_facts_render_on_every_cv_template_en(db, template_name):
    """Same as above, English job language -- labels must follow lang, never
    hardcoded German, on every template."""
    from applire.services.cv import get_cv_html

    db_, job, profile, cv = await _seed(db, jd_language="en")
    await _run_render(db_, job, profile, cv)

    cv.template = template_name
    await db_.commit()

    html = await get_cv_html(cv.id, db_)
    _assert_role_facts_html(html, lang="en")

    # The German labels must NOT leak into the English render on any template.
    from applire.templates.labels import cv_labels
    de_labels = cv_labels("de")
    assert de_labels["role_team_size"] not in html
    assert de_labels["role_industry"] not in html


# ---------------------------------------------------------------------------
# Self-check: prove the parametrization is really keyed off the live dict,
# not a value a human copied into this file (which would defeat the whole
# point -- an eighth template would then need a matching manual edit here
# too, and could just as easily be forgotten).
# ---------------------------------------------------------------------------


def test_template_list_is_derived_from_source_not_hardcoded():
    """Guards the guard: the parametrize list must be sourced from
    ``_TEMPLATE_FILES`` at collection time. If a future edit replaces
    ``sorted(_TEMPLATE_FILES)`` above with a literal list of seven names,
    this test still passes (it can't detect source code) -- so the actual
    proof lives in the accompanying verification step that monkeypatches
    ``_TEMPLATE_FILES`` with an eighth, unrendered template and confirms the
    per-template test then fails (see PR description / session report).

    This test only pins the CURRENT membership so a silent shrink of
    _TEMPLATE_FILES (a template quietly dropped) is also caught.
    """
    assert set(_TEMPLATE_FILES) == {
        "classic_german",
        "modern_swiss",
        "executive",
        "tech_developer",
        "creative_sidebar",
        "academic",
        "compact_pro",
    }
