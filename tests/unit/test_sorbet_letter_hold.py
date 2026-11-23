# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet invite gate WP-L — a letter whose delivered audit sets stop_and_fix is not
delivered as if it passed (GARH, founder ruling 2026-11-17).

Recorded turn (delivery-q10, Daniel): the delivered letter carried Cargonaut's work
in the Finleap paragraph, the audit was red, and the status flipped to ``ready`` with
no further round — nothing in the delivery tail read the delivered verdict. Now the
delivery loop runs ONE author round over the delivered findings
(``_letter_hold_round``), the terminal reviewer reviews the change, the audit runs
again, and a still-red letter is logged ``LETTER_HELD`` for the person.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import uuid

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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



FIXED = {"body": {"paragraphs": ["Dear Sir or Madam,", "fixed by the hold round", "Kind regards"]}}


async def _drive(db, job, cl, *, stop_and_fix, hold):
    from applire.services import cover_letter as svc

    async def fake_loop(**kw):
        return kw["draft"]

    audits = []
    real_update = svc._update_ats_report_letter

    async def counting_update(cl_, db_, **kw):
        audits.append(dict(cl_.letter_data or {}))
        return await real_update(cl_, db_, **kw)

    provider = MagicMock()
    provider.aparse_json = AsyncMock(
        return_value={"body": {"paragraphs": ["Dear Sir or Madam,", "x", "Kind regards"]}})
    with patch("applire.services.cover_letter.AsyncSessionLocal") as sl, \
         patch("applire.services.cover_letter.get_provider", return_value=provider), \
         patch("applire.services.cover_letter.review_and_refine",
               new=AsyncMock(side_effect=fake_loop)), \
         patch("applire.services.cover_letter._letter_stop_and_fix", side_effect=stop_and_fix), \
         patch("applire.services.cover_letter._letter_hold_round", new=hold), \
         patch("applire.services.cover_letter._update_ats_report_letter", side_effect=counting_update), \
         patch("applire.services.cover_letter_pdf.render_pdf",
               new=AsyncMock(side_effect=RuntimeError("no browser in unit test"))):
        sl.return_value.__aenter__.return_value = db
        await svc._render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)
    await db.refresh(cl)
    return audits


def _round(draft):
    from applire.services.attribution_round import TruthFinding, TruthRoundResult

    f = TruthFinding(family="misattributed", kind="oracle:misattributed", text="I also mentor …",
                     location="body.paragraphs[2]", detail_key="misattributed")
    return TruthRoundResult(draft=draft, findings=[f], removed=[], applied=True, llm_calls=1)


@pytest.mark.asyncio
async def test_a_red_delivered_audit_gets_one_author_round_and_is_audited_again(seeded, monkeypatch):
    monkeypatch.delenv("LETTER_PIPELINE", raising=False)
    db, job, cl = seeded
    hold = AsyncMock(return_value=_round(FIXED))
    # red on the first delivered audit, clean after the hold round, clean at the end
    audits = await _drive(db, job, cl, stop_and_fix=[True, False, False], hold=hold)
    assert hold.await_count == 1
    assert len(audits) >= 2, audits
    assert audits[-1] == FIXED, "the corrected letter is what the last audit read"
    assert cl.letter_data == FIXED
    assert cl.status == "ready"


@pytest.mark.asyncio
async def test_a_letter_still_red_after_the_round_is_held_for_the_person(seeded, monkeypatch, caplog):
    monkeypatch.delenv("LETTER_PIPELINE", raising=False)
    db, job, cl = seeded
    hold = AsyncMock(return_value=_round(FIXED))
    caplog.set_level(logging.WARNING, logger="applire.services.cover_letter")
    await _drive(db, job, cl, stop_and_fix=lambda _cl: True, hold=hold)
    assert hold.await_count == 1, "ONE round — the loop never spins on a red letter"
    assert any("LETTER_HELD" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_clean_letter_gets_no_round(seeded, monkeypatch, caplog):
    monkeypatch.delenv("LETTER_PIPELINE", raising=False)
    db, job, cl = seeded
    hold = AsyncMock(return_value=_round(FIXED))
    caplog.set_level(logging.WARNING, logger="applire.services.cover_letter")
    await _drive(db, job, cl, stop_and_fix=lambda _cl: False, hold=hold)
    assert hold.await_count == 0
    assert not any("LETTER_HELD" in r.getMessage() for r in caplog.records)
    assert cl.letter_data != FIXED


@pytest.mark.asyncio
async def test_the_hold_round_feeds_the_delivered_report_to_the_truth_round(monkeypatch):
    """``_letter_hold_round`` hands ``cl.truthfulness_report`` — the DELIVERED audit —
    to the truth round, and a correction failing the letter shape is not used."""
    from applire.services import cover_letter as svc

    cl = MagicMock()
    cl.letter_data = {"body": {"paragraphs": ["a", "b", "c"]}}
    cl.truthfulness_report = {"claims": ["DELIVERED"]}
    seen = {}

    async def fake_truth_round(draft, **kw):
        seen.update(kw)
        return _round(FIXED)

    monkeypatch.setattr("applire.services.attribution_round.run_truth_round", fake_truth_round)
    out = await svc._letter_hold_round(cl, profile_json={}, grounding_source="SRC", provider=None,
                                       system="SYS", corrector_prompt_fn=None)
    assert seen["report"] == {"claims": ["DELIVERED"]} and seen["document"] == "cover_letter"
    assert out is not None and out.draft == FIXED
    monkeypatch.setattr(svc, "letter_shape_ok", lambda _d: False)
    assert await svc._letter_hold_round(cl, profile_json={}, grounding_source="SRC", provider=None,
                                        system="SYS", corrector_prompt_fn=None) is None
