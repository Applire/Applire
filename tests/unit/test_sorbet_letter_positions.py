# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet invite gate WP-L — the letter writer sees each position with its own facts.

Recorded turn (delivery-q10, 2026-11-18, letter-writer call 399; same shape in
delivery-2b): on the agent door no CV is linked to the flow, so the letter takes
the no-CV path and its ``cv_data`` is the profile view. ``build_cover_letter_prompt``
read only a CV's ``work_history`` / ``summary`` keys, so the CANDIDATE PROFILE went
out as ``Summary: `` and an EMPTY ``Recent experience:`` block in 11 of 12 captured
letter-writer calls. The writer had the work facts only as employer-less ledger
evidence and wrote one employer's mentoring, CI/CD and contract tests into the
other employer's paragraph. Luna replay of the captured call (n=5 each): as
captured 3/5 letters with a misplaced sentence; with this rendering 0/5; with the
CV's positions 0/5.

The names below are synthetic stand-ins for the recorded persona.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.prompts.cover_letter import (
    build_cover_letter_prompt,
    letter_profile_summary,
    letter_work_history,
)

CURRENT = "Nordwind Logistik GmbH"
EARLIER = "Brückenbau Fintech GmbH"


def _profile_view() -> dict:
    return {
        "personal_info": {"name": "Dario Beispiel", "email": "dario@example.com"},
        "professional_summary": {
            "de": "Backend-Entwickler mit Schwerpunkt Datenmodellierung.",
            "en": "Backend engineer with a focus on data modelling.",
        },
        "work_experience": [
            {
                "id": "w-nordwind",
                "company": CURRENT,
                "role": "Senior Backend Engineer",
                "start_date": "2021-03",
                "end_date": None,
                "is_current": True,
                "responsibilities": [
                    "Mentor two mid-level engineers.",
                    "Introduced contract tests between the tracking API and three consumer teams.",
                ],
                "achievements": ["Cut pager noise roughly in half."],
            },
            {
                "id": "w-brueckenbau",
                "company": EARLIER,
                "role": "Backend Engineer",
                "start_date": "2017-06",
                "end_date": "2021-02",
                "is_current": False,
                "responsibilities": ["Integrated Stripe checkout with webhooks."],
            },
        ],
        "skills": [{"name": "Python"}],
    }


def _block(prompt: str) -> str:
    start = prompt.index("Recent experience:")
    return prompt[start: prompt.index("\n\n", start)]


def test_profile_view_positions_reach_the_writer_under_their_own_employer():
    prompt = build_cover_letter_prompt(
        cv_data=_profile_view(), jd_text="Senior Backend Engineer", pre_gen_inputs={},
        detected_language="en",
    )
    block = _block(prompt)
    head_now = f"- Senior Backend Engineer at {CURRENT} (2021-03–present)"
    head_then = f"- Backend Engineer at {EARLIER} (2017-06–2021-02)"
    assert head_now in block and head_then in block, block
    now, then = block.index(head_now), block.index(head_then)
    mentor = block.index("Mentor two mid-level engineers.")
    contract = block.index("Introduced contract tests")
    pager = block.index("Cut pager noise")
    stripe = block.index("Integrated Stripe checkout")
    assert now < mentor < contract < pager < then < stripe, block
    assert "Summary: Backend engineer with a focus on data modelling." in prompt


def test_the_summary_follows_the_letter_language_and_falls_back():
    view = _profile_view()
    assert letter_profile_summary(view, "de").startswith("Backend-Entwickler")
    view["professional_summary"]["de"] = None
    assert letter_profile_summary(view, "de").startswith("Backend engineer")
    assert letter_profile_summary({"summary": "CV summary."}, "de") == "CV summary."


