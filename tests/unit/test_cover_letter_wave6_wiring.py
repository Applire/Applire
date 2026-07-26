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

"""#272 wave-6 letter composition — Task 3 (retain_if wiring) and Task 6
(word-floor reviewer wrapper wiring), driven end to end through
``_render_cover_letter_background`` (the real service entrypoint), mirroring
the pattern in test_cover_letter_figure_guard_integration.py.
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


def _letter(paragraphs):
    return {
        "header": {"name": "Anna Bauer"},
        "recipient": {"name": None, "company": "Vector Analytics", "date": None},
        "body": {"paragraphs": paragraphs},
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

    user = User(id=uuid.uuid4(), email="wave6-wiring@test.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="wave6wiring123",
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
    profile = MasterProfile(profile_json={
        "personal_info": {"name": "Anna Bauer", "email": "anna@example.com"},
        "work_experience": [],
        "skills": [],
    })
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
async def test_review_and_refine_receives_has_closing_paragraph_as_retain_if(seeded):
    """#272 Task 3: the primary review_and_refine call for the cover-letter
    chain must be wired with retain_if=has_closing_paragraph — the SAME
    function object services/cover_letter_positioning.py exports (not a
    reimplementation)."""
    db, job, profile, cl = seeded

    from applire.services.cover_letter import _render_cover_letter_background
    from applire.services.cover_letter_positioning import has_closing_paragraph

    mock_provider = MagicMock()
    mock_provider.aparse_json = AsyncMock(return_value=_letter(["Dear team,", "Sincerely,"]))

    calls: list[dict] = []

    async def fake_review(**kwargs):
        calls.append(kwargs)
        return kwargs["draft"]

    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local,
        patch("applire.services.cover_letter.get_provider", return_value=mock_provider),
        patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review),
        patch(
            "applire.services.cover_letter_pdf.render_pdf",
            AsyncMock(side_effect=RuntimeError("no browser in unit test")),
        ),
    ):
        mock_session_local.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    assert calls, "review_and_refine was not called"
    assert calls[0].get("retain_if") is has_closing_paragraph


@pytest.mark.asyncio
async def test_condense_pass_also_receives_retain_if(seeded):
    """The condense/refine loop is a SEPARATE review_and_refine call — the
    retain_if wiring must be present there too."""
    db, job, profile, cl = seeded

    from applire.services.cover_letter import _render_cover_letter_background
    from applire.services.cover_letter_positioning import has_closing_paragraph

    mock_provider = MagicMock()
    mock_provider.aparse_json = AsyncMock(
        side_effect=[
            _letter(["placeholder"]),  # initial writer call
            _letter(["Dear team,", "condensed closing"]),  # build_condense_prompt call
        ]
    )

    calls: list[dict] = []

    async def fake_review(**kwargs):
        calls.append(kwargs)
        return kwargs["draft"]

    async def _fake_render_pdf(cl_id, allow_unready=False):
        return b"%PDF-1.4 fake"

    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local,
        patch("applire.services.cover_letter.get_provider", return_value=mock_provider),
        patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review),
        patch("applire.services.cover_letter_pdf.render_pdf", AsyncMock(side_effect=_fake_render_pdf)),
        patch(
            "applire.services.ats_audit.extract_text_and_pages",
            return_value=("text", 3),  # over the 1-page DACH letter norm — forces condense
        ),
    ):
        mock_session_local.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    assert len(calls) == 2, "expected primary + condense review_and_refine calls"
    assert calls[0].get("retain_if") is has_closing_paragraph
    assert calls[1].get("retain_if") is has_closing_paragraph


@pytest.mark.asyncio
async def test_review_and_refine_receives_prefer_if_word_budget_check(seeded):
    """Wave-6 follow-up (charter run #6, Task 2): the primary review_and_refine
    call must also be wired with a ``prefer_if`` that checks the region's word
    budget — proven behaviourally (it is a closure, not an importable stable
    function, unlike retain_if) against a within-budget and an over-budget
    draft."""
    db, job, profile, cl = seeded

    from applire.services.cover_letter import _render_cover_letter_background
    from applire.norms import DEFAULT_REGION, REGION_NORMS

    budget = REGION_NORMS[DEFAULT_REGION].letter_body_word_budget

    mock_provider = MagicMock()
    mock_provider.aparse_json = AsyncMock(return_value=_letter(["Dear team,", "Sincerely,"]))

    calls: list[dict] = []

    async def fake_review(**kwargs):
        calls.append(kwargs)
        return kwargs["draft"]

    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local,
        patch("applire.services.cover_letter.get_provider", return_value=mock_provider),
        patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review),
        patch(
            "applire.services.cover_letter_pdf.render_pdf",
            AsyncMock(side_effect=RuntimeError("no browser in unit test")),
        ),
    ):
        mock_session_local.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    assert calls, "review_and_refine was not called"
    prefer_if = calls[0].get("prefer_if")
    assert prefer_if is not None, "prefer_if was not wired into the primary review_and_refine call"

    within = {"body": {"paragraphs": [" ".join(["word"] * (budget - 5))]}}
    over = {"body": {"paragraphs": [" ".join(["word"] * (budget + 50))]}}
    assert prefer_if(within) is True
    assert prefer_if(over) is False


@pytest.mark.asyncio
async def test_condense_pass_also_receives_prefer_if(seeded):
    """The condense/refine loop's review_and_refine call must ALSO carry the
    word-budget prefer_if — mirroring test_condense_pass_also_receives_retain_if."""
    db, job, profile, cl = seeded

    from applire.services.cover_letter import _render_cover_letter_background
    from applire.norms import DEFAULT_REGION, REGION_NORMS

    budget = REGION_NORMS[DEFAULT_REGION].letter_body_word_budget

    mock_provider = MagicMock()
    mock_provider.aparse_json = AsyncMock(
        side_effect=[
            _letter(["placeholder"]),  # initial writer call
            _letter(["Dear team,", "condensed closing"]),  # build_condense_prompt call
        ]
    )

    calls: list[dict] = []

    async def fake_review(**kwargs):
        calls.append(kwargs)
        return kwargs["draft"]

    async def _fake_render_pdf(cl_id, allow_unready=False):
        return b"%PDF-1.4 fake"

    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local,
        patch("applire.services.cover_letter.get_provider", return_value=mock_provider),
        patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review),
        patch("applire.services.cover_letter_pdf.render_pdf", AsyncMock(side_effect=_fake_render_pdf)),
        patch(
            "applire.services.ats_audit.extract_text_and_pages",
            return_value=("text", 3),  # over the 1-page DACH letter norm — forces condense
        ),
    ):
        mock_session_local.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    assert len(calls) == 2, "expected primary + condense review_and_refine calls"
    for call in calls:
        prefer_if = call.get("prefer_if")
        assert prefer_if is not None
        within = {"body": {"paragraphs": [" ".join(["word"] * (budget - 5))]}}
        over = {"body": {"paragraphs": [" ".join(["word"] * (budget + 50))]}}
        assert prefer_if(within) is True
        assert prefer_if(over) is False


@pytest.mark.asyncio
async def test_condense_over_budget_result_emits_letter_over_budget_log(seeded, caplog):
    """Wave-6 follow-up (charter run #6, Task 3): if the condense pass's settled
    result STILL exceeds the region's word budget (e.g. because retain_if
    correctly refused a shorter draft that lost its closing), that must be
    countable after the fact via a distinct, stable LETTER_OVER_BUDGET log line
    naming the chain, the measured word count, and the norm."""
    import logging

    db, job, profile, cl = seeded

    from applire.services.cover_letter import _render_cover_letter_background
    from applire.norms import DEFAULT_REGION, REGION_NORMS

    budget = REGION_NORMS[DEFAULT_REGION].letter_body_word_budget
    over_budget_body = " ".join(["word"] * (budget + 50))

    mock_provider = MagicMock()
    mock_provider.aparse_json = AsyncMock(
        side_effect=[
            _letter(["placeholder"]),  # initial writer call
            _letter(["Dear team,", over_budget_body]),  # condense call — still over budget
        ]
    )

    async def fake_review(**kwargs):
        return kwargs["draft"]

    async def _fake_render_pdf(cl_id, allow_unready=False):
        return b"%PDF-1.4 fake"

    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local,
        patch("applire.services.cover_letter.get_provider", return_value=mock_provider),
        patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review),
        patch("applire.services.cover_letter_pdf.render_pdf", AsyncMock(side_effect=_fake_render_pdf)),
        patch(
            "applire.services.ats_audit.extract_text_and_pages",
            return_value=("text", 3),  # over the 1-page DACH letter norm — forces condense
        ),
    ):
        mock_session_local.return_value.__aenter__.return_value = db
        with caplog.at_level(logging.WARNING, logger="applire.llm.review"):
            await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    over_budget_lines = [r for r in caplog.records if "LETTER_OVER_BUDGET" in r.getMessage()]
    assert over_budget_lines, "expected a LETTER_OVER_BUDGET log line"
    assert any("cover_letter_condense" in r.getMessage() for r in over_budget_lines)
    assert any(str(budget) in r.getMessage() for r in over_budget_lines)


@pytest.mark.asyncio
async def test_condense_within_budget_result_emits_no_over_budget_log(seeded, caplog):
    """No LETTER_OVER_BUDGET noise when the condensed result actually fits the
    budget — the log must be specific to the genuine violation, not fire on
    every condense pass."""
    import logging

    db, job, profile, cl = seeded

    from applire.services.cover_letter import _render_cover_letter_background

    mock_provider = MagicMock()
    mock_provider.aparse_json = AsyncMock(
        side_effect=[
            _letter(["placeholder"]),  # initial writer call
            _letter(["Dear team,", "a short condensed closing paragraph indeed"]),
        ]
    )

    async def fake_review(**kwargs):
        return kwargs["draft"]

    async def _fake_render_pdf(cl_id, allow_unready=False):
        return b"%PDF-1.4 fake"

    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local,
        patch("applire.services.cover_letter.get_provider", return_value=mock_provider),
        patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review),
        patch("applire.services.cover_letter_pdf.render_pdf", AsyncMock(side_effect=_fake_render_pdf)),
        patch(
            "applire.services.ats_audit.extract_text_and_pages",
            return_value=("text", 3),
        ),
    ):
        mock_session_local.return_value.__aenter__.return_value = db
        with caplog.at_level(logging.WARNING, logger="applire.llm.review"):
            await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    assert not any("LETTER_OVER_BUDGET" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_reviewer_prompt_fn_carries_word_floor_block_when_under_floor(seeded):
    """#272 Task 6: the composed reviewer_prompt_fn wired into review_and_refine
    must append the deterministic WORD FLOOR block when the CURRENT draft's body
    is under REGION_NORMS[DACH].letter_body_word_floor — proven by invoking the
    captured reviewer_prompt_fn directly against a thin draft."""
    db, job, profile, cl = seeded

    from applire.services.cover_letter import _render_cover_letter_background
    from applire.norms import DEFAULT_REGION, REGION_NORMS

    floor = REGION_NORMS[DEFAULT_REGION].letter_body_word_floor

    mock_provider = MagicMock()
    mock_provider.aparse_json = AsyncMock(
        return_value=_letter(["Dear team,", "Short closing paragraph right here today."])
    )

    calls: list[dict] = []

    async def fake_review(**kwargs):
        calls.append(kwargs)
        return kwargs["draft"]

    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local,
        patch("applire.services.cover_letter.get_provider", return_value=mock_provider),
        patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review),
        patch(
            "applire.services.cover_letter_pdf.render_pdf",
            AsyncMock(side_effect=RuntimeError("no browser in unit test")),
        ),
    ):
        mock_session_local.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    assert calls
    reviewer_prompt_fn = calls[0]["reviewer_prompt_fn"]
    thin_draft = {"body": {"paragraphs": ["Only a few words in this whole letter body."]}}
    rendered = reviewer_prompt_fn(calls[0]["source"], thin_draft)
    assert "WORD FLOOR" in rendered
    assert "insufficient selected evidence" in rendered.lower()
    assert str(floor) in rendered


@pytest.mark.asyncio
async def test_reviewer_prompt_fn_omits_word_floor_block_when_at_or_above_floor(seeded):
    """No WORD FLOOR block when the draft's body is at/above the floor."""
    db, job, profile, cl = seeded

    from applire.services.cover_letter import _render_cover_letter_background
    from applire.norms import DEFAULT_REGION, REGION_NORMS

    floor = REGION_NORMS[DEFAULT_REGION].letter_body_word_floor

    mock_provider = MagicMock()
    mock_provider.aparse_json = AsyncMock(
        return_value=_letter(["Dear team,", "Short closing paragraph right here today."])
    )

    calls: list[dict] = []

    async def fake_review(**kwargs):
        calls.append(kwargs)
        return kwargs["draft"]

    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local,
        patch("applire.services.cover_letter.get_provider", return_value=mock_provider),
        patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review),
        patch(
            "applire.services.cover_letter_pdf.render_pdf",
            AsyncMock(side_effect=RuntimeError("no browser in unit test")),
        ),
    ):
        mock_session_local.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    assert calls
    reviewer_prompt_fn = calls[0]["reviewer_prompt_fn"]
    full_draft = {"body": {"paragraphs": [" ".join(["word"] * (floor + 20))]}}
    rendered = reviewer_prompt_fn(calls[0]["source"], full_draft)
    assert "WORD FLOOR" not in rendered
