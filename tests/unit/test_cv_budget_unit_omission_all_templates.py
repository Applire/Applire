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

"""#382 — a budget figure with no unit is OMITTED from the delivered CV, on
every template (PO decision 2026-08-08, **Option A**).

``GET /api/cv/{id}/html`` rendered ``Budget: 6000000`` (a raw digit string, no
separator, no currency) two lines above the writer's own polished prose quoting
the SAME figure ("ca. 6 Mio. € pro Jahr"). The adversarial pass of 2026-07-30
(finding 1) fixed the *legibility* at the template layer — ``6.000.000`` — and
this file pinned that. **The PO superseded it**: grouping leaves the figure
exactly as ambiguous (six million of what?), furniture is read as authoritative
structured data, and inventing a currency would be fabrication. So a unit-less
budget is not rendered at all; the vault keeps it and the user is asked for the
unit (Health hub + master profile page).

Mirrors ``test_cv_role_facts_all_templates.py``'s structure and drives the same
real ``get_cv_html`` entrypoint against every template in ``_TEMPLATE_FILES``,
so an eighth template cannot ship the omission un-wired. Three cases, one
render:

* ``w1`` — the exact real-run shape (bare ``"6000000"``, the reconciler's own
  ``SetField`` int→str coercion): the whole labelled budget item disappears,
  while the entry's OTHER role facts stay.
* ``w2`` — no quantified facts at all: no furniture line, no dangling separator.
* ``w3`` — a unit-bearing value: renders verbatim, unreformatted. This is the
  half of the contract that keeps the fix from being "drop budgets".
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

PROFILE_JSON = {
    "personal_info": {"name": "Jonas Feldmann", "email": "jonas@example.com"},
    "professional_summary": {
        "de": "Erfahrener Produktionsleiter.",
        "en": "Experienced production manager.",
    },
    "work_experience": [
        {
            "id": "w1",
            "company": "Weberit Kunststofftechnik GmbH",
            "role": "Produktionsleiter",
            "start_date": "2016-01",
            "end_date": None,
            "is_current": True,
            "responsibilities": ["Budgetverantwortung von ca. 6 Mio. EUR pro Jahr."],
            "achievements": [],
            "team_size": 38,
            # The exact real-run shape: a bare digit string, no separator, no
            # currency -- the reconciler's own coercion (SetField int -> str).
            "budget_managed": "6000000",
            "industry_context": "Kunststofftechnik (Spritzguss, Montage)",
        },
        {
            # No quantified facts at all -- the furniture line must be
            # suppressed entirely, never rendered with a dangling separator.
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
        {
            # The other half of the contract: a value that says what it means
            # still renders, verbatim and unreformatted. Option A omits; it
            # never rewrites the candidate's own wording, and it is not a
            # blanket "drop budgets".
            "id": "w3",
            "company": "Hansen Verpackungen KG",
            "role": "Schichtleiter",
            "start_date": "2010-01",
            "end_date": "2012-12",
            "is_current": False,
            "responsibilities": ["Verantwortung fuer das Schichtbudget."],
            "achievements": [],
            "team_size": None,
            "budget_managed": "ca. 1,2 Mio. EUR",
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
            "name": "Jonas Feldmann", "email": "jonas@example.com",
            "phone": None, "location": None, "linkedin": None,
        },
        "summary": "Erfahrener Produktionsleiter mit Fokus auf Kunststofftechnik.",
        "work_history": [
            {
                "id": "w1", "company": "Weberit Kunststofftechnik GmbH",
                "role": "Produktionsleiter",
                "start_date": "2016-01", "end_date": None,
                "bullets": ["Budgetverantwortung"],
            },
            {
                "id": "w2", "company": "Beta GmbH", "role": "Junior Consultant",
                "start_date": "2013-01", "end_date": "2015-12",
                "bullets": ["Unterstützte das Beraterteam bei Kundenprojekten."],
            },
            {
                "id": "w3", "company": "Hansen Verpackungen KG",
                "role": "Schichtleiter",
                "start_date": "2010-01", "end_date": "2012-12",
                "bullets": ["Verantwortung fuer das Schichtbudget."],
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

    user = User(id=uuid.uuid4(), email=f"budget-display-{jd_language}@test.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=f"budgetdisplay{jd_language}",
        raw_text="Produktionsleitung" if jd_language == "de" else "Production management",
        role_title="Produktionsleiter" if jd_language == "de" else "Production Manager",
        company_name="Weberit Kunststofftechnik GmbH",
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


def _assert_budget_html(html: str, *, budget_label: str) -> None:
    """Shared assertions, independent of which template produced ``html``."""
    # w1: the bare figure is gone in EVERY shape it could have taken -- raw,
    # German-grouped and English-grouped -- so a re-introduced formatting pass
    # cannot slip it back in under a different spelling.
    for shape in ("6000000", "6.000.000", "6,000,000"):
        assert shape not in html, f"unit-less budget leaked into the CV as {shape!r}"
    # ...and no currency was invented to rescue it.
    assert "6 Mio" not in html and "6 000 000" not in html

    # Slice the document at the entry boundaries rather than by a fixed window:
    # the three entries render in vault (date-descending) order, so each slice
    # holds exactly one entry's furniture and a neighbour's budget cannot mask
    # or fake a result. w1 -> w2 -> w3.
    beta_idx = html.index("Beta GmbH")
    hansen_idx = html.index("Hansen Verpackungen")
    assert beta_idx < hansen_idx
    weberit_slice, beta_slice, hansen_slice = (
        html[:beta_idx], html[beta_idx:hansen_idx], html[hansen_idx:],
    )

    assert budget_label not in weberit_slice, (
        "the budget item must disappear whole -- label, value and separator"
    )
    # The omission is scoped to the budget: the entry's other role facts stay.
    assert "38" in weberit_slice, "team_size was collateral damage of the omission"
    assert "Kunststofftechnik" in weberit_slice, "industry_context was collateral damage"

    # w2 has no quantified facts at all -- no dangling "· " before/after an
    # empty budget slot.
    assert " ·  ·" not in beta_slice and "·  ·" not in beta_slice, "dangling separator"
    assert budget_label not in beta_slice

    # w3 carries a unit -- it renders, verbatim.
    assert budget_label in hansen_slice, "a unit-bearing budget must still render"
    assert "ca. 1,2 Mio. EUR" in hansen_slice, "the candidate's wording was reformatted"


@pytest.mark.asyncio
@pytest.mark.parametrize("template_name", sorted(_TEMPLATE_FILES))
async def test_unit_less_budget_is_omitted_on_every_cv_template_de(db, template_name):
    from applire.services.cv import get_cv_html
    from applire.templates.labels import cv_labels

    db_, job, profile, cv = await _seed(db, jd_language="de")
    await _run_render(db_, job, profile, cv)

    cv.template = template_name
    await db_.commit()

    html = await get_cv_html(cv.id, db_)
    _assert_budget_html(html, budget_label=cv_labels("de")["role_budget"])


@pytest.mark.asyncio
@pytest.mark.parametrize("template_name", sorted(_TEMPLATE_FILES))
async def test_unit_less_budget_is_omitted_on_every_cv_template_en(db, template_name):
    from applire.services.cv import get_cv_html
    from applire.templates.labels import cv_labels

    db_, job, profile, cv = await _seed(db, jd_language="en")
    await _run_render(db_, job, profile, cv)

    cv.template = template_name
    await db_.commit()

    html = await get_cv_html(cv.id, db_)
    _assert_budget_html(html, budget_label=cv_labels("en")["role_budget"])
