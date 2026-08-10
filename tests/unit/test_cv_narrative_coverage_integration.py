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

"""#376 wired into the real generation path.

**Scope note (2026-07-30): #303 was REVERTED and its assertions removed from
this module.** Its narrative-presence predicate demanded that a ledger
concept's own literal surface form appear in the CV's narrative — but the
Keyword Ledger's surface forms for this defect class are abstract German
compound nouns (``Investitionsplanung``, ``Arbeitsvorbereitung``), which
honest testimony never contains verbatim ("Instandhaltungsinvestitionen plane
und priorisiere ich selbst"). On a real-provider charter run the predicate was
therefore unsatisfiable: both CV reviewer loops churned to full retry
exhaustion without ever reaching ``approved: true``, the writer began emitting
keyword-labelled bullets to feed the literal scan, and the pre-existing bullet
cap — which cuts no-hit bullets first, "hit" meaning literal ledger-form
presence — deleted the candidate's most quantified achievement (an LTIF
accident-rate reduction) from the delivered document. The fixture below still
carries the Kubernetes shape #303 was filed against; it is retained as
ground truth for the re-scoped issue, and this module deliberately asserts
nothing about it. See the re-filed issue for the full run evidence.

Drives ``_render_cv_background`` end to end (the actual service entrypoint,
not just the pure guard functions), against a real SQLite DB via
``async_sessionmaker`` — mirrors
``tests/unit/test_cover_letter_figure_guard_integration.py``. Only
``review_and_refine``'s return value is mocked (as an identity pass-through
of the draft it is handed, never a fixed value substituted for the prompt's
own effect) and the initial writer call (``_tailor_cv_with_fallback``) is
mocked to hand back the synthetic ground-truth draft — the SAME idiom
``tests/unit/test_cv_ledger_bullet_guard.py::
TestRestoreLedgerBulletsWiredIntoBackgroundRender`` already establishes for
this exact service entrypoint. Nothing about prompt CONTENT is asserted on;
every assertion below reads the PERSISTED ``tailored_data`` after
``await db.refresh(record)`` (#328: three prior fixes in this area passed
their tests and changed nothing on the page — this is the guard against
exactly that).

Fixture (synthetic candidate, ``panel_review_case``-style, no real profile
data): a Leiter-Operations CV where
  * the vault's own narrative names ``Kubernetes`` on a bullet the writer's
    draft dropped, while the draft's skills list still carries the bare
    "Kubernetes" tag (#303's exact shape), and
  * the writer's draft narrates "SAP PP und SAP MM" in a work bullet but its
    own skills list lists only "SAP PP" (#376's exact reported shape).
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

_VAULT_BULLET_GENERIC = "Owned the platform roadmap for the infrastructure team."
_VAULT_BULLET_KUBERNETES = (
    "Migrated the deployment pipeline onto Kubernetes across three regions."
)
_SAP_BULLET = (
    "Tägliche Arbeit mit SAP PP und SAP MM (Disposition und Bestellanforderungen)."
)

PROFILE_JSON = {
    "personal_info": {"name": "Stefan Brandt", "email": "stefan@example.com"},
    "professional_summary": {"de": "Erfahrener Leiter Operations."},
    "work_experience": [
        {
            "id": "w1",
            "company": "Rheinwerk",
            "role": "Leiter Operations",
            "start_date": "2018-01",
            "end_date": None,
            "is_current": True,
            # The Kubernetes bullet is here (vault) but the DRAFT below drops it —
            # #303's restoration guard must pull it back in.
            "responsibilities": [_VAULT_BULLET_GENERIC, _VAULT_BULLET_KUBERNETES],
            "achievements": [],
        }
    ],
    "skills": [
        {"name": "SAP PP", "category": "technical"},
        {"name": "SAP MM", "category": "technical"},
        {"name": "Kubernetes", "category": "technical"},
    ],
    "projects": [],
    "education": [],
    "languages": [],
}

# The Keyword Ledger — "Kubernetes" is a hard requirement the candidate
# genuinely has (status=direct, claimable=True, fit_weight=1.0) with NO
# figure in its evidence, exactly the #303 shape (contrast #315's
# figure-carrying "Budgetverantwortung", already caught by the older guard).
KEYWORD_LEDGER = [
    {
        "concept": "Kubernetes",
        "surface_forms": ["Kubernetes", "K8s"],
        "claimable": True,
        "status": "direct",
        "sources": ["required"],
        "fit_weight": 1.0,
        "evidence": "Explicitly listed as a skill (Kubernetes, expert, 6 years).",
    },
]


def _writer_draft():
    """The writer's draft, exactly as the review loop hands it onward:
    Kubernetes dropped from the narrative (bare skills tag only) -- #303;
    "SAP MM" named in the bullet but missing from the skills list -- #376.

    E049/ADR-067: the writer's response is the shared PROSE shape (summary +
    id-keyed work + skills) -- contact/company/role/dates/education/languages
    are joined from the vault by assemble_tailored_cv, never emitted here.
    """
    return {
        "summary": "Erfahrener Leiter Operations mit Fokus auf ERP und Infrastruktur.",
        "work": [
            {"id": "w1", "bullets": [_VAULT_BULLET_GENERIC, _SAP_BULLET]},
        ],
        "skills": ["Kubernetes", "SAP PP"],
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

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(db):
    from applire.models.cv import CVGenerationStatus, GeneratedCV
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.user import User

    user = User(id=uuid.uuid4(), email="narrative-coverage-it@test.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="narrativecov123",
        raw_text="Leiter Operations bei Rheinwerk",
        role_title="Leiter Operations",
        company_name="Rheinwerk",
        required_skills=["Kubernetes"],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="de",
        jd_language="de",
    )
    db.add(job)
    profile = make_master_profile(profile_json=PROFILE_JSON)
    db.add(profile)
    await db.flush()

    gap = GapAnalysis(
        job_analysis_id=job.id,
        profile_id=profile.id,
        keyword_ledger=KEYWORD_LEDGER,
    )
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


@pytest.mark.asyncio
async def test_named_skill_guard_changes_the_persisted_cv(seeded):
    """The full pipeline, end to end: #376's skills-list addition must be
    visible in the PERSISTED tailored_data -- not merely computable by calling
    the guard function directly."""
    db, job, profile, cv = seeded

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
        # Return bytes rather than raising: whether the render/measure step's
        # exception is swallowed depends on which path runs (the condense path
        # catches, the plain render path does not), so a raising mock makes the
        # test depend on the draft's page count instead of on the guard.
        patch(
            "applire.services.cv._html_to_pdf",
            AsyncMock(return_value=b"%PDF-1.4 synthetic"),
        ),
    ):
        mock_session_local.return_value.__aenter__.return_value = db
        await _render_cv_background(
            cv_id=cv.id, job_id=job.id, profile_id=profile.id, template="classic_german",
        )

    await db.refresh(cv)
    from applire.models.cv import CVGenerationStatus

    assert cv.status == CVGenerationStatus.ready.value
    tailored = cv.tailored_data
    bullets = tailored["work_history"][0]["bullets"]
    skills = tailored["skills"]

    # NOTE: #303 was reverted (see the module docstring). This module
    # deliberately makes NO assertion about the Kubernetes bullet in either
    # direction -- neither that it is restored (it is not, post-revert) nor
    # that it stays absent, since the re-scoped fix is expected to change it.
    # Pinning current behaviour here would turn a known gap into a ratchet.

    # --- #376: "SAP MM" -- named in the delivered bullet -- is added to the
    # delivered skills list, which the writer's draft otherwise left it out
    # of. ---
    assert "SAP MM" in skills, (
        "#376: a skill named twice in the delivered document's own prose is "
        "missing from its own skills list"
    )
    assert "SAP PP" in skills
    assert _SAP_BULLET in bullets  # the asserting bullet itself is untouched
