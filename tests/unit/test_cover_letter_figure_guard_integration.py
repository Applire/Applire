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

"""#254 — the letter-figure guard wired into the real generation path.

Drives ``_render_cover_letter_background`` end to end (the actual service
entrypoint, not just the pure guard function) so the wiring itself — not just
the guard's own logic — is under test. ``review_and_refine`` is mocked to
hand back the FABRICATED corrector output directly: the live bug was
introduced by the corrector call inside that loop, not the writer's initial
draft, so this is the faithful reproduction of the #254 vector — the guard
must catch it on review_and_refine's OWN return value, exactly as it does in
production.
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

# The live #254 shape: "five" is a DataCore Systems fact; the corrector must
# never be allowed to substantiate a Vector Analytics mentoring claim with it.
PROFILE_JSON = {
    "personal_info": {"name": "Anna Bauer", "email": "anna@example.com"},
    "professional_summary": {"en": "Engineering leader shipping production systems."},
    "work_experience": [
        {
            "id": "w-datacore",
            "company": "DataCore Systems",
            "role": "Platform Engineering Lead",
            "achievements": [
                "Lead a team of five tech leads and system owners across the "
                "platform organisation.",
            ],
        },
        {
            "id": "w-vector",
            "company": "Vector Analytics",
            "role": "Senior Backend Engineer",
            "achievements": [
                "Delivered a 70% reduction in checkout latency through async "
                "pipeline redesign.",
            ],
        },
    ],
}


def _fabricated_letter():
    """Stand-in for the corrector's OWN output — the writer draft never had
    this figure; only the reviewer-driven retry call does (ground truth)."""
    return {
        "header": {"name": "Anna Bauer"},
        "recipient": {"name": None, "company": "Vector Analytics", "date": None},
        "body": {
            "paragraphs": [
                "Dear Hiring Team,",
                "At Vector Analytics, I have experience mentoring teams of 5+ "
                "engineers and driving delivery excellence, having delivered a "
                "70% reduction in checkout latency.",
                "Sincerely,",
            ]
        },
        "signature": {"closing": None, "name": None},
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
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.user import User

    user = User(id=uuid.uuid4(), email="guard-it@test.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="guardit123",
        raw_text="Senior Backend Engineer at Vector Analytics",
        role_title="Senior Backend Engineer",
        company_name="Vector Analytics",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="en",
    )
    db.add(job)
    profile = MasterProfile(profile_json=PROFILE_JSON)
    db.add(profile)
    await db.flush()

    cl = GeneratedCoverLetter(
        job_analysis_id=job.id,
        profile_id=profile.id,
        template="classic_german",
        letter_data={},
        pre_gen_inputs={},
        status=CoverLetterStatus.pending.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)
    return db, job, profile, cl


@pytest.mark.asyncio
async def test_corrector_borrowed_figure_is_stripped_before_persist(seeded):
    """The primary review_and_refine loop: the guard must run on ITS return
    value and strip the borrowed '5+' before letter_data is ever persisted."""
    db, job, profile, cl = seeded

    from applire.services.cover_letter import _render_cover_letter_background

    mock_provider = MagicMock()
    mock_provider.aparse_json = AsyncMock(return_value={"body": {"paragraphs": []}})

    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local,
        patch("applire.services.cover_letter.get_provider", return_value=mock_provider),
        patch(
            "applire.services.cover_letter.review_and_refine",
            AsyncMock(return_value=_fabricated_letter()),
        ),
        patch(
            "applire.services.cover_letter_pdf.render_pdf",
            AsyncMock(side_effect=RuntimeError("no browser in unit test")),
        ),
    ):
        mock_session_local.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    await db.refresh(cl)
    from applire.models.cover_letter import CoverLetterStatus

    assert cl.status == CoverLetterStatus.ready.value
    body_text = " ".join(cl.letter_data["body"]["paragraphs"])

    # the fabricated, misattributed headcount must be gone
    assert "5+" not in body_text
    # the honestly-grounded BioNTech-style figure in the SAME letter survives
    assert "70%" in body_text
    # the surrounding clause survives, figure-free — not deleted wholesale
    assert "mentoring teams of" in body_text
    assert "Vector Analytics" in body_text


@pytest.mark.asyncio
async def test_condense_pass_output_is_also_guarded(seeded):
    """The condense/refine loop is a SEPARATE review_and_refine call — the
    corrector shape can reappear there too, so the guard must run on ITS
    output as well, not just the primary loop's."""
    db, job, profile, cl = seeded

    from applire.services.cover_letter import _render_cover_letter_background

    mock_provider = MagicMock()
    mock_provider.aparse_json = AsyncMock(
        side_effect=[
            {"body": {"paragraphs": ["placeholder"]}},  # initial writer call
            _fabricated_letter(),  # build_condense_prompt call
        ]
    )

    async def _fake_render_pdf(cl_id, allow_unready=False):
        return b"%PDF-1.4 fake"

    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local,
        patch("applire.services.cover_letter.get_provider", return_value=mock_provider),
        patch(
            "applire.services.cover_letter.review_and_refine",
            # First call = primary loop (a clean, unrelated draft); second call
            # = the condense loop's OWN review — returns the fabricated
            # corrector shape, exactly like the primary-loop test.
            AsyncMock(
                side_effect=[
                    {"body": {"paragraphs": ["Dear Hiring Team,", "Sincerely,"]}},
                    _fabricated_letter(),
                ]
            ),
        ),
        patch("applire.services.cover_letter_pdf.render_pdf", AsyncMock(side_effect=_fake_render_pdf)),
        patch(
            "applire.services.ats_audit.extract_text_and_pages",
            return_value=("text", 3),  # over the 1-page DACH letter norm — forces condense
        ),
    ):
        mock_session_local.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    await db.refresh(cl)
    body_text = " ".join(cl.letter_data["body"]["paragraphs"])
    assert "5+" not in body_text
    assert "70%" in body_text
