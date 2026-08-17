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

from tests.support.profile_factory import make_master_profile

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
    profile = make_master_profile(profile_json={
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
    """#539: the round that reviews the condense rewrite is the TERMINAL
    review's invocation (shared budget, not a separate condense chain) — the
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
async def test_primary_loop_does_not_receive_prefer_if(seeded):
    """#420 (ADR-021 amended 2026-08-02): the PRIMARY content loop must NOT
    carry the word-budget prefer_if. On a content loop the writer writes to
    the feedforward budget and correctors ADD demanded content, so the only
    draft satisfying the budget preference is structurally the pre-review
    draft — run 14's settle substituted it, silently discarding the attested
    scope fact and every reviewer-demanded delivery. The budget belongs to
    the feedforward prompt + the page-gated condense pass."""
    db, job, profile, cl = seeded

    from applire.services.cover_letter import _render_cover_letter_background

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
    assert calls[0].get("prefer_if") is None, (
        "the primary content loop must not carry the word-budget prefer_if (#420)"
    )


@pytest.mark.asyncio
async def test_condense_pass_alone_receives_prefer_if(seeded):
    """The terminal invocation that reviews the CONDENSE rewrite keeps the
    word-budget prefer_if — narrowing among condense-descendant drafts is its
    designed use (charter run #6; #539 wires it per-invocation). The primary
    loop must not carry it (#420) — asserted per call below."""
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
    assert calls[0].get("prefer_if") is None, "primary loop must not carry prefer_if (#420)"
    prefer_if = calls[1].get("prefer_if")
    assert prefer_if is not None, "condense loop must keep the word-budget prefer_if"
    within = {"body": {"paragraphs": [" ".join(["word"] * (budget - 5))]}}
    over = {"body": {"paragraphs": [" ".join(["word"] * (budget + 50))]}}
    assert prefer_if(within) is True
    assert prefer_if(over) is False


@pytest.mark.asyncio
async def test_condense_over_budget_result_emits_letter_over_budget_log(seeded, caplog):
    """Wave-6 follow-up (charter run #6, Task 3), carried into #539: if the
    condense rewrite's settled result STILL exceeds the region's word budget
    (e.g. because retain_if correctly refused a shorter draft that lost its
    closing), that must be countable after the fact via a distinct, stable
    LETTER_OVER_BUDGET log line naming the chain, the measured word count, and
    the norm. Since #539 the condense re-enters the terminal review, so the
    line carries the ``letter_terminal_review`` chain id (the
    ``cover_letter_condense`` vocabulary is retired with that chain)."""
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
    assert any("letter_terminal_review" in r.getMessage() for r in over_budget_lines)
    assert not any("cover_letter_condense" in r.getMessage() for r in over_budget_lines), (
        "the cover_letter_condense chain (and its log vocabulary) is retired (#539)"
    )
    assert any(str(budget) in r.getMessage() for r in over_budget_lines)


@pytest.mark.asyncio
async def test_primary_over_budget_settle_emits_letter_over_budget_log(seeded, caplog):
    """#420 (ADR-021 amended 2026-08-02): with prefer_if gone from the primary
    loop, an over-budget settled letter that never trips the page gate must
    still be countable — the norms violation is recorded, never resolved by
    reverting content."""
    import logging

    db, job, profile, cl = seeded

    from applire.services.cover_letter import _render_cover_letter_background
    from applire.norms import DEFAULT_REGION, REGION_NORMS

    budget = REGION_NORMS[DEFAULT_REGION].letter_body_word_budget
    over_budget_body = " ".join(["word"] * (budget + 50))

    mock_provider = MagicMock()
    mock_provider.aparse_json = AsyncMock(
        return_value=_letter(["Dear team,", over_budget_body])
    )

    async def fake_review(**kwargs):
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
        with caplog.at_level(logging.WARNING, logger="applire.llm.review"):
            await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    over_budget_lines = [r for r in caplog.records if "LETTER_OVER_BUDGET" in r.getMessage()]
    assert over_budget_lines, "expected a LETTER_OVER_BUDGET line for the primary settle"
    assert any(
        "chain=cover_letter " in r.getMessage() for r in over_budget_lines
    ), "the line must name the primary chain, not the condense chain"


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


@pytest.mark.asyncio
async def test_review_and_refine_receives_load_bearing_fn_from_keyword_ledger(seeded):
    """#306 (b): both the primary AND the condense review_and_refine calls must
    be wired with a load_bearing_fn built from the SAME keyword_ledger already
    routed to the reviewer prompt — proven behaviourally: a draft carrying a
    ledger-backed figure scores higher than one that doesn't."""
    db, job, profile, cl = seeded

    from applire.services.cover_letter import _render_cover_letter_background
    from applire.models.gap import GapAnalysis

    gap = GapAnalysis(
        job_analysis_id=job.id,
        profile_id=profile.id,
        keyword_ledger=[
            {
                "concept": "Budget- und Investitionsverantwortung",
                "status": "direct",
                "claimable": True,
                "surface_forms": ["Budgetverantwortung"],
                "evidence": "Budgetverantwortung ca. 6 Mio. € (Personal, Instandhaltung).",
            }
        ],
    )
    db.add(gap)
    await db.commit()

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
    load_bearing_fn = calls[0].get("load_bearing_fn")
    assert load_bearing_fn is not None, "load_bearing_fn was not wired into the primary review_and_refine call"

    with_figure = {"body": {"paragraphs": ["Ich trage eine Budgetverantwortung von 6 Mio. €."]}}
    without_figure = {"body": {"paragraphs": ["Ich trage Budgetverantwortung."]}}
    assert len(load_bearing_fn(with_figure)) > len(load_bearing_fn(without_figure))


@pytest.mark.asyncio
async def test_condense_pass_also_receives_load_bearing_fn(seeded):
    """The condense/refine loop must ALSO carry load_bearing_fn — mirroring
    test_condense_pass_also_receives_retain_if / _prefer_if."""
    db, job, profile, cl = seeded

    from applire.services.cover_letter import _render_cover_letter_background

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
        assert call.get("load_bearing_fn") is not None
    # Both calls share the SAME closure (built once, from the same ledger
    # snapshot) — never re-derived per call.
    assert calls[0]["load_bearing_fn"] is calls[1]["load_bearing_fn"]


# --- #306: the corrector must be told the coverage it already holds -----------
#
# backend/logs/llm/2026-08-06.jsonl, chain=cover_letter, 13:57-14:05 UTC (real
# provider): the loop's own deterministic coverage scan reported
# {Shopfloor-Management, Deutsch, SAP MM, Englisch} at round 1, {Deutsch,
# Englisch} at round 2, and {SMED, KVP} at round 3 — neither ever demanded
# before, both present in drafts 0 AND 1. Round 2's reviewer asked for an
# employer anchor on one sentence; the corrector's rewrite of that sentence
# deleted the clause carrying KVP (4,1 % -> 2,3 %) and the sentence carrying
# SMED (87 % -> 96 %). Rounds 3-4 went on recovering what draft 1 already had,
# and the chain exhausted at 5/5. The per-round coverage state existed all
# along — it was computed for the REVIEWER only.

_COVERAGE_LEDGER = [
    {
        "concept": "SMED",
        "status": "direct",
        "claimable": True,
        "surface_forms": ["SMED"],
        "evidence": "9 Jahre SMED, Ruestworkshops",
    }
]


@pytest.mark.asyncio
async def test_primary_loop_corrector_prompt_carries_the_coverage_it_holds(seeded):
    """#306: the primary loop's generator_prompt_fn must append the deterministic
    "already surfaced, do not drop" block for the draft being patched — proven
    behaviourally by calling the wired function, not by identity."""
    db, job, profile, cl = seeded

    from applire.models.gap import GapAnalysis
    from applire.services.cover_letter import _render_cover_letter_background

    db.add(GapAnalysis(job_analysis_id=job.id, profile_id=profile.id,
                       keyword_ledger=_COVERAGE_LEDGER))
    await db.commit()

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
    gen_fn = calls[0]["generator_prompt_fn"]
    holds = gen_fn(_letter(["We ran SMED workshops."]), "fix the anchor", "src")
    drops = gen_fn(_letter(["We ran workshops."]), "fix the anchor", "src")
    # The retention INSTRUCTION, not the echoed PREVIOUS OUTPUT block — that
    # block quotes the term either way, and is exactly what the real corrector
    # ignored on 2026-08-06.
    assert "COVERAGE ALREADY ACHIEVED" in holds
    assert "SMED" in holds.split("COVERAGE ALREADY ACHIEVED", 1)[1]
    assert "COVERAGE ALREADY ACHIEVED" not in drops


@pytest.mark.asyncio
async def test_condense_loop_corrector_prompt_carries_it_too(seeded):
    """The condense pass is a fresh rewrite under length pressure — the chain
    that lost SMED at round 2 on 2026-08-06 — so it needs the same block."""
    db, job, profile, cl = seeded

    from applire.models.gap import GapAnalysis
    from applire.services.cover_letter import _render_cover_letter_background

    db.add(GapAnalysis(job_analysis_id=job.id, profile_id=profile.id,
                       keyword_ledger=_COVERAGE_LEDGER))
    await db.commit()

    mock_provider = MagicMock()
    mock_provider.aparse_json = AsyncMock(
        side_effect=[
            _letter(["placeholder"]),
            _letter(["Dear team,", "condensed closing"]),
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
            return_value=("text", 3),
        ),
    ):
        mock_session_local.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    assert len(calls) == 2, "expected primary + condense review_and_refine calls"
    for call in calls:
        gen_fn = call["generator_prompt_fn"]
        prompt = gen_fn(_letter(["We ran SMED workshops."]), "fb", "src")
        assert "COVERAGE ALREADY ACHIEVED" in prompt
        assert "SMED" in prompt.split("COVERAGE ALREADY ACHIEVED", 1)[1]


# --- ADR-021 amended 2026-08-13, clause 4: the DO-NOT-CLAIM presence fact -----
#
# Gate charter run 1 / #531: 2 of the 3 DO-NOT-CLAIM findings named a term
# appearing nowhere in the graded draft, and the third conceded in its own text
# ("The sentence '...' is fine, but the broader context of the paragraph
# implies..."). The reviewer is asked a usage-honesty question that presupposes
# a presence determination, and the prompt forbids it from string-matching to
# answer. The wiring is what CI can pin; whether the reviewer ACTS on the fact
# is a prompt effect and needs a real-provider run (ADR-062 clause 7).

_FORBIDDEN_LEDGER = [
    {
        "concept": "Digitalisierung",
        "status": "gap",
        "claimable": False,
        "surface_forms": ["Digitalisierung"],
        "evidence": "",
    }
]


@pytest.mark.asyncio
async def test_primary_loop_reviewer_prompt_carries_the_forbidden_presence_fact(seeded):
    db, job, profile, cl = seeded

    from applire.models.gap import GapAnalysis
    from applire.services.cover_letter import _render_cover_letter_background

    db.add(
        GapAnalysis(
            job_analysis_id=job.id,
            profile_id=profile.id,
            keyword_ledger=_FORBIDDEN_LEDGER,
        )
    )
    await db.commit()

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
    fn = calls[0]["reviewer_prompt_fn"]

    carrying = fn(calls[0]["source"], _letter(["Die Digitalisierung Ihrer Werke reizt mich."]))
    assert "DO-NOT-CLAIM PRESENCE" in carrying
    present_block = carrying.split("DO-NOT-CLAIM PRESENCE", 1)[1]
    assert "Digitalisierung" in present_block

    # ...and the other direction, which is the #531 shape: the scan found none,
    # and the block says so rather than leaving the model to improvise.
    without = fn(calls[0]["source"], _letter(["Bei Weberit habe ich MES ausgerollt."]))
    assert "DO-NOT-CLAIM PRESENCE" in without
    assert "(none" in without.split("DO-NOT-CLAIM PRESENCE", 1)[1]


@pytest.mark.asyncio
async def test_condense_pass_carries_the_forbidden_presence_fact_too(seeded):
    """The condense loop reuses the composed reviewer_prompt_fn — mirroring
    test_condense_pass_also_receives_load_bearing_fn."""
    db, job, profile, cl = seeded

    from applire.models.gap import GapAnalysis
    from applire.services.cover_letter import _render_cover_letter_background

    db.add(
        GapAnalysis(
            job_analysis_id=job.id,
            profile_id=profile.id,
            keyword_ledger=_FORBIDDEN_LEDGER,
        )
    )
    await db.commit()

    mock_provider = MagicMock()
    mock_provider.aparse_json = AsyncMock(
        side_effect=[
            _letter(["placeholder"]),
            _letter(["Dear team,", "condensed closing"]),
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
            return_value=("text", 3),
        ),
    ):
        mock_session_local.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    assert len(calls) == 2, "expected primary + condense review_and_refine calls"
    for call in calls:
        rendered = call["reviewer_prompt_fn"](
            call["source"], _letter(["Die Digitalisierung Ihrer Werke reizt mich."])
        )
        assert "DO-NOT-CLAIM PRESENCE" in rendered