def test_a_cv_keeps_its_tailored_selection():
    cv = {"work_history": [{"role": "R", "company": "C", "start_date": "2020", "end_date": None,
                            "bullets": [f"b{i}" for i in range(9)]}]}
    (pos,) = letter_work_history(cv)
    assert pos["bullets"] == [f"b{i}" for i in range(6)]
    prompt = build_cover_letter_prompt(cv_data=cv, jd_text="x", pre_gen_inputs={},
                                       detected_language="de")
    assert "- R at C (2020–present)" in _block(prompt)


# ── SEAM: the real background task, no-CV branch (the agent door's path) ──────────


@pytest_asyncio.fixture
async def seeded():
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
    from tests.support.profile_factory import make_master_profile
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(User(id=uuid.uuid4(), email="t-wpl@test.com"))
        job = JobAnalysis(
            id=uuid.uuid4(), raw_text_hash="twplhash",
            raw_text="Senior Backend Engineer (m/w/d) bei Beispiel Pay",
            role_title="Senior Backend Engineer", company_name="Beispiel Pay",
            required_skills=[], nice_to_have_skills=[], keywords=[],
            seniority_level="senior", company_culture_signals=[], language_requirement="en",
        )
        db.add(job)
        profile = make_master_profile(profile_json=_profile_view())
        db.add(profile)
        await db.flush()
        cl = GeneratedCoverLetter(
            job_analysis_id=job.id, profile_id=profile.id, template="classic_german",
            letter_data={}, pre_gen_inputs={}, status=CoverLetterStatus.pending.value,
            document_language="en",
        )
        db.add(cl)
        await db.commit()
        await db.refresh(cl)
        yield db, job, cl
    await engine.dispose()


@pytest.mark.asyncio
async def test_SEAM_no_cv_letter_writer_prompt_carries_the_positions(seeded, monkeypatch):
    monkeypatch.delenv("LETTER_PIPELINE", raising=False)
    db, job, cl = seeded
    captured: dict = {}

    def spy(**kw):
        captured["prompt"] = build_cover_letter_prompt(**kw)
        return captured["prompt"]

    async def fake_loop(**kw):
        return kw["draft"]

    provider = MagicMock()
    provider.aparse_json = AsyncMock(
        return_value={"body": {"paragraphs": ["Dear Sir or Madam,", "x", "Kind regards"]}})
    with patch("applire.services.cover_letter.AsyncSessionLocal") as sl, \
         patch("applire.services.cover_letter.get_provider", return_value=provider), \
         patch("applire.services.cover_letter.build_cover_letter_prompt", side_effect=spy), \
         patch("applire.services.cover_letter.review_and_refine",
               new=AsyncMock(side_effect=fake_loop)), \
         patch("applire.services.cover_letter_pdf.render_pdf",
               new=AsyncMock(side_effect=RuntimeError("no browser in unit test"))):
        sl.return_value.__aenter__.return_value = db
        from applire.services.cover_letter import _render_cover_letter_background
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    block = _block(captured["prompt"])
    assert f"at {CURRENT}" in block and f"at {EARLIER}" in block, block
    assert block.index("Mentor two mid-level engineers.") < block.index(f"at {EARLIER}")


def test_v2_writer_prompt_carries_the_cross_language_title_rule():
    """Q3-1 / MD-Q9 R2 applied to the letter. With the positions now in the prompt
    (above), the verbatim-title rule printed "Produktionsleiter" in English letters
    (Luna replay, delivery-2b cross A/B, n=5 each: German leftovers other than the
    posting's own "KVP" in 4/5 and 3/5 letters; with this rule 1/5 and 1/5)."""
    from applire.prompts.cover_letter import SYSTEM_PROMPT, build_system_prompt

    v2 = build_system_prompt("en", pipeline="v2")
    assert "CROSS-LANGUAGE" in v2 and '"Produktionsleiter" -> "Production' in v2
    assert "vocational-qualification names stay exactly as recorded" in v2
    assert build_system_prompt("en", pipeline="v1") == SYSTEM_PROMPT
    assert "CROSS-LANGUAGE" not in SYSTEM_PROMPT
